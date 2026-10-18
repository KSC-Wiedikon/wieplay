"""Players in a video and who made each action (PLAN §10.5, §11.5), API side.

    trajectory done ──players job──▶ tracks and clusters ("a person in this video"), back
                                     crops (contact sheets) and a box index in the bucket
    coach, once per video ──gallery──▶ each cluster gets a number from the video's roster
                                     snapshot (or "not a player"); merge, split, swap teams
    every action (the model's suggestions, the coach's tags without a number) ──▶ the
        player nearest the ball at the contact, on the action's side ──▶ cluster ──▶ number

Policy (PLAN §5.7): no faces and nothing across videos. A cluster is anonymous until a coach
gives it a number; the number reader only suggests one. Identity is the number in the
video's roster snapshot (`video_roster`), as for hand-made tags.

Attribution rules (like every model output, PLAN §11):
- a model's suggested action (source "model", unreviewed) gets the player written into it
  (`number`, `member_id`, `track_ref`), as a suggestion the coach reviews with the action;
  a number a person set is never overwritten, and a number WiePlay wrote is taken back when
  the gallery no longer supports it;
- a coach's tag without a number is never changed: its suggestion stays beside it
  (`event_player`) until the coach takes it ("apply");
- everything is recomputed when the gallery changes, and for new or changed tags on the
  next read.
"""

from __future__ import annotations

import base64
import bisect
import gzip
import json
import math
import os
import re
import sys
import threading
import uuid
import zlib
from array import array
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from wieplay.core import analysis, calibration, court, jobs, tagging
from wieplay.core.db import Base
from wieplay.core.models import (
    AnalysisArtifact,
    Event,
    Job,
    ModelRun,
    Period,
    Segment,
    Video,
    VideoRoster,
    WdMember,
    WdTeamMember,
)

KINDS = frozenset({"players"})
# The Lenovo's background lane; a GPU worker (PLAN §8.3, §8.6) takes the job once the API
# asks for its caps instead, e.g. WIEPLAY_PLAYERS_CAPS=gpu,nas.
CAPS = tuple(
    c.strip() for c in os.environ.get("WIEPLAY_PLAYERS_CAPS", "analyse,nas").split(",") if c.strip()
)
# After the analyses and trajectories of a backlog: the heaviest of them.
PRIORITY = analysis.ANALYSE_PRIORITY - 2
SPORTS = frozenset({"volleyball"})
INDEX_SCHEMA = 1
MAX_INDEX_BYTES = 64 * 1024 * 1024
MAX_SHEET_BYTES = 8 * 1024 * 1024
MAX_SHEETS = 400
MAX_CLUSTERS = 1000  # v1 made ~450 on a 2 h match; v2 150 to 300 with the brief ones
MAX_TRACKS = 20_000
NAS_PATH = re.compile(r"[A-Za-z0-9._/-]+")
SHEET_NAME = re.compile(r"sheet-(\d{1,3})\.jpg")
RENDITION_FILE = re.compile(r"init\.mp4|seg_\d{5}\.m4s")
# A GPU worker without the NAS share reads the video from this rendition (PLAN §8.2).
RENDITION = "1080p"
# Cards below this confidence ask the coach to check them (wieplay/ml/identities.py).
UNCERTAIN = 0.5

# Attribution: the contact is looked for this close to the tag (a live tag lags the play).
CONTACT_WINDOW_MS = {"frame": 250, "adjusted": 400, "live": 700}
FRAME_WINDOW_MS = 160  # an analysed frame (10 fps) this close to the contact
# Ball height at the contact for the court distance: digs and receptions low, the rest high.
LOW_CONTACT_M = 0.8
HIGH_CONTACT_M = 2.3
LOW_SKILLS = frozenset({"R", "D", "F"})
# A suggestion is written into a model's action from this confidence on.
APPLY_CONFIDENCE = 0.35
# The nearest player in the image (distance to the box in box heights), not on the floor: on
# the Legends game (17.10.2026, 919 contacts) the floor distance through the recovered camera
# was 4.7 m at the median (the ball's height is a guess, a wide lens bends the picture), the
# image distance 0.25 box heights. The floor distance stays for a better camera model.
COURT_DISTANCE = False

Team = Literal["us", "them"]


class PlayerCluster(Base):
    """A person seen in a video (a cluster of tracks), and what the coach says it is."""

    __tablename__ = "player_cluster"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video.id", ondelete="CASCADE"), index=True
    )
    model_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_run.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[int] = mapped_column(Integer)  # "Player 7" until it has a number
    team: Mapped[str] = mapped_column(String(8))  # us | them (after any swap)
    # [{id, t0, t1, n, side, number, number_conf, crop: [page, cell], h, foot}]
    tracks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    crops: Mapped[list[list[int]]] = mapped_column(JSONB)  # [[page, cell], ...]
    detections: Mapped[int] = mapped_column(Integer)
    # The number reader's suggestion (never an identity on its own).
    ocr_number: Mapped[int | None] = mapped_column(Integer)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    # The coach's answer: a number of the roster snapshot, or not a player (referee, bench).
    number: Mapped[int | None] = mapped_column(Integer)
    member_id: Mapped[int | None] = mapped_column(Integer)
    not_player: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    referee_guess: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Players v2 (0020): how sure the job is that the card is one whole person (0..1); the
    # gallery asks the coach to check cards below UNCERTAIN. NULL: an older run.
    confidence: Mapped[float | None] = mapped_column(Float)
    merged_into: Mapped[uuid.UUID | None] = mapped_column(UUID)
    source: Mapped[str] = mapped_column(String(16), default="model")  # model | split
    assigned_by: Mapped[int | None] = mapped_column(Integer)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EventPlayer(Base):
    """Who made an action, as WiePlay sees it: the track nearest the ball at the contact and
    the number its cluster has. Beside the event, so a coach's tag is never changed."""

    __tablename__ = "event_player"

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("event.id", ondelete="CASCADE"), primary_key=True
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video.id", ondelete="CASCADE"), index=True
    )
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_run.id", ondelete="SET NULL")
    )
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    track_id: Mapped[int | None] = mapped_column(BigInteger)
    number: Mapped[int | None] = mapped_column(Integer)
    member_id: Mapped[int | None] = mapped_column(Integer)
    method: Mapped[str | None] = mapped_column(String(8))  # court | image
    distance: Mapped[float | None] = mapped_column(Float)  # metres (court), box heights (image)
    confidence: Mapped[float | None] = mapped_column(Float)
    contact_ms: Mapped[int | None] = mapped_column(BigInteger)
    # The number WiePlay wrote into a model's action (to take back only its own).
    applied_number: Mapped[int | None] = mapped_column(Integer)
    event_version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- queueing --------------------------------------------------------------------------------


