"""The ball's path and the court of a video (PLAN §10.4, §10.6), API side.

    analyse done ──trajectory job──▶ the ball track, smoothed, with contacts and bounces
                                     (gzipped JSON in the bucket, a copy on the NAS)
                                   + the court found in the picture (a proposal)
    trajectory done ──court job──▶ the camera's setups (it moved: a bump, a drift), the
                                     court through the hall's memory (calibrated pictures
                                     of earlier videos) or the court finder (a new hall)
    coach clicks or confirms the corners ──▶ the video's calibration (a homography, the
                                     camera): floor-level evidence maps to the court

A calibration row per video and status: `confirmed` is the one that counts, the coach's
or, until a coach looks at it, the court job's when it was sure (`checked` false);
`proposed` is the court job's (or the trajectory job's finder's) best guess, offered in the
dialog. A camera that moved during the match gives `segments`: a setup per time range, each
with its own landmarks and homography (`segment_at` picks the one for a time); the row's
own fields are those of the longest setup, so a reader that knows nothing of setups gets
the court of most of the match. New videos of a team are pre-filled from that team's last
confirmed calibration (the camera usually stands at the same spot); a coach's calibration
becomes a picture in the hall's memory for the next videos.
"""

from __future__ import annotations

import math
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.orm.attributes import flag_modified

from wieplay.core import court, jobs
from wieplay.core.db import Base
from wieplay.core.models import AnalysisArtifact, Job, ModelRun, TeamLineage, Video, WdGame

KINDS = frozenset({"trajectory", "court"})
CAPS = ("analyse", "nas")
PRIORITY = jobs.FOLLOW_UP_PRIORITY  # quick: before the next analysis
TRACK_FORMAT = "json"
TRACK_SCHEMA = 1
NAS_PATH = re.compile(r"[A-Za-z0-9._/-]+")
# The dialog is pre-filled with a court the finder found at or above this confidence.
AUTO_CONFIDENT = 0.6
# The court job sets the video's court by itself at or above these (the hall's memory is
# surer than the finder in a hall it never saw); below, its best guess pre-fills the dialog
# from GUESS_MIN.
AUTO_SET = {"auto-hall": 0.6, "auto": 0.7}
GUESS_MIN = 0.45
# A court set by the job this sure serves the hall's memory too (a coach's always does).
REFERENCE_MIN = 0.75
MAX_REFERENCES = 16
COURT_FRAMES_FORMAT = "npz"
DEFAULT_ASPECT = 16 / 9

Status = Literal["confirmed", "proposed"]
Method = Literal["manual", "auto", "copied", "auto-hall"]


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
    # Since 0009: the camera's setups (None: one for the whole video, the fields above);
    # whether a person set or checked it (False: the court job set it); where in the video
    # the coach set it up.
    segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    checked: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    at_ms: Mapped[int | None] = mapped_column(Integer)

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
            # The court job finds the court now (since 0009); older workers ignore this.
            "court": False,
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
    db.flush()
    queue_court(db, video, str(job.params["out_dir"]))  # the court, all match long


# --- the court job ------------------------------------------------------------------------------


def active_court_job(db: Session, video_id: uuid.UUID) -> Job | None:
    return db.scalars(
        select(Job).where(
            Job.kind == "court", Job.video_id == video_id, Job.status.in_(jobs.ACTIVE)
        )
    ).first()


def latest_court_frames(db: Session, video_id: uuid.UUID) -> AnalysisArtifact | None:
    """The newest picture of the empty hall of a video (its court job's), or None."""
    return db.scalars(
        select(AnalysisArtifact)
        .where(AnalysisArtifact.video_id == video_id, AnalysisArtifact.kind == "court_frames")
        .order_by(AnalysisArtifact.created_at.desc())
        .limit(1)
    ).first()


