"""Automatic analysis of a video (PLAN §10.1): its state, a rerun, "rallies done".

GET  /api/videos/{id}/analysis          status, latest model run, proposal counts, the
                                        model's score against the coach's rallies, the
                                        automatic scoring (PLAN §10.3), the suggested
                                        actions (§10.4a)
POST /api/videos/{id}/analyse           (re)run the analysis (whoever may tag)
POST /api/videos/{id}/scoring           score the video again now (whoever may tag)
POST /api/videos/{id}/rallies-reviewed  the rally list is complete and right (or not)
POST /api/videos/{id}/actions           suggest the actions again (whoever may tag)
POST /api/videos/{id}/actions-reviewed  the video's actions are complete and right (or not)

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
from nomal.core import actions, analysis, players, scoring, tagging
from nomal.core.config import Settings
from nomal.core.models import Job, ModelRun, Segment, Video
from nomal.core.permissions import (
    Viewer,
    can_delete_video,
    can_run_analysis,
    can_tag,
    can_upload,
)

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
    # The scoreboard's part (PLAN §10.3, step 5), when it was read: which side of it is ours,
    # how many rallies it named, how often the score line follows it, its winners against the
    # coach's, its disagreements with the serves.
    board: dict[str, Any] | None = None


class ActionsVsCoachOut(BaseModel):
    """The suggestions against the coach's tags in the rallies the coach tagged (contacts
    within ±0.3 s; PLAN §10.4a)."""

    coach: int
    suggested: int
    matched: int
    precision: float
    recall: float
    f1: float
    skill_accuracy: float | None
    side_accuracy: float | None
    action_precision: float | None = None  # fully right (time, skill, side) of the suggested
    action_f1: float
    labels_complete: bool  # the coach marked the actions done


class ActionModelOut(BaseModel):
    """The action proposer Nomal uses now."""

    finder: str  # v1 when a training beat the rules (v0) on held-out games
    run_id: uuid.UUID | None
    trained_at: datetime | None
    games: int
    summary: dict[str, Any]


class ActionGateOut(BaseModel):
    """Whether Nomal writes suggested actions: only once its action finder's held-out score on
    games whose actions a coach reviewed passes the bars (mode auto; on and off force it)."""

    mode: str  # auto | on | off
    open: bool
    reason: str  # on | off | passed | no_training | too_few_games | below_bar
    finder: str
    games: int  # games with actions reviewed in the newest training
    precision: float | None  # fully right (±0.3 s, skill, side) of the suggested, held out
    contact_recall: float | None
    min_precision: float
    min_recall: float
    trained_at: datetime | None


class PlayersSummaryOut(BaseModel):
    """The players of the video (PLAN §10.5; the gallery: GET /api/videos/{id}/players)."""

    status: str  # none | queued | running | done | failed (the newest players job)
    run_id: uuid.UUID | None
    # Cards in the gallery: people found (merged ones folded, "not a player" excluded); many
    # more than the players who played while the clusters are fragmented (beta).
    clusters: int
    named: int  # of them, with a number from the coach
    clusters_us: int
    clusters_them: int
    numbers_read: int  # clusters with a number the reader suggests
    # Hudl's "% identified": actions with a player (the coach's or Nomal's)
    actions: int
    identified: int
    identified_share: float | None
    suggested: int
    # Nomal's players against the coach's numbers where both exist
    compared: int
    accuracy: float | None


class ActionsOut(BaseModel):
    """The suggested actions of the video (PLAN §10.4a)."""

    # none | queued | running | done | failed (the newest actions job)
    status: str
    run_id: uuid.UUID | None
    created_at: datetime | None
    finder: str | None  # v0 | v1: who labelled the newest suggestions
    # Suggestions by review state (rejected: deleted ones).
    suggestions: dict[str, int]
    # The newest run: written per skill, rallies with sides, rallies a coach tagged.
    counts: dict[str, int]
    proposed: int
    rallies: int
    coach_rallies: int
    vs_coach: ActionsVsCoachOut | None
    reviewed_at: datetime | None
    model: ActionModelOut
    gate: ActionGateOut | None = None


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
    # Start long jobs by hand (analysis, action suggestions, finding the players).
    can_run: bool = False
    job: JobStateOut | None
    human_rallies: int
    rallies_reviewed_at: datetime | None
    rally_model: RallyModelOut
    scoring: ScoringOut | None = None
    actions: ActionsOut | None = None
    players: PlayersSummaryOut | None = None


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
        board=m.get("board"),
    )


def _action_model(db: DbDep) -> ActionModelOut:
    current = actions.current_model(db)
    run = current[0] if current is not None else actions.latest_training(db)
    return ActionModelOut(
        finder="v1" if current is not None else "v0",
        run_id=run.id if run else None,
        trained_at=run.created_at if run else None,
        games=len((run.dataset_manifest or {}).get("videos", [])) if run else 0,
        summary=run.metrics.get("summary", {}) if run else {},
    )


def gate_out(db: DbDep, settings: Settings) -> ActionGateOut:
    g = actions.gate(db, settings)
    return ActionGateOut(
        mode=g.mode,
        open=g.open,
        reason=g.reason,
        finder=g.finder,
        games=g.games,
        precision=g.precision,
        contact_recall=g.contact_recall,
        min_precision=g.min_precision,
        min_recall=g.min_recall,
        trained_at=g.trained_at,
    )


def _actions(db: DbDep, video: Video, settings: Settings) -> ActionsOut | None:
    if video.sport not in actions.SPORTS:
        return None
    run = actions.latest_run(db, video.id)
    job = actions.latest_job(db, video.id)
    m = run.metrics if run else {}
    vs = m.get("vs_coach")
    return ActionsOut(
        status=STATUS.get(job.status, job.status) if job else "none",
        run_id=run.id if run else None,
        created_at=run.created_at if run else None,
        finder=m.get("finder"),
        suggestions=actions.suggestion_counts(db, video.id),
        counts=m.get("counts", {}),
        proposed=m.get("proposed", 0),
        rallies=m.get("rallies", 0),
        coach_rallies=m.get("coach_rallies", 0),
        vs_coach=ActionsVsCoachOut.model_validate(vs) if vs else None,
        reviewed_at=video.actions_reviewed_at,
        model=_action_model(db),
        gate=gate_out(db, settings),
    )


def _players(db: DbDep, video: Video) -> PlayersSummaryOut | None:
    if video.sport not in players.SPORTS:
        return None
    run = players.latest_run(db, video.id)
    job = players.latest_job(db, video.id)
    # status "none": nobody has looked for the players yet (off by default; on demand).
    rows = players.clusters(db, video.id) if run is not None else []
    people = [c for c in rows if not c.not_player]
    found = ((run.metrics or {}).get("attribution") if run is not None else None) or {}
    vs = found.get("vs_coach") or {}
    status = STATUS.get(job.status, job.status) if job is not None else "done" if run else "none"
    if job is not None and job.status == "leased":
        status = "running"
    return PlayersSummaryOut(
        status=status,
        run_id=run.id if run is not None else None,
        clusters=len(people),
        named=sum(1 for c in people if c.number is not None),
        clusters_us=sum(1 for c in people if c.team == "us"),
        clusters_them=sum(1 for c in people if c.team == "them"),
        numbers_read=sum(1 for c in people if c.ocr_number is not None),
        actions=int(found.get("actions", 0)),
        identified=int(found.get("identified", 0)),
        identified_share=found.get("identified_share"),
        suggested=int(found.get("suggested", 0)),
        compared=int(vs.get("compared", 0)),
        accuracy=vs.get("accuracy"),
    )


def analysis_out(
    db: DbDep, video: Video, viewer: Viewer, access: TeamAccess, settings: Settings
) -> AnalysisOut:
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
        can_run=can_run_analysis(viewer, access.ref, access.leads),
        can_analyse=can_run_analysis(viewer, access.ref, access.leads)
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
        actions=_actions(db, video, settings),
        players=_players(db, video),
    )


@router.get("/videos/{video_id}/analysis", response_model=AnalysisOut)
def get_analysis(
    video_id: uuid.UUID, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> AnalysisOut:
    video, access = _readable(db, viewer, video_id)
    return analysis_out(db, video, viewer, access, settings)


def _runner(db: DbDep, viewer: Viewer, video_id: uuid.UUID) -> tuple[Video, TeamAccess]:
    """Starting a long job by hand: coaches, helpers, sport admins and admins (players tag)."""
    video, access = _readable(db, viewer, video_id)
    if not can_run_analysis(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches and helpers start the analysis")
    return video, access


def _taggable(db: DbDep, viewer: Viewer, video_id: uuid.UUID) -> tuple[Video, TeamAccess]:
    video, access = _readable(db, viewer, video_id)
    if not can_tag(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's members, coaches and helpers tag this video")
    db.execute(
        select(Video)
        .where(Video.id == video.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return video, access


@router.post("/videos/{video_id}/analyse", response_model=AnalysisOut, status_code=202)
def analyse(
    video_id: uuid.UUID, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> AnalysisOut:
    """Queue the analysis (again): new signals, new proposals for a video nobody has done
    the rallies of yet, else a new score against the coach's rallies."""
    _runner(db, viewer, video_id)
    video, access = _taggable(db, viewer, video_id)
    reason = analysis.can_analyse(video)
    if reason:
        raise HTTPException(409, reason)
    job = analysis.queue_analyse(db, video)
    audit(db, viewer.member_id, "analysis_requested", "video", str(video.id), job=str(job.id))
    db.commit()
    return analysis_out(db, video, viewer, access, settings)