def can_run(video: Video) -> str | None:
    if video.sport not in SPORTS:
        return f"No player detection for {video.sport} yet"
    if video.status != "ready":
        return "Find the players once the video is ready"
    if not video.files or any(not f.nas_path for f in video.files):
        return "The video's originals are not on the NAS"
    return None


def active_job(db: Session, video_id: uuid.UUID) -> Job | None:
    return db.scalars(
        select(Job)
        .where(Job.kind == "players", Job.video_id == video_id, Job.status.in_(jobs.ACTIVE))
        .order_by(Job.created_at)
    ).first()


def latest_job(db: Session, video_id: uuid.UUID) -> Job | None:
    return db.scalars(
        select(Job)
        .where(Job.kind == "players", Job.video_id == video_id)
        .order_by(Job.created_at.desc())
        .limit(1)
    ).first()


def queue_players(db: Session, video: Video, caps: Sequence[str] = CAPS) -> Job:
    """Queue the players job of a ready volleyball video; an active one is reused."""
    reason = can_run(video)
    if reason:
        raise ValueError(reason)
    found = active_job(db, video.id)
    if found is not None:
        return found
    token = uuid.uuid4().hex[:12]
    return jobs.enqueue(
        db,
        "players",
        caps=tuple(caps),
        video_id=video.id,
        priority=PRIORITY,
        max_attempts=2,
        params={
            "video_id": str(video.id),
            "inputs": [f.nas_path for f in video.files],
            "parts": [
                {"offset_ms": f.offset_ms or 0, "duration_ms": f.duration_ms or 0}
                for f in video.files
            ],
            "duration_ms": video.duration_ms,
            "width": video.width or 1920,
            "height": video.height or 1080,
            "out_dir": analysis.analysis_subdir(db, video),
            "prefix": f"media/{video.id}/analysis/players-{token}/",
            # A worker without the NAS reads this rendition through signed URLs.
            "rendition": _rendition(video).name if _rendition(video) is not None else None,
        },
    )


def _rendition(video: Video) -> Any:
    """The rendition a GPU worker reads: 1080p, else the largest there is."""
    found = [r for r in video.renditions if r.segment_count and r.playlist]
    by_name = {r.name: r for r in found}
    if RENDITION in by_name:
        return by_name[RENDITION]
    return max(found, key=lambda r: r.height, default=None)


def after_court(db: Session, video: Video | None, job: Job, auto: bool = False) -> None:
    """The ball's path and the court (its setups) are there: the players follow, when they
    are switched on (`players_auto`, off by default: a beta at ~2 h of CPU a match); once per
    video unless asked again (a rerun replaces the clusters)."""
    if not auto or video is None or job.kind != "court" or job.status != "succeeded":
        return
    if can_run(video) is not None or video.meets_standard is False:
        return
    if latest_run(db, video.id) is not None or active_job(db, video.id) is not None:
        return
    queue_players(db, video)


def may_sign(job: Job, op: str, key: str) -> bool:
    """The job writes its index, sheets and (without the NAS) its boxes under its prefix;
    it reads the video's rendition (a GPU worker without the NAS, PLAN §8.2)."""
    if job.kind != "players":
        return False
    if op == "get":
        name = job.params.get("rendition") or RENDITION
        folder = f"media/{job.params.get('video_id')}/{name}/"
        return key.startswith(folder) and bool(RENDITION_FILE.fullmatch(key[len(folder) :]))
    prefix = str(job.params.get("prefix", ""))
    if op != "put" or not prefix or not key.startswith(prefix):
        return False
    name = key[len(prefix) :]
    return name in ("index.json.gz", "tracks.npz") or bool(SHEET_NAME.fullmatch(name))


# --- the sides of the rallies (inputs) -------------------------------------------------------


def near_team(seg: Segment, period: Period | None) -> str | None:
    """Which team played at the camera's end in a rally: from where the server stood and
    who served (the coach's, else the model's), else from the set's side."""
    serving = seg.serving_manual or seg.serving or seg.model_serving
    if serving in ("us", "them") and seg.serving_court in ("near", "far"):
        return "us" if (serving == "us") == (seg.serving_court == "near") else "them"
    if period is not None:
        side = period.our_court_side_at_start or period.model_court_side
        if side in ("near", "far"):
            return "us" if side == "near" else "them"
    return None


def rallies_with_sides(db: Session, video: Video) -> list[dict[str, Any]]:
    periods = list(
        db.scalars(
            select(Period)
            .where(Period.video_id == video.id, Period.deleted_at.is_(None))
            .order_by(Period.start_ms)
        )
    )
    starts = [p.start_ms for p in periods]
    rows = db.scalars(
        select(Segment)
        .where(
            Segment.video_id == video.id,
            Segment.kind == "rally",
            Segment.deleted_at.is_(None),
            Segment.review.not_in(("rejected", "merged")),
        )
        .order_by(Segment.start_ms)
    )
    out = []
    for seg in rows:
        k = bisect.bisect_right(starts, seg.start_ms) - 1
        period = periods[k] if k >= 0 else None
        if period is not None and period.end_ms is not None and seg.start_ms > period.end_ms:
            period = None
        out.append(
            {"start_ms": seg.start_ms, "end_ms": seg.end_ms, "near_team": near_team(seg, period)}
        )
    return out


