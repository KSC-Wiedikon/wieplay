"""The automatic chain of a video (PLAN §10, 20.10.2026: "everything gets analysed and
automatically assigned"): which steps ran, and the hand-offs that keep it going by itself.

    ready ──analyse──▶ rallies, then at once the scoring (points, sets, sides, breaks, the
        scoresheet's lineups and rotations) ──trajectory──▶ the ball's path ──court──▶ the
        court (hall memory, the finder, else the nominal camera) ──▶ actions and players (the
        GPU worker; its busy guard only makes the job wait) ──▶ who made each action

Each arrow is a job queued by the step before it when it finishes (wieplay/core/pipeline.py).
A step that fails for good does not stop the chain where the next one can do without it: no
ball path, the court is still looked for; no court, the actions and players run on the nominal
camera. `resume` queues a step nobody queued (an older video, a hand-off that was lost); the
API's reaper runs it hourly over the ready videos, `python -m wieplay.cli chain` on demand.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from wieplay.core.config import Settings, get_settings
from wieplay.core.models import Job, ModelRun, Video

StepKey = Literal["rallies", "score", "ball", "court", "actions", "players", "attribution"]
Status = Literal["none", "queued", "running", "done", "failed", "skipped"]
STEPS: tuple[StepKey, ...] = (
    "rallies", "score", "ball", "court", "actions", "players", "attribution",
)  # fmt: skip
JOB_OF: dict[str, str] = {
    "rallies": "analyse",
    "ball": "trajectory",
    "court": "court",
    "actions": "actions",
    "players": "players",
}
FINISHED: frozenset[str] = frozenset({"done", "failed", "skipped"})
SPORTS = frozenset({"volleyball"})


@dataclass(frozen=True)
class Step:
    key: StepKey
    status: Status


@dataclass(frozen=True)
class Steps:
    items: tuple[Step, ...]

    @property
    def done(self) -> int:
        return sum(1 for s in self.items if s.status in FINISHED)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def running(self) -> bool:
        return any(s.status in ("queued", "running") for s in self.items)

    def of(self, key: StepKey) -> Status:
        return next((s.status for s in self.items if s.key == key), "none")


def _job_states(db: Session, video_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Status]]:
    """Per video and job kind: done once a job of it ever succeeded (a rerun later does not
    undo that), else running or queued while one is active, else failed, else none."""
    out: dict[uuid.UUID, dict[str, Status]] = {v: {} for v in video_ids}
    if not video_ids:
        return out
    rows = db.execute(
        select(Job.video_id, Job.kind, Job.status, Job.created_at)
        .where(Job.video_id.in_(video_ids), Job.kind.in_(tuple(JOB_OF.values())))
        .order_by(Job.created_at)
    ).all()
    seen: dict[tuple[uuid.UUID, str], set[str]] = {}
    for video_id, kind, status, _ in rows:
        if video_id is not None:
            seen.setdefault((video_id, kind), set()).add(status)
    for (video_id, kind), statuses in seen.items():
        state: Status
        if "succeeded" in statuses:
            state = "done"
        elif "running" in statuses:
            state = "running"
        elif "queued" in statuses:
            state = "queued"
        else:
            state = "failed"  # failed or cancelled, nothing left to do
        out[video_id][kind] = state
    return out


def _has_runs(db: Session, video_ids: list[uuid.UUID], kind: str) -> set[uuid.UUID]:
    if not video_ids:
        return set()
    return set(
        db.scalars(
            select(ModelRun.video_id)
            .where(ModelRun.video_id.in_(video_ids), ModelRun.kind == kind)
            .distinct()
        )
    )


def _attributed(db: Session, video_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Videos whose newest players run has attributed the actions."""
    if not video_ids:
        return set()
    newest: dict[uuid.UUID, ModelRun] = {}
    for run in db.scalars(
        select(ModelRun)
        .where(ModelRun.video_id.in_(video_ids), ModelRun.kind == "players")
        .order_by(ModelRun.created_at)
    ):
        if run.video_id is not None:
            newest[run.video_id] = run
    return {v for v, run in newest.items() if (run.metrics or {}).get("attribution") is not None}


