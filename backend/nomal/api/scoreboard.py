"""The scoreboard in the picture (PLAN §10.3, step 5).

GET    /api/videos/{id}/scoreboard        the box (if any), what the hall's memory offers, the
                                          reading job's state and what it found
PUT    /api/videos/{id}/scoreboard        save the box (whoever may tag) and read the board
DELETE /api/videos/{id}/scoreboard        no board in this video: the box and its readings go,
                                          the scoring goes back to the serves
POST   /api/videos/{id}/scoreboard/read   read it again

Whoever may watch the video may look; its taggers draw the box.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from nomal.api.deps import DbDep, ViewerDep, audit
from nomal.api.tagging import _readable
from nomal.core import analysis, scoring
from nomal.core import scoreboard as sb
from nomal.core.models import Job, Video
from nomal.core.permissions import can_tag

router = APIRouter(prefix="/api", tags=["scoreboard"])

STATUS = {"running": "running", "queued": "queued", "succeeded": "done", "failed": "failed"}


class RectOut(BaseModel):
    x: float
    y: float
    w: float
    h: float


class RegionOut(BaseModel):
    id: uuid.UUID
    rect: RectOut
    sets: RectOut | None
    kind: str
    hall_name: str | None
    at_ms: int | None
    copied_from: uuid.UUID | None
    updated_at: datetime


class SuggestionOut(BaseModel):
    source: Literal["hall", "team"]
    region: RegionOut
    video_title: str
    recorded_on: date
    hall_name: str | None


class SegmentOut(BaseModel):
    first_ms: int
    last_ms: int
    left: int
    right: int
    set_no: int
    confidence: float
    kind: str


class ReadingOut(BaseModel):
    at: datetime
    summary: dict[str, Any]
    segments: list[SegmentOut]


class JobOut(BaseModel):
    status: str  # queued | running | done | failed | cancelled
    progress: float | None
    error: str | None


class ScoreboardOut(BaseModel):
    video_id: uuid.UUID
    can_edit: bool
    aspect: float
    region: RegionOut | None
    suggestion: SuggestionOut | None
    job: JobOut | None
    reading: ReadingOut | None


class SaveIn(BaseModel):
    rect: sb.RectIn
    sets: sb.RectIn | None = None
    kind: sb.BoardKind = "auto"
    at_ms: int | None = Field(default=None, ge=0)
    copied_from: uuid.UUID | None = None


def _rect(value: dict[str, Any] | None) -> RectOut | None:
    return RectOut(**value) if value else None


def _region_out(row: sb.ScoreboardRegion) -> RegionOut:
    rect = _rect(row.rect)
    assert rect is not None
    layout = row.layout or {}
    return RegionOut(
        id=row.id,
        rect=rect,
        sets=_rect(layout.get("sets")),
        kind=str(layout.get("kind") or "auto"),
        hall_name=row.hall_name,
        at_ms=row.at_ms,
        copied_from=row.copied_from,
        updated_at=row.updated_at,
    )


def _job_out(db: DbDep, video: Video) -> JobOut | None:
    job = (
        sb.active_job(db, video.id)
        or db.scalars(
            select(Job)
            .where(Job.kind == "scoreboard", Job.video_id == video.id)
            .order_by(Job.created_at.desc())
            .limit(1)
        ).first()
    )
    if job is None:
        return None
    return JobOut(
        status=STATUS.get(job.status, job.status),
        progress=job.progress if job.status in ("queued", "running") else None,
        error=job.error if job.status == "failed" else None,
    )


def _reading(db: DbDep, video: Video) -> ReadingOut | None:
    run = sb.latest_run(db, video.id)
    tl = sb.timeline(db, video.id)
    if run is None or tl is None:
        return None
    return ReadingOut(
        at=run.created_at,
        summary=run.metrics or {},
        segments=[
            SegmentOut(
                first_ms=s.first_ms,
                last_ms=s.last_ms,
                left=s.left,
                right=s.right,
                set_no=s.set_no,
                confidence=s.confidence,
                kind=s.kind,
            )
            for s in tl.segments
        ],
    )


def _state(db: DbDep, viewer: ViewerDep, video_id: uuid.UUID) -> ScoreboardOut:
    video, access = _readable(db, viewer, video_id)
    row = sb.current(db, video.id)
    found = sb.suggestion(db, video) if row is None else None
    return ScoreboardOut(
        video_id=video.id,
        can_edit=can_tag(viewer, access.ref, access.leads) and video.status == "ready",
        aspect=(video.width / video.height) if video.width and video.height else 16 / 9,
        region=_region_out(row) if row is not None else None,
        suggestion=SuggestionOut(
            source=found.source,
            region=_region_out(found.region),
            video_title=found.video_title,
            recorded_on=found.recorded_on,
            hall_name=found.hall_name,
        )
        if found is not None
        else None,
        job=_job_out(db, video),
        reading=_reading(db, video) if row is not None else None,
    )


@router.get("/videos/{video_id}/scoreboard", response_model=ScoreboardOut)
def get_scoreboard(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> ScoreboardOut:
    out = _state(db, viewer, video_id)
    db.rollback()
    return out


def _editable(db: DbDep, viewer: ViewerDep, video_id: uuid.UUID) -> Video:
    video, access = _readable(db, viewer, video_id)
    if not can_tag(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches and sport admins mark the scoreboard")
    if video.status != "ready":
        raise HTTPException(409, "Mark the scoreboard once the video is ready")
    if video.sport != "volleyball":
        raise HTTPException(409, "The scoreboard is read for volleyball matches")
    return video


@router.put("/videos/{video_id}/scoreboard", response_model=ScoreboardOut)
def save(video_id: uuid.UUID, body: SaveIn, viewer: ViewerDep, db: DbDep) -> ScoreboardOut:
    video = _editable(db, viewer, video_id)
    if body.copied_from is not None:
        source = db.get(sb.ScoreboardRegion, body.copied_from)
        if source is None:
            raise HTTPException(422, "Copied from an unknown scoreboard")
    row = sb.save(
        db,
        video,
        body.rect,
        body.sets,
        body.kind,
        actor=viewer.member_id,
        at_ms=body.at_ms,
        copied_from=body.copied_from,
    )
    job = sb.queue_reading(db, video, row, analysis.analysis_subdir(db, video))
    audit(db, viewer.member_id, "scoreboard_marked", "video", str(video.id), kind=body.kind,
          sets=body.sets is not None, copied=body.copied_from is not None,
          job=str(job.id) if job else None)  # fmt: skip
    db.commit()
    return _state(db, viewer, video.id)


@router.post("/videos/{video_id}/scoreboard/read", response_model=ScoreboardOut)
def read_again(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> ScoreboardOut:
    video = _editable(db, viewer, video_id)
    row = sb.current(db, video.id)
    if row is None:
        raise HTTPException(404, "Mark the scoreboard first")
    job = sb.queue_reading(db, video, row, analysis.analysis_subdir(db, video))
    if job is None:
        raise HTTPException(409, sb.can_read(video) or "Can't read the scoreboard now")
    db.commit()
    return _state(db, viewer, video.id)


@router.delete("/videos/{video_id}/scoreboard", status_code=204)
def remove(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> Response:
    video = _editable(db, viewer, video_id)
    if not sb.remove(db, video):
        raise HTTPException(404, "The video has no scoreboard marked")
    scoring.score_video(db, video, reason="scoreboard")
    audit(db, viewer.member_id, "scoreboard_removed", "video", str(video.id))
    db.commit()
    return Response(status_code=204)
