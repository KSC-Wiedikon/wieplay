"""Automatic analysis of a video (PLAN §10.1): its state, a rerun, "rallies done".

GET  /api/videos/{id}/analysis          status, latest model run, proposal counts, the
                                        model's score against the coach's rallies, the
                                        automatic scoring (PLAN §10.3)
POST /api/videos/{id}/analyse           (re)run the analysis (whoever may tag)
POST /api/videos/{id}/scoring           score the video again now (whoever may tag)
POST /api/videos/{id}/rallies-reviewed  the rally list is complete and right (or not)

The proposals themselves are ordinary segments in GET /api/videos/{id}/tagging with
source "model", a confidence and a review state.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from nomal.api.access import TeamAccess, visible_video
from nomal.api.deps import DbDep, SettingsDep, ViewerDep, audit
from nomal.core import analysis, scoring, tagging
from nomal.core.models import Job, ModelRun, Segment, Video
from nomal.core.permissions import Viewer, can_delete_video, can_tag, can_upload

router = APIRouter(prefix="/api", tags=["analysis"])


class JobStateOut(BaseModel):
    id: uuid.UUID
    status: str
    progress: float | None
    message: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ModelRunOut(BaseModel):
    """The newest run that produced this video's rallies or their score: its analyse run,
    or a training that re-proposed or scored it. `metrics` is this video's part."""

    id: uuid.UUID
    kind: str  # analyse | train_rallies
    model_name: str
    model_version: str
    created_at: datetime
    params: dict[str, Any]
    # finder (v0 rules | v1 learned); mode: proposals (rallies were proposed), metrics (a
    # coach has rallies here, they were only scored), trained_on (a reviewed game the
    # finder learned from, scored while held out); proposed; eval; lovo; signals
    metrics: dict[str, Any]
    timing: dict[str, Any]


class MetricsVsHumanOut(BaseModel):
    """The model's rallies against the coach's (boundaries within ±1 s; PLAN §10.1)."""

    f1_1s: float
    precision: float
    recall: float
    iou: float  # overlap of the live time (union of rallies)
    rallies_human: int
    rallies_model: int
    finder: str | None
    # The coach marked the rally list done; else the coach's list may still be partial.
    labels_complete: bool
    held_out: bool  # scored while this game was left out of the training


class RallyModelOut(BaseModel):
    """The finder Nomal uses now for every volleyball video."""

    finder: str  # v1 when a training beat v0 on held-out games, else v0
    run_id: uuid.UUID | None
    trained_at: datetime | None
    games: int
    summary: dict[str, Any]


class ScoringSetOut(BaseModel):
    no: int
    start_ms: int
    end_ms: int | None
    source: str  # coach | model: whose set boundaries
    official: list[int] | None  # [us, them] from the federation
    model: list[int] | None  # the score line's final
    evidence: list[int] | None  # where the serve evidence alone ends the set
    matches_official: bool | None  # the evidence alone reaches the official score
    rallies: int  # point rallies
    flagged: int
    missed: int = 0  # points without a rally (the finder missed them)
    extra: int = 0  # rallies that look like no point
    first_serve: str | None
    our_side_at_start: str | None


class ScoringVsCoachOut(BaseModel):
    """The model scored blind to the coach's serves and winners, against them."""

    serving_side_accuracy: float | None
    serving_known: int
    winner_accuracy: float | None
    winners_known: int


class ScoringOut(BaseModel):
    """The newest automatic scoring of the video (PLAN §10.3)."""

    run_id: uuid.UUID
    created_at: datetime
    serve_finder: str | None  # v0 | v1; null: no serve evidence for this video yet
    rallies: int
    flagged: int
    sets: list[ScoringSetOut]
    sets_matching_official: int
    sets_official: int
    sides_confidence: float | None
    gaps: dict[str, int]
    first_serve_ms: int | None
    vs_coach: ScoringVsCoachOut | None


class AnalysisOut(BaseModel):
    video_id: uuid.UUID
    # none | queued | running | done | failed (the newest analyse job of the video)
    status: str
    model_run: ModelRunOut | None
    # Model rally proposals by review state: unreviewed, confirmed, corrected, rejected,
    # merged (deleted ones are rejected or merged).
    proposals: dict[str, int]
    metrics_vs_human: MetricsVsHumanOut | None
    available: bool  # the sport has automatic analysis
    can_analyse: bool
    job: JobStateOut | None
    human_rallies: int
    rallies_reviewed_at: datetime | None
    rally_model: RallyModelOut
    scoring: ScoringOut | None = None


