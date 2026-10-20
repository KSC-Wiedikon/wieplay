"""Suggested actions, API side (PLAN §10.4a): serve, reception, set, attack, block, dig and
freeball per rally, as events a coach reviews, and the loop that learns from the answers.

    trajectory done, a training, rallies done, a set or serve changed ──actions job──▶
        suggested events (source "model", review "unreviewed") in every rally nobody tagged;
        in the rallies a coach tagged, only their score against the coach's tags
    coach marks the actions done ──train_actions (debounced)──▶ v1 learned from every
        reviewed game, kept only when it beats v0 on held-out games; new suggestions for
        every game nobody has done yet

The worker (wieplay/worker/actions.py, wieplay/ml/actions.py) works in the camera's terms (near
and far); the sides of the rallies (which end we play at, PLAN §10.3) turn them into us and
them here, when the suggestions are written.

Suggestion rules (like the rally proposals', §10.1): a model never writes into a rally that
has a person's tags (made by hand, imported, or a suggestion a person accepted or
corrected); suggestions nobody touched are replaced on the next run; a new suggestion within
±0.3 s of one a coach rejected, or of a tag that is there, is dropped. Stats, smart reels and
the score ignore a suggestion until it is accepted (tagging.event_stands).

What a suggestion carries for the player attribution that follows (§10.5): the contact's
time (`t_ms`, `frame`, `time_precision` "frame" when the analysis ran at the video's rate,
else "adjusted"), the side, the ball at the contact (`ball_x`, `ball_y`, normalised image
coordinates) and, with a calibrated court, the floor below it (`court_x`, `court_y`, metres).
The player (`number`) stays empty.
"""

from __future__ import annotations

import uuid
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wieplay.core import analysis, calibration, guests, jobs, tagging
from wieplay.core.config import Settings, get_settings
from wieplay.core.models import (
    AnalysisArtifact,
    Event,
    Job,
    ModelRun,
    Period,
    Segment,
    Video,
)
from wieplay.core.taxonomy import load
from wieplay.ml import action_eval

KINDS = frozenset({"actions", "train_actions"})
CAPS = ("analyse", "nas")
PRIORITY = analysis.FOLLOW_UP_PRIORITY
TRAIN_PRIORITY = analysis.TRAIN_PRIORITY
SPORTS = frozenset({"volleyball"})
MODELS_SUBDIR = "models/actions"
SKILLS = ("S", "R", "E", "A", "B", "D", "F")
# After a scoring caused by a coach (sets, serve calls), wait for the next change.
AFTER_SCORING = timedelta(seconds=90)
# Scorings after which the suggestions follow (an analysis is followed by the trajectory job,
# which queues them itself).
SCORING_REASONS = frozenset({"train", "rallies_reviewed", "request", "changes", "cli"})
TOLERANCE_MS = action_eval.TOLERANCE_MS
MAX_ACTIONS = tagging.MAX_EVENTS
HUMAN_REVIEWS = ("confirmed", "corrected")
SURE_SIDE = 0.97  # a coach's server or winner, in the camera's terms


# --- what counts as the coach's tags --------------------------------------------------------


def coach_event_filter() -> Any:
    """Tags a person stands behind: made by hand (or imported), or a suggestion a person
    accepted or corrected."""
    return Event.deleted_at.is_(None) & (
        Event.source.in_(("manual", "import")) | Event.review.in_(HUMAN_REVIEWS)
    )


def _untouched() -> Any:
    return (
        (Event.source == "model")
        & (Event.review == "unreviewed")
        & Event.updated_by.is_(None)
        & Event.deleted_at.is_(None)
    )


