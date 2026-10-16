"""Worker endpoints (PLAN §8.1). Workers reach the API through an SSH tunnel to the VPS
(since 16.10.2026; before, the tailnet): anything that arrives through Cloudflare
(CF-Connecting-IP) gets 404. Each worker has its own token.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from nomal.api.deps import BridgeDep, DbDep, SettingsDep, audit
from nomal.core import jobs, pipeline
from nomal.core.filming import CAMERA_DATE_WINDOW_DAYS, camera_day, check_filming
from nomal.core.models import InboxFile, Job, Video, WdTeam, Worker
from nomal.core.storage import ObjectStore
from nomal.core.sync import BridgeClient

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/worker", tags=["worker"])

LONG_POLL_SECONDS = 25.0
POLL_EVERY_SECONDS = 1.0


def current_worker(request: Request, db: DbDep) -> Worker:
    if request.headers.get("cf-connecting-ip"):
        raise HTTPException(404, "Not found")
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Worker token required")
    worker = db.scalars(
        select(Worker).where(Worker.token_hash == jobs.hash_token(auth[7:].strip()))
    ).first()
    if worker is None or worker.revoked_at is not None:
        raise HTTPException(401, "Unknown worker")
    return worker


WorkerDep = Annotated[Worker, Depends(current_worker)]


class ClaimIn(BaseModel):
    caps: list[str] = Field(max_length=32)
    max: int = Field(default=1, ge=1, le=4)
    version: str | None = Field(default=None, max_length=64)
    wait: bool = True


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    video_id: uuid.UUID | None
    params: dict[str, Any]
    attempt: int
    checkpoint: dict[str, Any] | None


class ClaimOut(BaseModel):
    jobs: list[JobOut]
    paused: bool


class HeartbeatIn(BaseModel):
    progress: float | None = None
    message: str | None = Field(default=None, max_length=500)
    checkpoint: dict[str, Any] | None = None


class HeartbeatOut(BaseModel):
    cancel: bool


class SignIn(BaseModel):
    op: Literal["get", "put", "delete"]
    keys: list[str] = Field(min_length=1, max_length=500)


class SignOut(BaseModel):
    urls: dict[str, str]


class FailIn(BaseModel):
    error: str = Field(max_length=4000)
    retryable: bool = True


class InboxEntry(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    size: int = Field(gt=0)
    mtime: datetime
    probe: dict[str, Any] = Field(default_factory=dict)
    recorded_at_guess: datetime | None = None


class InboxIn(BaseModel):
    files: list[InboxEntry] = Field(max_length=2000)


def _job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        kind=job.kind,
        video_id=job.video_id,
        params=job.params,
        attempt=job.attempts,
        checkpoint=job.checkpoint,
    )


def _lease(settings: SettingsDep) -> timedelta:
    return timedelta(seconds=settings.job_lease_seconds)


@router.post("/claim", response_model=ClaimOut)
async def claim(
    body: ClaimIn, request: Request, worker: WorkerDep, db: DbDep, settings: SettingsDep
) -> ClaimOut:
    """Lease runnable jobs; waits up to 25 s for one to appear (long poll)."""
    if body.version:
        worker.version = body.version
    deadline = asyncio.get_running_loop().time() + (LONG_POLL_SECONDS if body.wait else 0)
    while True:
        if worker.paused:
            worker.last_seen_at = datetime.now(UTC)
            db.commit()
            return ClaimOut(jobs=[], paused=True)
        leased = await asyncio.to_thread(
            jobs.claim, db, worker, body.caps, body.max, _lease(settings)
        )
        if leased or asyncio.get_running_loop().time() >= deadline:
            return ClaimOut(jobs=[_job_out(j) for j in leased], paused=False)
        if await request.is_disconnected():
            return ClaimOut(jobs=[], paused=False)
        await asyncio.sleep(POLL_EVERY_SECONDS)
        db.expire_all()


def _running(db: Session, job_id: uuid.UUID, worker: Worker) -> Job:
    job = jobs.owned_running(db, job_id, worker)
    if job is None:
        raise HTTPException(409, "This job is not leased to you")
    return job


@router.post("/jobs/{job_id}/heartbeat", response_model=HeartbeatOut)
def heartbeat(
    job_id: uuid.UUID, body: HeartbeatIn, worker: WorkerDep, db: DbDep, settings: SettingsDep
) -> HeartbeatOut:
    job = jobs.owned_running(db, job_id, worker)
    if job is None:
        db.rollback()
        return HeartbeatOut(cancel=True)  # cancelled, reaped or leased elsewhere: stop
    jobs.heartbeat(job, _lease(settings), body.progress, body.message, body.checkpoint)
    worker.last_seen_at = datetime.now(UTC)
    db.commit()
    return HeartbeatOut(cancel=False)


@router.post("/jobs/{job_id}/sign", response_model=SignOut)
def sign(
    job_id: uuid.UUID,
    body: SignIn,
    request: Request,
    worker: WorkerDep,
    db: DbDep,
    settings: SettingsDep,
) -> SignOut:
    store: ObjectStore | None = request.app.state.store
    if store is None:
        raise HTTPException(503, "Video storage is not configured")
    job = _running(db, job_id, worker)
    refused = [k for k in body.keys if not pipeline.may_sign(job, body.op, k)]
    db.rollback()
    if refused:
        raise HTTPException(403, f"Not allowed for this job: {refused[:3]}")
    ttl = max(settings.job_lease_seconds * 4, 3600)
    if body.op == "delete":
        store.delete(body.keys)
        return SignOut(urls={})
    sign_one = store.presign_get if body.op == "get" else store.presign_put
    return SignOut(urls={k: sign_one(k, ttl) for k in body.keys})


def _check_camera_day(db: Session, bridge: BridgeClient | None, video: Video) -> None:
    """The coach picks the recording day; the camera's clock may disagree. When it names
    another plausible day, the filming rules must hold on that day too (PLAN §5.3)."""
    day = camera_day(video.capture)
    if day is None or day == video.recorded_on:
        return
    if abs((day - video.recorded_on).days) > CAMERA_DATE_WINDOW_DAYS:
        audit(db, None, "camera_clock_unset", "video", str(video.id), camera_day=day.isoformat())
        return
    team = db.get(WdTeam, video.team_id)
    assert team is not None
    check = check_filming(db, bridge, team, day, fresh=True)
    codes = [b.code for b in check.blockers]
    if check.allowed:
        audit(db, None, "camera_day_differs", "video", str(video.id), camera_day=day.isoformat())
        return
    pipeline.refuse_after_ingest(
        db,
        video,
        f"The camera recorded this on {day:%d.%m.%Y}, and filming was not allowed that day: "
        + ", ".join(codes),
    )
    audit(
        db,
        None,
        "upload_refused",
        "video",
        str(video.id),
        stage="camera_day",
        camera_day=day.isoformat(),
        blockers=codes,
    )


@router.post("/jobs/{job_id}/complete", status_code=204)
def complete(
    job_id: uuid.UUID, body: dict[str, Any], worker: WorkerDep, db: DbDep, bridge: BridgeDep
) -> Response:
    job = _running(db, job_id, worker)
    video = db.get(Video, job.video_id, with_for_update=True) if job.video_id else None
    try:
        pipeline.complete(db, video, job, body)
    except ValueError as exc:
        db.rollback()
        job = _running(db, job_id, worker)
        video = db.get(Video, job.video_id) if job.video_id else None
        jobs.fail(job, f"invalid result: {exc}", retryable=False)
        pipeline.on_failed(db, video, job)
        db.commit()
        raise HTTPException(422, f"Invalid result: {exc}") from exc
    if job.kind == "ingest" and video is not None and video.status == "processing":
        _check_camera_day(db, bridge, video)
    if video is not None and pipeline.finish_if_done(db, video):
        audit(db, None, "video_ready", "video", str(video.id), worker=worker.name)
    worker.last_seen_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=204)


@router.post("/jobs/{job_id}/fail", status_code=204)
def fail(job_id: uuid.UUID, body: FailIn, worker: WorkerDep, db: DbDep) -> Response:
    job = _running(db, job_id, worker)
    final = jobs.fail(job, body.error, retryable=body.retryable)
    if final:
        video = db.get(Video, job.video_id) if job.video_id else None
        pipeline.on_failed(db, video, job)
    log.warning("job %s (%s) failed on %s: %s", job.id, job.kind, worker.name, body.error[:200])
    db.commit()
    return Response(status_code=204)


@router.post("/inbox", status_code=204)
def report_inbox(body: InboxIn, worker: WorkerDep, db: DbDep) -> Response:
    """The media worker reports the stable files in the NAS inbox (PLAN §7.2)."""
    if "inbox" not in worker.caps:
        raise HTTPException(403, "This worker has no inbox capability")
    now = datetime.now(UTC)
    seen = {f.path for f in body.files}
    existing = {row.path: row for row in db.scalars(select(InboxFile))}
    for entry in body.files:
        row = existing.get(entry.path)
        if row is None:
            db.add(
                InboxFile(
                    path=entry.path,
                    size=entry.size,
                    mtime=entry.mtime,
                    probe=entry.probe,
                    recorded_at_guess=entry.recorded_at_guess,
                    status="new",
                    seen_at=now,
                )
            )
        else:
            if row.status == "gone":
                row.status = "new"
            row.size, row.mtime, row.probe = entry.size, entry.mtime, entry.probe
            row.recorded_at_guess = entry.recorded_at_guess
            row.seen_at = now
    for path, row in existing.items():
        if path not in seen and row.status == "new":
            row.status = "gone"
    worker.last_seen_at = now
    db.commit()
    return Response(status_code=204)


def reap(db: Session) -> int:
    """Expired leases go back to the queue, or fail after max attempts."""
    expired = jobs.expired_leases(db)
    for job in expired:
        if jobs.fail(job, "lease expired (worker gone?)", retryable=True):
            video = db.get(Video, job.video_id) if job.video_id else None
            pipeline.on_failed(db, video, job)
    db.commit()
    return len(expired)
