"""The job queue in Postgres (PLAN §8.1). Only the API touches it; workers pull over HTTP.

A job is claimed with SELECT … FOR UPDATE SKIP LOCKED when the worker has every required
capability and every job it depends on has succeeded. A claim is a lease: the worker sends
heartbeats; when a lease runs out the reaper puts the job back (with back-off) or fails it.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import any_, exists, or_, select
from sqlalchemy.orm import Session, aliased

from wieplay.core.models import Job, Worker

ACTIVE = ("queued", "running")


def new_worker_token() -> tuple[str, str]:
    """A worker token and the hash stored for it."""
    token = "nmw_" + secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# The quick jobs after an analysis (trajectory, court, actions, scoreboard: seconds to
# minutes) come before the next hour-long analysis (-10), so a game's ball path, court and
# suggestions appear right after its own analysis instead of after the whole backlog; still
# below everything that leads to playback.
FOLLOW_UP_PRIORITY = -7


def enqueue(
    db: Session,
    kind: str,
    *,
    caps: Sequence[str],
    video_id: uuid.UUID | None = None,
    params: dict[str, Any] | None = None,
    depends_on: Iterable[uuid.UUID] = (),
    priority: int = 0,
    max_attempts: int = 3,
    not_before: datetime | None = None,
) -> Job:
    job = Job(
        id=uuid.uuid4(),
        kind=kind,
        video_id=video_id,
        params=params or {},
        required_caps=sorted(set(caps)),
        depends_on=list(depends_on),
        priority=priority,
        max_attempts=max_attempts,
        status="queued",
        attempts=0,
        not_before=not_before,
    )
    db.add(job)
    db.flush()
    return job


def claim(
    db: Session,
    worker: Worker,
    caps: Sequence[str],
    max_jobs: int,
    lease: timedelta,
    now: datetime | None = None,
    *,
    kinds: Sequence[str] | None = None,
) -> list[Job]:
    """Lease up to `max_jobs` runnable jobs for this worker (only `kinds`, when given).
    Commits."""
    now = now or datetime.now(UTC)
    usable = sorted(set(caps) & set(worker.caps))
    dep = aliased(Job)
    blocked = exists().where(dep.id == any_(Job.depends_on), dep.status != "succeeded")
    query = select(Job).where(
        Job.status == "queued",
        or_(Job.not_before.is_(None), Job.not_before <= now),
        Job.required_caps.contained_by(usable),
        ~blocked,
    )
    if kinds is not None:
        query = query.where(Job.kind.in_(list(kinds)))
    jobs = list(
        db.scalars(
            query.order_by(Job.priority.desc(), Job.created_at)
            .limit(max_jobs)
            .with_for_update(skip_locked=True, of=Job)
        )
    )
    for job in jobs:
        job.status = "running"
        job.lease_owner = worker.id
        job.lease_expires_at = now + lease
        job.heartbeat_at = now
        job.attempts += 1
        job.started_at = job.started_at or now
        job.error = None
    worker.last_seen_at = now
    db.commit()
    return jobs


def release(db: Session, worker: Worker) -> int:
    """A worker that just started runs nothing: its leases are left over from before the
    restart (or from a claim answered after it went away). Queue them again at once, and the
    stop does not count as an attempt."""
    left = db.scalars(
        select(Job).where(Job.status == "running", Job.lease_owner == worker.id)
    ).all()
    for job in left:
        job.status = "queued"
        job.lease_owner = None
        job.lease_expires_at = None
        job.not_before = None
        job.attempts = max(0, job.attempts - 1)
        job.error = "the worker restarted"
    return len(left)


def owned_running(db: Session, job_id: uuid.UUID, worker: Worker) -> Job | None:
    job = db.get(Job, job_id, with_for_update=True)
    if job is None or job.status != "running" or job.lease_owner != worker.id:
        return None
    return job


def heartbeat(
    job: Job,
    lease: timedelta,
    progress: float | None,
    message: str | None,
    checkpoint: dict[str, Any] | None,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    job.lease_expires_at = now + lease
    job.heartbeat_at = now
    if progress is not None:
        job.progress = max(0.0, min(1.0, progress))
    if message is not None:
        job.message = message[:500]
    if checkpoint is not None:
        job.checkpoint = checkpoint


def succeed(job: Job, result: dict[str, Any], now: datetime | None = None) -> None:
    job.status = "succeeded"
    job.result = result
    job.progress = 1.0
    job.finished_at = now or datetime.now(UTC)
    job.lease_owner = None
    job.lease_expires_at = None


def backoff(attempts: int) -> timedelta:
    return timedelta(minutes=min(60, 2 ** max(0, attempts - 1)))


def fail(
    job: Job,
    error: str,
    *,
    retryable: bool,
    retry_after: timedelta | None = None,
    now: datetime | None = None,
) -> bool:
    """Record a failed attempt. Returns True when the job is finally failed. A worker may
    ask for a longer pause than the usual backoff (YouTube's bot check: an hour)."""
    now = now or datetime.now(UTC)
    job.error = error[:4000]
    job.lease_owner = None
    job.lease_expires_at = None
    if retryable and job.attempts < job.max_attempts:
        job.status = "queued"
        job.not_before = now + (retry_after or backoff(job.attempts))
        return False
    job.status = "failed"
    job.finished_at = now
    return True


def hold(db: Session, kind: str, until: datetime) -> int:
    """Hold every queued job of a kind until a time (imports after YouTube's bot check:
    the next one would meet the same check)."""
    held = 0
    for job in db.scalars(select(Job).where(Job.kind == kind, Job.status == "queued")):
        if job.not_before is None or job.not_before < until:
            job.not_before = until
            held += 1
    return held


def cancel_active(db: Session, video_id: uuid.UUID, reason: str) -> int:
    """Cancel every queued or running job of a video; a running worker learns on heartbeat."""
    jobs = db.scalars(select(Job).where(Job.video_id == video_id, Job.status.in_(ACTIVE))).all()
    now = datetime.now(UTC)
    for job in jobs:
        job.status = "cancelled"
        job.error = reason
        job.finished_at = now
        job.lease_owner = None
        job.lease_expires_at = None
    return len(jobs)


def expired_leases(db: Session, now: datetime | None = None) -> list[Job]:
    now = now or datetime.now(UTC)
    return list(
        db.scalars(
            select(Job)
            .where(Job.status == "running", Job.lease_expires_at < now)
            .with_for_update(skip_locked=True)
        )
    )