def calibration_row(db: Session, video: Video) -> calibration.Calibration | None:
    """The video's calibration (the coach's, or the court job's when it was sure), else the
    court finder's proposal when it is confident."""
    row = calibration.current(db, video.id)
    if row is None:
        row = calibration.current(db, video.id, "proposed")
        if row is None or (row.confidence or 0) < calibration.AUTO_CONFIDENT:
            return None
    return row


def setups_for(db: Session, video: Video) -> list[dict[str, Any]]:
    """The camera's setups (one when it never moved): [{from_ms, to_ms, homography, camera}];
    a homography of None: no court in the picture then."""
    row = calibration_row(db, video)
    if row is None:
        return []
    return [
        {
            "from_ms": int(seg["from_ms"]),
            "to_ms": None if seg.get("to_ms") is None else int(seg["to_ms"]),
            "homography": seg.get("homography"),
            "camera": seg.get("camera"),
        }
        for seg in calibration.segments_of(row)
    ]


def calibration_for(db: Session, video: Video) -> dict[str, Any] | None:
    """The calibration of most of the match (the longest setup)."""
    row = calibration_row(db, video)
    if row is None:
        return None
    return {"homography": row.homography, "camera": row.camera, "status": row.status}


def rendition_segments(video: Video) -> dict[str, Any] | None:
    """The rendition a worker without the NAS reads: its init segment and every media
    segment with its place in the video (from the media playlist the API keeps)."""
    r = _rendition(video)
    if r is None:
        return None
    folder = f"media/{video.id}/{r.name}/"
    segments: list[list[Any]] = []
    t = 0.0
    duration: float | None = None
    for raw in r.playlist.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            duration = float(line[len("#EXTINF:") :].split(",", 1)[0])
        elif line and not line.startswith("#") and duration is not None:
            if RENDITION_FILE.fullmatch(line):
                segments.append([folder + line, round(t * 1000), round(duration * 1000)])
            t += duration
            duration = None
    return {
        "name": r.name,
        "init": folder + "init.mp4",
        "segments": segments,
        "width": r.width,
        "height": r.height,
        "fps": r.fps,
    }


def roster_for_job(db: Session, video: Video) -> dict[str, list[int]]:
    """The shirt numbers each side wears (the video's roster snapshot, else the team's
    roster now, read without taking the snapshot): readings of other numbers are misreads.
    Numbers only: no names, no members."""
    rows = list(db.scalars(select(VideoRoster).where(VideoRoster.video_id == video.id)))
    if rows:
        out: dict[str, set[int]] = {"us": set(), "them": set()}
        for r in rows:
            if r.side in out and r.number is not None:
                out[r.side].add(int(r.number))
        return {k: sorted(v) for k, v in out.items() if v}
    numbers = db.scalars(
        select(WdMember.number)
        .join(WdTeamMember, WdTeamMember.member_id == WdMember.id)
        .where(
            WdTeamMember.team_id == video.team_id,
            WdTeamMember.rel == "player",
            WdMember.number.is_not(None),
        )
    )
    us = sorted({int(n) for n in numbers if n is not None})
    return {"us": us} if us else {}


def job_inputs(db: Session, job: Job) -> dict[str, Any]:
    video = db.get(Video, job.video_id) if job.video_id else None
    if video is None:
        raise ValueError("job has no video")
    return {
        "rallies": rallies_with_sides(db, video),
        # Older workers read `calibration` only; newer ones pick the setup of each window.
        "calibration": calibration_for(db, video),
        "setups": setups_for(db, video),
        "rendition": rendition_segments(video),
        "roster": roster_for_job(db, video),
    }


# --- results from the worker ----------------------------------------------------------------


class TrackIn(BaseModel):
    id: int = Field(ge=0)
    t0: int = Field(ge=0, le=tagging.MAX_MEDIA_MS)
    t1: int = Field(ge=0, le=tagging.MAX_MEDIA_MS)
    n: int = Field(ge=1)
    side: Literal["near", "far"]
    number: int | None = Field(default=None, ge=0, le=99)
    number_conf: float = Field(default=0.0, ge=0, le=1)
    crop: list[int] | None = Field(default=None, min_length=2, max_length=2)
    h: float = Field(default=0.0, ge=0)
    foot: list[float] | None = Field(default=None, min_length=2, max_length=2)


class ClusterIn(BaseModel):
    key: int = Field(ge=1)
    team: Team
    referee: bool = False
    number: int | None = Field(default=None, ge=0, le=99)
    number_conf: float = Field(default=0.0, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)  # players v2
    crops: list[list[int]] = Field(default_factory=list, max_length=16)
    tracks: list[TrackIn] = Field(min_length=1, max_length=MAX_TRACKS)


