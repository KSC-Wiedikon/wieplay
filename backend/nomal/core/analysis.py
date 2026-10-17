"""Automatic analysis, API side (PLAN §10.1, §11): the loop that learns from the coaches.

    video ready ──analyse──▶ signals on the NAS + rally proposals (or, when a coach has
                              rallies there already, only their score against them)
    coach marks the rallies done ──train_rallies (debounced)──▶ v1 learned from every
                              reviewed game, kept only when it beats v0 on held-out games;
                              new proposals for every game nobody has done yet

Workers hold no database access: they ask for their inputs (`job_inputs`), compute, and
send a result that is validated and written here, in the worker's request transaction.

Proposal rules: a model never overwrites or duplicates a coach's rally. A video whose
rallies a person made, confirmed or corrected gets no proposals, only metrics. Proposals
nobody touched are replaced on the next run; a new proposal that overlaps a rally that is
there, or one a coach rejected, is dropped.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from nomal.core import jobs, pipeline, scoring, tagging
from nomal.core.models import (
    AnalysisArtifact,
    AnalysisTrack,
    Job,
    ModelRun,
    Segment,
    Video,
    WdTeam,
)
from nomal.ml import metrics
from nomal.ml import tracks as track_format

ANALYSE_CAPS = ("analyse", "nas")
TRAIN_CAPS = ("analyse", "nas")
# Below every job that leads to playback or a download (purge 1, export 3, transcode 4-5,
# thumbs 6, ingest/import 10); training before the analyses, so a backfill uses the newest
# model as soon as it exists.
ANALYSE_PRIORITY = -10
TRAIN_PRIORITY = -5
FOLLOW_UP_PRIORITY = jobs.FOLLOW_UP_PRIORITY
KINDS = frozenset({"analyse", "train_rallies"})
SPORTS = frozenset({"volleyball"})
MAX_PROPOSALS = 1000
# A proposal is dropped when it covers this much of a rally a coach rejected.
REJECTED_OVERLAP = 0.5
NAS_PATH = re.compile(r"[A-Za-z0-9._/-]+")
HUMAN_REVIEWS = ("confirmed", "corrected")

Finder = Literal["v0", "v1"]


# --- what counts as the coach's rallies ----------------------------------------------------


def human_rally_filter() -> Any:
    """Rallies a person stands behind: made by hand (or imported from a scoresheet), or a
    model's proposal a person confirmed or corrected. Rejected and deleted ones are not."""
    return (
        (Segment.kind == "rally")
        & Segment.deleted_at.is_(None)
        & (Segment.review != "rejected")
        & (Segment.source.in_(("manual", "import")) | Segment.review.in_(HUMAN_REVIEWS))
    )


def human_rallies(db: Session, video_id: uuid.UUID) -> list[tuple[int, int]]:
    rows = db.execute(
        select(Segment.start_ms, Segment.end_ms)
        .where(Segment.video_id == video_id, human_rally_filter())
        .order_by(Segment.start_ms)
    ).all()
    return [(int(s), int(e)) for s, e in rows]


def _untouched_proposals() -> Any:
    return (
        (Segment.kind == "rally")
        & (Segment.source == "model")
        & (Segment.review == "unreviewed")
        & Segment.updated_by.is_(None)
        & Segment.deleted_at.is_(None)
    )


# --- queueing --------------------------------------------------------------------------------


def analysis_subdir(db: Session, video: Video) -> str:
    """The video's folder under the analysis root, named like its raw folder (the purge
    of a deleted video removes both)."""
    raw = pipeline.raw_folder(db, video)
    if raw is None:
        team = db.get(WdTeam, video.team_id)
        assert team is not None
        raw = pipeline.raw_subdir(video, team)
    return f"videos/{raw}"


MODELS_SUBDIR = "models/rallies"


