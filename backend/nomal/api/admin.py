"""Nomal administration: which teams use Nomal, the Wiedisync sync, workers, jobs and
each team's filming policy."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from nomal.api.deps import AdminDep, DbDep, audit
from nomal.core import imports, jobs
from nomal.core.models import (
    ImportChannel,
    Job,
    SyncState,
    TeamLineage,
    TeamPolicy,
    Video,
    WdTeam,
    Worker,
)
from nomal.core.sync import BridgeError, run_sync

router = APIRouter(prefix="/api/admin", tags=["admin"])

SPORT_ORDER = {"volleyball": 0, "basketball": 1}


class AdminTeamOut(BaseModel):
    id: int
    name: str
    sport: Literal["volleyball", "basketball"]
    season: str
    league: str | None
    enabled: bool


class EnableIn(BaseModel):
    enabled: bool


class SyncStateOut(BaseModel):
    last_run: datetime | None
    last_ok: datetime | None
    error: str | None
    counts: dict[str, int]


def _admin_team(team: WdTeam) -> AdminTeamOut:
    return AdminTeamOut(
        id=team.id,
        name=team.name,
        sport=team.sport,
        season=team.season,
        league=team.league,
        enabled=team.lineage.enabled,
    )


def _state(db: DbDep) -> SyncStateOut:
    state = db.get(SyncState, 1)
    if state is None:
        return SyncStateOut(last_run=None, last_ok=None, error=None, counts={})
    return SyncStateOut(
        last_run=state.last_run, last_ok=state.last_ok, error=state.error, counts=state.counts
    )


@router.get("/teams", response_model=list[AdminTeamOut])
def admin_teams(_: AdminDep, db: DbDep) -> list[AdminTeamOut]:
    teams = db.scalars(select(WdTeam).where(WdTeam.listed.is_(True))).all()
    # Sport, then newest season first ("2026/27" sorts after "2025/26"), then name.
    ordered = sorted(teams, key=lambda t: (SPORT_ORDER.get(t.sport, 9), t.name))
    ordered = sorted(ordered, key=lambda t: t.season, reverse=True)
    ordered = sorted(ordered, key=lambda t: SPORT_ORDER.get(t.sport, 9))
    return [_admin_team(t) for t in ordered]


@router.put("/teams/{team_id}", response_model=AdminTeamOut)
def set_team_enabled(team_id: int, body: EnableIn, viewer: AdminDep, db: DbDep) -> AdminTeamOut:
    team = db.get(WdTeam, team_id)
    if team is None or not team.listed:
        raise HTTPException(404, "Team not found")
    lineage = team.lineage
    if lineage.enabled != body.enabled:
        lineage.enabled = body.enabled
        lineage.enabled_by = viewer.member_id
        lineage.enabled_at = datetime.now(UTC)
        audit(
            db,
            viewer.member_id,
            "team_enabled" if body.enabled else "team_disabled",
            "team_lineage",
            str(lineage.id),
            team_id=team_id,
            team=team.name,
        )
        db.commit()
    return _admin_team(team)


@router.get("/sync", response_model=SyncStateOut)
def sync_status(_: AdminDep, db: DbDep) -> SyncStateOut:
    return _state(db)


@router.post("/sync", response_model=SyncStateOut)
def sync_now(request: Request, viewer: AdminDep, db: DbDep) -> SyncStateOut:
    client = request.app.state.bridge_client_factory()
    try:
        run_sync(db, client)
    except BridgeError as exc:
        raise HTTPException(502, f"Sync failed: {exc}") from exc
    finally:
        audit(db, viewer.member_id, "sync_run")
        db.commit()
    return _state(db)


# --- Workers, jobs, filming policy (M1) ---------------------------------------------------

KNOWN_CAPS = frozenset(
    {
        "ingest",
        "import",
        "inbox",
        "transcode",
        "thumbs",
        "audio",
        "cpu_analysis",
        "analysis_copy",
        "export",
        "retention",
        "nas",
        "cuda",
        "ml",
        "gpu_dispatch",
    }
)


class WorkerOut(BaseModel):
    id: uuid.UUID
    name: str
    caps: list[str]
    paused: bool
    version: str | None
    last_seen_at: datetime | None
    created_at: datetime
    revoked: bool


class WorkerCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    caps: list[str] = Field(min_length=1, max_length=16)


class WorkerCreatedOut(BaseModel):
    worker: WorkerOut
    token: str  # shown once


class WorkerUpdateIn(BaseModel):
    paused: bool | None = None
    # Replaces the worker's capabilities, e.g. to add "import" to an existing worker.
    caps: list[str] | None = Field(default=None, min_length=1, max_length=16)


class JobAdminOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    video_id: uuid.UUID | None
    video_title: str | None
    attempts: int
    max_attempts: int
    progress: float | None
    message: str | None
    error: str | None
    worker: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class PolicyOut(BaseModel):
    lineage_id: uuid.UUID
    sport: Literal["volleyball", "basketball"]
    label: str
    matches_allowed: bool
    reason: str | None
    set_at: datetime | None


class PolicyIn(BaseModel):
    # Matches only (16.10.2026): team_policy.trainings_allowed stays in the table, unused.
    matches_allowed: bool
    reason: str | None = Field(default=None, max_length=500)


def _worker_out(w: Worker) -> WorkerOut:
    return WorkerOut(
        id=w.id,
        name=w.name,
        caps=sorted(w.caps),
        paused=w.paused,
        version=w.version,
        last_seen_at=w.last_seen_at,
        created_at=w.created_at,
        revoked=w.revoked_at is not None,
    )


@router.get("/workers", response_model=list[WorkerOut])
def list_workers(_: AdminDep, db: DbDep) -> list[WorkerOut]:
    return [_worker_out(w) for w in db.scalars(select(Worker).order_by(Worker.name))]


@router.post("/workers", response_model=WorkerCreatedOut, status_code=201)
def create_worker(body: WorkerCreateIn, viewer: AdminDep, db: DbDep) -> WorkerCreatedOut:
    unknown = set(body.caps) - KNOWN_CAPS
    if unknown:
        raise HTTPException(422, f"Unknown capabilities: {sorted(unknown)}")
    if db.scalar(select(Worker.id).where(Worker.name == body.name)):
        raise HTTPException(409, "A worker with this name exists")
    token, token_hash = jobs.new_worker_token()
    worker = Worker(
        id=uuid.uuid4(),
        name=body.name,
        caps=sorted(set(body.caps)),
        token_hash=token_hash,
        created_by=viewer.member_id,
    )
    db.add(worker)
    audit(
        db,
        viewer.member_id,
        "worker_created",
        "worker",
        str(worker.id),
        name=body.name,
        caps=worker.caps,
    )
    db.commit()
    return WorkerCreatedOut(worker=_worker_out(worker), token=token)


@router.put("/workers/{worker_id}", response_model=WorkerOut)
def update_worker(
    worker_id: uuid.UUID, body: WorkerUpdateIn, viewer: AdminDep, db: DbDep
) -> WorkerOut:
    worker = db.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(404, "Worker not found")
    if body.paused is not None and worker.paused != body.paused:
        worker.paused = body.paused
        audit(
            db,
            viewer.member_id,
            "worker_paused" if body.paused else "worker_resumed",
            "worker",
            str(worker.id),
        )
    if body.caps is not None:
        unknown = set(body.caps) - KNOWN_CAPS
        if unknown:
            raise HTTPException(422, f"Unknown capabilities: {', '.join(sorted(unknown))}")
        caps = sorted(set(body.caps))
        if caps != sorted(worker.caps):
            audit(
                db,
                viewer.member_id,
                "worker_caps_changed",
                "worker",
                str(worker.id),
                before=sorted(worker.caps),
                after=caps,
            )
            worker.caps = caps
    db.commit()
    return _worker_out(worker)


@router.delete("/workers/{worker_id}", status_code=204)
def revoke_worker(worker_id: uuid.UUID, viewer: AdminDep, db: DbDep) -> None:
    worker = db.get(Worker, worker_id)
    if worker is None:
        raise HTTPException(404, "Worker not found")
    if worker.revoked_at is None:
        worker.revoked_at = datetime.now(UTC)
        audit(db, viewer.member_id, "worker_revoked", "worker", str(worker.id), name=worker.name)
        db.commit()


@router.get("/jobs", response_model=list[JobAdminOut])
def list_jobs(
    _: AdminDep,
    db: DbDep,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[JobAdminOut]:
    query = (
        select(Job, Video.title, Worker.name)
        .outerjoin(Video, Video.id == Job.video_id)
        .outerjoin(Worker, Worker.id == Job.lease_owner)
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    if status:
        query = query.where(Job.status == status)
    return [
        JobAdminOut(
            id=j.id,
            kind=j.kind,
            status=j.status,
            video_id=j.video_id,
            video_title=title,
            attempts=j.attempts,
            max_attempts=j.max_attempts,
            progress=j.progress,
            message=j.message,
            error=j.error,
            worker=worker_name,
            created_at=j.created_at,
            started_at=j.started_at,
            finished_at=j.finished_at,
        )
        for j, title, worker_name in db.execute(query).all()
    ]


@router.get("/policies", response_model=list[PolicyOut])
def list_policies(_: AdminDep, db: DbDep) -> list[PolicyOut]:
    rows = db.execute(
        select(TeamLineage, TeamPolicy)
        .outerjoin(TeamPolicy, TeamPolicy.lineage_id == TeamLineage.id)
        .where(TeamLineage.enabled.is_(True))
        .order_by(TeamLineage.sport.desc(), TeamLineage.label)
    ).all()
    return [
        PolicyOut(
            lineage_id=lineage.id,
            sport=lineage.sport,
            label=_lineage_label(db, lineage),
            matches_allowed=policy.matches_allowed if policy else True,
            reason=policy.reason if policy else None,
            set_at=policy.set_at if policy else None,
        )
        for lineage, policy in rows
    ]


def _lineage_label(db: DbDep, lineage: TeamLineage) -> str:
    """The newest season's team name (D2 became D1 after promotion)."""
    newest = db.scalars(
        select(WdTeam).where(WdTeam.lineage_id == lineage.id).order_by(WdTeam.season.desc())
    ).first()
    return newest.name if newest else lineage.label


