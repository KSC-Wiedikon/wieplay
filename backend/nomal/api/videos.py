"""Videos: create, upload (S3 multipart, parts signed here), list, delete (PLAN §7.2).

Before a video row exists, the filming rules run fresh (core/filming.py): refused means
nothing is stored, not even a staging object.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from nomal.api.access import TeamAccess, team_refs, visible_team, visible_video
from nomal.api.deps import (
    BridgeDep,
    DbDep,
    SettingsDep,
    StoreDep,
    ViewerDep,
    audit,
)
from nomal.api.media import permission_cache
from nomal.core import pipeline
from nomal.core.config import Settings
from nomal.core.filming import (
    NOTICE_VERSION,
    Blocker,
    FilmingCheck,
    check_filming,
    club_today,
    refresh_rosters,
)
from nomal.core.models import InboxFile, Job, Video, VideoSourceFile, WdGame, WdTeam, WdTraining
from nomal.core.permissions import Viewer, can_delete_video, can_upload, can_use_inbox
from nomal.core.storage import ObjectStore, safe_file_name

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["videos"])

VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".mkv", ".avi")
DASH = "\u2013"
UPCOMING_DAYS = 21
FILMING_PAST_DAYS = 400
DELETED_SWEEP_AFTER = timedelta(hours=2)  # beyond the 1 h worker upload URLs
UPCOMING_MAX = 12


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
    kind: Literal["match", "training"]
    id: int
    date: date
    label: str


class VideoConfigOut(BaseModel):
    enabled: bool
    part_size: int
    max_file_bytes: int
    max_files: int
    notice_version: str


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


class VideoOut(BaseModel):
    id: uuid.UUID
    team_id: int
    team_name: str
    team_season: str
    sport: Literal["volleyball", "basketball"]
    kind: Literal["match", "training"]
    game_id: int | None
    training_id: int | None
    title: str
    recorded_on: date
    source: str
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
    files: list[FileOut]


class FileIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    type: str | None = Field(default=None, max_length=128)


class VideoCreateIn(BaseModel):
    kind: Literal["match", "training"]
    game_id: int | None = None
    training_id: int | None = None
    recorded_on: date | None = None
    title: str | None = Field(default=None, max_length=200)
    files: list[FileIn] = Field(default_factory=list, max_length=50)
    inbox_file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


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


IN_PROGRESS = ("uploading", "queued", "processing")


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


def _video_out(
    db: Session,
    video: Video,
    access: TeamAccess,
    viewer: Viewer,
    settings: Settings,
    progress_by_video: dict[uuid.UUID, float] | None = None,
) -> VideoOut:
    manager = _manages(viewer, access)
    if progress_by_video is None:
        progress_by_video = _progress(db, [video])
    progress = progress_by_video.get(video.id)
    fps = video.fps_num / video.fps_den if video.fps_num and video.fps_den else None
    return VideoOut(
        id=video.id,
        team_id=video.team_id,
        team_name=access.team.name,
        team_season=access.team.season,
        sport=access.team.sport,
        kind=video.kind,
        game_id=video.game_id,
        training_id=video.training_id,
        title=video.title,
        recorded_on=video.recorded_on,
        source=video.source,
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
        has_thumbs=bool(video.thumbs),
        progress=progress,
        error=video.error if manager else None,
        created_at=video.created_at,
        created_by=video.created_by,
        can_delete=can_delete_video(viewer, access.ref, access.leads),
        can_upload=can_upload(viewer, access.ref),
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
        notice_version=NOTICE_VERSION,
    )


@router.get("/teams/{team_id}/filming", response_model=FilmingOut)
def filming_status(
    team_id: int,
    viewer: ViewerDep,
    db: DbDep,
    bridge: BridgeDep,
    kind: Literal["match", "training"],
    on: Annotated[date, Query()],
) -> FilmingOut:
    access = visible_team(db, viewer, team_id)
    today = club_today()
    if not today - timedelta(days=FILMING_PAST_DAYS) <= on <= today + timedelta(days=UPCOMING_DAYS):
        raise HTTPException(422, "Date out of range")
    check = check_filming(db, bridge, access.team, kind, on, fresh=False)
    db.commit()
    return FilmingOut(
        allowed=check.allowed, blockers=_blockers(check), adult_checked_at=check.adult_checked_at
    )


@router.get("/teams/{team_id}/upcoming-filming", response_model=list[UpcomingOut])
def upcoming_filming(
    team_id: int, viewer: ViewerDep, db: DbDep, bridge: BridgeDep
) -> list[UpcomingOut]:
    """Filming allowed / blocked for the team's next games and trainings (PLAN §5.3)."""
    access = visible_team(db, viewer, team_id)
    today = club_today()
    until = today + timedelta(days=UPCOMING_DAYS)
    items: list[tuple[date, Literal["match", "training"], int, str]] = []
    for g in db.scalars(
        select(WdGame).where(WdGame.team_id == team_id, WdGame.date >= today, WdGame.date <= until)
    ):
        assert g.date is not None
        items.append((g.date, "match", g.id, f"{g.home_name or '?'} {DASH} {g.away_name or '?'}"))
    for t in db.scalars(
        select(WdTraining).where(
            WdTraining.team_id == team_id, WdTraining.date >= today, WdTraining.date <= until
        )
    ):
        assert t.date is not None
        items.append((t.date, "training", t.id, t.hall_name or ""))
    items.sort(key=lambda i: (i[0], i[1]))
    out = []
    for day, kind, item_id, label in items[:UPCOMING_MAX]:
        check = check_filming(db, bridge, access.team, kind, day, fresh=False)
        out.append(
            UpcomingOut(
                kind=kind,
                id=item_id,
                date=day,
                label=label,
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
    progress = _progress(db, videos)
    return [_video_out(db, v, access, viewer, settings, progress) for v in videos]


def _resolve_event(
    db: Session, team_id: int, body: VideoCreateIn
) -> tuple[date, str, int | None, int | None]:
    if body.kind == "match":
        if body.game_id is None:
            raise HTTPException(422, "A match video needs its game")
        game = db.get(WdGame, body.game_id)
        if game is None or game.team_id != team_id or game.date is None:
            raise HTTPException(422, "Game not found for this team")
        return game.date, f"{game.home_name or '?'} {DASH} {game.away_name or '?'}", game.id, None
    if body.training_id is not None:
        training = db.get(WdTraining, body.training_id)
        if training is None or training.team_id != team_id or training.date is None:
            raise HTTPException(422, "Training not found for this team")
        return training.date, f"Training {training.date:%d.%m.%Y}", None, training.id
    if body.recorded_on is None:
        raise HTTPException(422, "A training video needs a training or a date")
    return body.recorded_on, f"Training {body.recorded_on:%d.%m.%Y}", None, None


def _check_files(body: VideoCreateIn, settings: Settings) -> None:
    if bool(body.files) == bool(body.inbox_file_ids):
        raise HTTPException(422, "Give either files to upload or inbox files, not both")
    count = len(body.files) or len(body.inbox_file_ids)
    if count > settings.max_files_per_video:
        raise HTTPException(422, f"At most {settings.max_files_per_video} files per video")
    for f in body.files:
        if f.size > settings.max_file_bytes:
            raise HTTPException(413, f"{f.name} is larger than the limit")
        # The extension decides, never the browser's MIME type: the worker probes the
        # file, and a playlist (.m3u8) or other container must not reach ffprobe.
        if not f.name.lower().endswith(VIDEO_EXTENSIONS):
            raise HTTPException(415, f"{f.name} is not a video file Nomal accepts")


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
    if store is None and body.files:
        raise HTTPException(503, "Video storage is not configured")
    access = visible_team(db, viewer, team_id)
    if not can_upload(viewer, access.ref):
        raise HTTPException(403, "Only the team's coaches and sport admins upload videos")
    _check_files(body, settings)
    on, default_title, game_id, training_id = _resolve_event(db, team_id, body)
    if on > club_today():
        raise HTTPException(422, "The recording date is in the future")

    synced = refresh_rosters(db, bridge)
    check = check_filming(db, bridge, access.team, body.kind, on, fresh=True, roster_fresh=synced)
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
        training_id=training_id,
        title=(body.title or "").strip() or default_title,
        sport=access.team.sport,
        recorded_on=on,
        source="inbox" if inbox else "file",
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
    db.flush()
    if inbox:
        pipeline.start_ingest(db, video, access.team)
    audit(
        db,
        viewer.member_id,
        "video_created",
        "video",
        str(video.id),
        team=team_id,
        kind=body.kind,
        source=video.source,
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
    # (consent may have been withdrawn while the upload ran; PLAN §5.3).
    synced = refresh_rosters(db, bridge)
    _lock(db, video)
    if video.status != "uploading":
        return _video_out(db, video, access, viewer, settings)  # someone else finished it
    check = check_filming(
        db, bridge, access.team, video.kind, video.recorded_on, fresh=True, roster_fresh=synced
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
    cancelling_own_upload = video.status == "uploading" and can_upload(viewer, access.ref)
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
            for prefix in (f"media/{video.id}/", f"incoming/{video.id}/"):
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
    upload_ids = {r.id for r in refs if can_upload(viewer, r)}
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
