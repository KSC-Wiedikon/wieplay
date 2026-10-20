"""Action jobs (PLAN §10.4a): `actions` (the suggested actions of one video) and
`train_actions` (the proposer learns from every game whose actions a coach marked done).

Seconds of CPU each, on the worker's background lane next to the analyses. They read the
video's signals from the NAS and ask the API for the rest (the rallies with their sides,
the court, the current model): workers hold no database access.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from pathlib import Path
from typing import Any

from wieplay.ml import action_eval
from wieplay.ml import actions as ac
from wieplay.ml import contact_pose as cp
from wieplay.ml.extract import lower_thread_priority
from wieplay.ml.people import WEIGHTS_DIR
from wieplay.worker import ffmpeg
from wieplay.worker.analyse import _artifact, _out_dir, _root, _write, code_version, load_signals
from wieplay.worker.handlers import Context, PermanentError, inside

log = logging.getLogger(__name__)
MODEL = "wieplay-actions"
TRAIN_MODEL = "actions-v1-hgb"


def _rallies(entry: dict[str, Any]) -> list[ac.RallyIn]:
    return [
        ac.RallyIn(
            id=str(r["id"]),
            start_ms=int(r["start_ms"]),
            end_ms=int(r["end_ms"]),
            serve_near=None if r.get("serve_near") is None else float(r["serve_near"]),
            won_near=None if r.get("won_near") is None else float(r["won_near"]),
            serve_sure=bool(r.get("serve_sure")),
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
                "sure": a.sure,
                "alternative": a.alternative or None,
                "ball_x": round(a.x, 4),
                "ball_y": round(a.y, 4),
                "court_x": a.court[0] if a.court else None,
                "court_y": a.court[1] if a.court else None,
            }
        )
    return out


# --- the player at the ball (wieplay/ml/contact_pose.py) -------------------------------------


def _parts(ctx: Context, found: dict[str, Any]) -> list[dict[str, Any]]:
    """The camera files of the video, each confined to the raw originals on the NAS."""
    out = []
    for part in found.get("parts") or []:
        try:
            path = inside(ctx.settings.raw_root, ctx.settings.raw_root / str(part["path"]))
        except (PermanentError, KeyError):
            return []
        if not path.is_file():
            return []
        out.append({**part, "path": path})
    return out


def _cache_path(root: Path, features: dict[str, Any]) -> Path:
    """Next to the signals: the cue depends on them (the candidates) and the pose weights."""
    signals = inside(root, root / features["nas_path"])
    return signals.with_name(f"contact-pose-{str(features['sha256'])[:16]}.json.gz")


def _read_cache(path: Path, weights: str) -> dict[int, list[float] | None]:
    try:
        data = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, ValueError):
        return {}
    if data.get("weights") != weights or data.get("crop") != cp.CROP:
        return {}
    return {int(f): v for f, v in data.get("frames", {}).items()}


def pose_cues(
    ctx: Context, found: dict[str, Any], va: ac.VideoActions, rallies: list[ac.RallyIn]
) -> dict[str, Any]:
    """The player-at-the-ball cue of every candidate in a rally (the serve estimates have
    none): read from the cache next to the signals, the rest from the video's frames. Off,
    or without weights or the camera files, the finder goes on with the ball's path alone."""
    if not ctx.settings.actions_pose:
        return {"pose": "off"}
    weights = (ctx.settings.players_weights or WEIGHTS_DIR) / cp.POSE_FILE
    if not weights.is_file():
        return {"pose": "no weights"}
    parts = _parts(ctx, found)
    if not parts:
        return {"pose": "no camera files"}
    import cv2

    started = time.perf_counter()
    root = _root(ctx)
    model = cp.PoseModel(weights, threads=ctx.settings.analysis_threads)
    cache_path = _cache_path(root, found["features"])
    cache = _read_cache(cache_path, model.sha256)
    wanted: dict[int, ac.Candidate] = {}
    for rally in rallies:
        for c in ac.in_rally(va.candidates, rally, va.fps):
            if c.kind < ac.TOSS:
                wanted[c.frame] = c
    todo = sorted(f for f in wanted if f not in cache)
    done = 0
    for part in parts:
        offset = int(part.get("offset_ms") or 0) / 1000
        length = int(part.get("duration_ms") or 0) / 1000
        frames = [
            f for f in todo if f / va.fps >= offset and (not length or f / va.fps < offset + length)
        ]
        if not frames:
            continue
        cap = cv2.VideoCapture(str(part["path"]))
        pos: float | None = None
        try:
            for f in frames:
                if ctx.cancel.is_set():
                    raise ffmpeg.Cancelled()
                t = f / va.fps - offset
                if pos is None or t < pos or t - pos > 3.0:
                    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t * 1000 - 40))
                img = None
                while cap.grab():
                    pos = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
                    if pos + 1e-3 >= t:
                        _, img = cap.retrieve()
                        break
                if img is None:
                    break
                c = wanted[f]
                height, width = img.shape[:2]
                x0, y0 = cp.crop_origin(c.x, c.y, width, height)
                crop = img[y0 : y0 + cp.CROP, x0 : x0 + cp.CROP, ::-1]
                if crop.shape[0] != cp.CROP or crop.shape[1] != cp.CROP:
                    cache[f] = None
                    continue
                people = cp.in_frame(model.people(crop), x0, y0)
                found_at = cp.at_ball(people, c.x * width, c.y * height)
                cache[f] = found_at.as_list() if found_at is not None else None
                done += 1
                if done % 50 == 0:
                    ctx.report(0.4 + 0.35 * done / max(len(todo), 1), "the players at the ball")
        finally:
            cap.release()
    if done:
        payload = {
            "weights": model.sha256,
            "crop": cp.CROP,
            "frames": {str(f): v for f, v in cache.items()},
        }
        _write(cache_path, data=gzip.compress(json.dumps(payload).encode()))
    seen = 0
    for f, c in wanted.items():
        value = cache.get(f)
        c.pose = cp.AtBall.of(value).llr() if value is not None else None
        seen += value is not None
    return {
        "pose": cp.POSE_NAME,
        "pose_weights_sha256": model.sha256,
        "pose_candidates": len(wanted),
        "pose_seen": seen,
        "pose_new": done,
        "pose_s": round(time.perf_counter() - started, 1),
    }


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
    rallies = _rallies(found)
    try:
        pose = pose_cues(ctx, found, va, rallies)
    except (OSError, RuntimeError, ValueError) as exc:  # the cue is a help, never a blocker
        log.warning("no player-at-the-ball cue: %s", exc)
        pose = {"pose": f"failed: {str(exc)[:200]}"}
    ctx.report(0.8, "labelling the rallies")
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
            "sure": sum(1 for a in actions if a.sure),
            **pose,
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
