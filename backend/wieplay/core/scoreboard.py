"""The scoreboard in the picture (PLAN §10.3, step 5), API side.

    coach draws the box (once per hall) ──scoreboard job──▶ the board read twice a second,
                                         smoothed: what it showed and when (a small track)
    scoring ◀── the board before each rally: it wins over the serve evidence, a disagreement
                 is flagged ("board")

A region per video (`scoreboard_region`): the score's box in normalised frame coordinates,
optionally a second box for the set counts, and the kind of board (flip cards, LED digits,
or "auto"). Many halls have no board in the picture: it is optional. The hall's memory: a
video without a region is offered the last one drawn in the same hall (the board stands where
it stands), else the team's last one (the camera usually stands at the same spot).

The `scoreboard` job (worker, background lane, CPU) crops the box from the NAS originals at 2
fps, reads the digits (wieplay/ml/scoreboard.py) and smooths them (wieplay/ml/board.py). Its
readings stay on the NAS (`analysis_artifact` kind "scoreboard"); the segments (a few KB) are
kept as `analysis_track` kind "scoreboard", where the scoring reads them.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import DateTime, ForeignKey, Integer, String, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from wieplay.core import jobs
from wieplay.core.db import Base
from wieplay.core.models import (
    AnalysisArtifact,
    AnalysisTrack,
    Job,
    ModelRun,
    Period,
    Segment,
    TeamLineage,
    Video,
)
from wieplay.ml import board as bd
from wieplay.ml import scoring as sc

KINDS = frozenset({"scoreboard"})
CAPS = ("analyse", "nas")
PRIORITY = jobs.FOLLOW_UP_PRIORITY  # quick: before the next analysis
TRACK_KIND = "scoreboard"
FPS = 2.0
NAS_PATH = re.compile(r"[A-Za-z0-9._/-]+")
MAX_SEGMENTS = 2000
FINAL_WINDOW_MS = 60_000  # the board after a set's last rally: seen within this
BLIND_SURE = 0.7  # the serve evidence's winner counts as a disagreement from this sure

BoardKind = Literal["flip", "led", "auto"]


class ScoreboardRegion(Base):
    """Where the score is in a video's picture (PLAN §6.9 `scoreboard_region`)."""

    __tablename__ = "scoreboard_region"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video.id", ondelete="CASCADE"), unique=True
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_lineage.id"), index=True)
    hall_name: Mapped[str | None] = mapped_column(String(255), index=True)
    rect: Mapped[dict[str, float]] = mapped_column(JSONB)  # x, y, w, h (0..1)
    # {"kind": flip | led | auto, "sets": {x, y, w, h} | null}
    layout: Mapped[dict[str, Any]] = mapped_column(JSONB)
    at_ms: Mapped[int | None] = mapped_column(Integer)  # the frame it was drawn on
    copied_from: Mapped[uuid.UUID | None] = mapped_column(UUID)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RectIn(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0.003, le=1)
    h: float = Field(gt=0.003, le=1)

    @model_validator(mode="after")
    def _inside(self) -> RectIn:
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ValueError("the box leaves the picture")
        return self

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 5) for k, v in self.model_dump().items()}


# --- the region and the hall's memory -----------------------------------------------------------


def current(db: Session, video_id: uuid.UUID) -> ScoreboardRegion | None:
    return db.scalars(select(ScoreboardRegion).where(ScoreboardRegion.video_id == video_id)).first()


@dataclass(frozen=True)
class Suggestion:
    source: Literal["hall", "team"]
    region: ScoreboardRegion
    video_title: str
    recorded_on: date
    hall_name: str | None


def hall_of(db: Session, video: Video) -> str | None:
    from wieplay.core.calibration import hall_of as court_hall  # the court's hall memory too

    return court_hall(db, video)


def suggestion(db: Session, video: Video) -> Suggestion | None:
    """The last region drawn in the video's hall (any team's: the board belongs to the
    hall), else the team's last one."""
    hall = hall_of(db, video)
    base = (
        select(ScoreboardRegion, Video.title, Video.recorded_on)
        .join(Video, Video.id == ScoreboardRegion.video_id)
        .join(TeamLineage, TeamLineage.id == Video.lineage_id)
        .where(ScoreboardRegion.video_id != video.id, Video.status != "deleted")
        .order_by(ScoreboardRegion.updated_at.desc())
        .limit(1)
    )
    # The suggestion names its video: a guest club's stay with its team, and it sees none of
    # KSCW's (PLAN §4.5).
    own = db.get(TeamLineage, video.lineage_id)
    if own is not None and own.guest_club is not None:
        base = base.where(Video.lineage_id == video.lineage_id)
    else:
        base = base.where(TeamLineage.guest_club.is_(None))
    if hall:
        row = db.execute(base.where(ScoreboardRegion.hall_name == hall)).first()
        if row is not None:
            return Suggestion("hall", row[0], row[1], row[2], row[0].hall_name)
    row = db.execute(base.where(ScoreboardRegion.lineage_id == video.lineage_id)).first()
    if row is not None:
        return Suggestion("team", row[0], row[1], row[2], row[0].hall_name)
    return None