def open_suggestions(db: Session, video_id: uuid.UUID) -> int:
    """Suggested actions nobody has accepted, corrected or rejected yet."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.video_id == video_id,
                Event.source == "model",
                Event.review == "unreviewed",
                Event.deleted_at.is_(None),
            )
        )
        or 0
    )


def suggestion_counts(db: Session, video_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(
        select(Event.review, Event.deleted_at.is_(None), func.count())
        .where(Event.video_id == video_id, Event.source == "model")
        .group_by(Event.review, Event.deleted_at.is_(None))
    ).all()
    counts = {"unreviewed": 0, "confirmed": 0, "corrected": 0, "rejected": 0}
    for review, live, n in rows:
        key = review if live and review in counts else "rejected"
        counts[key] += int(n)
    return counts


# --- the rallies in the camera's terms --------------------------------------------------------


def _live_rallies(db: Session, video: Video) -> list[Segment]:
    return list(
        db.scalars(
            select(Segment)
            .where(
                Segment.video_id == video.id,
                Segment.kind == "rally",
                Segment.deleted_at.is_(None),
                Segment.review.not_in(("rejected", "merged")),
            )
            .order_by(Segment.start_ms, Segment.id)
        )
    )


def near_is_us(db: Session, video: Video, rallies: list[Segment]) -> dict[uuid.UUID, bool]:
    """Per rally in a set: whether we play at the camera's end (near). The coach's side for
    the set, else the scoring's guess; switched in a deciding set from its switch score."""
    periods = {
        p.id: p
        for p in db.scalars(
            select(Period).where(Period.video_id == video.id, Period.deleted_at.is_(None))
        )
    }
    out: dict[uuid.UUID, bool] = {}
    before: dict[uuid.UUID, tuple[int, int]] = {}
    for rally in rallies:
        period = periods.get(rally.period_id) if rally.period_id else None
        if period is None:
            continue
        score = before.get(period.id, (period.start_us, period.start_them))
        if rally.score_us_after is not None and rally.score_them_after is not None:
            before[period.id] = (rally.score_us_after, rally.score_them_after)
        side = period.our_court_side_at_start or period.model_court_side
        if side not in ("near", "far"):
            continue
        switch = period.side_switch_at_score or (8 if period.no == 5 else None)
        near = side == "near"
        if switch is not None and max(score) >= switch:
            near = not near
        out[rally.id] = near
    return out


def _p_near(team: str | None, near_us: bool, sure: float) -> float | None:
    if team not in ("us", "them"):
        return None
    return sure if (team == "us") == near_us else 1 - sure


def rally_inputs(db: Session, video: Video) -> list[dict[str, Any]]:
    """The rallies the worker labels: every live rally in a set (proposals too), with the
    server's and the winner's side as probabilities of "near"."""
    rallies = _live_rallies(db, video)
    sides = near_is_us(db, video, rallies)
    out = []
    for rally in rallies:
        near_us = sides.get(rally.id)
        if near_us is None:
            continue
        serve_near = _p_near(rally.serving, near_us, SURE_SIDE)
        if serve_near is None and rally.score_sure:
            # The joint decode is sure of the rally (§10.3), so of who served it too.
            serve_near = _p_near(rally.model_serving, near_us, SURE_SIDE)
        if serve_near is None:
            model = _p_near(rally.model_serving, near_us, 0.85)
            court = None
            if rally.serving_court in ("near", "far") and rally.serve_confidence is not None:
                c = float(rally.serve_confidence)
                court = c if rally.serving_court == "near" else 1 - c
            found = [p for p in (model, court) if p is not None]
            serve_near = sum(found) / len(found) if found else None
        won_near = _p_near(rally.won_by, near_us, SURE_SIDE)
        if won_near is None and rally.model_won_by is not None:
            conf = float(rally.score_confidence or 0.5)
            won_near = _p_near(rally.model_won_by, near_us, conf)
        out.append(
            {
                "id": str(rally.id),
                "start_ms": rally.start_ms,
                "end_ms": rally.end_ms,
                "serve_near": None if serve_near is None else round(serve_near, 4),
                "won_near": None if won_near is None else round(won_near, 4),
                "serve_sure": bool(rally.score_sure),
            }
        )
    return out