@router.post("/videos/{video_id}/scoring", response_model=AnalysisOut)
def rescore(
    video_id: uuid.UUID, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> AnalysisOut:
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
    return analysis_out(db, video, viewer, access, settings)


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


class ActionsReviewedOut(BaseModel):
    actions_reviewed_at: datetime | None
    training_job_id: uuid.UUID | None


@router.post("/videos/{video_id}/actions", response_model=AnalysisOut, status_code=202)
def suggest_actions(
    video_id: uuid.UUID, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> AnalysisOut:
    """Suggest the actions again: untouched suggestions are replaced, nothing a coach tagged
    or answered is changed."""
    _runner(db, viewer, video_id)
    video, access = _taggable(db, viewer, video_id)
    reason = actions.can_propose(db, video)
    if reason:
        raise HTTPException(409, reason)
    job = actions.queue_actions(db, video, reason="request")
    audit(db, viewer.member_id, "actions_requested", "video", str(video.id),
          job=str(job.id) if job else None)  # fmt: skip
    db.commit()
    return analysis_out(db, video, viewer, access, settings)


@router.post("/videos/{video_id}/actions-reviewed", response_model=ActionsReviewedOut)
def actions_reviewed(
    video_id: uuid.UUID, body: ReviewedIn, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> ActionsReviewedOut:
    """The coach marks the video's actions complete and right: its tags become training data
    and the action proposer retrains (debounced). Refused (409, detail {"message",
    "open_suggestions": n}) while suggested actions are still unanswered. `reviewed: false`
    takes it back."""
    video, _ = _taggable(db, viewer, video_id)
    if video.status != "ready":
        raise HTTPException(409, "Review actions once the video is ready")
    if video.sport not in actions.SPORTS:
        raise HTTPException(409, f"No suggested actions for {video.sport}")
    try:
        out = actions.mark_reviewed(
            db,
            video,
            viewer.member_id,
            body.reviewed,
            timedelta(seconds=settings.analysis_train_delay_seconds),
        )
    except actions.OpenSuggestions as exc:
        raise HTTPException(
            409,
            {
                "message": "Accept, correct or reject the open suggestions first",
                "open_suggestions": exc.count,
            },
        ) from exc
    audit(
        db,
        viewer.member_id,
        "actions_reviewed" if body.reviewed else "actions_review_withdrawn",
        "video",
        str(video.id),
    )
    db.commit()
    return ActionsReviewedOut(
        actions_reviewed_at=video.actions_reviewed_at,
        training_job_id=uuid.UUID(out["training_job_id"]) if out["training_job_id"] else None,
    )