def save(
    db: Session,
    video: Video,
    rect: RectIn,
    sets: RectIn | None,
    kind: BoardKind,
    *,
    actor: int,
    at_ms: int | None = None,
    copied_from: uuid.UUID | None = None,
) -> ScoreboardRegion:
    hall = hall_of(db, video)
    row = current(db, video.id)
    if row is None:
        row = ScoreboardRegion(id=uuid.uuid4(), video_id=video.id, created_by=actor)
    row.lineage_id = video.lineage_id
    row.hall_name = hall
    row.rect = rect.as_dict()
    row.layout = {"kind": kind, "sets": sets.as_dict() if sets else None}
    row.at_ms = at_ms
    row.copied_from = copied_from
    row.updated_by = actor
    db.add(row)
    db.flush()
    return row


def remove(db: Session, video: Video) -> bool:
    """Forget the region and what was read with it; the scoring goes back to the serves."""
    row = current(db, video.id)
    if row is None:
        return False
    db.delete(row)
    db.execute(
        delete(AnalysisTrack).where(
            AnalysisTrack.video_id == video.id, AnalysisTrack.kind == TRACK_KIND
        )
    )
    now = datetime.now(UTC)
    for job in db.scalars(
        select(Job).where(
            Job.kind == "scoreboard", Job.video_id == video.id, Job.status.in_(jobs.ACTIVE)
        )
    ):
        # A running one learns on its next heartbeat; its result would be stale anyway.
        job.status, job.error, job.finished_at = "cancelled", "scoreboard region removed", now
        job.lease_owner = job.lease_expires_at = None
    db.flush()
    return True


# --- the job --------------------------------------------------------------------------------


def can_read(video: Video) -> str | None:
    if video.status != "ready":
        return "Read the scoreboard once the video is ready"
    if not video.files or any(not f.nas_path for f in video.files):
        return "The video's originals are not on the NAS"
    if not video.width or not video.height:
        return "The video's picture size is unknown"
    return None


def active_job(db: Session, video_id: uuid.UUID) -> Job | None:
    return db.scalars(
        select(Job)
        .where(Job.kind == "scoreboard", Job.video_id == video_id, Job.status.in_(jobs.ACTIVE))
        .order_by(Job.created_at.desc())
    ).first()


def queue_reading(db: Session, video: Video, row: ScoreboardRegion, out_dir: str) -> Job | None:
    """Read the board of a video with its region (a queued job is updated, a running one gets
    a successor: it may have read the old box). None when the video cannot be read."""
    if can_read(video) is not None:
        return None
    params = {
        "video_id": str(video.id),
        "region_id": str(row.id),
        "inputs": [f.nas_path for f in video.files],
        "duration_ms": video.duration_ms,
        "width": video.width,
        "height": video.height,
        "rect": row.rect,
        "sets": (row.layout or {}).get("sets"),
        "kind": (row.layout or {}).get("kind", "auto"),
        "fps": FPS,
        "out_dir": out_dir,
    }
    queued = db.scalars(
        select(Job)
        .where(Job.kind == "scoreboard", Job.video_id == video.id, Job.status == "queued")
        .with_for_update()
    ).first()
    if queued is not None:
        queued.params = params
        return queued
    return jobs.enqueue(
        db,
        "scoreboard",
        caps=CAPS,
        video_id=video.id,
        priority=PRIORITY,
        max_attempts=2,
        params=params,
    )


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class ArtifactIn(BaseModel):
    nas_path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)
    format: Literal["npz"]
    schema_version: int = Field(ge=1, le=1000)
    stats: dict[str, Any] = Field(default_factory=dict)


class ScoreboardResult(BaseModel):
    model: ModelIn
    region_id: uuid.UUID
    artifact: ArtifactIn | None = None
    track: dict[str, str]  # wieplay/ml/board.py pack()
    summary: dict[str, Any] = Field(default_factory=dict)
    timing: dict[str, Any] = Field(default_factory=dict)