def coach_events(db: Session, video: Video, rallies: list[Segment]) -> dict[uuid.UUID, list[Event]]:
    """The coach's tags per rally (a serve up to 2 s before the rally belongs to it)."""
    events = list(
        db.scalars(
            select(Event)
            .where(Event.video_id == video.id, coach_event_filter())
            .order_by(Event.t_ms)
        )
    )
    out: dict[uuid.UUID, list[Event]] = {}
    for rally in rallies:
        lo = rally.start_ms - tagging.SERVE_SLACK_MS
        found = [e for e in events if lo <= e.t_ms <= rally.end_ms]
        if found:
            out[rally.id] = found
    return out


# --- queueing --------------------------------------------------------------------------------


def _features(db: Session, video: Video) -> AnalysisArtifact | None:
    return analysis.latest_features(db, [video.id]).get(video.id)


def can_propose(db: Session, video: Video) -> str | None:
    """Why a video gets no suggested actions, or None."""
    if video.sport not in SPORTS:
        return f"No suggested actions for {video.sport} yet"
    if video.status != "ready":
        return "Suggest actions once the video is ready"
    if _features(db, video) is None:
        return "Analyse the video first"
    return None


def queue_actions(
    db: Session, video: Video, delay: timedelta = timedelta(0), reason: str = "request"
) -> Job | None:
    """Queue the actions job of a video (`delay` from now). A queued one is moved instead of
    a second one added; a running one gets a successor (it may have read its inputs). None
    when the video cannot have suggestions."""
    if can_propose(db, video) is not None:
        return None
    at = datetime.now(UTC) + delay if delay > timedelta(0) else None
    queued = db.scalars(
        select(Job)
        .where(Job.kind == "actions", Job.video_id == video.id, Job.status == "queued")
        .order_by(Job.created_at)
        .with_for_update()
    ).first()
    if queued is not None:
        queued.not_before = at
        queued.params = {**queued.params, "reason": reason}
        return queued
    return jobs.enqueue(
        db,
        "actions",
        caps=CAPS,
        video_id=video.id,
        priority=PRIORITY,
        max_attempts=2,
        not_before=at,
        params={"video_id": str(video.id), "reason": reason},
    )


def after_trajectory(db: Session, video: Video | None, job: Job) -> None:
    """A new ball track, or a court found (its floor positions): the actions follow (a job
    still queued is reused)."""
    if video is not None and job.kind in ("trajectory", "court") and job.status == "succeeded":
        queue_actions(db, video, reason=job.kind)


def after_scoring(db: Session, video: Video, reason: str) -> None:
    """New rallies, servers, winners or sides: the suggestions follow (debounced)."""
    if reason in SCORING_REASONS and video.status == "ready" and video.sport in SPORTS:
        queue_actions(db, video, AFTER_SCORING, reason=f"score:{reason}")


def queue_training(db: Session, delay: timedelta, now: datetime | None = None) -> Job:
    """Train the action proposer `delay` from now (one queued training is moved instead of a
    second one added)."""
    now = now or datetime.now(UTC)
    at = now + delay
    queued = db.scalars(
        select(Job)
        .where(Job.kind == "train_actions", Job.status == "queued")
        .order_by(Job.created_at)
        .with_for_update()
    ).first()
    if queued is not None:
        queued.not_before = at
        return queued
    return jobs.enqueue(
        db,
        "train_actions",
        caps=CAPS,
        priority=TRAIN_PRIORITY,
        max_attempts=2,
        not_before=at,
        params={"sport": "volleyball", "out_dir": MODELS_SUBDIR},
    )


# --- inputs a worker asks for ---------------------------------------------------------------


def current_model(db: Session) -> tuple[ModelRun, AnalysisArtifact] | None:
    """The newest training whose model beat v0 on held-out games."""
    runs = db.scalars(
        select(ModelRun)
        .where(ModelRun.kind == "train_actions", ModelRun.metrics["accepted"].as_boolean())
        .order_by(ModelRun.created_at.desc())
    )
    for run in runs:
        artifact = db.scalars(
            select(AnalysisArtifact).where(
                AnalysisArtifact.model_run_id == run.id, AnalysisArtifact.kind == "action_model"
            )
        ).first()
        if artifact is not None:
            return run, artifact
    return None