class FileIn(BaseModel):
    """On the NAS (`nas_path`), or in the bucket (`key`, a GPU worker without the NAS)."""

    nas_path: str | None = Field(default=None, min_length=1, max_length=1024)
    key: str | None = Field(default=None, min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def _somewhere(self) -> FileIn:
        if (self.nas_path is None) == (self.key is None):
            raise ValueError("a file is on the NAS or in the bucket")
        return self


class IndexIn(BaseModel):
    key: str = Field(min_length=1, max_length=512)
    nas_path: str | None = Field(default=None, min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0, le=MAX_INDEX_BYTES)


class SheetsIn(BaseModel):
    keys: list[str] = Field(max_length=MAX_SHEETS)
    nas_dir: str | None = Field(default=None, min_length=1, max_length=1024)
    cell: list[int] = Field(min_length=2, max_length=2)
    cols: int = Field(ge=1, le=64)
    rows: int = Field(ge=1, le=64)


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    weights_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    params: dict[str, Any] = Field(default_factory=dict)


class PlayersResult(BaseModel):
    model: ModelIn
    tracks_artifact: FileIn
    index: IndexIn
    sheets: SheetsIn
    clusters: list[ClusterIn] = Field(max_length=MAX_CLUSTERS)
    stats: dict[str, Any] = Field(default_factory=dict)
    timing: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique(self) -> PlayersResult:
        keys = [c.key for c in self.clusters]
        tracks = [t.id for c in self.clusters for t in c.tracks]
        if len(set(keys)) != len(keys) or len(set(tracks)) != len(tracks):
            raise ValueError("cluster keys and track ids are unique")
        pages = len(self.sheets.keys)
        for c in self.clusters:
            for ref in [*c.crops, *(t.crop for t in c.tracks if t.crop)]:
                if len(ref) != 2 or not 0 <= ref[0] < pages or not 0 <= ref[1] < 4096:
                    raise ValueError("crop outside the contact sheets")
        return self


def _inside(path: str, out_dir: str) -> bool:
    return (
        bool(NAS_PATH.fullmatch(path))
        and path.startswith(out_dir.rstrip("/") + "/")
        and "/../" not in f"/{path}/"
        and "/./" not in f"/{path}/"
    )


def latest_run(db: Session, video_id: uuid.UUID) -> ModelRun | None:
    return db.scalars(
        select(ModelRun)
        .where(ModelRun.video_id == video_id, ModelRun.kind == "players")
        .order_by(ModelRun.created_at.desc())
        .limit(1)
    ).first()


def artifact(db: Session, run_id: uuid.UUID, kind: str) -> AnalysisArtifact | None:
    return db.scalars(
        select(AnalysisArtifact).where(
            AnalysisArtifact.model_run_id == run_id, AnalysisArtifact.kind == kind
        )
    ).first()


def roster_numbers(db: Session, video: Video) -> dict[int, int | None]:
    """Our numbers in the video's roster snapshot → member."""
    rows = tagging.ensure_roster(db, video)
    return {r.number: r.member_id for r in rows if r.side == "us"}


def complete(db: Session, video: Video | None, job: Job, payload: dict[str, Any]) -> None:
    """Store a players result: the run, its artifacts, the clusters (replacing the last
    run's, carrying the coach's numbers over by team and read number). Raises ValueError on
    a bad result."""
    if video is None:
        raise ValueError("job has no video")
    result = PlayersResult.model_validate(payload)
    out_dir = str(job.params["out_dir"])
    prefix = str(job.params["prefix"])
    for path in (result.tracks_artifact.nas_path, result.index.nas_path):
        if path is not None and not _inside(path, out_dir):
            raise ValueError("artifact path outside the job's analysis folder")
    nas_dir = result.sheets.nas_dir
    if nas_dir is not None and not _inside(nas_dir.rstrip("/") + "/x", out_dir):
        raise ValueError("sheets outside the job's analysis folder")
    if result.tracks_artifact.key is not None and result.tracks_artifact.key != (
        f"{prefix}tracks.npz"
    ):
        raise ValueError("boxes stored under another key")
    if result.index.key != f"{prefix}index.json.gz":
        raise ValueError("index stored under another key")
    if result.sheets.keys != [f"{prefix}sheet-{n}.jpg" for n in range(len(result.sheets.keys))]:
        raise ValueError("sheets stored under other keys")
    if video.status != "ready":
        jobs.succeed(job, {"skipped": f"video is {video.status}"})
        return
    run = ModelRun(
        id=uuid.uuid4(),
        kind="players",
        job_id=job.id,
        video_id=video.id,
        model_name=result.model.name,
        model_version=result.model.version,
        weights_sha256=result.model.weights_sha256,
        params=result.model.params,
        metrics={"players": result.stats},
        timing=result.timing,
    )
    db.add(run)
    db.flush()
    # A file a GPU worker wrote to the bucket instead of the NAS: "s3:<key>" (the column
    # holds a path relative to the analysis root otherwise; the key is in stats too).
    tracks_key = result.tracks_artifact.key
    db.add_all(
        [
            AnalysisArtifact(
                video_id=video.id, model_run_id=run.id, kind="player_tracks",
                nas_path=result.tracks_artifact.nas_path or f"s3:{tracks_key}",
                format="npz", schema_version=1,
                sha256=result.tracks_artifact.sha256, bytes=result.tracks_artifact.bytes,
                stats={**result.stats, **({"object_key": tracks_key} if tracks_key else {})},
            ),
            AnalysisArtifact(
                video_id=video.id, model_run_id=run.id, kind="player_index",
                nas_path=result.index.nas_path or f"s3:{result.index.key}",
                format="json", schema_version=INDEX_SCHEMA,
                sha256=result.index.sha256, bytes=result.index.bytes,
                stats={"object_key": result.index.key},
            ),
            AnalysisArtifact(
                video_id=video.id, model_run_id=run.id, kind="player_sheets",
                nas_path=result.sheets.nas_dir or f"s3:{prefix}", format="jpeg", schema_version=1,
                sha256=result.index.sha256, bytes=len(result.sheets.keys),
                stats={
                    "keys": result.sheets.keys, "cell": result.sheets.cell,
                    "cols": result.sheets.cols, "rows": result.sheets.rows,
                },
            ),
        ]
    )  # fmt: skip
    previous = _current_clusters(db, video.id)
    carried = 0
    # The snapshot is needed (and taken) only to carry a coach's numbers over.
    roster = roster_numbers(db, video) if any(p.number is not None for p in previous) else {}
    for c in result.clusters:
        row = PlayerCluster(
            id=uuid.uuid4(),
            video_id=video.id,
            model_run_id=run.id,
            key=c.key,
            team=c.team,
            tracks=[t.model_dump() for t in c.tracks],
            crops=c.crops,
            detections=sum(t.n for t in c.tracks),
            ocr_number=c.number,
            ocr_confidence=c.number_conf if c.number is not None else None,
            not_player=c.referee,
            referee_guess=c.referee,
            confidence=c.confidence,
            source="model",
        )
        # A rerun keeps the coach's work where it can tell: same team, same number read.
        old = next(
            (
                p for p in previous
                if p.number is not None and p.team == c.team and c.number is not None
                and p.number == c.number
            ),
            None,
        )  # fmt: skip
        if old is not None:
            row.number = old.number
            row.member_id = (
                roster.get(old.number) if c.team == "us" and old.number is not None else None
            )
            row.assigned_by, row.assigned_at = old.assigned_by, old.assigned_at
            carried += 1
        db.add(row)
    db.execute(
        delete(PlayerCluster).where(
            PlayerCluster.video_id == video.id, PlayerCluster.model_run_id != run.id
        )
    )
    run.metrics = {**run.metrics, "carried": carried}
    jobs.succeed(job, {"model_run_id": str(run.id)})


# --- the gallery -----------------------------------------------------------------------------


def _current_clusters(db: Session, video_id: uuid.UUID) -> list[PlayerCluster]:
    return list(
        db.scalars(
            select(PlayerCluster)
            .where(PlayerCluster.video_id == video_id, PlayerCluster.merged_into.is_(None))
            .order_by(PlayerCluster.key)
        )
    )


def clusters(db: Session, video_id: uuid.UUID) -> list[PlayerCluster]:
    """The video's clusters as the gallery shows them (merged ones folded away)."""
    return _current_clusters(db, video_id)


class GalleryError(ValueError):
    pass


def assign(
    db: Session,
    video: Video,
    cluster: PlayerCluster,
    *,
    number: int | None,
    not_player: bool,
    actor: int,
) -> None:
    """The coach names a cluster: a number of the roster snapshot (ours) or any number
    (theirs), no number, or not a player."""
    if not_player:
        cluster.number, cluster.member_id, cluster.not_player = None, None, True
    else:
        member = None
        if number is not None and cluster.team == "us":
            roster = roster_numbers(db, video)
            if number not in roster:
                raise GalleryError(f"#{number} is not in this video's roster")
            member = roster[number]
        cluster.number, cluster.member_id, cluster.not_player = number, member, False
    cluster.assigned_by = actor
    cluster.assigned_at = datetime.now(UTC)


def merge(db: Session, target: PlayerCluster, sources: Sequence[PlayerCluster], actor: int) -> None:
    """Fold clusters into one: their tracks and crops move, the sources stay as merged."""
    if any(s.id == target.id for s in sources):
        raise GalleryError("a cluster cannot be merged into itself")
    if any(s.team != target.team for s in sources):
        raise GalleryError("merge clusters of one team; swap a wrong team first")
    tracks = list(target.tracks)
    crops = list(target.crops)
    for s in sources:
        tracks.extend(s.tracks)
        crops.extend(c for c in s.crops if c not in crops)
        s.merged_into = target.id
        s.tracks = []
        s.crops = []
        s.detections = 0
    tracks.sort(key=lambda t: int(t["t0"]))
    target.tracks = tracks
    target.crops = crops[:8]
    target.detections = sum(int(t["n"]) for t in tracks)
    target.confidence = None  # the coach's grouping now, not the job's
    if target.number is None and not target.not_player:
        named = {s.number for s in sources if s.number is not None}
        if len(named) == 1:
            target.number = named.pop()
            target.member_id = next(s.member_id for s in sources if s.number == target.number)
    target.assigned_by = actor
    target.assigned_at = datetime.now(UTC)


def split(
    db: Session, cluster: PlayerCluster, track_ids: Sequence[int], actor: int
) -> PlayerCluster:
    """Move some tracks of a cluster into a new one (two people grouped as one)."""
    wanted = set(track_ids)
    moving = [t for t in cluster.tracks if int(t["id"]) in wanted]
    staying = [t for t in cluster.tracks if int(t["id"]) not in wanted]
    if not moving or not staying or len(moving) != len(wanted):
        raise GalleryError("pick some, not all, of the cluster's tracks")
    key = int(
        db.scalar(
            select(func.max(PlayerCluster.key)).where(PlayerCluster.video_id == cluster.video_id)
        )
        or 0
    )
    new = PlayerCluster(
        id=uuid.uuid4(),
        video_id=cluster.video_id,
        model_run_id=cluster.model_run_id,
        key=key + 1,
        team=cluster.team,
        tracks=moving,
        crops=[t["crop"] for t in moving if t.get("crop")][:4],
        detections=sum(int(t["n"]) for t in moving),
        source="split",
        assigned_by=actor,
        assigned_at=datetime.now(UTC),
    )
    cluster.confidence = None  # the coach's grouping now, not the job's
    cluster.tracks = staying
    moved = {tuple(t["crop"]) for t in moving if t.get("crop")}
    cluster.crops = [c for c in cluster.crops if tuple(c) not in moved] or [
        t["crop"] for t in staying if t.get("crop")
    ][:4]
    cluster.detections = sum(int(t["n"]) for t in staying)
    cluster.assigned_by = actor
    cluster.assigned_at = datetime.now(UTC)
    db.add(new)
    db.flush()
    return new


def swap_teams(db: Session, video: Video, actor: int) -> int:
    """The side assignment was wrong: ours become theirs and back. Numbers given to our
    players no longer name roster members; they stay as numbers."""
    rows = _current_clusters(db, video.id)
    roster = roster_numbers(db, video)
    for c in rows:
        c.team = "them" if c.team == "us" else "us"
        c.member_id = roster.get(c.number) if c.team == "us" and c.number is not None else None
        c.assigned_by = actor
        c.assigned_at = datetime.now(UTC)
    return len(rows)


# --- the index and the ball, read from the bucket --------------------------------------------


def _unpack(text: str, code: str) -> array[int]:
    out: array[int] = array(code)
    out.frombytes(zlib.decompress(base64.b64decode(text.encode(), validate=True)))
    if sys.byteorder != "little":
        out.byteswap()
    return out


@dataclass(frozen=True)
class Index:
    """The players' boxes per analysed frame (wieplay/ml/players.py encode_index)."""

    t_ms: array[int]
    start: array[int]
    track: array[int]
    box: array[int]  # 4 per row, 1/65535 of the frame

    @classmethod
    def from_bytes(cls, data: bytes) -> Index:
        payload = json.loads(gzip.decompress(data))
        if payload.get("schema") != INDEX_SCHEMA:
            raise ValueError("unknown player index schema")
        index = cls(
            t_ms=_unpack(payload["t_ms"], "I"),
            start=_unpack(payload["start"], "I"),
            track=_unpack(payload["track"], "I"),
            box=_unpack(payload["box"], "H"),
        )
        rows = len(index.track)
        if (
            len(index.start) != len(index.t_ms) + 1
            or len(index.box) != 4 * rows
            or (len(index.start) and index.start[-1] != rows)
        ):
            raise ValueError("inconsistent player index")
        return index

    def frame_near(self, t_ms: int, within: int = FRAME_WINDOW_MS) -> int | None:
        k = bisect.bisect_left(self.t_ms, t_ms)
        best = None
        for j in (k - 1, k):
            if not (0 <= j < len(self.t_ms) and abs(self.t_ms[j] - t_ms) <= within):
                continue
            if best is None or abs(self.t_ms[j] - t_ms) < abs(self.t_ms[best] - t_ms):
                best = j
        return best

    def boxes(self, frame: int) -> list[tuple[int, tuple[float, float, float, float]]]:
        out = []
        for r in range(self.start[frame], self.start[frame + 1]):
            b = self.box[4 * r : 4 * r + 4]
            out.append((self.track[r], (b[0] / 65535, b[1] / 65535, b[2] / 65535, b[3] / 65535)))
        return out


@dataclass(frozen=True)
class Ball:
    """The ball track's contacts (t_ms, u, v, kind) and samples, normalised."""

    contacts: list[tuple[int, float, float, int]]
    fps: float
    samples: dict[int, tuple[float, float]]

    @classmethod
    def from_bytes(cls, data: bytes) -> Ball:
        payload = json.loads(gzip.decompress(data))
        scale = float(payload.get("scale") or 1000)
        contacts = sorted(
            (int(c[0]), c[1] / scale, c[2] / scale, int(c[3])) for c in payload.get("contacts", [])
        )
        samples: dict[int, tuple[float, float]] = {}
        for item in payload.get("tracks", []):
            s0, deltas = int(item[0]), item[1:]
            x = y = 0
            for i in range(0, len(deltas) - 1, 2):
                x += int(deltas[i])
                y += int(deltas[i + 1])
                samples[s0 + i // 2] = (x / scale, y / scale)
        return cls(contacts, float(payload.get("fps") or 15.0), samples)

    def contact_near(self, t_ms: int, within: int) -> tuple[int, float, float, int] | None:
        times = [c[0] for c in self.contacts]
        k = bisect.bisect_left(times, t_ms)
        best = None
        for j in range(max(0, k - 3), min(len(times), k + 3)):
            c = self.contacts[j]
            if c[3] == 0 or abs(c[0] - t_ms) > within:  # a bounce is the floor, no player
                continue
            if best is None or abs(c[0] - t_ms) < abs(best[0] - t_ms):
                best = c
        return best

    def at(self, t_ms: int) -> tuple[float, float] | None:
        k = round(t_ms / 1000 * self.fps)
        for d in (0, -1, 1):
            if (k + d) in self.samples:
                return self.samples[k + d]
        return None


_CACHE: OrderedDict[str, Any] = OrderedDict()
_CACHE_LOCK = threading.Lock()
CACHE_SIZE = 6


def _cached(key: str, load: Callable[[], Any]) -> Any:
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return _CACHE[key]
    value = load()
    with _CACHE_LOCK:
        _CACHE[key] = value
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return value


Reader = Callable[[str, int], bytes | None]  # (object key, max bytes) → bytes


def load_index(db: Session, run: ModelRun, read: Reader) -> Index | None:
    art = artifact(db, run.id, "player_index")
    if art is None:
        return None
    key = str((art.stats or {}).get("object_key", ""))

    def load() -> Index | None:
        data = read(key, MAX_INDEX_BYTES) if key else None
        return Index.from_bytes(data) if data else None

    found: Index | None = _cached(f"index:{art.sha256}", load)
    return found


def load_ball(db: Session, video_id: uuid.UUID, read: Reader) -> Ball | None:
    art = calibration.latest_track(db, video_id)
    if art is None:
        return None
    key = str((art.stats or {}).get("object_key", ""))

    def load() -> Ball | None:
        data = read(key, 16 * 1024 * 1024) if key else None
        return Ball.from_bytes(data) if data else None

    found: Ball | None = _cached(f"ball:{art.sha256}", load)
    return found


# --- attribution ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    track_id: int
    box: tuple[float, float, float, float]


@dataclass(frozen=True)
class Pick:
    track_id: int
    distance: float
    confidence: float
    method: Literal["court", "image"]


def box_distance(point: tuple[float, float], box: tuple[float, float, float, float]) -> float:
    """From a point to a box, in the box's heights (0 inside): fair to near and far players."""
    u, v = point
    x1, y1, x2, y2 = box
    dx = max(x1 - u, 0.0, u - x2)
    dy = max(y1 - v, 0.0, v - y2)
    return math.hypot(dx, dy) / max(y2 - y1, 1e-6)


def nearest(
    ball: tuple[float, float],
    candidates: Sequence[Candidate],
    *,
    to_court: court.Mat3 | None = None,
    camera: court.Camera | None = None,
    height: float = HIGH_CONTACT_M,
) -> Pick | None:
    """The player nearest the ball: on the floor in metres with a calibrated camera (the
    ball's point `height` above the floor, the player's feet), else in the image."""
    if not candidates:
        return None
    method: Literal["court", "image"] = "image"
    dists: list[float] = []
    if to_court is not None and camera is not None:
        try:
            bx, by = camera.to_court(ball[0], ball[1], height)
            for c in candidates:
                fx, fy = court.apply(to_court, ((c.box[0] + c.box[2]) / 2, c.box[3]))
                dists.append(math.hypot(fx - bx, fy - by))
            method = "court"
        except court.CourtError:
            dists = []
    if not dists:
        dists = [box_distance(ball, c.box) for c in candidates]
    order = sorted(range(len(candidates)), key=lambda i: dists[i])
    d1 = dists[order[0]]
    d2 = dists[order[1]] if len(order) > 1 else math.inf
    scale, slack = (1.5, 0.5) if method == "court" else (0.5, 0.25)
    near = math.exp(-d1 / scale)
    margin = 1.0 if math.isinf(d2) else (d2 - d1) / (d2 + slack)
    return Pick(
        candidates[order[0]].track_id,
        round(d1, 3),
        round(near * (0.5 + 0.5 * margin), 3),
        method,
    )


@dataclass
class Mapping:
    """Track → cluster, and what each cluster is now."""

    by_track: dict[int, PlayerCluster]

    @classmethod
    def of(cls, rows: Sequence[PlayerCluster]) -> Mapping:
        out: dict[int, PlayerCluster] = {}
        for c in rows:
            for t in c.tracks:
                out[int(t["id"])] = c
        return cls(out)


def _ball_for(ev: Event, ball: Ball | None) -> tuple[int, tuple[float, float], int | None] | None:
    """The contact time, the ball there and its kind (None: unknown)."""
    own_x, own_y = getattr(ev, "ball_x", None), getattr(ev, "ball_y", None)
    if own_x is not None and own_y is not None:  # a suggested action carries it
        return ev.t_ms, (float(own_x), float(own_y)), None
    if ball is None:
        return None
    within = CONTACT_WINDOW_MS.get(ev.time_precision, CONTACT_WINDOW_MS["live"])
    contact = ball.contact_near(ev.t_ms, within)
    if contact is not None:
        return contact[0], (contact[1], contact[2]), contact[3]
    seen = ball.at(ev.t_ms)
    return (ev.t_ms, seen, None) if seen is not None else None


def _camera(c: dict[str, Any] | None) -> court.Camera | None:
    if not c:
        return None
    return court.Camera(
        center=(float(c["center"][0]), float(c["center"][1]), float(c["center"][2])),
        focal=float(c["focal"]),
        rays=court.from_lists(c["rays"]),
        aspect=float(c["aspect"]),
    )


Geometry = tuple[court.Mat3 | None, court.Camera | None]


class Geometries:
    """The court mapping at a time of the video: the camera's setup then (PLAN §10.6; a
    camera bumped during the match has several)."""

    def __init__(self, row: calibration.Calibration | None) -> None:
        self.row = row
        self._cache: dict[int, Geometry] = {}

    def at(self, t_ms: int) -> Geometry:
        if self.row is None:
            return None, None
        seg = calibration.segment_at(self.row, t_ms)
        key = int(seg["from_ms"])
        if key not in self._cache:
            h = seg.get("homography")
            self._cache[key] = (
                (court.from_lists(h), _camera(seg.get("camera"))) if h else (None, None)
            )
        return self._cache[key]


def attribute_event(
    ev: Event,
    index: Index,
    ball: Ball | None,
    mapping: Mapping,
    geometry: Callable[[int], Geometry] = lambda _t: (None, None),
) -> tuple[Pick | None, PlayerCluster | None, int | None]:
    """The player who made an action, as WiePlay sees it (module docstring); the court's
    mapping at the contact comes from `geometry` (the camera's setup then)."""
    found = _ball_for(ev, ball)
    if found is None:
        return None, None, None
    t_ms, point, kind = found
    to_court, camera = geometry(t_ms) if COURT_DISTANCE else (None, None)
    frame = index.frame_near(t_ms)
    if frame is None:
        return None, None, t_ms
    candidates = []
    for track_id, box in index.boxes(frame):
        c = mapping.by_track.get(track_id)
        if c is None or c.not_player or c.team != ev.side:
            continue
        candidates.append(Candidate(track_id, box))
    low = ev.code in LOW_SKILLS or kind == 1
    pick = nearest(
        point, candidates, to_court=to_court, camera=camera,
        height=LOW_CONTACT_M if low else HIGH_CONTACT_M,
    )  # fmt: skip
    if pick is None:
        return None, None, t_ms
    return pick, mapping.by_track[pick.track_id], t_ms


def _model_owned(ev: Event) -> bool:
    """A model's action nobody has answered: WiePlay may write its player."""
    return ev.source == "model" and ev.review == "unreviewed" and ev.deleted_at is None


def attribute(
    db: Session,
    video: Video,
    read: Reader,
    *,
    only_stale: bool = False,
) -> dict[str, Any] | None:
    """Attribute the video's actions (all, or only new and changed ones) and refresh the
    metrics. None when there is no players run (or its index cannot be read)."""
    run = latest_run(db, video.id)
    if run is None:
        return None
    index = load_index(db, run, read)
    if index is None:
        return None
    ball = load_ball(db, video.id, read)
    geometries = Geometries(calibration_row(db, video))
    rows = _current_clusters(db, video.id)
    mapping = Mapping.of(rows)
    events = list(
        db.scalars(select(Event).where(Event.video_id == video.id, Event.deleted_at.is_(None)))
    )
    existing = {
        ep.event_id: ep
        for ep in db.scalars(select(EventPlayer).where(EventPlayer.video_id == video.id))
    }
    done = 0
    for ev in events:
        ep = existing.get(ev.id)
        if (
            only_stale
            and ep is not None
            and ep.event_version == ev.version
            and (ep.model_run_id == run.id)
        ):
            continue
        pick, cluster, contact = attribute_event(ev, index, ball, mapping, geometries.at)
        if ep is None:
            ep = EventPlayer(event_id=ev.id, video_id=video.id, event_version=ev.version)
            db.add(ep)
        number = cluster.number if cluster is not None and pick is not None else None
        ep.model_run_id = run.id
        ep.cluster_id = cluster.id if cluster is not None else None
        ep.track_id = pick.track_id if pick else None
        ep.number = number
        ep.member_id = cluster.member_id if cluster is not None and number is not None else None
        ep.method = pick.method if pick else None
        ep.distance = pick.distance if pick else None
        ep.confidence = pick.confidence if pick else None
        ep.contact_ms = contact
        _write_into_model_event(ev, ep, run)
        ep.event_version = ev.version
        done += 1
    db.flush()
    metrics = summary(db, video)
    run.metrics = {**(run.metrics or {}), "attribution": {**metrics, "updated": done}}
    return metrics


def _write_into_model_event(ev: Event, ep: EventPlayer, run: ModelRun) -> None:
    """Fill (or take back) the player of a model's unanswered action; never touch a number
    a person set (one that is not the number WiePlay wrote)."""
    if not _model_owned(ev):
        return
    ours = ev.number is None or (ep.applied_number is not None and ev.number == ep.applied_number)
    if not ours:
        return
    want = ep.number if (ep.confidence or 0) >= APPLY_CONFIDENCE else None
    ref = f"{str(run.id)[:8]}:{ep.track_id}" if want is not None else None
    if ev.number == want and ev.track_ref == ref:
        ep.applied_number = want
        return
    ev.number = want
    ev.member_id = ep.member_id if want is not None else None
    ev.track_ref = ref
    ev.version = (ev.version or 0) + 1
    ep.applied_number = want


def stale(db: Session, video_id: uuid.UUID) -> bool:
    """Actions that are new or changed since they were attributed (cheap: one query)."""
    run = latest_run(db, video_id)
    if run is None:
        return False
    count = db.scalar(
        select(func.count())
        .select_from(Event)
        .outerjoin(EventPlayer, EventPlayer.event_id == Event.id)
        .where(
            Event.video_id == video_id,
            Event.deleted_at.is_(None),
            (EventPlayer.event_id.is_(None))
            | (EventPlayer.event_version != Event.version)
            | (EventPlayer.model_run_id.is_distinct_from(run.id)),
        )
    )
    return bool(count)


def apply_suggestions(
    db: Session, video: Video, actor: int, event_ids: Sequence[uuid.UUID] | None = None
) -> int:
    """The coach takes WiePlay's players for their tags without a number (the "player
    identification" step): written as the coach's, through the normal tag path, so the
    revision names the coach."""
    query = (
        select(Event, EventPlayer)
        .join(EventPlayer, EventPlayer.event_id == Event.id)
        .where(
            Event.video_id == video.id,
            Event.deleted_at.is_(None),
            Event.number.is_(None),
            EventPlayer.number.is_not(None),
            EventPlayer.event_version == Event.version,
        )
    )
    if event_ids is not None:
        query = query.where(Event.id.in_(list(event_ids)))
    done = 0
    for ev, ep in db.execute(query).all():
        if _model_owned(ev):
            continue  # already carries it as a suggestion
        ev.number = ep.number
        ev.member_id = ep.member_id
        ev.track_ref = f"{str(ep.model_run_id)[:8]}:{ep.track_id}" if ep.model_run_id else None
        ev.updated_by = actor
        ev.version = (ev.version or 0) + 1
        ep.event_version = ev.version
        done += 1
    return done


def summary(db: Session, video: Video) -> dict[str, Any]:
    """How far the players are identified (Hudl's "% identified"), and the attribution
    against the coach's numbers where the coach has them."""
    rows = db.execute(
        select(Event, EventPlayer)
        .outerjoin(EventPlayer, EventPlayer.event_id == Event.id)
        .where(Event.video_id == video.id, Event.deleted_at.is_(None))
    ).all()
    actions = identified = by_coach = suggested = 0
    compared = correct = 0
    per_cluster: dict[str, int] = {}
    for ev, ep in rows:
        if ev.source == "model" and ev.review == "rejected":
            continue
        actions += 1
        wieplay_number = ep.number if ep is not None else None
        # WiePlay's own number in a model's action nobody has answered is no coach's number.
        ours = (
            ep is not None and _model_owned(ev) and ev.number is not None
            and ev.number == ep.applied_number
        )  # fmt: skip
        coach_number = None if ours else ev.number
        if ev.number is not None or wieplay_number is not None:
            identified += 1
        if coach_number is not None:
            by_coach += 1
        elif wieplay_number is not None:
            suggested += 1
        if coach_number is not None and wieplay_number is not None:
            compared += 1
            correct += int(coach_number == wieplay_number)
        if ep is not None and ep.cluster_id is not None:
            per_cluster[str(ep.cluster_id)] = per_cluster.get(str(ep.cluster_id), 0) + 1
    return {
        "actions": actions,
        "identified": identified,
        "identified_share": round(identified / actions, 4) if actions else None,
        "by_coach": by_coach,
        "suggested": suggested,
        "vs_coach": {
            "compared": compared,
            "correct": correct,
            "accuracy": round(correct / compared, 4) if compared else None,
        },
        "per_cluster": per_cluster,
    }


def roster(db: Session, video: Video, *, create: bool = True) -> list[VideoRoster]:
    """The roster snapshot; taken now (as the first tag would) only when `create`."""
    if create:
        tagging.ensure_roster(db, video)
    return list(
        db.scalars(
            select(VideoRoster)
            .where(VideoRoster.video_id == video.id)
            .order_by(VideoRoster.side, VideoRoster.number)
        )
    )
