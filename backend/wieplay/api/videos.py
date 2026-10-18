"""Videos: create, upload (S3 multipart, parts signed here), list, delete (PLAN §7.2).

Before a video row exists, the filming rules run fresh (core/filming.py): refused means
nothing is stored, not even a staging object.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wieplay.api.access import TeamAccess, team_refs, visible_team, visible_video
from wieplay.api.deps import (
    BridgeDep,
    DbDep,
    SettingsDep,
    StoreDep,
    ViewerDep,
    audit,
)
from wieplay.api.media import permission_cache
from wieplay.core import guests, imports, names, pipeline
from wieplay.core.config import Settings
from wieplay.core.filming import (
    Blocker,
    FilmingCheck,
    check_filming,
    club_today,
    refresh_rosters,
)
from wieplay.core.models import InboxFile, Job, Segment, Video, VideoSourceFile, WdGame, WdTeam
from wieplay.core.permissions import (
    Viewer,
    can_delete_video,
    can_export,
    can_import_link,
    can_share_with_team,
    can_tag,
    can_upload,
    can_use_inbox,
    lead_lineages,
    visible_team_ids,
)
from wieplay.core.storage import ObjectStore, safe_file_name

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["videos"])

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".mkv", ".avi")
DASH = "\u2013"
UPCOMING_DAYS = 21
FILMING_PAST_DAYS = 400
DELETED_SWEEP_AFTER = timedelta(hours=2)  # beyond the 1 h worker upload URLs
UPCOMING_MAX = 12
MAX_INT4 = 2**31 - 1  # Wiedisync's ids are Postgres integers


class FilmingBlocked(Exception):
    """Rendered as 409 with the blockers (see app.py)."""

    def __init__(self, check: FilmingCheck) -> None:
        super().__init__("filming blocked")
        self.check = check


class BlockerOut(BaseModel):
    code: str
    count: int | None
    detail: str | None


class FilmingOut(BaseModel):
    allowed: bool
    blockers: list[BlockerOut]
    adult_checked_at: datetime | None


class UpcomingOut(FilmingOut):
    kind: Literal["match"] = "match"  # games only (matches only since 16.10.2026)
    id: int
    date: date
    label: str


class VideoConfigOut(BaseModel):
    enabled: bool
    part_size: int
    max_file_bytes: int
    max_files: int


class RenditionOut(BaseModel):
    name: str
    width: int
    height: int
    fps: float


class FileOut(BaseModel):
    id: uuid.UUID
    part_no: int
    name: str
    size: int
    part_size: int
    part_count: int
    uploaded: bool


class ImportedFromOut(BaseModel):
    platform: str
    url: str
    channel: str | None
    title: str | None


class AnalysisBriefOut(BaseModel):
    """Where the automatic analysis of a ready video stands (PLAN §10.1), for lists."""

    # none: never queued; queued | running; done; failed (the latest analyse job)
    status: Literal["none", "queued", "running", "done", "failed"]
    # Rally suggestions nobody has answered yet.
    open: int = 0


class NamesOut(BaseModel):
    """What the app writes instead of us and them (wieplay/core/names.py)."""

    us: str  # short: "KSCW H3"
    them: str | None  # short; None: not known ("Opponent")
    us_full: str  # the official name, for tooltips
    them_full: str | None
    # Where the opponent's name came from: set by a coach, the game, the video's title.
    them_from: Literal["set", "game", "title"] | None
    # What a coach set for this video (the setup's fields); None: derived.
    us_set: str | None
    them_set: str | None


class VideoOut(BaseModel):
    id: uuid.UUID
    team_id: int
    team_name: str
    team_season: str
    sport: Literal["volleyball", "basketball"]
    kind: str  # "match"; older rows from before 16.10.2026 may say "training"
    game_id: int | None
    title: str
    recorded_on: date
    source: str
    # For an imported video: where it came from (PLAN §7.2.2).
    imported_from: ImportedFromOut | None = None
    status: str
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    has_audio: bool | None
    meets_standard: bool | None
    standard_issues: list[str]
    renditions: list[RenditionOut]
    has_thumbs: bool
    progress: float | None
    error: str | None
    created_at: datetime
    created_by: int
    can_delete: bool
    can_upload: bool
    can_tag: bool
    can_share: bool
    can_export: bool
    files: list[FileOut]
    # The rally list is complete and right (a coach said so); PLAN §10.1.
    rallies_reviewed_at: datetime | None = None
    # The automatic analysis; None while the video is not ready.
    analysis: AnalysisBriefOut | None = None
    # The two teams' names, for where the app would say us and them.
    names: NamesOut | None = None
    # A coach said there is no scoreboard in the picture (the setup's scoreboard step).
    no_scoreboard: bool = False
    # ... or that there is no official scoresheet for this match (the setup's scoresheet step).
    no_scoresheet: bool = False


class SetupIn(BaseModel):
    """The video's setup a coach changes on the video page. Fields left out stay; a name set
    to null or "" goes back to the derived one."""

    name_us: str | None = Field(default=None, max_length=80)
    name_them: str | None = Field(default=None, max_length=80)
    no_scoreboard: bool | None = None
    no_scoresheet: bool | None = None


class FileIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    type: str | None = Field(default=None, max_length=128)


class VideoCreateIn(BaseModel):
    """A match: a Wiedisync game, or a date and a title for one that is not in Wiedisync
    (a friendly, tournament or cup)."""

    kind: Literal["match"] = "match"  # matches only (16.10.2026); kept for older clients
    game_id: int | None = None
    recorded_on: date | None = None
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    files: list[FileIn] = Field(default_factory=list, max_length=50)
    inbox_file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    # A YouTube or Twitch video of an allow-listed channel (PLAN §7.2.2).
    import_url: str | None = Field(default=None, min_length=5, max_length=500)


class PartsIn(BaseModel):
    parts: list[int] = Field(min_length=1, max_length=100)


class PartUrlOut(BaseModel):
    part: int
    url: str


class UploadedPartOut(BaseModel):
    part: int
    size: int


def _blockers(check: FilmingCheck) -> list[BlockerOut]:
    return [BlockerOut(code=b.code, count=b.count, detail=b.detail) for b in check.blockers]


def blocked_body(check: FilmingCheck) -> dict[str, Any]:
    return {
        "detail": "Filming is not allowed for this team on that day",
        "blockers": [b.model_dump() for b in _blockers(check)],
    }


def _part_count(size: int, settings: Settings) -> int:
    return max(1, math.ceil(size / settings.upload_part_bytes))


def _manages(viewer: Viewer, access: TeamAccess) -> bool:
    """Coaches, team managers and sport admins of the team, and admins: they see videos
    before they are ready, with errors and file details. Players see ready videos only."""
    return can_upload(viewer, access.ref) or can_delete_video(viewer, access.ref, access.leads)


IN_PROGRESS = ("uploading", "importing", "queued", "processing")


def _progress(db: Session, videos: list[Video]) -> dict[uuid.UUID, float]:
    """Share of each unfinished video's jobs that is done, in one query."""
    ids = [v.id for v in videos if v.status in IN_PROGRESS]
    if not ids:
        return {}
    done: dict[uuid.UUID, list[float]] = {}
    for video_id, status, progress in db.execute(
        select(Job.video_id, Job.status, Job.progress).where(
            Job.video_id.in_(ids), Job.kind != "purge"
        )
    ):
        if video_id is not None:
            done.setdefault(video_id, []).append(
                1.0 if status == "succeeded" else (progress or 0.0)
            )
    return {k: round(sum(v) / len(v), 3) for k, v in done.items()}