def can_analyse(video: Video) -> str | None:
    """Why a video cannot be analysed, or None."""
    if video.sport not in SPORTS:
        return f"No automatic analysis for {video.sport} yet"
    if video.status != "ready":
        return "Analyse a video once it is ready"
    if not video.files or any(not f.nas_path for f in video.files):
        return "The video's originals are not on the NAS"
    return None


def active_job(db: Session, kind: str, video_id: uuid.UUID | None = None) -> Job | None:
    query = select(Job).where(Job.kind == kind, Job.status.in_(jobs.ACTIVE))
    if video_id is not None:
        query = query.where(Job.video_id == video_id)
    return db.scalars(query.order_by(Job.created_at)).first()


def queue_analyse(db: Session, video: Video) -> Job:
    """Queue the analyse job of a ready volleyball video; an active one is reused."""
    reason = can_analyse(video)
    if reason:
        raise ValueError(reason)
    found = active_job(db, "analyse", video.id)
    if found is not None:
        return found
    fps = video.fps_num / video.fps_den if video.fps_num and video.fps_den else 0.0
    return jobs.enqueue(
        db,
        "analyse",
        caps=ANALYSE_CAPS,
        video_id=video.id,
        priority=ANALYSE_PRIORITY,
        max_attempts=2,
        params={
            "video_id": str(video.id),
            "inputs": [f.nas_path for f in video.files],
            "duration_ms": video.duration_ms,
            "fps": fps,
            "has_audio": bool(video.has_audio),
            "out_dir": analysis_subdir(db, video),
        },
    )


def on_video_ready(db: Session, video: Video, auto: bool) -> Job | None:
    """After a video becomes ready: analyse it when automatic analysis is on, it is a
    volleyball video and it meets the capture standard (PLAN §6.4: tier 2 skips the rest;
    a coach can still ask for it)."""
    if not auto or video.meets_standard is False or can_analyse(video) is not None:
        return None
    return queue_analyse(db, video)


def queue_training(db: Session, delay: timedelta, now: datetime | None = None) -> Job:
    """Train the rally finder `delay` from now. A queued training is moved instead of a
    second one added, so a coach marking several games in a row causes one run. A running
    one gets a successor (it may have read the training set already)."""
    now = now or datetime.now(UTC)
    at = now + delay
    queued = db.scalars(
        select(Job)
        .where(Job.kind == "train_rallies", Job.status == "queued")
        .order_by(Job.created_at)
        .with_for_update()
    ).first()
    if queued is not None:
        queued.not_before = at
        return queued
    return jobs.enqueue(
        db,
        "train_rallies",
        caps=TRAIN_CAPS,
        priority=TRAIN_PRIORITY,
        max_attempts=2,
        not_before=at,
        params={"sport": "volleyball", "out_dir": MODELS_SUBDIR},
    )


# --- inputs a worker asks for ---------------------------------------------------------------


def _artifact_out(a: AnalysisArtifact) -> dict[str, Any]:
    return {"artifact_id": str(a.id), "nas_path": a.nas_path, "sha256": a.sha256}


def latest_features(db: Session, video_ids: list[uuid.UUID]) -> dict[uuid.UUID, AnalysisArtifact]:
    rows = db.scalars(
        select(AnalysisArtifact)
        .where(AnalysisArtifact.kind == "features", AnalysisArtifact.video_id.in_(video_ids))
        .order_by(AnalysisArtifact.created_at)
    )
    return {a.video_id: a for a in rows if a.video_id is not None}  # the newest wins


def current_rally_model(db: Session) -> tuple[ModelRun, AnalysisArtifact] | None:
    """The newest training that beat v0 and wrote a model."""
    runs = db.scalars(
        select(ModelRun)
        .where(ModelRun.kind == "train_rallies", ModelRun.metrics["accepted"].as_boolean())
        .order_by(ModelRun.created_at.desc())
    )
    for run in runs:
        artifact = db.scalars(
            select(AnalysisArtifact).where(
                AnalysisArtifact.model_run_id == run.id, AnalysisArtifact.kind == "rally_model"
            )
        ).first()
        if artifact is not None:
            return run, artifact
    return None