def queue_court(db: Session, video: Video, out_dir: str) -> Job | None:
    """Queue the court job of a ready video (an active one is reused): its setups, and its
    court through the hall's memory or the finder."""
    if video.status != "ready" or not any(f.nas_path for f in video.files):
        return None
    found = active_court_job(db, video.id)
    if found is not None:
        return found
    track = latest_track(db, video.id)
    return jobs.enqueue(
        db,
        "court",
        caps=CAPS,
        video_id=video.id,
        priority=PRIORITY,
        max_attempts=2,
        params={
            "video_id": str(video.id),
            "inputs": [f.nas_path for f in video.files if f.nas_path],
            "duration_ms": video.duration_ms,
            "aspect": aspect_of(video),
            "out_dir": out_dir,
            "track": (
                {"nas_path": track.nas_path, "sha256": track.sha256} if track is not None else None
            ),
            "references": references(db, video),
        },
    )


def _main_index(segments: list[dict[str, Any]]) -> int:
    for k, seg in enumerate(segments):
        if seg.get("main"):
            return k
    return max(range(len(segments)), key=lambda k: segments[k]["to_ms"] - segments[k]["from_ms"])


def references(db: Session, video: Video) -> list[dict[str, Any]]:
    """The hall's memory for a video: calibrated pictures of other videos, the same team's
    and hall's first, then everyone's (a team plays away, another team plays in our hall);
    a coach's calibrations before the court job's."""
    hall = hall_of(db, video)
    query = (
        select(Calibration, Video.lineage_id)
        .join(Video, Video.id == Calibration.video_id)
        .join(TeamLineage, TeamLineage.id == Video.lineage_id)
        .where(
            Calibration.status == "confirmed",
            Calibration.video_id != video.id,
            Video.status != "deleted",
            Calibration.checked.is_(True) | (Calibration.confidence >= REFERENCE_MIN),
        )
    )
    # A guest club's pictures stay with its own team, and it sees none of KSCW's (PLAN §4.5).
    own = db.get(TeamLineage, video.lineage_id)
    if own is not None and own.guest_club is not None:
        query = query.where(Video.lineage_id == video.lineage_id)
    else:
        query = query.where(TeamLineage.guest_club.is_(None))
    rows = db.execute(query.order_by(Calibration.updated_at.desc()).limit(200)).all()
    ranked = sorted(
        rows,
        key=lambda r: (
            not (hall is not None and r[0].hall_name == hall),
            r[1] != video.lineage_id,
            not r[0].checked,
        ),
    )
    out: list[dict[str, Any]] = []
    for row, lineage in ranked:
        frames = latest_court_frames(db, row.video_id)
        if frames is None:
            continue
        seg = _reference_setup(row, frames)
        if not seg.get("homography"):
            continue
        try:
            to_image = court.invert(court.from_lists(seg["homography"]))
        except court.CourtError:
            continue
        out.append(
            {
                "calibration_id": str(row.id),
                "video_id": str(row.video_id),
                "checked": row.checked,
                "same_team": lineage == video.lineage_id,
                "same_hall": hall is not None and row.hall_name == hall,
                "frames": {"nas_path": frames.nas_path, "sha256": frames.sha256},
                "court_to_image": court.as_lists(to_image),
            }
        )
        if len(out) >= MAX_REFERENCES:
            break
    return out


def _reference_setup(row: Calibration, frames: AnalysisArtifact) -> dict[str, Any]:
    """The calibration's setup that the empty hall's picture shows (the main setup of the
    court job that took it)."""
    segments = row.segments
    if not segments:
        return {"homography": row.homography}
    window = (frames.stats or {}).get("window")
    if not window:
        return segments[_main_index(segments)]
    lo, hi = int(window[0]), int(window[1])

    def overlap(seg: dict[str, Any]) -> int:
        return min(hi, int(seg["to_ms"])) - max(lo, int(seg["from_ms"]))

    return max((seg for seg in segments if seg.get("homography")), key=overlap)