JOB_TO_ANALYSIS: dict[str, Literal["queued", "running", "done", "failed"]] = {
    "queued": "queued",
    "running": "running",
    "succeeded": "done",
    "failed": "failed",
}


def _analysis(db: Session, videos: list[Video]) -> dict[uuid.UUID, AnalysisBriefOut]:
    """The latest analyse job and the open rally suggestions of each ready video, in two
    queries: "Ready" says the video plays, this says whether WiePlay has looked at it yet."""
    ids = [v.id for v in videos if v.status == "ready"]
    if not ids:
        return {}
    latest: dict[uuid.UUID, tuple[datetime, str]] = {}
    for video_id, status, created in db.execute(
        select(Job.video_id, Job.status, Job.created_at).where(
            Job.video_id.in_(ids), Job.kind == "analyse"
        )
    ):
        if video_id is not None and (video_id not in latest or created > latest[video_id][0]):
            latest[video_id] = (created, status)
    open_by_video = dict(
        db.execute(
            select(Segment.video_id, func.count())
            .where(
                Segment.video_id.in_(ids),
                Segment.source == "model",
                Segment.kind == "rally",
                Segment.review == "unreviewed",
                Segment.deleted_at.is_(None),
            )
            .group_by(Segment.video_id)
        ).all()
    )
    return {
        video_id: AnalysisBriefOut(
            status=JOB_TO_ANALYSIS.get(latest[video_id][1], "none")
            if video_id in latest
            else "none",
            open=int(open_by_video.get(video_id, 0)),
        )
        for video_id in ids
    }


def _imported_from(video: Video) -> ImportedFromOut | None:
    origin = (video.capture or {}).get("import")
    if video.source != "import" or not isinstance(origin, dict):
        return None
    return ImportedFromOut(
        platform=str(origin.get("platform") or ""),
        url=str(origin.get("url") or ""),
        channel=origin.get("channel"),
        title=origin.get("title"),
    )