def current_serve_model(db: Session) -> tuple[ModelRun, AnalysisArtifact] | None:
    """The newest training whose serve model beat the serve rule (v0) on held-out games."""
    runs = db.scalars(
        select(ModelRun)
        .where(
            ModelRun.kind == "train_rallies",
            ModelRun.metrics["serve"]["accepted"].as_boolean(),
        )
        .order_by(ModelRun.created_at.desc())
    )
    for run in runs:
        artifact = db.scalars(
            select(AnalysisArtifact).where(
                AnalysisArtifact.model_run_id == run.id, AnalysisArtifact.kind == "serve_model"
            )
        ).first()
        if artifact is not None:
            return run, artifact
    return None


def _serve_model_out(db: Session) -> dict[str, Any] | None:
    found = current_serve_model(db)
    if found is None:
        return None
    run, artifact = found
    return {"model_run_id": str(run.id), **_artifact_out(artifact)}


def latest_training(db: Session) -> ModelRun | None:
    return db.scalars(
        select(ModelRun)
        .where(ModelRun.kind == "train_rallies")
        .order_by(ModelRun.created_at.desc())
        .limit(1)
    ).first()


def _rally_model_out(db: Session) -> dict[str, Any] | None:
    found = current_rally_model(db)
    if found is None:
        return None
    run, artifact = found
    return {"model_run_id": str(run.id), **_artifact_out(artifact)}


def _analysable_videos(db: Session) -> list[Video]:
    return list(
        db.scalars(
            select(Video)
            .where(Video.sport.in_(SPORTS), Video.status == "ready")
            .order_by(Video.recorded_on, Video.id)
        )
    )


def job_inputs(db: Session, job: Job) -> dict[str, Any]:
    """What a leased analysis job needs from the database, read when it runs (not when it
    was queued): the current rally model; for a training, the reviewed games with their
    rallies and the other analysed games to propose rallies for."""
    if job.kind == "analyse":
        return {"rally_model": _rally_model_out(db), "serve_model": _serve_model_out(db)}
    if job.kind != "train_rallies":
        raise ValueError(f"{job.kind} has no inputs")
    videos = _analysable_videos(db)
    features = latest_features(db, [v.id for v in videos])
    have_tracks: dict[uuid.UUID, set[str]] = {}
    for video_id, kind in db.execute(
        select(AnalysisTrack.video_id, AnalysisTrack.kind)
        .where(AnalysisTrack.video_id.in_([v.id for v in videos]))
        .distinct()
    ):
        have_tracks.setdefault(video_id, set()).add(kind)
    train: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for video in videos:
        artifact = features.get(video.id)
        if artifact is None:
            continue
        entry: dict[str, Any] = {
            "video_id": str(video.id),
            "features": _artifact_out(artifact),
            # Series the API already has for it (the worker adds what is missing).
            "tracks": sorted(have_tracks.get(video.id, set())),
        }
        rallies = human_rallies(db, video.id) if video.rallies_reviewed_at else []
        if rallies and open_proposals(db, video.id):
            # Reopened after "done": not complete now, so no labels from it until it is.
            skipped.append({**entry, "reason": "open proposals"})
        elif rallies:
            train.append(
                {
                    **entry,
                    "rallies": [list(r) for r in rallies],
                    "serves": scoring.serve_labels(db, video),
                }
            )
        else:
            others.append(entry)
    return {
        "rally_model": _rally_model_out(db),
        "serve_model": _serve_model_out(db),
        "train": train,
        "others": others,
        "skipped": skipped,
    }


# --- results from the worker ----------------------------------------------------------------


class ProposalIn(BaseModel):
    start_ms: int = Field(ge=0, le=tagging.MAX_MEDIA_MS)
    end_ms: int = Field(ge=0, le=tagging.MAX_MEDIA_MS)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _order(self) -> ProposalIn:
        if self.end_ms <= self.start_ms:
            raise ValueError("a proposal ends after it starts")
        return self