def latest_training(db: Session) -> ModelRun | None:
    return db.scalars(
        select(ModelRun)
        .where(ModelRun.kind == "train_actions")
        .order_by(ModelRun.created_at.desc())
        .limit(1)
    ).first()


# --- whether suggestions are written ---------------------------------------------------------

# How the finder did on hand-checked rallies (docs/research/actions-2026-10.md): two matches
# filmed from behind the end line, every contact labelled by eye. Shown to the coach as it
# is; the numbers on a coach's own games (vs_coach, the training's held-out score) follow.
# Measured 20.10.2026 on 54 rallies (353 contacts); the pose cue's weights fitted on one match
# and scored on the other; the sure rule set on these same rallies.
MEASURED: dict[str, Any] = {
    "rallies": 54,
    "contacts": 353,
    "found": 0.26,  # the contacts with a suggestion fully right (time ±0.3 s, skill, side)
    "precision": 0.54,  # the suggestions fully right
    "sure": 19,  # suggestions marked sure ...
    "sure_wrong": 0,  # ... and how many of them were wrong
}


@dataclass(frozen=True)
class Gate:
    """Whether suggested actions are written (PLAN §10.4a). Since 20.10.2026 always, unless
    switched off: the rules (v0) suggest from the start, each action with its posterior and
    a "sure" flag; a learned finder (v1) takes over once it beats them on games a coach
    reviewed. Its held-out score on those games is reported with the gate."""

    mode: str  # auto | on | off
    open: bool
    reason: str  # auto, on, off
    finder: str  # v0 | v1: the finder in use
    games: int
    precision: float | None  # the finder's fully right suggestions on reviewed games, held out
    contact_recall: float | None  # the coaches' tags with a contact candidate
    run_id: uuid.UUID | None = None
    trained_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "open": self.open,
            "reason": self.reason,
            "finder": self.finder,
            "games": self.games,
            "precision": self.precision,
            "contact_recall": self.contact_recall,
            "run_id": str(self.run_id) if self.run_id else None,
            "trained_at": self.trained_at.isoformat() if self.trained_at else None,
        }


def gate_of(run: ModelRun | None, settings: Settings) -> Gate:
    mode = settings.action_suggestions
    metrics = run.metrics if run is not None else {}
    finder = "v1" if metrics.get("accepted") else "v0"
    summary = metrics.get("summary") or {}
    scores = summary.get(finder) or {}
    games = int(summary.get("games") or scores.get("games") or 0)
    precision = scores.get("action_precision")
    recall = summary.get("contact_recall")
    return Gate(
        mode=mode,
        open=mode != "off",
        reason=mode,
        finder=finder,
        games=games,
        precision=None if precision is None else float(precision),
        contact_recall=None if recall is None else float(recall),
        run_id=run.id if run is not None else None,
        trained_at=run.created_at if run is not None else None,
    )


def gate(db: Session, settings: Settings | None = None) -> Gate:
    return gate_of(latest_training(db), settings or get_settings())


def _model_out(db: Session) -> dict[str, Any] | None:
    found = current_model(db)
    if found is None:
        return None
    run, artifact = found
    return {
        "model_run_id": str(run.id),
        "artifact_id": str(artifact.id),
        "nas_path": artifact.nas_path,
        "sha256": artifact.sha256,
    }


def _camera(db: Session, video: Video) -> tuple[dict[str, Any] | None, str | None]:
    """The coach's calibration, else the court finder's when it is sure enough."""
    row = calibration.current(db, video.id, "confirmed")
    if row is not None and row.camera:
        return dict(row.camera), "confirmed"
    row = calibration.current(db, video.id, "proposed")
    if row is not None and row.camera and (row.confidence or 0) >= calibration.AUTO_CONFIDENT:
        return dict(row.camera), "proposed"
    return None, None


