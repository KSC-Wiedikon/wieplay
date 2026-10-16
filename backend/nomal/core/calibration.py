"""The ball's path and the court of a video (PLAN §10.4, §10.6), API side.

    analyse done ──trajectory job──▶ the ball track, smoothed, with contacts and bounces
                                     (gzipped JSON in the bucket, a copy on the NAS)
                                   + the court found in the picture (a proposal)
    coach clicks or confirms the corners ──▶ the video's calibration (a homography, the
                                     camera): floor-level evidence maps to the court

A calibration row per video and status: `confirmed` is the coach's and the one that
counts; `proposed` is the court finder's, offered in the dialog when it is confident.
New videos of a team are pre-filled from that team's last confirmed calibration (the
camera usually stands at the same spot).
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from nomal.core import court, jobs
from nomal.core.db import Base
from nomal.core.models import AnalysisArtifact, Job, ModelRun, Video, WdGame

KINDS = frozenset({"trajectory"})
CAPS = ("analyse", "nas")
PRIORITY = -10  # with the analyses: below everything that leads to playback
TRACK_FORMAT = "json"
TRACK_SCHEMA = 1
NAS_PATH = re.compile(r"[A-Za-z0-9._/-]+")
# The dialog is pre-filled with a court the finder found at or above this confidence.
AUTO_CONFIDENT = 0.6
DEFAULT_ASPECT = 16 / 9

Status = Literal["confirmed", "proposed"]
Method = Literal["manual", "auto", "copied"]


class Calibration(Base):
    """The court in a video's picture (PLAN §6.9 `calibration`): landmarks clicked or found,
    the homography image → court and the camera recovered from it."""

    __tablename__ = "calibration"
    __table_args__ = (UniqueConstraint("video_id", "status", name="uq_calibration_video_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video.id", ondelete="CASCADE"), index=True
    )
    # For "the team's last calibration" and later "the hall's" (PLAN §10.6).
    lineage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_lineage.id"), index=True)
    hall_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))  # confirmed | proposed
    method: Mapped[str] = mapped_column(String(16))  # manual | auto | copied
    landmarks: Mapped[list[str]] = mapped_column(JSONB)
    image_points: Mapped[list[list[float]]] = mapped_column(JSONB)  # normalised (u, v)
    court_points: Mapped[list[list[float]]] = mapped_column(JSONB)  # metres (x, y)
    image_aspect: Mapped[float] = mapped_column(Float)
    homography: Mapped[list[list[float]]] = mapped_column(JSONB)  # image → court
    camera: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reproj_error_cm: Mapped[float | None] = mapped_column(Float)  # RMS over the landmarks
    max_error_cm: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)  # the court finder's
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_run.id", ondelete="SET NULL")
    )
    copied_from: Mapped[uuid.UUID | None] = mapped_column(UUID)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def points(self) -> dict[str, court.Point]:
        return {
            name: (float(p[0]), float(p[1]))
            for name, p in zip(self.landmarks, self.image_points, strict=True)
        }


# --- the ball track -------------------------------------------------------------------------


def latest_track(db: Session, video_id: uuid.UUID) -> AnalysisArtifact | None:
    return db.scalars(
        select(AnalysisArtifact)
        .where(AnalysisArtifact.video_id == video_id, AnalysisArtifact.kind == "ball_track")
        .order_by(AnalysisArtifact.created_at.desc())
        .limit(1)
    ).first()


def _latest_features(db: Session, video_id: uuid.UUID) -> AnalysisArtifact | None:
    return db.scalars(
        select(AnalysisArtifact)
        .where(AnalysisArtifact.video_id == video_id, AnalysisArtifact.kind == "features")
        .order_by(AnalysisArtifact.created_at.desc())
        .limit(1)
    ).first()


def active_job(db: Session, video_id: uuid.UUID) -> Job | None:
    return db.scalars(
        select(Job).where(
            Job.kind == "trajectory", Job.video_id == video_id, Job.status.in_(jobs.ACTIVE)
        )
    ).first()


def queue_trajectory(db: Session, video: Video, out_dir: str) -> Job | None:
    """Queue the trajectory job of a video with signals (an active one is reused). None
    when the video has no signals yet."""
    features = _latest_features(db, video.id)
    if features is None or video.status != "ready":
        return None
    found = active_job(db, video.id)
    if found is not None:
        return found
    token = secrets.token_hex(6)
    return jobs.enqueue(
        db,
        "trajectory",
        caps=CAPS,
        video_id=video.id,
        priority=PRIORITY,
        max_attempts=2,
        params={
            "video_id": str(video.id),
            "features": {
                "artifact_id": str(features.id),
                "nas_path": features.nas_path,
                "sha256": features.sha256,
            },
            "inputs": [f.nas_path for f in video.files if f.nas_path],
            "duration_ms": video.duration_ms,
            "out_dir": out_dir,
            "track_key": f"media/{video.id}/analysis/ball-track-{token}.json.gz",
        },
    )


def after_analyse(db: Session, video: Video | None, job: Job) -> None:
    """New signals: their ball track and court follow."""
    if video is not None and job.kind == "analyse" and job.status == "succeeded":
        queue_trajectory(db, video, str(job.params["out_dir"]))


def may_sign(job: Job, op: str, key: str) -> bool:
    return job.kind == "trajectory" and op == "put" and key == job.params.get("track_key")


# --- results from the worker ----------------------------------------------------------------


class TrackIn(BaseModel):
    nas_path: str = Field(min_length=1, max_length=1024)
    key: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    schema_version: int = Field(ge=1, le=1000)
    stats: dict[str, Any] = Field(default_factory=dict)


class CourtGuessIn(BaseModel):
    points: dict[str, tuple[float, float]] = Field(max_length=len(court.LANDMARKS))
    confidence: float = Field(ge=0, le=1)
    stats: dict[str, Any] = Field(default_factory=dict)

    @field_validator("points")
    @classmethod
    def _known(cls, value: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
        if set(value) - set(court.LANDMARKS):
            raise ValueError("unknown landmark")
        if any(not (-1 <= u <= 2 and -1 <= v <= 2) for u, v in value.values()):
            raise ValueError("landmark far outside the frame")
        return value


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class TrajectoryResult(BaseModel):
    model: ModelIn
    track: TrackIn
    court: CourtGuessIn | None = None
    timing: dict[str, Any] = Field(default_factory=dict)


def _inside(path: str, out_dir: str) -> bool:
    return (
        bool(NAS_PATH.fullmatch(path))
        and not path.startswith("/")
        and path.startswith(out_dir.rstrip("/") + "/")
        and "/../" not in f"/{path}/"
        and "/./" not in f"/{path}/"
    )


def aspect_of(video: Video) -> float:
    if video.width and video.height:
        return video.width / video.height
    return DEFAULT_ASPECT


def hall_of(db: Session, video: Video) -> str | None:
    if video.game_id is None:
        return None
    game = db.get(WdGame, video.game_id)
    return game.hall_name if game is not None else None


def complete(db: Session, video: Video | None, job: Job, payload: dict[str, Any]) -> None:
    """Validate and store a trajectory result: the track's artifact, and the court the
    finder proposes (replacing its earlier proposal). Raises ValueError on a bad result."""
    if video is None:
        raise ValueError("job has no video")
    result = TrajectoryResult.model_validate(payload)
    if not _inside(result.track.nas_path, str(job.params["out_dir"])):
        raise ValueError("track path outside the job's analysis folder")
    if result.track.key != job.params.get("track_key"):
        raise ValueError("track stored under another key")
    if video.status != "ready":
        jobs.succeed(job, {"skipped": f"video is {video.status}"})
        return
    run = ModelRun(
        id=uuid.uuid4(),
        kind="trajectory",
        job_id=job.id,
        video_id=video.id,
        model_name=result.model.name,
        model_version=result.model.version,
        params=result.model.params,
        metrics={
            "track": result.track.stats,
            "court": (
                {"confidence": result.court.confidence, **result.court.stats}
                if result.court
                else None
            ),
        },
        timing=result.timing,
    )
    db.add(run)
    db.flush()
    db.add(
        AnalysisArtifact(
            video_id=video.id,
            model_run_id=run.id,
            kind="ball_track",
            nas_path=result.track.nas_path,
            format=TRACK_FORMAT,
            schema_version=result.track.schema_version,
            sha256=result.track.sha256,
            bytes=result.track.bytes,
            stats={
                **result.track.stats,
                "object_key": result.track.key,
                "features_artifact_id": job.params["features"]["artifact_id"],
            },
        )
    )
    if result.court is not None:
        try:
            solution = court.solve(dict(result.court.points), aspect_of(video))
        except court.CourtError:
            solution = None
        if solution is not None:
            save(
                db,
                video,
                dict(result.court.points),
                solution,
                status="proposed",
                method="auto",
                actor=None,
                confidence=result.court.confidence,
                model_run_id=run.id,
            )
    jobs.succeed(job, {"model_run_id": str(run.id)})


# --- calibrations -----------------------------------------------------------------------------


def current(db: Session, video_id: uuid.UUID, status: Status = "confirmed") -> Calibration | None:
    return db.scalars(
        select(Calibration).where(Calibration.video_id == video_id, Calibration.status == status)
    ).first()


def save(
    db: Session,
    video: Video,
    points: dict[str, court.Point],
    solution: court.Solution,
    *,
    status: Status,
    method: Method,
    actor: int | None,
    confidence: float | None = None,
    model_run_id: uuid.UUID | None = None,
    copied_from: uuid.UUID | None = None,
) -> Calibration:
    """Write (or replace) the video's calibration of that status."""
    names = list(points)
    rms, worst = solution.rms_m, solution.max_m
    values: dict[str, Any] = {
        "lineage_id": video.lineage_id,
        "hall_name": hall_of(db, video),
        "method": method,
        "landmarks": names,
        "image_points": [[round(points[n][0], 6), round(points[n][1], 6)] for n in names],
        "court_points": [list(court.LANDMARKS[n]) for n in names],
        "image_aspect": aspect_of(video),
        "homography": court.as_lists(solution.image_to_court),
        "camera": solution.camera.as_dict() if solution.camera else None,
        "reproj_error_cm": round(rms * 100, 1) if rms is not None else None,
        "max_error_cm": round(worst * 100, 1) if worst is not None else None,
        "confidence": confidence,
        "model_run_id": model_run_id,
        "copied_from": copied_from,
        "updated_by": actor,
    }
    row = current(db, video.id, status)
    if row is None:
        row = Calibration(
            id=uuid.uuid4(), video_id=video.id, status=status, created_by=actor, **values
        )
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.flush()
    return row