class ReviewedIn(BaseModel):
    reviewed: bool = True


class ReviewedOut(BaseModel):
    rallies_reviewed_at: datetime | None
    training_job_id: uuid.UUID | None


def _manages(viewer: Viewer, access: TeamAccess) -> bool:
    return can_upload(viewer, access.ref) or can_delete_video(viewer, access.ref, access.leads)


def _readable(db: DbDep, viewer: Viewer, video_id: uuid.UUID) -> tuple[Video, TeamAccess]:
    video, access = visible_video(db, viewer, video_id)
    if video.status != "ready" and not _manages(viewer, access):
        raise HTTPException(404, "Video not found")
    return video, access


def _rally_model(db: DbDep) -> RallyModelOut:
    current = analysis.current_rally_model(db)
    latest = analysis.latest_training(db)
    if current is not None:
        run, _ = current
        return RallyModelOut(
            finder="v1",
            run_id=run.id,
            trained_at=run.created_at,
            games=len((run.dataset_manifest or {}).get("videos", [])),
            summary=run.metrics.get("summary", {}),
        )
    return RallyModelOut(
        finder="v0",
        run_id=latest.id if latest else None,
        trained_at=latest.created_at if latest else None,
        games=len((latest.dataset_manifest or {}).get("videos", [])) if latest else 0,
        summary=latest.metrics.get("summary", {}) if latest else {},
    )


STATUS = {
    "queued": "queued",
    "running": "running",
    "succeeded": "done",
    "failed": "failed",
    "cancelled": "failed",
}


def _vs_human(run: ModelRun, entry: dict[str, Any], video: Video) -> MetricsVsHumanOut | None:
    held_out = False
    found = entry.get("eval")
    finder = entry.get("finder")
    if found is None and entry.get("lovo"):
        finder = run.metrics.get("finder") or "v0"
        found = entry["lovo"].get(finder) or entry["lovo"].get("v0")
        held_out = True
    if not found:
        return None
    boundary = found.get("boundary", {})
    return MetricsVsHumanOut(
        f1_1s=boundary.get("f1", 0.0),
        precision=boundary.get("precision", 0.0),
        recall=boundary.get("recall", 0.0),
        iou=found.get("live_iou", 0.0),
        rallies_human=found.get("rallies_true", 0),
        rallies_model=found.get("rallies_pred", 0),
        finder=finder,
        labels_complete=bool(entry.get("labels_complete", held_out))
        or video.rallies_reviewed_at is not None,
        held_out=held_out,
    )


def _scoring(db: DbDep, video: Video) -> ScoringOut | None:
    run = scoring.latest_run(db, video.id)
    if run is None:
        return None
    m = run.metrics
    return ScoringOut(
        run_id=run.id,
        created_at=run.created_at,
        serve_finder=m.get("serve_finder"),
        rallies=m.get("rallies", 0),
        flagged=m.get("flagged", 0),
        sets=[ScoringSetOut.model_validate(s) for s in m.get("sets", [])],
        sets_matching_official=m.get("sets_matching_official", 0),
        sets_official=m.get("sets_official", 0),
        sides_confidence=m.get("sides_confidence"),
        gaps=m.get("gaps", {}),
        first_serve_ms=m.get("first_serve_ms"),
        vs_coach=ScoringVsCoachOut.model_validate(m["vs_coach"]) if m.get("vs_coach") else None,
    )