class SetupIn(BaseModel):
    from_ms: int = Field(ge=0)
    to_ms: int = Field(ge=0)
    start: Literal["start", "bump", "drift", "moved"] = "start"  # moved: to another view
    shift_px: float = Field(default=0.0, ge=0, le=10_000)
    samples: int = Field(default=0, ge=0)
    view: int = Field(default=0, ge=0, le=15)  # the camera carried elsewhere: another view
    # 3x3: this setup's picture → its view's main setup's picture (normalised)
    to_main: list[list[float]] | None = None

    @field_validator("to_main")
    @classmethod
    def _square(cls, value: list[list[float]] | None) -> list[list[float]] | None:
        if value is not None and (len(value) != 3 or any(len(row) != 3 for row in value)):
            raise ValueError("a 3x3 matrix")
        return value


class SetupCourtIn(BaseModel):
    points: dict[str, tuple[float, float]] | None = Field(
        default=None, max_length=len(court.LANDMARKS)
    )

    @field_validator("points")
    @classmethod
    def _known(
        cls, value: dict[str, tuple[float, float]] | None
    ) -> dict[str, tuple[float, float]] | None:
        if value is None:
            return value
        if set(value) - set(court.LANDMARKS):
            raise ValueError("unknown landmark")
        if any(not (-1 <= u <= 2 and -1 <= v <= 2) for u, v in value.values()):
            raise ValueError("landmark far outside the frame")
        return value


class CourtFoundIn(BaseModel):
    method: Literal["auto-hall", "auto"]
    confidence: float = Field(ge=0, le=1)
    reference: dict[str, Any] | None = None
    setups: list[SetupCourtIn] = Field(min_length=1, max_length=64)
    stats: dict[str, Any] = Field(default_factory=dict)


class FramesIn(BaseModel):
    nas_path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    schema_version: int = Field(ge=1, le=1000)
    stats: dict[str, Any] = Field(default_factory=dict)


class CourtResult(BaseModel):
    model: ModelIn
    frames: FramesIn
    setups: list[SetupIn] = Field(min_length=1, max_length=64)
    main: int = Field(ge=0)
    court: CourtFoundIn | None = None
    candidates: dict[str, Any] = Field(default_factory=dict)
    timing: dict[str, Any] = Field(default_factory=dict)


def complete_court(db: Session, video: Video | None, job: Job, payload: dict[str, Any]) -> None:
    """Store a court job's result: its run, the empty hall's picture (the hall's memory),
    the court it found as the video's proposal and, when it is sure and no person set the
    court, as the video's calibration (to be checked). A coach's calibration stays; it only
    gains the setups when the camera moved. Raises ValueError on a bad result."""
    if video is None:
        raise ValueError("job has no video")
    result = CourtResult.model_validate(payload)
    if not _inside(result.frames.nas_path, str(job.params["out_dir"])):
        raise ValueError("frames path outside the job's analysis folder")
    if result.main >= len(result.setups):
        raise ValueError("the main setup is not one of the setups")
    if result.court is not None and len(result.court.setups) != len(result.setups):
        raise ValueError("a court per setup")
    if video.status != "ready":
        jobs.succeed(job, {"skipped": f"video is {video.status}"})
        return
    setups = [s.model_dump() for s in result.setups]
    main = result.main
    active = current(db, video.id)
    found = _found_segments(video, result) if result.court is not None else None
    vs_coach = _vs_coach(active, found) if active is not None and active.checked else None
    run = ModelRun(
        id=uuid.uuid4(),
        kind="court",
        job_id=job.id,
        video_id=video.id,
        model_name=result.model.name,
        model_version=result.model.version,
        params=result.model.params,
        metrics={
            "setups": [{k: v for k, v in s.items() if k != "to_main"} for s in setups],
            "main": main,
            "court": (
                {
                    "method": result.court.method,
                    "confidence": result.court.confidence,
                    "reference": result.court.reference,
                    **result.court.stats,
                }
                if result.court is not None
                else None
            ),
            "candidates": result.candidates,
            "vs_coach": vs_coach,
        },
        timing=result.timing,
    )
    db.add(run)
    db.flush()
    db.add(
        AnalysisArtifact(
            video_id=video.id,
            model_run_id=run.id,
            kind="court_frames",
            nas_path=result.frames.nas_path,
            format=COURT_FRAMES_FORMAT,
            schema_version=result.frames.schema_version,
            sha256=result.frames.sha256,
            bytes=result.frames.bytes,
            stats={
                **result.frames.stats,
                "window": [setups[main]["from_ms"], setups[main]["to_ms"]],
                "main": main,
                "setups": [
                    {k: s[k] for k in ("from_ms", "to_ms", "start", "shift_px", "view", "to_main")}
                    for s in setups
                ],
            },
        )
    )
    if found is not None and result.court is not None:
        method = result.court.method
        confidence = result.court.confidence
        save_segments(
            db,
            video,
            found,
            status="proposed",
            method=method,
            confidence=confidence,
            model_run_id=run.id,
        )
        if (active is None or not active.checked) and confidence >= AUTO_SET[method]:
            save_segments(
                db,
                video,
                found,
                status="confirmed",
                method=method,
                confidence=confidence,
                model_run_id=run.id,
            )
    if active is not None and active.checked and len(setups) > 1 and not active.segments:
        _extend(active, setups, main)
    jobs.succeed(job, {"model_run_id": str(run.id), "setups": len(setups)})