@dataclass(frozen=True)
class Suggestion:
    source: Literal["auto", "team"]
    points: dict[str, court.Point]
    confidence: float | None
    video_id: uuid.UUID
    video_title: str | None
    recorded_on: date | None
    calibration_id: uuid.UUID


def suggestions(db: Session, video: Video) -> list[Suggestion]:
    """What pre-fills the dialog when the video has no calibration yet, best first: the
    court finder's (when confident), then the team's last confirmed calibration (same hall
    first), whose camera likely stood at the same spot."""
    out: list[Suggestion] = []
    auto = current(db, video.id, "proposed")
    if auto is not None and (auto.confidence or 0) >= AUTO_CONFIDENT:
        out.append(
            Suggestion("auto", auto.points(), auto.confidence, video.id, None, None, auto.id)
        )
    hall = hall_of(db, video)
    rows = db.execute(
        select(Calibration, Video.title, Video.recorded_on)
        .join(Video, Video.id == Calibration.video_id)
        .where(
            Calibration.lineage_id == video.lineage_id,
            Calibration.status == "confirmed",
            Calibration.video_id != video.id,
            Video.status != "deleted",
        )
        .order_by(Calibration.updated_at.desc())
        .limit(20)
    ).all()
    if rows:
        same_hall = [r for r in rows if hall is not None and r[0].hall_name == hall]
        row, title, recorded_on = (same_hall or rows)[0]
        out.append(Suggestion("team", row.points(), None, row.video_id, title, recorded_on, row.id))
    return out
