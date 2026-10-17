"""Players in a video (PLAN §10.5): the gallery a coach names once, and who made each action.

GET   /api/videos/{id}/players                     the clusters with their crops, numbers and
                                                   action counts, the roster snapshot, how
                                                   far the actions are identified
GET   /api/videos/{id}/players/sheets/{n}          a contact sheet of crops (JPEG)
POST  /api/videos/{id}/players/find                find the players (again): on demand, the
                                                   job does not run by itself unless
                                                   NOMAL_PLAYERS_AUTO is on
PATCH /api/videos/{id}/players/clusters/{cid}      give a cluster a number, or "not a player"
POST  /api/videos/{id}/players/merge               two or more clusters are one person
POST  /api/videos/{id}/players/clusters/{cid}/split  some of its tracks are someone else
POST  /api/videos/{id}/players/swap-teams          ours and theirs were the wrong way round
POST  /api/videos/{id}/players/apply               take Nomal's players for the coach's tags
                                                   that have no number

Whoever may watch the video may read (the crops show who played, like the video does);
whoever may tag it names, merges, splits and applies. Every change re-attributes the
video's actions. No faces, nothing across videos (PLAN §5.7).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nomal.api.access import TeamAccess
from nomal.api.analysis import JobStateOut, _manages
from nomal.api.deps import DbDep, StoreDep, ViewerDep, audit
from nomal.api.tagging import RosterOut, _readable, _roster_out
from nomal.core import guests, players, tagging
from nomal.core.models import Event, Video
from nomal.core.permissions import Viewer, can_run_analysis, can_tag
from nomal.core.players import EventPlayer, PlayerCluster
from nomal.core.storage import ObjectStore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["players"])

STATUS = {
    "queued": "queued",
    "leased": "running",
    "running": "running",
    "succeeded": "done",
    "failed": "failed",
    "cancelled": "failed",
}


class TrackOut(BaseModel):
    id: int
    t0: int
    t1: int
    n: int
    side: str
    number: int | None
    crop: list[int] | None


class ClusterOut(BaseModel):
    id: uuid.UUID
    key: int
    team: str  # us | them
    number: int | None  # the coach's
    member_id: int | None
    name: str | None
    not_player: bool
    referee_guess: bool  # the job thought: a referee or line judge
    ocr_number: int | None  # the number reader's suggestion
    ocr_confidence: float | None
    crops: list[list[int]]  # [page, cell] in the contact sheets
    detections: int
    seconds: float  # footage of this person, at the detection rate
    actions: int  # actions attributed to this person
    source: str  # model | split
    tracks: list[TrackOut]


class SheetsOut(BaseModel):
    count: int
    cell_w: int
    cell_h: int
    cols: int
    rows: int
    version: str  # changes with every run (cache key)


class RunOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    model_name: str
    model_version: str
    params: dict[str, Any]
    stats: dict[str, Any]
    timing: dict[str, Any]


class VsCoachOut(BaseModel):
    compared: int
    correct: int
    accuracy: float | None


class AttributionOut(BaseModel):
    """Hudl's "% identified": actions with a player (the coach's number or Nomal's), and
    Nomal's players against the coach's numbers where both exist."""

    actions: int
    identified: int
    identified_share: float | None
    by_coach: int
    suggested: int
    vs_coach: VsCoachOut


class PlayersOut(BaseModel):
    video_id: uuid.UUID
    available: bool
    can_edit: bool
    # Start finding the players (~2 h of CPU): coaches and helpers, not every player.
    can_find: bool = False
    status: str  # none | queued | running | done | failed
    job: JobStateOut | None
    run: RunOut | None
    sheets: SheetsOut | None
    clusters: list[ClusterOut]
    roster: list[RosterOut]
    attribution: AttributionOut | None
    # The coach's tags without a number for which Nomal has a player ("apply" takes them).
    open_suggestions: int


class AssignIn(BaseModel):
    number: int | None = Field(default=None, ge=0, le=99)
    not_player: bool = False


class MergeIn(BaseModel):
    into: uuid.UUID
    sources: list[uuid.UUID] = Field(min_length=1, max_length=50)


class SplitIn(BaseModel):
    tracks: list[int] = Field(min_length=1, max_length=5000)


class ApplyIn(BaseModel):
    event_ids: list[uuid.UUID] | None = Field(default=None, max_length=5000)


class ApplyOut(BaseModel):
    applied: int
    players: PlayersOut


def _summary_out(found: dict[str, Any] | None) -> AttributionOut | None:
    if not found:
        return None
    return AttributionOut(
        actions=found["actions"],
        identified=found["identified"],
        identified_share=found["identified_share"],
        by_coach=found["by_coach"],
        suggested=found["suggested"],
        vs_coach=VsCoachOut.model_validate(found["vs_coach"]),
    )


def _open_suggestions(db: Session, video_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Event)
            .join(EventPlayer, EventPlayer.event_id == Event.id)
            .where(
                Event.video_id == video_id,
                Event.deleted_at.is_(None),
                Event.number.is_(None),
                Event.source != "model",
                EventPlayer.number.is_not(None),
                EventPlayer.event_version == Event.version,
            )
        )
        or 0
    )


def refresh(db: Session, video: Video, store: ObjectStore | None) -> dict[str, Any] | None:
    """Attribute new and changed actions (cheap when nothing changed); the summary."""
    if store is not None and players.stale(db, video.id):
        try:
            with db.begin_nested():
                players.attribute(db, video, store.read, only_stale=True)
        except Exception:  # the gallery still shows; the next read tries again
            log.exception("attribution of %s failed", video.id)
    run = players.latest_run(db, video.id)
    if run is None:
        return None
    found = (run.metrics or {}).get("attribution")
    return found if isinstance(found, dict) else players.summary(db, video)


def players_out(
    db: Session, video: Video, viewer: Viewer, access: TeamAccess, store: ObjectStore | None
) -> PlayersOut:
    summary = refresh(db, video, store)
    run = players.latest_run(db, video.id)
    job = players.latest_job(db, video.id)
    sheets = None
    if run is not None and (art := players.artifact(db, run.id, "player_sheets")) is not None:
        stats = art.stats or {}
        cell = stats.get("cell") or [80, 160]
        sheets = SheetsOut(
            count=len(stats.get("keys", [])),
            cell_w=int(cell[0]),
            cell_h=int(cell[1]),
            cols=int(stats.get("cols", 10)),
            rows=int(stats.get("rows", 8)),
            version=str(run.id)[:8],
        )
    rows = players.clusters(db, video.id)
    can_edit = can_tag(viewer, access.ref, access.leads) and video.status == "ready"
    roster_rows = players.roster(db, video, create=can_edit)
    roster = _roster_out(db, roster_rows)
    names: dict[tuple[str, int], str | None] = {(r.side, r.number): r.name for r in roster}
    per_cluster = (summary or {}).get("per_cluster", {})
    fps = float((run.params or {}).get("detect_fps") or 10.0) if run is not None else 10.0
    manager = _manages(viewer, access)
    if job is not None:
        status = STATUS.get(job.status, job.status)
    else:
        status = "done" if run is not None else "none"
    out = PlayersOut(
        video_id=video.id,
        available=video.sport in players.SPORTS,
        can_edit=can_edit,
        can_find=can_edit and can_run_analysis(viewer, access.ref, access.leads),
        status=status,
        job=JobStateOut(
            id=job.id,
            status=job.status,
            progress=job.progress,
            message=job.message,
            error=guests.error_for(viewer, job.error) if manager else None,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
        if job is not None
        else None,
        run=RunOut(
            id=run.id,
            created_at=run.created_at,
            model_name=run.model_name,
            model_version=run.model_version,
            params=run.params,
            stats=(run.metrics or {}).get("players", {}),
            timing=run.timing,
        )
        if run is not None
        else None,
        sheets=sheets,
        clusters=[
            ClusterOut(
                id=c.id,
                key=c.key,
                team=c.team,
                number=c.number,
                member_id=c.member_id,
                name=names.get((c.team, c.number)) if c.number is not None else None,
                not_player=c.not_player,
                referee_guess=c.referee_guess,
                ocr_number=c.ocr_number,
                ocr_confidence=c.ocr_confidence,
                crops=c.crops,
                detections=c.detections,
                seconds=round(c.detections / fps, 1),
                actions=int(per_cluster.get(str(c.id), 0)),
                source=c.source,
                tracks=[
                    TrackOut(
                        id=int(t["id"]),
                        t0=int(t["t0"]),
                        t1=int(t["t1"]),
                        n=int(t["n"]),
                        side=str(t["side"]),
                        number=t.get("number"),
                        crop=t.get("crop"),
                    )
                    for t in c.tracks
                ],
            )
            for c in rows
        ],
        roster=roster,
        attribution=_summary_out(summary),
        open_suggestions=_open_suggestions(db, video.id),
    )
    db.commit()  # the roster snapshot and any refreshed attribution
    return out


def _store(request: Request) -> ObjectStore | None:
    store: ObjectStore | None = request.app.state.store
    return store


@router.get("/videos/{video_id}/players", response_model=PlayersOut)
def get_players(video_id: uuid.UUID, request: Request, viewer: ViewerDep, db: DbDep) -> PlayersOut:
    video, access = _readable(db, viewer, video_id)
    if video.status == "ready" and video.sport in players.SPORTS:
        tagging.set_actor(db, viewer.member_id)
    return players_out(db, video, viewer, access, _store(request))


@router.get("/videos/{video_id}/players/sheets/{page}")
def sheet(
    video_id: uuid.UUID,
    page: int,
    request: Request,
    viewer: ViewerDep,
    db: DbDep,
    store: StoreDep,
) -> Response:
    """A contact sheet of the newest run (JPEG, immutable per run: cached privately)."""
    video, _ = _readable(db, viewer, video_id)
    run = players.latest_run(db, video.id)
    art = players.artifact(db, run.id, "player_sheets") if run is not None else None
    keys = list((art.stats or {}).get("keys", [])) if art is not None else []
    if not 0 <= page < len(keys) or run is None:
        raise HTTPException(404, "No such contact sheet")
    etag = f'"{str(run.id)[:8]}-{page}"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=86400"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    db.rollback()
    data = store.read(str(keys[page]), players.MAX_SHEET_BYTES)
    if data is None:
        raise HTTPException(404, "The contact sheet is not available")
    return Response(data, media_type="image/jpeg", headers=headers)


def _editable(db: Session, viewer: Viewer, video_id: uuid.UUID) -> tuple[Video, TeamAccess]:
    video, access = _readable(db, viewer, video_id)
    if not can_tag(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches and sport admins name the players")
    if video.status != "ready":
        raise HTTPException(409, "Name the players once the video is ready")
    if video.sport not in players.SPORTS:
        raise HTTPException(409, f"No player detection for {video.sport} yet")
    db.execute(
        select(Video)
        .where(Video.id == video.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    tagging.set_actor(db, viewer.member_id)
    return video, access


def _cluster(db: Session, video: Video, cluster_id: uuid.UUID) -> PlayerCluster:
    row = db.get(PlayerCluster, cluster_id)
    if row is None or row.video_id != video.id or row.merged_into is not None:
        raise HTTPException(404, "No such player in this video")
    return row


def _reattribute(db: Session, video: Video, store: ObjectStore | None) -> None:
    db.flush()
    if store is None:
        return
    try:
        with db.begin_nested():
            players.attribute(db, video, store.read)
    except Exception:  # the gallery change stands; the next read attributes again
        log.exception("attribution of %s failed", video.id)


@router.post("/videos/{video_id}/players/find", response_model=PlayersOut, status_code=202)
def find_players(video_id: uuid.UUID, request: Request, viewer: ViewerDep, db: DbDep) -> PlayersOut:
    """Find the players (again), on demand (beta, ~2 h of CPU a match). A new run replaces
    the clusters; numbers the coach gave stay where the same number is read on the same
    team."""
    video, access = _editable(db, viewer, video_id)
    if not can_run_analysis(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches and helpers start finding the players")
    reason = players.can_run(video)
    if reason:
        raise HTTPException(409, reason)
    job = players.queue_players(db, video)
    audit(db, viewer.member_id, "players_requested", "video", str(video.id), job=str(job.id))
    db.commit()
    return players_out(db, video, viewer, access, _store(request))


@router.patch("/videos/{video_id}/players/clusters/{cluster_id}", response_model=PlayersOut)
def assign(
    video_id: uuid.UUID,
    cluster_id: uuid.UUID,
    body: AssignIn,
    request: Request,
    viewer: ViewerDep,
    db: DbDep,
) -> PlayersOut:
    video, access = _editable(db, viewer, video_id)
    row = _cluster(db, video, cluster_id)
    try:
        players.assign(
            db, video, row, number=body.number, not_player=body.not_player,
            actor=viewer.member_id,
        )  # fmt: skip
    except players.GalleryError as exc:
        raise HTTPException(422, str(exc)) from exc
    _reattribute(db, video, _store(request))
    audit(
        db, viewer.member_id, "player_named", "video", str(video.id),
        cluster=str(row.id), number=body.number, not_player=body.not_player,
    )  # fmt: skip
    db.commit()
    return players_out(db, video, viewer, access, _store(request))


@router.post("/videos/{video_id}/players/merge", response_model=PlayersOut)
def merge(
    video_id: uuid.UUID, body: MergeIn, request: Request, viewer: ViewerDep, db: DbDep
) -> PlayersOut:
    video, access = _editable(db, viewer, video_id)
    target = _cluster(db, video, body.into)
    sources = [_cluster(db, video, cid) for cid in dict.fromkeys(body.sources)]
    try:
        players.merge(db, target, sources, viewer.member_id)
    except players.GalleryError as exc:
        raise HTTPException(422, str(exc)) from exc
    _reattribute(db, video, _store(request))
    audit(
        db, viewer.member_id, "players_merged", "video", str(video.id),
        into=str(target.id), sources=[str(s.id) for s in sources],
    )  # fmt: skip
    db.commit()
    return players_out(db, video, viewer, access, _store(request))


@router.post("/videos/{video_id}/players/clusters/{cluster_id}/split", response_model=PlayersOut)
def split(
    video_id: uuid.UUID,
    cluster_id: uuid.UUID,
    body: SplitIn,
    request: Request,
    viewer: ViewerDep,
    db: DbDep,
) -> PlayersOut:
    video, access = _editable(db, viewer, video_id)
    row = _cluster(db, video, cluster_id)
    try:
        new = players.split(db, row, body.tracks, viewer.member_id)
    except players.GalleryError as exc:
        raise HTTPException(422, str(exc)) from exc
    _reattribute(db, video, _store(request))
    audit(
        db, viewer.member_id, "player_split", "video", str(video.id),
        cluster=str(row.id), new=str(new.id), tracks=len(body.tracks),
    )  # fmt: skip
    db.commit()
    return players_out(db, video, viewer, access, _store(request))


@router.post("/videos/{video_id}/players/swap-teams", response_model=PlayersOut)
def swap_teams(video_id: uuid.UUID, request: Request, viewer: ViewerDep, db: DbDep) -> PlayersOut:
    video, access = _editable(db, viewer, video_id)
    if not players.clusters(db, video.id):
        raise HTTPException(409, "No players found in this video yet")
    players.swap_teams(db, video, viewer.member_id)
    _reattribute(db, video, _store(request))
    audit(db, viewer.member_id, "players_teams_swapped", "video", str(video.id))
    db.commit()
    return players_out(db, video, viewer, access, _store(request))


@router.post("/videos/{video_id}/players/apply", response_model=ApplyOut)
def apply(
    video_id: uuid.UUID, body: ApplyIn, request: Request, viewer: ViewerDep, db: DbDep
) -> ApplyOut:
    """The coach takes Nomal's players for their tags without a number (all, or the ones
    listed): the numbers become the coach's (the revision names them)."""
    video, access = _editable(db, viewer, video_id)
    refresh(db, video, _store(request))
    applied = players.apply_suggestions(db, video, viewer.member_id, body.event_ids)
    _reattribute(db, video, _store(request))
    audit(db, viewer.member_id, "players_applied", "video", str(video.id), events=applied)
    db.commit()
    return ApplyOut(
        applied=applied, players=players_out(db, video, viewer, access, _store(request))
    )