def steps_of(
    db: Session, videos: list[Video], settings: Settings | None = None
) -> dict[uuid.UUID, Steps]:
    """Where the chain stands for each video ("Analysing: 4 of 7 steps"). Sports without
    automatic analysis get no steps."""
    settings = settings or get_settings()
    ours = [v for v in videos if v.sport in SPORTS]
    ids = [v.id for v in ours]
    states = _job_states(db, ids)
    scored = _has_runs(db, ids, "score")
    attributed = _attributed(db, ids)
    out: dict[uuid.UUID, Steps] = {}
    for video in ours:
        jobs_of = states.get(video.id, {})
        items: list[Step] = []
        for key in STEPS:
            status: Status
            if key == "score":
                rallies = jobs_of.get("analyse", "none")
                status = (
                    "done" if video.id in scored else "failed" if rallies == "failed" else "none"
                )
            elif key == "attribution":
                players = jobs_of.get("players", "none")
                status = (
                    "done"
                    if video.id in attributed
                    else "skipped"
                    if players in ("failed", "skipped")
                    else "none"
                )
            else:
                status = jobs_of.get(JOB_OF[key], "none")
                if key == "players" and status == "none" and not settings.players_auto:
                    status = "skipped"  # switched off: found on request only
            items.append(Step(key, status))
        if items[-2].status == "skipped" and items[-1].status == "none":
            items[-1] = Step("attribution", "skipped")
        out[video.id] = Steps(tuple(items))
    return out


def steps(db: Session, video: Video, settings: Settings | None = None) -> Steps | None:
    return steps_of(db, [video], settings).get(video.id)


# --- keeping it going -----------------------------------------------------------------------


def after_failed(
    db: Session, video: Video | None, job: Job, settings: Settings | None = None
) -> None:
    """A step failed for good: the next ones run without it where they can (module
    docstring). The analysis itself has no stand-in: without its signals nothing follows."""
    from wieplay.core import actions, analysis, calibration, players

    if video is None or video.status != "ready" or video.sport not in SPORTS:
        return
    settings = settings or get_settings()
    if job.kind == "trajectory":
        calibration.queue_court(db, video, analysis.analysis_subdir(db, video))
    elif job.kind == "court":
        actions.queue_actions(db, video, reason="court_failed")
        players.after_court(db, video, job, auto=settings.players_auto)


def resume(db: Session, video: Video, settings: Settings | None = None) -> str | None:
    """Queue the first step of the chain nobody queued yet, when nothing of the video's chain
    is waiting or running (a failed step is not tried again: that is a coach's "Analyse" or
    the CLI). Returns the job kind queued, or None."""
    from wieplay.core import actions, analysis, calibration, players

    settings = settings or get_settings()
    if video.status != "ready" or video.sport not in SPORTS or video.meets_standard is False:
        return None
    if not video.files or any(not f.nas_path for f in video.files):
        return None
    states = _job_states(db, [video.id])[video.id]
    if any(state in ("queued", "running") for state in states.values()):
        return None
    if "analyse" not in states:
        if not settings.analysis_auto:
            return None
        analysis.queue_analyse(db, video)
        return "analyse"
    if states["analyse"] != "done":
        return None
    out_dir = analysis.analysis_subdir(db, video)
    if "trajectory" not in states:
        return "trajectory" if calibration.queue_trajectory(db, video, out_dir) else None
    if "court" not in states:
        return "court" if calibration.queue_court(db, video, out_dir) else None
    if "actions" not in states:
        return "actions" if actions.queue_actions(db, video, reason="resume") else None
    if "players" not in states and settings.players_auto and players.can_run(video) is None:
        players.queue_players(db, video)
        return "players"
    return None


def resume_all(db: Session, limit: int = 5, settings: Settings | None = None) -> list[str]:
    """`resume` over the ready videos, the oldest first, at most `limit` jobs a run (a
    backlog drains over the hours instead of flooding the queue). Returns "video:kind"."""
    done: list[str] = []
    videos = db.scalars(
        select(Video)
        .where(Video.status == "ready", Video.sport.in_(SPORTS), Video.deleted_at.is_(None))
        .order_by(Video.recorded_on, Video.id)
    )
    for video in videos:
        if len(done) >= limit:
            break
        kind = resume(db, video, settings)
        if kind is not None:
            done.append(f"{video.id}:{kind}")
    return done