@router.put("/policies/{lineage_id}", response_model=PolicyOut)
def set_policy(lineage_id: uuid.UUID, body: PolicyIn, viewer: AdminDep, db: DbDep) -> PolicyOut:
    lineage = db.get(TeamLineage, lineage_id)
    if lineage is None:
        raise HTTPException(404, "Team not found")
    policy = db.get(TeamPolicy, lineage_id) or TeamPolicy(lineage_id=lineage_id)
    policy.matches_allowed = body.matches_allowed
    policy.reason = (body.reason or "").strip() or None
    policy.set_by = viewer.member_id
    policy.set_at = datetime.now(UTC)
    db.add(policy)
    audit(
        db,
        viewer.member_id,
        "team_policy_set",
        "team_lineage",
        str(lineage_id),
        matches_allowed=body.matches_allowed,
        reason=policy.reason,
    )
    db.commit()
    return PolicyOut(
        lineage_id=lineage.id,
        sport=lineage.sport,
        label=_lineage_label(db, lineage),
        matches_allowed=policy.matches_allowed,
        reason=policy.reason,
        set_at=policy.set_at,
    )


@router.post("/jobs/{job_id}/retry", response_model=JobAdminOut)
def retry_job(job_id: uuid.UUID, viewer: AdminDep, db: DbDep) -> JobAdminOut:
    """Run a failed video job again, with the jobs its failure cancelled."""
    job = db.get(Job, job_id, with_for_update=True)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "Only failed or cancelled jobs can be retried")
    video = db.get(Video, job.video_id, with_for_update=True) if job.video_id else None
    if video is not None and video.status == "deleted":
        raise HTTPException(409, "The video was deleted")
    if video is not None and video.status == "refused":
        raise HTTPException(409, "The filming rules refused this video; it cannot be retried")
    again = [job]
    if video is not None:
        again += db.scalars(
            select(Job).where(Job.video_id == video.id, Job.status == "cancelled", Job.id != job.id)
        ).all()
    for j in again:
        j.status, j.attempts, j.not_before, j.error, j.finished_at = "queued", 0, None, None, None
    if video is not None and video.status == "failed":
        video.status = "queued" if job.kind == "ingest" else "processing"
        video.error = None
    audit(
        db,
        viewer.member_id,
        "job_retried",
        "job",
        str(job.id),
        kind=job.kind,
        video=str(job.video_id) if job.video_id else None,
        with_cancelled=len(again) - 1,
    )
    db.commit()
    return next(r for r in list_jobs(viewer, db, None, 200) if r.id == job.id)