def analysis_out(db: DbDep, video: Video, viewer: Viewer, access: TeamAccess) -> AnalysisOut:
    manager = _manages(viewer, access)
    job = db.scalars(
        select(Job)
        .where(Job.video_id == video.id, Job.kind == "analyse")
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()
    model_run = vs_human = None
    if (found := analysis.latest_rally_result(db, video.id)) is not None:
        run, entry = found
        signals = None
        if run.kind != "analyse":
            # The signals' numbers come from the video's own analyse run.
            own = db.scalars(
                select(ModelRun)
                .where(ModelRun.video_id == video.id, ModelRun.kind == "analyse")
                .order_by(ModelRun.created_at.desc())
                .limit(1)
            ).first()
            signals = own.metrics.get("signals") if own else None
        else:
            signals = run.metrics.get("signals")
        model_run = ModelRunOut(
            id=run.id,
            kind=run.kind,
            model_name=run.model_name,
            model_version=run.model_version,
            created_at=run.created_at,
            params=run.params,
            metrics={**entry, "signals": signals},
            timing=run.timing,
        )
        vs_human = _vs_human(run, entry, video)
    return AnalysisOut(
        video_id=video.id,
        status=STATUS.get(job.status, job.status) if job else "none",
        model_run=model_run,
        proposals=analysis.proposal_counts(db, video.id),
        metrics_vs_human=vs_human,
        available=video.sport in analysis.SPORTS,
        can_analyse=can_tag(viewer, access.ref, access.leads)
        and analysis.can_analyse(video) is None,
        job=JobStateOut(
            id=job.id,
            status=job.status,
            progress=job.progress,
            message=job.message,
            error=job.error if manager else None,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
        if job
        else None,
        human_rallies=len(analysis.human_rallies(db, video.id)),
        rallies_reviewed_at=video.rallies_reviewed_at,
        rally_model=_rally_model(db),
        scoring=_scoring(db, video),
    )


@router.get("/videos/{video_id}/analysis", response_model=AnalysisOut)
def get_analysis(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> AnalysisOut:
    video, access = _readable(db, viewer, video_id)
    return analysis_out(db, video, viewer, access)


def _taggable(db: DbDep, viewer: Viewer, video_id: uuid.UUID) -> tuple[Video, TeamAccess]:
    video, access = _readable(db, viewer, video_id)
    if not can_tag(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches and sport admins run the analysis")
    db.execute(
        select(Video)
        .where(Video.id == video.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return video, access


@router.post("/videos/{video_id}/analyse", response_model=AnalysisOut, status_code=202)
def analyse(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> AnalysisOut:
    """Queue the analysis (again): new signals, new proposals for a video nobody has done
    the rallies of yet, else a new score against the coach's rallies."""
    video, access = _taggable(db, viewer, video_id)
    reason = analysis.can_analyse(video)
    if reason:
        raise HTTPException(409, reason)
    job = analysis.queue_analyse(db, video)
    audit(db, viewer.member_id, "analysis_requested", "video", str(video.id), job=str(job.id))
    db.commit()
    return analysis_out(db, video, viewer, access)


@router.post("/videos/{video_id}/scoring", response_model=AnalysisOut)
def rescore(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> AnalysisOut:
    """Score the video again now: the model's server and winner per rally, the sets when the
    coach has none, the gap labels (PLAN §10.3). Nothing a coach set is changed."""
    video, access = _taggable(db, viewer, video_id)
    if video.status != "ready":
        raise HTTPException(409, "Score a video once it is ready")
    if video.sport not in scoring.SPORTS:
        raise HTTPException(409, f"No automatic scoring for {video.sport} yet")
    tagging.set_actor(db, viewer.member_id)
    scoring.score_video(db, video, reason="request")
    db.commit()
    return analysis_out(db, video, viewer, access)


@router.post("/videos/{video_id}/rallies-reviewed", response_model=ReviewedOut)
def rallies_reviewed(
    video_id: uuid.UUID, body: ReviewedIn, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> ReviewedOut:
    """The coach marks the video's rally list complete and right: its rallies become
    training data and the finder retrains (debounced). Refused (409, detail
    {"message", "open_proposals": n}) while model proposals are still unreviewed: the coach
    answers each one first. `reviewed: false` takes it back."""
    video, _ = _taggable(db, viewer, video_id)
    if video.status != "ready":
        raise HTTPException(409, "Review rallies once the video is ready")
    if video.sport not in analysis.SPORTS:
        raise HTTPException(409, f"No rally finder for {video.sport}")
    if body.reviewed and not db.scalar(
        select(Segment.id).where(
            Segment.video_id == video.id, Segment.kind == "rally", Segment.deleted_at.is_(None)
        )
    ):
        raise HTTPException(409, "The video has no rallies yet")
    try:
        out = analysis.mark_reviewed(
            db,
            video,
            viewer.member_id,
            body.reviewed,
            timedelta(seconds=settings.analysis_train_delay_seconds),
        )
    except analysis.OpenProposals as exc:
        raise HTTPException(
            409,
            {
                "message": "Accept, correct, merge or reject the open proposals first",
                "open_proposals": exc.count,
            },
        ) from exc
    audit(
        db,
        viewer.member_id,
        "rallies_reviewed" if body.reviewed else "rallies_review_withdrawn",
        "video",
        str(video.id),
    )
    db.commit()
    return ReviewedOut(
        rallies_reviewed_at=video.rallies_reviewed_at,
        training_job_id=uuid.UUID(out["training_job_id"]) if out["training_job_id"] else None,
    )