# --- calibrations -----------------------------------------------------------------------------


def current(db: Session, video_id: uuid.UUID, status: Status = "confirmed") -> Calibration | None:
    return db.scalars(
        select(Calibration).where(Calibration.video_id == video_id, Calibration.status == status)
    ).first()


def _fields(
    points: dict[str, court.Point], solution: court.Solution, aspect: float
) -> dict[str, Any]:
    names = list(points)
    rms, worst = solution.rms_m, solution.max_m
    return {
        "landmarks": names,
        "image_points": [[round(points[n][0], 6), round(points[n][1], 6)] for n in names],
        "court_points": [list(court.LANDMARKS[n]) for n in names],
        "image_aspect": aspect,
        "homography": court.as_lists(solution.image_to_court),
        "camera": solution.camera.as_dict() if solution.camera else None,
        "reproj_error_cm": round(rms * 100, 1) if rms is not None else None,
        "max_error_cm": round(worst * 100, 1) if worst is not None else None,
    }


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
    segments: list[dict[str, Any]] | None = None,
    checked: bool = True,
    at_ms: int | None = None,
) -> Calibration:
    """Write (or replace) the video's calibration of that status. With `segments` (the
    camera's setups), `points` and `solution` are the main setup's."""
    values: dict[str, Any] = {
        "lineage_id": video.lineage_id,
        "hall_name": hall_of(db, video),
        "method": method,
        **_fields(points, solution, aspect_of(video)),
        "confidence": confidence,
        "model_run_id": model_run_id,
        "copied_from": copied_from,
        "updated_by": actor,
        "segments": segments,
        "checked": checked,
        "at_ms": at_ms,
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
        flag_modified(row, "segments")
    db.flush()
    return row


# --- the camera's setups --------------------------------------------------------------------------


def _segment(
    points: dict[str, court.Point],
    solution: court.Solution,
    aspect: float,
    *,
    from_ms: int,
    to_ms: int,
    start: str,
    shift_px: float,
    method: str,
    checked: bool,
    to_main: list[list[float]] | None,
    main: bool,
    view: int = 0,
) -> dict[str, Any]:
    fields = _fields(points, solution, aspect)
    fields.pop("court_points")
    fields.pop("image_aspect")
    return {
        "from_ms": from_ms,
        "to_ms": to_ms,
        "start": start,
        "shift_px": shift_px,
        **fields,
        "method": method,
        "checked": checked,
        "to_main": to_main,
        "main": main,
        "view": view,
    }


def _no_court(
    *,
    from_ms: int,
    to_ms: int,
    start: str,
    shift_px: float,
    view: int,
    to_main: list[list[float]] | None,
) -> dict[str, Any]:
    """A setup whose view of the hall showed no court (the camera elsewhere): nothing maps."""
    return {
        "from_ms": from_ms,
        "to_ms": to_ms,
        "start": start,
        "shift_px": shift_px,
        "landmarks": [],
        "image_points": [],
        "homography": None,
        "camera": None,
        "reproj_error_cm": None,
        "max_error_cm": None,
        "method": "none",
        "checked": False,
        "to_main": to_main,
        "main": False,
        "view": view,
    }


@dataclass(frozen=True)
class Found:
    """The court job's court in every setup (each a segment, solved), and the main one's."""

    segments: list[dict[str, Any]]
    main: int
    points: dict[str, court.Point]
    solution: court.Solution


def _found_segments(video: Video, result: CourtResult) -> Found | None:
    assert result.court is not None
    aspect = aspect_of(video)
    main = result.main
    main_points = result.court.setups[main].points
    if not main_points:
        return None
    try:
        main_solution = court.solve(dict(main_points), aspect)
    except court.CourtError:
        return None
    segments: list[dict[str, Any]] = []
    for k, (setup, found) in enumerate(zip(result.setups, result.court.setups, strict=True)):
        points, solution = dict(main_points), main_solution
        if k != main:
            try:
                if not found.points:
                    raise court.CourtError("no court in this view")
                points, solution = dict(found.points), court.solve(dict(found.points), aspect)
            except court.CourtError:
                segments.append(
                    _no_court(
                        from_ms=setup.from_ms,
                        to_ms=setup.to_ms,
                        start=setup.start,
                        shift_px=setup.shift_px,
                        view=setup.view,
                        to_main=setup.to_main,
                    )
                )
                continue
        segments.append(
            _segment(
                points,
                solution,
                aspect,
                from_ms=setup.from_ms,
                to_ms=setup.to_ms,
                start=setup.start,
                shift_px=setup.shift_px,
                method=result.court.method,
                checked=False,
                to_main=setup.to_main,
                main=k == main,
                view=setup.view,
            )
        )
    return Found(segments, main, dict(main_points), main_solution)


def save_segments(
    db: Session,
    video: Video,
    found: Found,
    *,
    status: Status,
    method: Method,
    confidence: float,
    model_run_id: uuid.UUID,
) -> Calibration:
    return save(
        db,
        video,
        found.points,
        found.solution,
        status=status,
        method=method,
        actor=None,
        confidence=confidence,
        model_run_id=model_run_id,
        segments=found.segments if len(found.segments) > 1 else None,
        checked=False,
    )


def segments_of(row: Calibration) -> list[dict[str, Any]]:
    """The calibration's setups; one for the whole video when the camera never moved."""
    if row.segments:
        return row.segments
    return [
        {
            "from_ms": 0,
            "to_ms": None,
            "start": "start",
            "shift_px": 0.0,
            "landmarks": row.landmarks,
            "image_points": row.image_points,
            "homography": row.homography,
            "camera": row.camera,
            "reproj_error_cm": row.reproj_error_cm,
            "max_error_cm": row.max_error_cm,
            "method": row.method,
            "checked": row.checked,
            "to_main": None,
            "main": True,
        }
    ]


def segment_at(row: Calibration, t_ms: int) -> dict[str, Any]:
    """The setup that holds a time of the video (ms): its `homography` (image → court) and
    `camera` map what was seen then; a `homography` of None: the camera showed no court
    then (carried elsewhere). Times before the first or after the last setup take the
    nearest."""
    segments = segments_of(row)
    for seg in segments:
        if seg["from_ms"] <= t_ms and (seg["to_ms"] is None or t_ms < seg["to_ms"]):
            return seg
    return segments[0] if t_ms < segments[0]["from_ms"] else segments[-1]


def _derive(
    to_image: court.Mat3, to_main_from: list[list[float]], to_main_to: list[list[float]]
) -> court.Mat3:
    """A court seen in one setup's picture, carried to another's: court → picture k →
    the main picture → picture j."""
    return court.matmul(
        court.invert(court.from_lists(to_main_to)),
        court.matmul(court.from_lists(to_main_from), to_image),
    )


def _points_in(to_image: court.Mat3) -> dict[str, court.Point]:
    out: dict[str, court.Point] = {}
    for name, xy in court.LANDMARKS.items():
        try:
            u, v = court.apply(to_image, xy)
        except court.CourtError:
            continue
        if -0.02 <= u <= 1.02 and -0.02 <= v <= 1.02:
            out[name] = (round(u, 6), round(v, 6))
    return out


def _carried(
    seg: dict[str, Any], to_image: court.Mat3, source: dict[str, Any], aspect: float
) -> dict[str, Any] | None:
    """Setup `seg` with the court of setup `source` (to_image) carried over, or None (no
    picture to carry it through: another view of the hall)."""
    if (
        seg.get("to_main") is None
        or source.get("to_main") is None
        or seg.get("view", 0) != source.get("view", 0)
    ):
        return None
    try:
        points = _points_in(_derive(to_image, source["to_main"], seg["to_main"]))
        solution = court.solve(points, aspect)
    except court.CourtError:
        return None
    return _segment(
        points,
        solution,
        aspect,
        from_ms=seg["from_ms"],
        to_ms=seg["to_ms"],
        start=seg["start"],
        shift_px=seg["shift_px"],
        method="derived",
        checked=False,
        to_main=seg["to_main"],
        main=bool(seg.get("main")),
        view=int(seg.get("view", 0)),
    )


def _apply_main(row: Calibration, segments: list[dict[str, Any]]) -> None:
    main = segments[_main_index(segments)]
    for key in (
        "landmarks",
        "image_points",
        "homography",
        "camera",
        "reproj_error_cm",
        "max_error_cm",
    ):
        setattr(row, key, main[key])
    row.court_points = [list(court.LANDMARKS[n]) for n in main["landmarks"]]
    row.segments = segments
    flag_modified(row, "segments")


def save_segment(
    db: Session,
    video: Video,
    row: Calibration,
    index: int,
    points: dict[str, court.Point],
    solution: court.Solution,
    *,
    method: Method,
    actor: int | None,
    propagate: bool = True,
) -> Calibration:
    """A coach sets one setup's court; the setups nobody set follow it through the pictures'
    homographies (`propagate`). The calibration counts as checked from then on."""
    aspect = aspect_of(video)
    segments = [dict(s) for s in segments_of(row)]
    if not 0 <= index < len(segments):
        raise court.CourtError("no such setup")
    seg = segments[index]
    segments[index] = _segment(
        points,
        solution,
        aspect,
        from_ms=seg["from_ms"],
        to_ms=seg["to_ms"],
        start=seg["start"],
        shift_px=seg["shift_px"],
        method=method,
        checked=True,
        to_main=seg.get("to_main"),
        main=bool(seg.get("main")),
        view=int(seg.get("view", 0)),
    )
    if propagate:
        for j, other in enumerate(segments):
            if j == index or other.get("checked"):
                continue
            carried = _carried(other, solution.court_to_image, segments[index], aspect)
            if carried is not None:
                segments[j] = carried
    if row.segments:
        _apply_main(row, segments)
    else:
        for key, value in _fields(points, solution, aspect).items():
            setattr(row, key, value)
    row.method = method
    row.checked = True
    row.updated_by = actor
    row.confidence = None
    db.flush()
    return row


def _extend(row: Calibration, setups: list[dict[str, Any]], main: int) -> None:
    """A coach's calibration of a video whose camera moved: it holds for the setup it was
    made in (where the coach paused, else the main one); the others follow it. Not when
    the camera was carried to another view (nothing to carry the court through)."""
    k = main
    if row.at_ms is not None:
        k = next((i for i, s in enumerate(setups) if s["from_ms"] <= row.at_ms < s["to_ms"]), main)
    aspect = row.image_aspect
    try:
        solution = court.solve(row.points(), aspect)
    except court.CourtError:
        return
    own = _segment(
        row.points(),
        solution,
        aspect,
        from_ms=setups[k]["from_ms"],
        to_ms=setups[k]["to_ms"],
        start=setups[k]["start"],
        shift_px=setups[k]["shift_px"],
        method=row.method,
        checked=True,
        to_main=setups[k]["to_main"],
        main=k == main,
        view=int(setups[k].get("view", 0)),
    )
    segments: list[dict[str, Any]] = []
    for j, setup in enumerate(setups):
        if j == k:
            segments.append(own)
            continue
        base = {**setup, "main": j == main}
        carried = _carried(base, solution.court_to_image, own, aspect)
        if carried is None:
            return
        segments.append(carried)
    _apply_main(row, segments)


def _vs_coach(row: Calibration, found: Found | None) -> dict[str, Any] | None:
    """How far the court job's court is from the coach's (held out: the job never sees the
    video's own calibration): the coach's landmarks through the job's homography, in cm on
    the court, in the setup the coach set (or the main one)."""
    if found is None:
        return None
    seg = found.segments[found.main]
    if row.at_ms is not None:
        seg = next((s for s in found.segments if s["from_ms"] <= row.at_ms < s["to_ms"]), seg)
    to_court = court.from_lists(seg["homography"])
    errors: list[float] = []
    for name, (u, v) in row.points().items():
        try:
            x, y = court.apply(to_court, (u, v))
        except court.CourtError:
            continue
        cx, cy = court.LANDMARKS[name]
        errors.append(math.hypot(x - cx, y - cy))
    if not errors:
        return None
    return {
        "rms_cm": round(100 * math.sqrt(sum(e * e for e in errors) / len(errors)), 1),
        "max_cm": round(100 * max(errors), 1),
        "points": len(errors),
    }


def after_save(db: Session, video: Video, row: Calibration, out_dir: str) -> None:
    """A coach saved the court of the whole video: when the court job saw the camera move,
    the calibration gains its setups now; without a picture of the empty hall yet, the
    court job makes one (the hall's memory)."""
    frames = latest_court_frames(db, video.id)
    if frames is None:
        if calibration_jobs_ok(video):
            queue_court(db, video, out_dir)
        return
    setups = (frames.stats or {}).get("setups") or []
    if len(setups) > 1 and all(s.get("to_main") for s in setups):
        main = int((frames.stats or {}).get("main", 0))
        _extend(row, setups, min(main, len(setups) - 1))
        db.flush()


def calibration_jobs_ok(video: Video) -> bool:
    return video.status == "ready" and any(f.nas_path for f in video.files)


@dataclass(frozen=True)
class Suggestion:
    source: Literal["auto", "auto-hall", "team"]
    points: dict[str, court.Point]
    confidence: float | None
    video_id: uuid.UUID
    video_title: str | None
    recorded_on: date | None
    calibration_id: uuid.UUID


def suggestions(db: Session, video: Video) -> list[Suggestion]:
    """What pre-fills the dialog when the video has no calibration yet, best first: the
    court job's (or the finder's) when confident, the team's last confirmed calibration
    (same hall first), whose camera likely stood at the same spot, then the court job's
    best guess when it was less sure."""
    out: list[Suggestion] = []
    auto = current(db, video.id, "proposed")
    source: Literal["auto", "auto-hall"] = (
        "auto-hall" if auto is not None and auto.method == "auto-hall" else "auto"
    )
    guess = (
        Suggestion(source, auto.points(), auto.confidence, video.id, None, None, auto.id)
        if auto is not None
        else None
    )
    if guess is not None and (guess.confidence or 0) >= AUTO_CONFIDENT:
        out.append(guess)
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
        .order_by(Calibration.checked.desc(), Calibration.updated_at.desc())
        .limit(20)
    ).all()
    if rows:
        same_hall = [r for r in rows if hall is not None and r[0].hall_name == hall]
        row, title, recorded_on = (same_hall or rows)[0]
        out.append(Suggestion("team", row.points(), None, row.video_id, title, recorded_on, row.id))
    if guess is not None and GUESS_MIN <= (guess.confidence or 0) < AUTO_CONFIDENT:
        out.append(guess)
    return out