def complete(db: Session, video: Video | None, job: Job, payload: dict[str, Any]) -> None:
    """Keep a scoreboard job's result and score the video again with it."""
    from wieplay.core import scoring

    if video is None:
        raise ValueError("job has no video")
    result = ScoreboardResult.model_validate(payload)
    found = bd.unpack(result.track)
    if len(found) > MAX_SEGMENTS:
        raise ValueError("too many scoreboard segments")
    if result.artifact is not None:
        path = result.artifact.nas_path
        out_dir = str(job.params["out_dir"])
        if not NAS_PATH.fullmatch(path) or ".." in path or not path.startswith(out_dir + "/"):
            raise ValueError("artifact outside the job's folder")
    row = current(db, video.id)
    if video.status != "ready" or row is None or row.id != result.region_id:
        # The video went away or the coach drew another box meanwhile: a stale reading.
        jobs.succeed(
            job, {"skipped": "region changed" if video.status == "ready" else video.status}
        )
        return
    run = ModelRun(
        id=uuid.uuid4(),
        kind="scoreboard",
        job_id=job.id,
        video_id=video.id,
        model_name=result.model.name,
        model_version=result.model.version,
        params={**result.model.params, "region": row.rect, "layout": row.layout},
        metrics=bd.summary([], found)
        | {k: v for k, v in result.summary.items() if k != "segments"},
        timing=result.timing,
    )
    db.add(run)
    db.flush()
    if result.artifact is not None:
        db.add(
            AnalysisArtifact(
                video_id=video.id,
                model_run_id=run.id,
                kind="scoreboard",
                **result.artifact.model_dump(),
            )
        )
    db.execute(
        delete(AnalysisTrack).where(
            AnalysisTrack.video_id == video.id, AnalysisTrack.kind == TRACK_KIND
        )
    )
    db.add(
        AnalysisTrack(
            video_id=video.id,
            model_run_id=run.id,
            kind=TRACK_KIND,
            step_ms=bd.STEP_MS,
            channels=bd.pack(found),
        )
    )
    db.flush()
    if video.sport in scoring.SPORTS:
        scoring.score_video(db, video, reason="scoreboard")
    jobs.succeed(job, {"model_run_id": str(run.id), "segments": len(found)})


def latest_run(db: Session, video_id: uuid.UUID) -> ModelRun | None:
    return db.scalars(
        select(ModelRun)
        .where(ModelRun.kind == "scoreboard", ModelRun.video_id == video_id)
        .order_by(ModelRun.created_at.desc())
        .limit(1)
    ).first()


def timeline(db: Session, video_id: uuid.UUID) -> bd.Timeline | None:
    track = db.scalars(
        select(AnalysisTrack)
        .where(AnalysisTrack.video_id == video_id, AnalysisTrack.kind == TRACK_KIND)
        .order_by(AnalysisTrack.created_at.desc())
        .limit(1)
    ).first()
    if track is None:
        return None
    try:
        found = bd.unpack(track.channels)
    except ValueError:
        return None
    return bd.Timeline(found) if found else None


# --- the board in the scoring --------------------------------------------------------------------


Score = tuple[int, int]


def says(before: Score | None, after: Score | None) -> sc.BoardSays | None:
    """What the board says about a rally from the score during it and during the next one (or
    after the set): +1 for one team names the winner, no change says no point; anything else
    (a rally the finder missed in between, a misreading) says nothing."""
    if before is None or after is None:
        return None
    step = (after[0] - before[0], after[1] - before[1])
    return {(1, 0): "us", (0, 1): "them", (0, 0): "none"}.get(step)  # type: ignore[return-value]


@dataclass
class Board:
    """The board's evidence for one scoring run, in the board's order (left, right)."""

    before: dict[str, tuple[Score, float]]  # rally key → the board during it
    finals: dict[int, tuple[Score, float]]  # set no → the board after its last rally
    left_is_us: bool = True
    orientation: str = "default"  # official | fit | default

    def ours(self, score: Score) -> Score:
        return score if self.left_is_us else (score[1], score[0])

    def apply(self, set_ins: list[sc.SetIn], left_is_us: bool | None = None) -> list[sc.SetIn]:
        """The sets with what the board says about each rally (us, them)."""
        if left_is_us is not None:
            self.left_is_us = left_is_us
        out = []
        for st in set_ins:
            seen: list[tuple[Score, float] | None] = [self.before.get(r.key) for r in st.rallies]
            seen.append(self.finals.get(st.no))
            rallies = []
            for i, r in enumerate(st.rallies):
                a, b = seen[i], seen[i + 1]
                verdict = says(self.ours(a[0]), self.ours(b[0])) if a and b else None
                p = min(a[1], b[1]) if a and b else 0.0
                rallies.append(replace(r, board=verdict, board_p=p if verdict else 0.0))
            out.append(replace(st, rallies=rallies))
        return out