def _video_out(
    db: Session,
    video: Video,
    access: TeamAccess,
    viewer: Viewer,
    settings: Settings,
    progress_by_video: dict[uuid.UUID, float] | None = None,
    analysis_by_video: dict[uuid.UUID, AnalysisBriefOut] | None = None,
) -> VideoOut:
    manager = _manages(viewer, access)
    if progress_by_video is None:
        progress_by_video = _progress(db, [video])
    if analysis_by_video is None:
        analysis_by_video = _analysis(db, [video])
    progress = progress_by_video.get(video.id)
    fps = video.fps_num / video.fps_den if video.fps_num and video.fps_den else None
    found = names.for_video(
        db,
        video,
        access.team.name,
        *guests.club_names(access.team, settings.club_short_name, settings.club_name),
    )
    return VideoOut(
        id=video.id,
        team_id=video.team_id,
        team_name=access.team.name,
        team_season=access.team.season,
        sport=access.team.sport,
        kind=video.kind,
        game_id=video.game_id,
        title=video.title,
        recorded_on=video.recorded_on,
        source=video.source,
        imported_from=_imported_from(video),
        status=video.status,
        duration_ms=video.duration_ms,
        width=video.width,
        height=video.height,
        fps=round(fps, 3) if fps else None,
        has_audio=video.has_audio,
        meets_standard=video.meets_standard,
        standard_issues=list(video.standard_issues or []),
        renditions=[
            RenditionOut(name=r.name, width=r.width, height=r.height, fps=r.fps)
            for r in video.renditions
        ],
        # Thumbnails exist before the renditions, but media URLs are served once the video
        # is ready: saying so earlier shows broken images (PLAN §7.6).
        has_thumbs=bool(video.thumbs) and video.status == "ready",
        progress=progress,
        error=(
            (video.error if video.status == "refused" else guests.error_for(viewer, video.error))
            if manager
            else None
        ),
        created_at=video.created_at,
        created_by=video.created_by,
        can_delete=can_delete_video(viewer, access.ref, access.leads),
        can_upload=can_upload(viewer, access.ref),
        can_tag=can_tag(viewer, access.ref, access.leads),
        can_share=can_share_with_team(viewer, access.ref, access.leads),
        can_export=can_export(viewer, access.ref, access.leads),
        rallies_reviewed_at=video.rallies_reviewed_at,
        analysis=analysis_by_video.get(video.id),
        names=NamesOut(**asdict(found)),
        no_scoreboard=video.no_scoreboard,
        no_scoresheet=video.no_scoresheet,
        files=[
            FileOut(
                id=f.id,
                part_no=f.part_no,
                name=f.file_name,
                size=f.bytes,
                part_size=settings.upload_part_bytes,
                part_count=_part_count(f.bytes, settings),
                uploaded=f.uploaded_at is not None,
            )
            for f in video.files
        ]
        if manager
        else [],
    )


@router.get("/video/config", response_model=VideoConfigOut)
def video_config(_: ViewerDep, settings: SettingsDep) -> VideoConfigOut:
    return VideoConfigOut(
        enabled=settings.video_enabled,
        part_size=settings.upload_part_bytes,
        max_file_bytes=settings.max_file_bytes,
        max_files=settings.max_files_per_video,
    )


@router.get("/teams/{team_id}/filming", response_model=FilmingOut)
def filming_status(
    team_id: int,
    viewer: ViewerDep,
    db: DbDep,
    bridge: BridgeDep,
    on: Annotated[date, Query()],
    kind: Literal["match"] = "match",  # matches only; older clients still send it
) -> FilmingOut:
    access = visible_team(db, viewer, team_id)
    today = club_today()
    if not today - timedelta(days=FILMING_PAST_DAYS) <= on <= today + timedelta(days=UPCOMING_DAYS):
        raise HTTPException(422, "Date out of range")
    check = check_filming(db, bridge, access.team, on, fresh=False)
    db.commit()
    return FilmingOut(
        allowed=check.allowed, blockers=_blockers(check), adult_checked_at=check.adult_checked_at
    )


@router.get("/teams/{team_id}/upcoming-filming", response_model=list[UpcomingOut])
def upcoming_filming(
    team_id: int, viewer: ViewerDep, db: DbDep, bridge: BridgeDep
) -> list[UpcomingOut]:
    """Filming allowed / blocked for the team's next games (PLAN §5.3)."""
    access = visible_team(db, viewer, team_id)
    today = club_today()
    until = today + timedelta(days=UPCOMING_DAYS)
    games = db.scalars(
        select(WdGame)
        .where(WdGame.team_id == team_id, WdGame.date >= today, WdGame.date <= until)
        .order_by(WdGame.date, WdGame.time)
        .limit(UPCOMING_MAX)
    )
    out = []
    for g in games:
        assert g.date is not None
        check = check_filming(db, bridge, access.team, g.date, fresh=False)
        out.append(
            UpcomingOut(
                id=g.id,
                date=g.date,
                label=f"{g.home_name or '?'} {DASH} {g.away_name or '?'}",
                allowed=check.allowed,
                blockers=_blockers(check),
                adult_checked_at=check.adult_checked_at,
            )
        )
    db.commit()
    return out