# --- Import channels: the allow-list for YouTube and Twitch links (PLAN §7.2.2) --------------


class ImportChannelIn(BaseModel):
    channel: str = Field(min_length=2, max_length=300)  # link, @handle or UC… id
    label: str = Field(min_length=1, max_length=200)


class ImportChannelOut(BaseModel):
    id: uuid.UUID
    platform: str
    ref: str
    label: str
    added_at: datetime


def _channel_out(c: ImportChannel) -> ImportChannelOut:
    return ImportChannelOut(
        id=c.id, platform=c.platform, ref=c.ref, label=c.label, added_at=c.added_at
    )


@router.get("/import-channels", response_model=list[ImportChannelOut])
def list_import_channels(_: AdminDep, db: DbDep) -> list[ImportChannelOut]:
    rows = db.scalars(select(ImportChannel).order_by(ImportChannel.label))
    return [_channel_out(c) for c in rows]


@router.post("/import-channels", response_model=ImportChannelOut, status_code=201)
def add_import_channel(body: ImportChannelIn, viewer: AdminDep, db: DbDep) -> ImportChannelOut:
    try:
        platform, ref = imports.parse_channel(body.channel)
    except imports.NotImportable as exc:
        raise HTTPException(422, str(exc)) from exc
    existing = db.scalar(
        select(ImportChannel).where(ImportChannel.platform == platform, ImportChannel.ref == ref)
    )
    if existing is not None:
        raise HTTPException(409, f"Already on the list as {existing.label}")
    channel = ImportChannel(
        id=uuid.uuid4(),
        platform=platform,
        ref=ref,
        label=body.label.strip(),
        added_by=viewer.member_id,
        added_at=datetime.now(UTC),
    )
    db.add(channel)
    audit(
        db,
        viewer.member_id,
        "import_channel_added",
        "import_channel",
        str(channel.id),
        platform=platform,
        ref=ref,
    )
    db.commit()
    return _channel_out(channel)


@router.delete("/import-channels/{channel_id}", status_code=204)
def remove_import_channel(channel_id: uuid.UUID, viewer: AdminDep, db: DbDep) -> None:
    channel = db.get(ImportChannel, channel_id)
    if channel is None:
        raise HTTPException(404, "Channel not found")
    audit(
        db,
        viewer.member_id,
        "import_channel_removed",
        "import_channel",
        str(channel.id),
        platform=channel.platform,
        ref=channel.ref,
    )
    db.delete(channel)
    db.commit()