class RalliesIn(BaseModel):
    finder: Literal["v0", "v1"]
    model_run_id: uuid.UUID | None = None  # the training whose model found them (v1)
    proposals: list[ProposalIn] = Field(max_length=MAX_PROPOSALS)


class ArtifactIn(BaseModel):
    nas_path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)
    format: Literal["npz", "pickle"]
    schema_version: int = Field(ge=1, le=1000)
    stats: dict[str, Any] = Field(default_factory=dict)


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    weights_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    params: dict[str, Any] = Field(default_factory=dict)


MAX_TRACK_TEXT = 512 * 1024  # base64 of a compressed channel; a 2 h match is ~20 KB


class TrackIn(BaseModel):
    """One of the small series of a video (nomal/ml/tracks.py)."""

    step_ms: int = Field(ge=100, le=10_000)
    channels: dict[str, str] = Field(max_length=8)
    finder: Literal["v0", "v1"] | None = None  # serve: the rule or the trained model
    model_run_id: uuid.UUID | None = None  # serve v1: its training

    @model_validator(mode="after")
    def _channels(self) -> TrackIn:
        for name, text in self.channels.items():
            if not re.fullmatch(r"[a-z_]{1,16}", name) or len(text) > MAX_TRACK_TEXT:
                raise ValueError("bad track channel")
            track_format.decode(text)  # raises on anything that is not one
        return self


class TracksIn(BaseModel):
    serve: TrackIn | None = None
    activity: TrackIn | None = None

    @model_validator(mode="after")
    def _kinds(self) -> TracksIn:
        if self.serve is not None and set(self.serve.channels) != set(track_format.SERVE_CHANNELS):
            raise ValueError("a serve track has the channel p_near")
        if self.activity is not None and set(self.activity.channels) != set(
            track_format.ACTIVITY_CHANNELS
        ):
            raise ValueError("an activity track has the channels ball, court, side, whistle")
        return self


class AnalyseResult(BaseModel):
    artifact: ArtifactIn
    model: ModelIn
    timing: dict[str, Any] = Field(default_factory=dict)
    rallies: RalliesIn
    tracks: TracksIn | None = None


class ServeTrainIn(BaseModel):
    accepted: bool  # v1 beat the rule on held-out games
    artifact: ArtifactIn | None = None  # the v1 model, when one was trained
    # summary {v0, v1 mean accuracy, games}; per video {"v0": {...}, "v1": {...}}
    evaluation: dict[str, Any] = Field(default_factory=dict)


class TrainResult(BaseModel):
    model: ModelIn
    artifact: ArtifactIn | None = None  # the v1 model, when one was trained
    accepted: bool
    timing: dict[str, Any] = Field(default_factory=dict)
    # summary (v0/v1 on held-out games) and per training video {"v0": {...}, "v1": {...}}
    evaluation: dict[str, Any] = Field(default_factory=dict)
    dataset: list[dict[str, Any]] = Field(default_factory=list, max_length=10_000)
    # New rallies for the games that were not trained on, by video id.
    predictions: dict[uuid.UUID, RalliesIn] = Field(default_factory=dict)
    # The serve model (PLAN §10.3): trained and scored on held-out games like the rally finder.
    serve: ServeTrainIn | None = None
    # New series by video id: the serve track with the current serve finder, activity where
    # the API had none.
    tracks: dict[uuid.UUID, TracksIn] = Field(default_factory=dict, max_length=10_000)


def _check_path(path: str, out_dir: str) -> None:
    """Artifact paths are relative to the analysis root and inside the job's folder."""
    if (
        not NAS_PATH.fullmatch(path)
        or path.startswith("/")
        or not path.startswith(out_dir.rstrip("/") + "/")
        or "/../" in f"/{path}/"
        or "/./" in f"/{path}/"
    ):
        raise ValueError("artifact path outside the job's analysis folder")