def video_inputs(db: Session, video: Video) -> dict[str, Any] | None:
    features = _features(db, video)
    if features is None:
        return None
    camera, source = _camera(db, video)
    fps = video.fps_num / video.fps_den if video.fps_num and video.fps_den else 0.0
    return {
        "video_id": str(video.id),
        "features": {
            "artifact_id": str(features.id),
            "nas_path": features.nas_path,
            "sha256": features.sha256,
        },
        "fps": fps,
        "aspect": calibration.aspect_of(video),
        "camera": camera,
        "calibration": source,
        "rallies": rally_inputs(db, video),
        # The camera files on the NAS: the frames of the player-at-the-ball cue.
        "parts": [
            {"path": f.nas_path, "offset_ms": f.offset_ms or 0, "duration_ms": f.duration_ms or 0}
            for f in video.files
        ]
        if video.files and all(f.nas_path for f in video.files)
        else [],
    }


def _training_events(db: Session, video: Video) -> list[list[Any]]:
    """The coach's tags of a reviewed game in the camera's terms: [t_ms, code, near|far,
    rally id] for every tag in a rally whose sides are known."""
    rallies = _live_rallies(db, video)
    sides = near_is_us(db, video, rallies)
    out: list[list[Any]] = []
    for rally_id, events in coach_events(db, video, rallies).items():
        near_us = sides.get(rally_id)
        if near_us is None:
            continue
        for e in events:
            if e.code not in SKILLS:
                continue
            near = (e.side == "us") == near_us
            out.append([e.t_ms, e.code, "near" if near else "far", str(rally_id)])
    return out


def job_inputs(db: Session, job: Job) -> dict[str, Any]:
    """What an actions job needs, read when it runs: the video's rallies with their sides,
    the court, the current model; for a training, every reviewed game with its tags."""
    if job.kind == "actions":
        video = db.get(Video, job.video_id) if job.video_id else None
        if video is None:
            raise ValueError("job has no video")
        found = video_inputs(db, video)
        if found is None:
            raise ValueError("the video has no signals")
        return {**found, "model": _model_out(db)}
    if job.kind != "train_actions":
        raise ValueError(f"{job.kind} has no inputs")
    train: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    videos = db.scalars(
        select(Video)
        .where(
            Video.sport.in_(SPORTS), Video.status == "ready", Video.actions_reviewed_at.is_not(None)
        )
        .order_by(Video.recorded_on, Video.id)
    )
    # Guest clubs that have not agreed to train the shared models (PLAN §4.5).
    no_training = guests.no_training_lineages(db)
    for video in videos:
        if video.lineage_id in no_training:
            skipped.append({"video_id": str(video.id), "reason": "guest club, no training"})
            continue
        entry = video_inputs(db, video)
        if entry is None:
            continue
        if open_suggestions(db, video.id):
            skipped.append({"video_id": str(video.id), "reason": "open suggestions"})
            continue
        events = _training_events(db, video)
        if not events:
            skipped.append({"video_id": str(video.id), "reason": "no tags"})
            continue
        train.append({**entry, "events": events})
    return {"model": _model_out(db), "train": train, "skipped": skipped}


# --- results from the worker ----------------------------------------------------------------