def board_for(
    db: Session,
    video: Video,
    rallies: list[Segment],
    periods: list[Period],
    by_period: dict[uuid.UUID, list[int]],
) -> Board | None:
    """What the board showed during each rally (the score before it) and after each set's
    last rally, or None without a reading. A segment that started before the rally's set and
    is not a fresh 0:0 is the last set's board (the scorer had not turned it back yet)."""
    tl = timeline(db, video.id)
    if tl is None:
        return None
    before: dict[str, tuple[Score, float]] = {}
    finals: dict[int, tuple[Score, float]] = {}
    for period in periods:
        idx = by_period.get(period.id)
        if not idx:
            continue
        for i in idx:
            rally = rallies[i]
            seg = tl.at((rally.start_ms + rally.end_ms) // 2)
            if seg is None:
                continue
            if seg.first_ms < period.start_ms and seg.left + seg.right > 2:
                continue
            before[str(rally.id)] = ((seg.left, seg.right), seg.confidence)
        last = rallies[idx[-1]]
        after = [
            s
            for s in tl.within(last.start_ms, last.end_ms + FINAL_WINDOW_MS)
            if s.first_ms > (last.start_ms + last.end_ms) // 2 and s.left + s.right > 2
        ]
        if after:
            seg = after[0]
            finals[period.no] = ((seg.left, seg.right), seg.confidence)
    if not before and not finals:
        return None
    return Board(before, finals)


def orient(board: Board, set_ins: list[sc.SetIn]) -> list[sc.SetIn]:
    """Which side of the board is ours: the official set scores when the board's finals say
    it, else whichever fits the rest of the evidence better (the alignment's cost)."""
    votes = 0
    for st in set_ins:
        final = board.finals.get(st.no)
        if st.official is None or final is None:
            continue
        score = final[0]
        if score == st.official and score[::-1] != st.official:
            votes += 1
        elif score[::-1] == st.official and score != st.official:
            votes -= 1
    if votes:
        board.orientation = "official"
        return board.apply(set_ins, votes > 0)
    costs = {}
    for left_us in (True, False):
        match = sc.score_match(board.apply(set_ins, left_us))
        costs[left_us] = sum(s.cost for s in match.sets)
    board.orientation = "fit"
    return board.apply(set_ins, costs[True] <= costs[False])


def flag_disagreements(
    with_board: list[sc.SetIn], match: sc.MatchOut
) -> tuple[sc.MatchOut, dict[str, int]]:
    """Rallies where the board decided against the serve evidence: the scoring blind to the
    board was sure of the other answer. Flagged "board" (the board's answer stands)."""
    blind_sets = [
        replace(st, rallies=[replace(r, board=None, board_p=0.0) for r in st.rallies])
        for st in with_board
    ]
    blind = sc.score_match(blind_sets)
    blind_out = {o.key: o for so in blind.sets for o in so.rallies}
    told = {r.key for st in with_board for r in st.rallies if r.board is not None}
    disagree = 0
    for so in match.sets:
        rallies = []
        for out in so.rallies:
            other = blind_out.get(out.key)
            if (
                out.key in told
                and other is not None
                and other.confidence >= BLIND_SURE
                and (other.point, other.won_by) != (out.point, out.won_by)
            ):
                disagree += 1
                if out.note is None or out.note in ("unsure", "serve"):
                    out = replace(out, note="board")
            rallies.append(out)
        so.rallies = rallies
    return match, {"disagree_with_serves": disagree}


def metrics(
    board: Board, with_board: list[sc.SetIn], match: sc.MatchOut, extra: dict[str, int]
) -> dict[str, Any]:
    """How often the board said something, how often the score line follows it; on a coach's
    winners, how often the board named the right team."""
    path = {o.key: o for so in match.sets for o in so.rallies}
    told = agree = right = known = 0
    for st in with_board:
        for r in st.rallies:
            if r.board is None:
                continue
            told += 1
            out = path.get(r.key)
            if out is not None:
                agree += int((out.won_by if out.point else "none") == r.board)
            if r.won_fact and r.board in ("us", "them"):
                known += 1
                right += int(r.board == r.won_fact)
    rallies = sum(len(st.rallies) for st in with_board)
    return {
        "orientation": board.orientation,
        "left_is_us": board.left_is_us,
        "rallies_seen": len(board.before),
        "rallies_told": told,
        "told_share": round(told / rallies, 4) if rallies else None,
        "path_agrees": agree,
        "finals_seen": len(board.finals),
        "vs_coach": {
            "winner_accuracy": round(right / known, 4) if known else None,
            "winners_known": known,
        },
        **extra,
    }