def _model_run_ref(db: Session, rallies: RalliesIn) -> uuid.UUID | None:
    if rallies.finder == "v0":
        return None
    if rallies.model_run_id is None:
        raise ValueError("v1 proposals must name their model run")
    run = db.get(ModelRun, rallies.model_run_id)
    if run is None or run.kind != "train_rallies":
        raise ValueError("unknown rally model run")
    return run.id


def apply_rallies(
    db: Session, video: Video, run: ModelRun, rallies: RalliesIn, *, source_run: uuid.UUID | None
) -> dict[str, Any]:
    """Write a model's rallies for a video under the proposal rules (module docstring).
    Returns what happened, for the run's per-video metrics."""
    proposals = sorted((p.start_ms, p.end_ms, p.confidence) for p in rallies.proposals)
    limit = (video.duration_ms or 0) + 1000
    proposals = [p for p in proposals if p[0] < limit]
    out: dict[str, Any] = {
        "finder": rallies.finder,
        "rally_model_run": str(source_run) if source_run else None,
    }
    human = human_rallies(db, video.id)
    if human:
        out.update(
            mode="metrics",
            eval=metrics.evaluate([(s, e) for s, e, _ in proposals], human),
            labels_complete=video.rallies_reviewed_at is not None,
        )
        return out
    replaced = db.execute(
        delete(Segment).where(Segment.video_id == video.id, _untouched_proposals())
    ).rowcount  # type: ignore[attr-defined]  # a CursorResult for DML
    db.flush()
    rallies_now = db.execute(
        select(Segment.start_ms, Segment.end_ms, Segment.deleted_at, Segment.review).where(
            Segment.video_id == video.id, Segment.kind == "rally"
        )
    ).all()
    live = [
        (int(s), int(e))
        for s, e, deleted, review in rallies_now
        if deleted is None and review not in ("rejected", "merged")
    ]
    rejected = [  # a merged proposal is not "no rally": the one it went into covers it
        (int(s), int(e))
        for s, e, deleted, review in rallies_now
        if review == "rejected" or (deleted is not None and review != "merged")
    ]
    room = tagging.MAX_SEGMENTS - int(
        db.scalar(select(func.count()).select_from(Segment).where(Segment.video_id == video.id))
        or 0
    )
    written = skipped = 0
    for start, end, confidence in proposals:
        span = (start, min(end, limit))
        if any(min(span[1], e) > max(span[0], s) for s, e in live) or any(
            (min(span[1], e) - max(span[0], s)) / (span[1] - span[0]) >= REJECTED_OVERLAP
            for s, e in rejected
        ):
            skipped += 1
            continue
        if written >= room:
            skipped += 1
            continue
        db.add(
            Segment(
                id=uuid.uuid4(),
                video_id=video.id,
                kind="rally",
                start_ms=span[0],
                end_ms=span[1],
                source="model",
                model_run_id=run.id,
                confidence=confidence,
                review="unreviewed",
                version=1,
            )
        )
        written += 1
    tagging.rescore(db, video)  # sets and tag links follow the new rallies
    out.update(mode="proposals", proposed=written, replaced=replaced, skipped=skipped)
    return out


def complete_analyse(db: Session, video: Video, job: Job, payload: dict[str, Any]) -> ModelRun:
    result = AnalyseResult.model_validate(payload)
    _check_path(result.artifact.nas_path, job.params["out_dir"])
    if result.artifact.format != "npz":
        raise ValueError("signals are stored as npz")
    source_run = _model_run_ref(db, result.rallies)
    run = ModelRun(
        id=uuid.uuid4(),
        kind="analyse",
        job_id=job.id,
        video_id=video.id,
        model_name=result.model.name,
        model_version=result.model.version,
        weights_sha256=result.model.weights_sha256,
        params=result.model.params,
        timing=result.timing,
    )
    db.add(run)
    db.flush()
    db.add(
        AnalysisArtifact(
            video_id=video.id,
            model_run_id=run.id,
            kind="features",
            **result.artifact.model_dump(),
        )
    )
    outcome = apply_rallies(db, video, run, result.rallies, source_run=source_run)
    if result.tracks is not None:
        _check_serve_run(db, result.tracks)
        outcome["tracks"] = scoring.store_tracks(
            db, video.id, run, result.tracks.model_dump(mode="json", exclude_none=True)
        )
    run.metrics = {"signals": result.artifact.stats, "videos": {str(video.id): outcome}}
    scoring.score_video(db, video, reason="analyse")
    return run


