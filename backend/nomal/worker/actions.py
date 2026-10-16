"""Action jobs (PLAN §10.4a): `actions` (the suggested actions of one video) and
`train_actions` (the proposer learns from every game whose actions a coach marked done).

Seconds of CPU each, on the worker's background lane next to the analyses. They read the
video's signals from the NAS and ask the API for the rest (the rallies with their sides,
the court, the current model): workers hold no database access.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from nomal.ml import action_eval
from nomal.ml import actions as ac
from nomal.ml.extract import lower_thread_priority
from nomal.worker import ffmpeg
from nomal.worker.analyse import _artifact, _out_dir, _root, _write, code_version, load_signals
from nomal.worker.handlers import Context, PermanentError, inside

log = logging.getLogger(__name__)
MODEL = "nomal-actions"
TRAIN_MODEL = "actions-v1-hgb"


def _rallies(entry: dict[str, Any]) -> list[ac.RallyIn]:
    return [
        ac.RallyIn(
            id=str(r["id"]),
            start_ms=int(r["start_ms"]),
            end_ms=int(r["end_ms"]),
            serve_near=None if r.get("serve_near") is None else float(r["serve_near"]),
            won_near=None if r.get("won_near") is None else float(r["won_near"]),
        )
        for r in entry.get("rallies", [])
    ]


def load_model(
    model: dict[str, Any] | None, root: Path
) -> tuple[ac.ActionModel | None, str | None]:
    """The accepted v1 model when there is one and it loads, else None (the rules)."""
    if not model:
        return None, None
    try:
        found = ac.ActionModel.load(inside(root, root / model["nas_path"]), model["sha256"])
        return found, str(model["model_run_id"])
    except (OSError, ValueError, KeyError, PermanentError) as exc:
        log.warning("action model unusable (%s); using the rules", exc)
        return None, None


def _precision(analysis_fps: float, video_fps: float) -> str:
    """Frame-precise when the analysis saw every frame of the video, else adjusted."""
    if video_fps > 0 and abs(analysis_fps - video_fps) < 0.5:
        return "frame"
    return "adjusted"


def to_payload(
    actions: list[ac.Action], analysis_fps: float, video_fps: float
) -> list[dict[str, Any]]:
    precision = _precision(analysis_fps, video_fps)
    out = []
    for a in actions:
        t_s = a.frame / analysis_fps
        out.append(
            {
                "rally_id": a.rally_id,
                "t_ms": round(t_s * 1000),
                "frame": round(t_s * video_fps) if video_fps > 0 else None,
                "time_precision": "adjusted" if a.gap else precision,
                "side": a.side,
                "code": a.code,
                "grade": a.grade,
                "confidence": a.confidence,
                "ball_x": round(a.x, 4),
                "ball_y": round(a.y, 4),
                "court_x": a.court[0] if a.court else None,
                "court_y": a.court[1] if a.court else None,
            }
        )
    return out


def run(ctx: Context) -> dict[str, Any]:
    lower_thread_priority()
    started = time.perf_counter()
    root = _root(ctx)
    found = ctx.api.inputs(ctx.job.id)
    ctx.report(0.1, "reading the signals")
    sig = load_signals(root, found["features"])
    ctx.report(0.4, "finding the contacts")
    va = ac.prepare(sig, found.get("camera"), float(found.get("aspect") or 16 / 9))
    prepared_s = time.perf_counter() - started
    if ctx.cancel.is_set():
        raise ffmpeg.Cancelled()
    model, model_run = load_model(found.get("model"), root)
    ctx.report(0.8, "labelling the rallies")
    rallies = _rallies(found)
    actions = ac.propose(va, rallies, model)
    in_rallies = sum(len(v) for v in va.per_rally.values())
    return {
        "model": {
            "name": MODEL,
            "version": code_version(),
            "params": {
                "finder": "v1" if model else "v0",
                "calibration": found.get("calibration"),
                "tolerance_ms": action_eval.TOLERANCE_MS,
            },
        },
        "finder": "v1" if model else "v0",
        "model_run_id": model_run,
        "actions": to_payload(actions, sig.fps, float(found.get("fps") or 0)),
        "stats": {
            "candidates": len(va.candidates),
            "candidates_in_rallies": in_rallies,
            "rallies": len(rallies),
            "per_rally": round(len(actions) / len(rallies), 2) if rallies else None,
            "calibrated": va.view.calibrated,
        },
        "timing": {
            "wall_s": round(time.perf_counter() - started, 2),
            "prepare_s": round(prepared_s, 2),
        },
    }


def train(ctx: Context) -> dict[str, Any]:
    """Learn v1 from every reviewed game, score v0 and v1 on held-out games, keep v1 when
    its mean action F1 beats v0's."""
    import sklearn

    lower_thread_priority()
    started = time.perf_counter()
    root, out = _out_dir(ctx)
    found = ctx.api.inputs(ctx.job.id)
    threads = ctx.settings.analysis_threads
    videos: list[ac.TrainVideo] = []
    dataset: list[dict[str, Any]] = []
    entries = found.get("train", [])
    for n, entry in enumerate(entries):
        ctx.report(0.4 * n / max(len(entries), 1), "loading reviewed games")
        if ctx.cancel.is_set():
            raise ffmpeg.Cancelled()
        try:
            sig = load_signals(root, entry["features"])
        except (OSError, ValueError, PermanentError) as exc:
            log.warning("skipping %s: %s", entry["video_id"], exc)
            continue
        va = ac.prepare(sig, entry.get("camera"), float(entry.get("aspect") or 16 / 9))
        events = [(int(t), str(c), str(s), str(r)) for t, c, s, r in entry["events"]]
        videos.append(ac.TrainVideo(entry["video_id"], va, _rallies(entry), events))
        dataset.append(
            {"video_id": entry["video_id"], "features_sha256": entry["features"]["sha256"],
             "tags": len(events), "rallies": len({e[3] for e in events})}
        )  # fmt: skip
    ctx.report(0.45, "held-out games")
    evaluation = ac.cross_validate(
        videos, threads, progress=lambda f: ctx.report(0.45 + 0.4 * f, "held-out games")
    )
    if ctx.cancel.is_set():
        raise ffmpeg.Cancelled()
    accepted = bool(evaluation["v1_better"])
    artifact = None
    model = ac.fit(videos, threads) if len(videos) >= 2 else None
    if model is None:
        accepted = False
    else:
        ctx.report(0.9, "training on every reviewed game")
        target = out / f"actions-v1-{ctx.job.id[:8]}.pkl"
        _write(target, data=model.dumps())
        artifact = _artifact(root, target, "pickle", {"games": len(videos)})
        artifact["schema_version"] = ac.MODEL_FORMAT
    return {
        "model": {
            "name": TRAIN_MODEL,
            "version": code_version(),
            "params": {"features": ac.FEATURES, "sklearn": sklearn.__version__},
        },
        "artifact": artifact,
        "accepted": accepted,
        "timing": {
            "wall_s": round(time.perf_counter() - started, 1),
            "games": len(videos),
            "threads": threads,
        },
        "evaluation": {"summary": evaluation["summary"], "videos": evaluation["videos"]},
        "dataset": dataset,
    }