class ActionIn(BaseModel):
    rally_id: uuid.UUID
    t_ms: int = Field(ge=0, le=tagging.MAX_MEDIA_MS)
    frame: int | None = Field(default=None, ge=0, le=tagging.MAX_FRAME)
    time_precision: Literal["frame", "adjusted"] = "adjusted"
    side: Literal["near", "far"]
    code: Literal["S", "R", "E", "A", "B", "D", "F"]
    grade: Literal["#", "+", "!", "-", "/", "="] | None = None
    confidence: float = Field(ge=0, le=1)
    sure: bool = False
    # The likeliest other answer at this contact: "<skill>:<near|far>", or "none".
    alternative: str | None = Field(default=None, pattern=r"^(none|[SREABDF]:(near|far))$")
    ball_x: float = Field(ge=-0.5, le=1.5)
    ball_y: float = Field(ge=-0.5, le=1.5)
    court_x: float | None = Field(default=None, ge=-20, le=40)
    court_y: float | None = Field(default=None, ge=-20, le=40)


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class ActionsResult(BaseModel):
    model: ModelIn
    finder: Literal["v0", "v1"]
    model_run_id: uuid.UUID | None = None  # v1: the training whose model labelled them
    actions: list[ActionIn] = Field(max_length=MAX_ACTIONS)
    stats: dict[str, Any] = Field(default_factory=dict)
    timing: dict[str, Any] = Field(default_factory=dict)


class ArtifactIn(BaseModel):
    nas_path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)
    format: Literal["pickle"]
    schema_version: int = Field(ge=1, le=1000)
    stats: dict[str, Any] = Field(default_factory=dict)


class TrainResult(BaseModel):
    model: ModelIn
    artifact: ArtifactIn | None = None
    accepted: bool
    timing: dict[str, Any] = Field(default_factory=dict)
    # summary (v0/v1 on held-out games, contact recall) and per video {"v0", "v1", ...}
    evaluation: dict[str, Any] = Field(default_factory=dict)
    dataset: list[dict[str, Any]] = Field(default_factory=list, max_length=10_000)


def _alternative(found: str | None, near_us: bool) -> str | None:
    """The worker's "D:near" in the coach's terms ("D:us")."""
    if not found or found == "none":
        return found
    code, side = found.split(":")
    return f"{code}:{'us' if (side == 'near') == near_us else 'them'}"


def _near(times: list[int], t: int, tol: int = TOLERANCE_MS) -> bool:
    return bisect_right(times, t + tol) > bisect_left(times, t - tol)