def _check_serve_run(db: Session, found: TracksIn) -> None:
    serve = found.serve
    if serve is None or serve.finder != "v1":
        return
    run = db.get(ModelRun, serve.model_run_id) if serve.model_run_id else None
    if run is None or run.kind != "train_rallies":
        raise ValueError("a v1 serve track must name its training")


def complete_train(db: Session, job: Job, payload: dict[str, Any]) -> ModelRun:
    result = TrainResult.model_validate(payload)
    if result.accepted and result.artifact is None:
        raise ValueError("an accepted training must store its model")
    if result.artifact is not None:
        _check_path(result.artifact.nas_path, job.params["out_dir"])
        if result.artifact.format != "pickle":
            raise ValueError("rally models are stored as pickle")
    serve = result.serve
    if serve is not None:
        if serve.accepted and serve.artifact is None:
            raise ValueError("an accepted serve model must be stored")
        if serve.artifact is not None:
            _check_path(serve.artifact.nas_path, job.params["out_dir"])
            if serve.artifact.format != "pickle":
                raise ValueError("serve models are stored as pickle")
    run = ModelRun(
        id=uuid.uuid4(),
        kind="train_rallies",
        job_id=job.id,
        model_name=result.model.name,
        model_version=result.model.version,
        weights_sha256=result.artifact.sha256 if result.artifact else None,
        dataset_manifest={"videos": result.dataset},
        params=result.model.params,
        timing=result.timing,
    )
    db.add(run)
    db.flush()
    if result.artifact is not None:
        db.add(
            AnalysisArtifact(
                video_id=None,
                model_run_id=run.id,
                kind="rally_model",
                **result.artifact.model_dump(),
            )
        )
    if serve is not None and serve.artifact is not None:
        db.add(
            AnalysisArtifact(
                video_id=None,
                model_run_id=run.id,
                kind="serve_model",
                **serve.artifact.model_dump(),
            )
        )
    per_video: dict[str, Any] = {}
    for video_id, entry in (result.evaluation.get("videos") or {}).items():
        per_video[str(video_id)] = {"mode": "trained_on", "lovo": entry}
    for video_id, rallies in result.predictions.items():
        video = db.get(Video, video_id, with_for_update=True)
        if video is None or video.status != "ready" or video.sport not in SPORTS:
            continue
        # The proposals come from this run when v1 was accepted, else from v0.
        if rallies.finder == "v1" and not result.accepted:
            raise ValueError("v1 proposals from a model that was not accepted")
        source = run.id if rallies.finder == "v1" else None
        per_video[str(video_id)] = apply_rallies(db, video, run, rallies, source_run=source)
    touched = {uuid.UUID(v) for v in per_video if per_video[v].get("mode") != "trained_on"}
    for video_id, found in result.tracks.items():
        video = db.get(Video, video_id)
        if video is None or video.status != "ready" or video.sport not in SPORTS:
            continue
        if found.serve is not None and found.serve.finder == "v1":
            if found.serve.model_run_id is None:  # this training's serve model
                if not (serve and serve.accepted):
                    raise ValueError("v1 serve tracks from a serve model that was not accepted")
                found.serve.model_run_id = run.id
            else:
                _check_serve_run(db, found)
        scoring.store_tracks(db, video.id, run, found.model_dump(mode="json", exclude_none=True))
        touched.add(video.id)
    run.metrics = {
        "accepted": result.accepted,
        "finder": "v1" if result.accepted else "v0",
        "summary": result.evaluation.get("summary", {}),
        "videos": per_video,
        "serve": {
            "accepted": bool(serve and serve.accepted),
            "finder": "v1" if serve and serve.accepted else "v0",
            "summary": serve.evaluation.get("summary", {}) if serve else {},
            "videos": serve.evaluation.get("videos", {}) if serve else {},
        },
    }
    for video_id in sorted(touched, key=str):
        video = db.get(Video, video_id)
        if video is not None and video.status == "ready" and video.sport in SPORTS:
            scoring.score_video(db, video, reason="train")
    return run