@router.get("/teams/{team_id}/videos", response_model=list[VideoOut])
def team_videos(
    team_id: int, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> list[VideoOut]:
    access = visible_team(db, viewer, team_id)
    query = select(Video).where(Video.team_id == team_id, Video.status != "deleted")
    if not _manages(viewer, access):
        query = query.where(Video.status == "ready")
    videos = list(
        db.scalars(query.order_by(Video.recorded_on.desc(), Video.created_at.desc())).all()
    )
    progress, analysis = _progress(db, videos), _analysis(db, videos)
    # Their games in one query: the names (_video_out) find them in the session.
    game_ids = {v.game_id for v in videos if v.game_id is not None}
    if game_ids:
        db.scalars(select(WdGame).where(WdGame.id.in_(game_ids))).all()
    return [_video_out(db, v, access, viewer, settings, progress, analysis) for v in videos]


class GameTeamOut(BaseModel):
    id: int
    name: str
    season: str
    sport: Literal["volleyball", "basketball"]
    can_upload: bool


class LinkedGameOut(BaseModel):
    id: int
    date: date | None
    time: time | None
    home_name: str | None
    away_name: str | None
    team: GameTeamOut


class GameVideosOut(BaseModel):
    """What a link from a Wiedisync game card opens (PLAN §4.4). `game` is None for a game the
    viewer may not see, and for one WiePlay does not know: the same answer either way."""

    game: LinkedGameOut | None
    videos: list[VideoOut]


@router.get("/games/{game_id}/videos", response_model=GameVideosOut)
def game_videos(
    game_id: Annotated[int, Path(ge=1, le=MAX_INT4)],
    viewer: ViewerDep,
    db: DbDep,
    settings: SettingsDep,
) -> GameVideosOut:
    """The videos of a Wiedisync game (its id is Wiedisync's) that the viewer may watch, from
    every team row they see; several when more than one camera filmed it. Players see ready
    videos only, as on the team page. A game the viewer may not see answers exactly as a game
    without videos, so the answer never tells whether it exists."""
    refs = team_refs(db)
    visible = visible_team_ids(viewer, refs)
    leads = lead_lineages(viewer, refs)
    ref_by_id = {r.id: r for r in refs}
    accesses: dict[int, TeamAccess] = {}

    def access_to(team_id: int) -> TeamAccess:
        if team_id not in accesses:
            team = db.get(WdTeam, team_id)
            assert team is not None
            accesses[team_id] = TeamAccess(team, ref_by_id[team_id], leads)
        return accesses[team_id]

    found = db.scalars(
        select(Video)
        .where(
            Video.game_id == game_id,
            Video.status != "deleted",
            Video.team_id.in_(sorted(visible)),
        )
        .order_by(Video.created_at, Video.id)
    ).all()
    videos = [v for v in found if v.status == "ready" or _manages(viewer, access_to(v.team_id))]
    progress, analysis = _progress(db, videos), _analysis(db, videos)

    game = db.get(WdGame, game_id)
    linked = None
    if game is not None and game.team_id in visible:
        access = access_to(game.team_id)
        linked = LinkedGameOut(
            id=game.id,
            date=game.date,
            time=game.time,
            home_name=game.home_name,
            away_name=game.away_name,
            team=GameTeamOut(
                id=access.team.id,
                name=access.team.name,
                season=access.team.season,
                sport=access.team.sport,
                can_upload=can_upload(viewer, access.ref),
            ),
        )
    return GameVideosOut(
        game=linked,
        videos=[
            _video_out(db, v, access_to(v.team_id), viewer, settings, progress, analysis)
            for v in videos
        ],
    )


def _resolve_match(db: Session, team: WdTeam, body: VideoCreateIn) -> tuple[date, str, int | None]:
    """The recording day, the title and the Wiedisync game. A match that is not in Wiedisync
    (a friendly, tournament or cup) has no game: the coach gives its date and a title, and
    the date must fall in this team row's season (retention counts from it, PLAN §5.5)."""
    if body.game_id is not None:
        if body.recorded_on is not None:
            raise HTTPException(422, "Give either the game or a date, not both")
        game = db.get(WdGame, body.game_id)
        if game is None or game.team_id != team.id or game.date is None:
            raise HTTPException(422, "Game not found for this team")
        title = body.title or f"{game.home_name or '?'} {DASH} {game.away_name or '?'}"
        return game.date, title, game.id
    if body.recorded_on is None:
        raise HTTPException(422, "A match video needs its game, or a date and a title")
    if not body.title:
        raise HTTPException(422, "A match without a Wiedisync game needs a title")
    season = pipeline.season_of(body.recorded_on)
    if season != team.season:
        raise HTTPException(
            422,
            f"{body.recorded_on:%d.%m.%Y} belongs to the {season} season; "
            f"this is the team of {team.season}",
        )
    return body.recorded_on, body.title, None


def _check_files(body: VideoCreateIn, settings: Settings) -> None:
    sources = [bool(body.files), bool(body.inbox_file_ids), body.import_url is not None]
    if sum(sources) != 1:
        raise HTTPException(422, "Give files to upload, inbox files or a link, one of them")
    count = len(body.files) or len(body.inbox_file_ids)
    if count > settings.max_files_per_video:
        raise HTTPException(422, f"At most {settings.max_files_per_video} files per video")
    for f in body.files:
        if f.size > settings.max_file_bytes:
            raise HTTPException(413, f"{f.name} is larger than the limit")
        # The extension decides, never the browser's MIME type: the worker probes the
        # file, and a playlist (.m3u8) or other container must not reach ffprobe.
        if not f.name.lower().endswith(VIDEO_EXTENSIONS):
            raise HTTPException(415, f"{f.name} is not a video file WiePlay accepts")


@router.post("/teams/{team_id}/videos", response_model=VideoOut, status_code=201)
def create_video(
    team_id: int,
    body: VideoCreateIn,
    viewer: ViewerDep,
    db: DbDep,
    bridge: BridgeDep,
    settings: SettingsDep,
    request: Request,
) -> VideoOut:
    store: ObjectStore | None = request.app.state.store
    if store is None and (body.files or body.import_url is not None):
        raise HTTPException(503, "Video storage is not configured")
    access = visible_team(db, viewer, team_id)
    if not can_upload(viewer, access.ref):
        raise HTTPException(403, "Only the team's coaches and sport admins upload videos")
    _check_files(body, settings)
    if body.inbox_file_ids and access.ref.guest:
        raise HTTPException(403, "The NAS inbox is for KSCW teams; upload the files instead")
    if body.import_url is not None and not can_import_link(access.ref):
        raise HTTPException(403, "Links are imported for KSCW teams only; upload the files instead")
    link: imports.ImportSource | None = None
    allowed: set[str] = set()
    if body.import_url is not None:
        try:
            link = imports.parse_link(body.import_url)
        except imports.NotImportable as exc:
            raise HTTPException(422, str(exc)) from exc
        allowed = imports.allowed_refs(db, link.platform)
        if not allowed:
            raise HTTPException(
                409,
                f"No {link.platform.capitalize()} channel is allowed for imports yet; "
                "an admin adds them under Admin → Import channels",
            )
    on, title, game_id = _resolve_match(db, access.team, body)
    if on > club_today():
        raise HTTPException(422, "The recording date is in the future")

    synced = refresh_rosters(db, bridge, access.team)
    check = check_filming(db, bridge, access.team, on, fresh=True, roster_fresh=synced)
    if not check.allowed:
        audit(
            db,
            viewer.member_id,
            "upload_refused",
            "team",
            str(team_id),
            kind=body.kind,
            on=on.isoformat(),
            blockers=[b.code for b in check.blockers],
        )
        db.commit()
        raise FilmingBlocked(check)
    # A guest team's videos per season are limited (PLAN §4.5); holds a lock on the team row.
    if problem := guests.quota_problem(db, access.team):
        db.rollback()
        audit(db, viewer.member_id, "upload_refused", "team", str(team_id), on=on.isoformat(),
              blockers=["video_limit"])  # fmt: skip
        db.commit()
        raise HTTPException(409, problem)

    inbox: list[InboxFile] = []
    if body.inbox_file_ids:
        inbox = list(
            db.scalars(
                select(InboxFile).where(InboxFile.id.in_(body.inbox_file_ids)).with_for_update()
            )
        )
        if len(inbox) != len(set(body.inbox_file_ids)) or any(i.status != "new" for i in inbox):
            raise HTTPException(409, "An inbox file is no longer available")
        folder = pipeline.team_folder(access.team).lower()
        if any(inbox_folder(i.path) != folder for i in inbox):
            raise HTTPException(
                409, f"Only files from the inbox folder {folder} belong to this team"
            )
        order = {fid: n for n, fid in enumerate(body.inbox_file_ids)}
        inbox.sort(key=lambda i: order[i.id])

    video = Video(
        id=uuid.uuid4(),
        team_id=team_id,
        lineage_id=access.team.lineage_id,
        kind=body.kind,
        game_id=game_id,
        title=title,
        sport=access.team.sport,
        recorded_on=on,
        source="import" if link else "inbox" if inbox else "file",
        status="uploading",
        created_by=viewer.member_id,
    )
    db.add(video)
    db.flush()  # the row first: inbox files point at it
    try:
        for n, f in enumerate(body.files, start=1):
            key = f"incoming/{video.id}/{n}/{safe_file_name(f.name)}"
            assert store is not None
            upload_id = store.create_multipart(key, f.type or "application/octet-stream")
            video.files.append(
                VideoSourceFile(
                    part_no=n,
                    file_name=f.name[:255],
                    bytes=f.size,
                    content_type=(f.type or "application/octet-stream")[:64],
                    object_key=key,
                    upload_id=upload_id,
                )
            )
    except (BotoCoreError, ClientError) as exc:
        log.exception("could not start multipart uploads")
        db.rollback()
        raise HTTPException(502, "Object storage did not answer") from exc
    now = datetime.now(UTC)
    for n, item in enumerate(inbox, start=1):
        video.files.append(
            VideoSourceFile(
                part_no=n,
                file_name=item.path.rsplit("/", 1)[-1][:255],
                bytes=item.size,
                content_type="video/*",
                inbox_file_id=item.id,
                uploaded_at=now,
                probe={"inbox_path": item.path},
            )
        )
        item.status = "assigned"
        item.video_id = video.id
    if link is not None:
        # Where it comes from is known now; the import adds channel and title.
        video.capture = {
            "import": {"platform": link.platform, "id": link.external_id, "url": link.url}
        }
        video.files.append(
            VideoSourceFile(part_no=1, file_name=link.file_name, bytes=0, content_type="video/mp4")
        )
    db.flush()
    if inbox:
        pipeline.start_ingest(db, video, access.team)
    if link is not None:
        pipeline.start_import(db, video, access.team, link, allowed)
    audit(
        db,
        viewer.member_id,
        "video_created",
        "video",
        str(video.id),
        team=team_id,
        kind=body.kind,
        source=video.source,
        link=link.url if link else None,
        files=len(video.files),
        bytes=sum(f.bytes for f in video.files),
    )
    db.commit()
    return _video_out(db, video, access, viewer, settings)


@router.get("/videos/{video_id}", response_model=VideoOut)
def get_video(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep, settings: SettingsDep) -> VideoOut:
    video, access = visible_video(db, viewer, video_id)
    if video.status != "ready" and not _manages(viewer, access):
        raise HTTPException(404, "Video not found")
    return _video_out(db, video, access, viewer, settings)


@router.patch("/videos/{video_id}/setup", response_model=VideoOut)
def update_setup(
    video_id: uuid.UUID, body: SetupIn, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> VideoOut:
    """The video's setup (the video page's "Setup"): the two teams' names, "no scoreboard in
    this video" (which forgets a box drawn before and scores again) and "no scoresheet"."""
    video, access = visible_video(db, viewer, video_id)
    if not can_tag(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's members, coaches and helpers set up this video")
    fields = body.model_fields_set
    changed: dict[str, Any] = {}
    if "name_us" in fields:
        video.name_us = changed["name_us"] = names.clean(body.name_us)
    if "name_them" in fields:
        video.name_them = changed["name_them"] = names.clean(body.name_them)
    if body.no_scoreboard is not None and body.no_scoreboard != video.no_scoreboard:
        video.no_scoreboard = changed["no_scoreboard"] = body.no_scoreboard
        if body.no_scoreboard:
            from wieplay.core import scoreboard, scoring

            if scoreboard.remove(db, video):
                scoring.score_video(db, video, reason="scoreboard")
    if body.no_scoresheet is not None and body.no_scoresheet != video.no_scoresheet:
        video.no_scoresheet = changed["no_scoresheet"] = body.no_scoresheet
    if changed:
        audit(db, viewer.member_id, "video_setup", "video", str(video.id), **changed)
    db.commit()
    return _video_out(db, video, access, viewer, settings)


def _lock(db: Session, video: Video) -> None:
    """Row lock on the video, then fresh values: completions and deletes are serialised."""
    db.execute(
        select(Video)
        .where(Video.id == video.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    db.expire(video, ["files"])


def _uploading_file(
    db: Session, viewer: Viewer, video_id: uuid.UUID, file_id: uuid.UUID, *, lock: bool = False
) -> tuple[Video, VideoSourceFile, TeamAccess]:
    video, access = visible_video(db, viewer, video_id)
    if lock:
        _lock(db, video)
    if not can_upload(viewer, access.ref):
        raise HTTPException(403, "Only the team's coaches and sport admins upload videos")
    if video.status != "uploading":
        raise HTTPException(409, "This video is not waiting for an upload")
    item = next((f for f in video.files if f.id == file_id), None)
    if item is None or item.object_key is None or item.upload_id is None:
        raise HTTPException(404, "File not found")
    return video, item, access


@router.post("/videos/{video_id}/files/{file_id}/parts", response_model=list[PartUrlOut])
def sign_parts(
    video_id: uuid.UUID,
    file_id: uuid.UUID,
    body: PartsIn,
    viewer: ViewerDep,
    db: DbDep,
    store: StoreDep,
    settings: SettingsDep,
) -> list[PartUrlOut]:
    _, item, _ = _uploading_file(db, viewer, video_id, file_id)
    if item.uploaded_at is not None:
        raise HTTPException(409, "This file is uploaded already")
    count = _part_count(item.bytes, settings)
    if any(p < 1 or p > count for p in body.parts):
        raise HTTPException(422, f"Parts run from 1 to {count}")
    assert item.object_key and item.upload_id
    part_size = settings.upload_part_bytes

    def size(p: int) -> int:  # every part is full size except the last
        return part_size if p < count else item.bytes - part_size * (count - 1)

    return [
        PartUrlOut(
            part=p,
            url=store.presign_part(
                item.object_key, item.upload_id, p, size(p), settings.upload_part_url_ttl_seconds
            ),
        )
        for p in sorted(set(body.parts))
    ]


@router.get("/videos/{video_id}/files/{file_id}/parts", response_model=list[UploadedPartOut])
def uploaded_parts(
    video_id: uuid.UUID, file_id: uuid.UUID, viewer: ViewerDep, db: DbDep, store: StoreDep
) -> list[UploadedPartOut]:
    """Parts already stored, so an interrupted upload resumes where it stopped."""
    _, item, _ = _uploading_file(db, viewer, video_id, file_id)
    if item.uploaded_at is not None:
        return []
    assert item.object_key and item.upload_id
    return [
        UploadedPartOut(part=p.number, size=p.size)
        for p in store.list_parts(item.object_key, item.upload_id)
    ]


@router.post("/videos/{video_id}/files/{file_id}/complete", response_model=VideoOut)
def complete_file(
    video_id: uuid.UUID,
    file_id: uuid.UUID,
    viewer: ViewerDep,
    db: DbDep,
    bridge: BridgeDep,
    store: StoreDep,
    settings: SettingsDep,
    background: BackgroundTasks,
) -> VideoOut:
    video, item, access = _uploading_file(db, viewer, video_id, file_id, lock=True)
    if item.uploaded_at is None:
        assert item.object_key and item.upload_id
        try:
            _complete_multipart(store, item, settings)
        except HTTPException:
            raise
        except Exception as exc:
            if not store.upload_gone(exc) or store.size(item.object_key) != item.bytes:
                raise
            # An earlier call completed it but did not get to record that.
        item.uploaded_at = datetime.now(UTC)
        item.upload_id = None
    db.commit()  # this file is done, whatever happens to the others

    if not all(f.uploaded_at is not None for f in video.files):
        return _video_out(db, video, access, viewer, settings)

    # The last file: check the filming rules again, with fresh rosters, before ingesting
    # (a roster or the team's policy may have changed while the upload ran; PLAN §5.3).
    synced = refresh_rosters(db, bridge, access.team)
    _lock(db, video)
    if video.status != "uploading":
        return _video_out(db, video, access, viewer, settings)  # someone else finished it
    check = check_filming(
        db, bridge, access.team, video.recorded_on, fresh=True, roster_fresh=synced
    )
    if not check.allowed:
        codes = [b.code for b in check.blockers]
        video.status = "refused"
        video.error = "Filming was no longer allowed when the upload finished: " + ", ".join(codes)
        audit(
            db,
            viewer.member_id,
            "upload_refused",
            "video",
            str(video.id),
            stage="complete",
            blockers=codes,
        )
        db.commit()
        background.add_task(_purge_objects, store, [f"incoming/{video.id}/"], [])
        raise FilmingBlocked(check)
    pipeline.start_ingest(db, video, access.team)
    audit(
        db,
        viewer.member_id,
        "video_uploaded",
        "video",
        str(video.id),
        bytes=sum(f.bytes for f in video.files),
    )
    db.commit()
    return _video_out(db, video, access, viewer, settings)


def _complete_multipart(store: ObjectStore, item: VideoSourceFile, settings: Settings) -> None:
    assert item.object_key and item.upload_id
    count = _part_count(item.bytes, settings)
    parts = sorted(store.list_parts(item.object_key, item.upload_id), key=lambda p: p.number)
    numbers = [p.number for p in parts]
    if numbers != list(range(1, count + 1)):
        missing = sorted(set(range(1, count + 1)) - set(numbers))
        raise HTTPException(409, f"Parts missing: {missing[:10]}")
    if sum(p.size for p in parts) != item.bytes:
        raise HTTPException(409, "Uploaded size does not match the file size")
    if any(p.size != settings.upload_part_bytes for p in parts[:-1]):
        raise HTTPException(409, "Unexpected part size")
    store.complete_multipart(item.object_key, item.upload_id, parts)


def _purge_objects(store: ObjectStore, prefixes: list[str], uploads: list[tuple[str, str]]) -> None:
    for key, upload_id in uploads:
        try:
            store.abort_multipart(key, upload_id)
        except (BotoCoreError, ClientError):
            log.warning("could not abort multipart upload %s", key)
    for prefix in prefixes:
        try:
            store.delete_prefix(prefix)
        except (BotoCoreError, ClientError):
            log.exception("could not delete %s (lifecycle rules clean incoming/)", prefix)


@router.delete("/videos/{video_id}", status_code=204)
def delete_video(
    video_id: uuid.UUID,
    viewer: ViewerDep,
    db: DbDep,
    request: Request,
    background: BackgroundTasks,
) -> None:
    video, access = visible_video(db, viewer, video_id)
    _lock(db, video)
    if video.status == "deleted":
        raise HTTPException(404, "Video not found")
    # Whoever may upload cancels their own unfinished upload; deleting is for the team's
    # coaches, sport admins and admins (helpers upload but do not delete).
    cancelling_own_upload = (
        video.status == "uploading"
        and video.created_by == viewer.member_id
        and can_upload(viewer, access.ref)
    )
    if not (cancelling_own_upload or can_delete_video(viewer, access.ref, access.leads)):
        raise HTTPException(403, "Only the team's coaches, sport admins and admins delete videos")
    uploads = [(f.object_key, f.upload_id) for f in video.files if f.object_key and f.upload_id]
    ingest_started = pipeline.ingest_started(db, video)
    prefixes = pipeline.mark_deleted(db, video, viewer.member_id)
    for f in video.files:
        if f.inbox_file_id:
            inbox = db.get(InboxFile, f.inbox_file_id)
            if inbox is not None and inbox.status == "assigned":
                # Not picked up yet: the file is still in the share. Otherwise ingest moved
                # it into the video's raw folder, which the purge job removes.
                inbox.status = "gone" if ingest_started else "new"
                inbox.video_id = None
    audit(db, viewer.member_id, "video_deleted", "video", str(video.id), title=video.title)
    db.commit()
    permission_cache.forget_video(video.id)  # cached segment permissions end now
    store: ObjectStore | None = request.app.state.store
    if store is not None:
        background.add_task(_purge_objects, store, prefixes, uploads)


def sweep_deleted(db: Session, store: ObjectStore, now: datetime | None = None) -> int:
    """Delete deleted videos' objects once more after late uploads can no longer land, and
    retry when the first purge failed. Run by the API's background loop."""
    now = now or datetime.now(UTC)
    videos = db.scalars(
        select(Video)
        .where(
            Video.status == "deleted",
            Video.purged_at.is_(None),
            Video.deleted_at < now - DELETED_SWEEP_AFTER,
        )
        .limit(20)
    ).all()
    for video in videos:
        try:
            for prefix in pipeline.bucket_prefixes(video.id):
                store.delete_prefix(prefix)
        except (BotoCoreError, ClientError):
            log.exception("sweep of deleted video %s failed; retrying later", video.id)
            continue
        video.purged_at = now
    db.commit()
    return len(videos)


class InboxFileOut(BaseModel):
    id: uuid.UUID
    path: str
    size: int
    mtime: datetime
    recorded_at_guess: datetime | None
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None


def inbox_folder(path: str) -> str:
    return path.split("/", 1)[0].lower() if "/" in path else ""


@router.get("/inbox", response_model=list[InboxFileOut])
def list_inbox(viewer: ViewerDep, db: DbDep) -> list[InboxFileOut]:
    """Camera files waiting in the NAS inbox, for coaches to attach to a game (PLAN §7.2).

    The inbox has one folder per team (vb-H3, bb-Herren-1, …): a coach sees the files in
    the folders of the teams they upload for, and nothing else."""
    refs = team_refs(db)
    if not can_use_inbox(viewer, refs):
        raise HTTPException(403, "Coaches and sport admins only")
    upload_ids = {r.id for r in refs if can_upload(viewer, r) and not r.guest}
    folders = {
        pipeline.team_folder(t).lower()
        for t in db.scalars(select(WdTeam).where(WdTeam.id.in_(upload_ids)))
    }
    rows = db.scalars(
        select(InboxFile)
        .where(InboxFile.status == "new")
        .order_by(InboxFile.recorded_at_guess.desc().nulls_last(), InboxFile.path)
    )
    out = []
    for row in rows:
        if inbox_folder(row.path) not in folders:
            continue
        probe = row.probe or {}
        out.append(
            InboxFileOut(
                id=row.id,
                path=row.path,
                size=row.size,
                mtime=row.mtime,
                recorded_at_guess=row.recorded_at_guess,
                duration_ms=probe.get("duration_ms"),
                width=probe.get("width"),
                height=probe.get("height"),
                fps=probe.get("fps"),
            )
        )
    return out


__all__ = ["Blocker", "FilmingBlocked", "blocked_body", "router"]