def apply_actions(
    db: Session, video: Video, run: ModelRun, result: ActionsResult, bar: Gate | None = None
) -> dict[str, Any]:
    """Write a model's actions for a video under the suggestion rules (module docstring), when
    the bar is passed (`bar`, else nothing is written and the old untouched suggestions go).
    Returns what happened, with the score against the coach's tags where there are some."""
    bar = bar or gate(db)
    tax = load(video.sport)
    rallies = _live_rallies(db, video)
    by_id = {r.id: r for r in rallies}
    sides = near_is_us(db, video, rallies)
    coach = coach_events(db, video, rallies)
    locked = set(coach)
    fps = video.fps_num / video.fps_den if video.fps_num and video.fps_den else None

    # Untouched suggestions are replaced, except in a rally a coach tagged (nothing is
    # written there, not even a deletion).
    stale = list(db.scalars(select(Event).where(Event.video_id == video.id, _untouched())))
    replaced = 0
    for event in stale:
        rally = by_id.get(event.segment_id) if event.segment_id else None
        if rally is not None and rally.id in locked:
            continue
        db.delete(event)
        replaced += 1
    db.flush()
    rejected = sorted(
        t
        for (t,) in db.execute(
            select(Event.t_ms).where(
                Event.video_id == video.id,
                Event.source == "model",
                Event.deleted_at.is_not(None),
            )
        )
    )
    present = sorted(
        t
        for (t,) in db.execute(
            select(Event.t_ms).where(Event.video_id == video.id, Event.deleted_at.is_(None))
        )
    )
    room = tagging.MAX_EVENTS - int(
        db.scalar(select(func.count()).select_from(Event).where(Event.video_id == video.id)) or 0
    )
    skipped: Counter[str] = Counter()
    written: Counter[str] = Counter()
    graded = 0
    sure = 0
    vs_pred: dict[uuid.UUID, list[action_eval.Action]] = {}
    for item in sorted(result.actions, key=lambda a: a.t_ms):
        rally = by_id.get(item.rally_id)
        near_us = sides.get(item.rally_id)
        if rally is None or near_us is None:
            skipped["rally"] += 1
            continue
        side = "us" if (item.side == "near") == near_us else "them"
        if rally.id in locked:
            vs_pred.setdefault(rally.id, []).append((item.t_ms, item.code, side))
            skipped["coach"] += 1
            continue
        if not bar.open:
            skipped["off"] += 1  # switched off: measured where a coach tagged, written nowhere
            continue
        if not rally.start_ms - tagging.SERVE_SLACK_MS <= item.t_ms <= rally.end_ms + 1000:
            skipped["outside"] += 1
            continue
        if _near(rejected, item.t_ms):
            skipped["rejected"] += 1
            continue
        if _near(present, item.t_ms):
            skipped["duplicate"] += 1
            continue
        if room <= 0:
            skipped["full"] += 1
            continue
        attributes = {"grade": item.grade} if item.grade and tax.skills[item.code].graded else {}
        precision = item.time_precision
        frame = item.frame
        if frame is None and fps:
            frame = round(item.t_ms / 1000 * fps)
        db.add(
            Event(
                id=uuid.uuid4(),
                video_id=video.id,
                segment_id=rally.id,
                sport=tax.sport,
                taxonomy_version=tax.version,
                code=item.code,
                t_ms=item.t_ms,
                frame=frame,
                time_precision=precision,
                side=side,
                number=None,
                member_id=None,
                attributes=attributes,
                court_x=item.court_x,
                court_y=item.court_y,
                ball_x=round(item.ball_x, 4),
                ball_y=round(item.ball_y, 4),
                source="model",
                model_run_id=run.id,
                confidence=round(item.confidence, 3),
                model_sure=item.sure,
                model_alternative=_alternative(item.alternative, near_us),
                review="unreviewed",
                version=1,
            )
        )
        present.insert(bisect_left(present, item.t_ms), item.t_ms)
        written[item.code] += 1
        sure += int(item.sure)
        graded += int(bool(attributes))
        room -= 1
    db.flush()
    out: dict[str, Any] = {
        "finder": result.finder,
        "action_model_run": str(result.model_run_id) if result.model_run_id else None,
        "proposed": sum(written.values()),
        "counts": {code: written.get(code, 0) for code in SKILLS},
        "graded": graded,
        "replaced": replaced,
        "skipped": dict(skipped),
        "sure": sure,
        "rallies": len(sides),
        "coach_rallies": len(locked),
        "stats": result.stats,
        "gate": bar.as_dict(),
    }
    if locked:
        true: list[action_eval.Action] = []
        pred: list[action_eval.Action] = []
        for rally_id in locked:
            true.extend((e.t_ms, e.code, e.side) for e in coach[rally_id] if e.code in SKILLS)
            pred.extend(vs_pred.get(rally_id, []))
        found = action_eval.evaluate(pred, true)
        found["labels_complete"] = video.actions_reviewed_at is not None
        out["vs_coach"] = found
    return out


def _check_model_run(db: Session, result: ActionsResult) -> uuid.UUID | None:
    if result.finder == "v0":
        return None
    if result.model_run_id is None:
        raise ValueError("v1 actions must name their model run")
    run = db.get(ModelRun, result.model_run_id)
    if run is None or run.kind != "train_actions":
        raise ValueError("unknown action model run")
    return run.id


def complete_actions(
    db: Session, video: Video, job: Job, payload: dict[str, Any], settings: Settings | None = None
) -> ModelRun:
    result = ActionsResult.model_validate(payload)
    _check_model_run(db, result)
    run = ModelRun(
        id=uuid.uuid4(),
        kind="actions",
        job_id=job.id,
        video_id=video.id,
        model_name=result.model.name,
        model_version=result.model.version,
        params={**result.model.params, "reason": job.params.get("reason")},
        timing=result.timing,
    )
    db.add(run)
    db.flush()
    run.metrics = apply_actions(db, video, run, result, gate(db, settings))
    return run