def complete(db: Session, video: Video | None, job: Job, payload: dict[str, Any]) -> None:
    if job.kind == "analyse":
        if video is None:
            raise ValueError("job has no video")
        if video.status != "ready":
            jobs.succeed(job, {"skipped": f"video is {video.status}"})
            return
        run = complete_analyse(db, video, job, payload)
    else:
        run = complete_train(db, job, payload)
    jobs.succeed(job, {"model_run_id": str(run.id)})


# --- the coach's side ------------------------------------------------------------------------


def open_proposals(db: Session, video_id: uuid.UUID) -> int:
    """Model rallies nobody has accepted, corrected, rejected or merged yet."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(Segment)
            .where(
                Segment.video_id == video_id,
                Segment.kind == "rally",
                Segment.source == "model",
                Segment.review == "unreviewed",
                Segment.deleted_at.is_(None),
            )
        )
        or 0
    )


class OpenProposals(Exception):
    def __init__(self, count: int) -> None:
        super().__init__(f"{count} proposals are still open")
        self.count = count


def mark_reviewed(
    db: Session, video: Video, actor: int, reviewed: bool, train_delay: timedelta
) -> dict[str, Any]:
    """The coach says the rally list is complete and right (or takes that back). Not while
    proposals are open (OpenProposals): the coach answers each one first, so every label
    is a person's. Either way the training set changed, so a (debounced) training is
    queued."""
    if reviewed:
        if count := open_proposals(db, video.id):
            raise OpenProposals(count)
        video.rallies_reviewed_at = datetime.now(UTC)
        video.rallies_reviewed_by = actor
    else:
        video.rallies_reviewed_at = None
        video.rallies_reviewed_by = None
    job = queue_training(db, train_delay) if video.sport in SPORTS else None
    scoring.score_video(db, video, reason="rallies_reviewed" if reviewed else "reopened")
    return {"training_job_id": str(job.id) if job else None}


def proposal_counts(db: Session, video_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(
        select(Segment.review, Segment.deleted_at.is_(None), func.count())
        .where(Segment.video_id == video_id, Segment.kind == "rally", Segment.source == "model")
        .group_by(Segment.review, Segment.deleted_at.is_(None))
    ).all()
    counts = {"unreviewed": 0, "confirmed": 0, "corrected": 0, "rejected": 0, "merged": 0}
    for review, live, n in rows:
        # A deleted proposal is rejected or merged; one deleted otherwise counts as rejected.
        key = review if live or review in ("rejected", "merged") else "rejected"
        counts[key] = counts.get(key, 0) + int(n)
    return counts


def latest_rally_result(db: Session, video_id: uuid.UUID) -> tuple[ModelRun, dict[str, Any]] | None:
    """The newest run that produced rallies or a score for this video."""
    run = db.scalars(
        select(ModelRun)
        .where(
            or_(ModelRun.video_id == video_id, ModelRun.kind == "train_rallies"),
            ModelRun.metrics["videos"].has_key(str(video_id)),
        )
        .order_by(ModelRun.created_at.desc())
        .limit(1)
    ).first()
    if run is None:
        return None
    return run, dict(run.metrics["videos"][str(video_id)])