def complete_train(
    db: Session, job: Job, payload: dict[str, Any], settings: Settings | None = None
) -> ModelRun:
    result = TrainResult.model_validate(payload)
    before = gate(db, settings)
    if result.accepted and result.artifact is None:
        raise ValueError("an accepted training must store its model")
    if result.artifact is not None:
        analysis._check_path(result.artifact.nas_path, job.params["out_dir"])
    run = ModelRun(
        id=uuid.uuid4(),
        kind="train_actions",
        job_id=job.id,
        model_name=result.model.name,
        model_version=result.model.version,
        weights_sha256=result.artifact.sha256 if result.artifact else None,
        dataset_manifest={"videos": result.dataset},
        params=result.model.params,
        timing=result.timing,
        metrics={
            "accepted": result.accepted,
            "finder": "v1" if result.accepted else "v0",
            "summary": result.evaluation.get("summary", {}),
            "videos": result.evaluation.get("videos", {}),
        },
    )
    db.add(run)
    db.flush()
    if result.artifact is not None:
        db.add(
            AnalysisArtifact(
                video_id=None,
                model_run_id=run.id,
                kind="action_model",
                **result.artifact.model_dump(),
            )
        )
    db.flush()
    after = gate_of(run, settings or get_settings())
    run.metrics = {**run.metrics, "gate": after.as_dict()}
    if after.open and (result.accepted or not before.open):
        # A new model that is good enough, or the bar passed for the first time: suggestions
        # for every game nobody has done yet.
        trained = {str(v.get("video_id")) for v in result.dataset}
        for video in db.scalars(
            select(Video).where(
                Video.sport.in_(SPORTS),
                Video.status == "ready",
                Video.actions_reviewed_at.is_(None),
            )
        ):
            if str(video.id) not in trained:
                queue_actions(db, video, reason="train_actions")
    return run


def complete(
    db: Session,
    video: Video | None,
    job: Job,
    payload: dict[str, Any],
    settings: Settings | None = None,
) -> None:
    if job.kind == "actions":
        if video is None:
            raise ValueError("job has no video")
        if video.status != "ready":
            jobs.succeed(job, {"skipped": f"video is {video.status}"})
            return
        run = complete_actions(db, video, job, payload, settings)
    else:
        run = complete_train(db, job, payload, settings)
    jobs.succeed(job, {"model_run_id": str(run.id)})


# --- the coach's side ------------------------------------------------------------------------


class OpenSuggestions(Exception):
    def __init__(self, count: int) -> None:
        super().__init__(f"{count} suggested actions are still open")
        self.count = count


def mark_reviewed(
    db: Session, video: Video, actor: int, reviewed: bool, train_delay: timedelta
) -> dict[str, Any]:
    """The coach says the video's actions are complete and right (or takes that back). Not
    while suggestions are open: each one is answered first, so every label is a person's.
    Either way the training set changed: a (debounced) training is queued."""
    if reviewed:
        if count := open_suggestions(db, video.id):
            raise OpenSuggestions(count)
        video.actions_reviewed_at = datetime.now(UTC)
        video.actions_reviewed_by = actor
    else:
        video.actions_reviewed_at = None
        video.actions_reviewed_by = None
    job = queue_training(db, train_delay) if video.sport in SPORTS else None
    return {"training_job_id": str(job.id) if job else None}


def latest_run(db: Session, video_id: uuid.UUID) -> ModelRun | None:
    return db.scalars(
        select(ModelRun)
        .where(ModelRun.kind == "actions", ModelRun.video_id == video_id)
        .order_by(ModelRun.created_at.desc(), ModelRun.id.desc())
        .limit(1)
    ).first()


def latest_job(db: Session, video_id: uuid.UUID) -> Job | None:
    return db.scalars(
        select(Job)
        .where(Job.kind == "actions", Job.video_id == video_id)
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()
