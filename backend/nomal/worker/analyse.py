"""Analysis job handlers (PLAN §10.1): `analyse` (signals and rallies of one video) and
`train_rallies` (the rally finder learns from every reviewed game).

Both run on the media worker's background lane (runner.py), at low CPU priority, and
write under the analysis root on the NAS (NOMAL_WORKER_ANALYSIS_ROOT). Imported only
when such a job runs: the API image has no numpy.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from nomal import __version__
from nomal.ml import ball, rallies, serve, tracks
from nomal.ml.extract import extract, lower_thread_priority, summary
from nomal.ml.features import HEIGHT, SCHEMA_VERSION, WIDTH, Signals, ball_tracks
from nomal.worker import ffmpeg
from nomal.worker.handlers import Context, PermanentError, inside, raw_inputs

log = logging.getLogger(__name__)
SIGNALS_MODEL = f"nomal-signals/{ball.MODEL_NAME}"
RALLY_MODEL = "rallies-v1-hgb"
SERVE_MODEL = "serve-v1-hgb"


def code_version() -> str:
    """The git sha of the running code: NOMAL_GIT_SHA (set from the image tag in
    deploy/lenovo/compose.yaml), else the checkout's HEAD, else the package version."""
    if sha := os.environ.get("NOMAL_GIT_SHA"):
        return sha[:64]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],  # noqa: S607 - git from PATH
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return __version__


def _root(ctx: Context) -> Path:
    root = ctx.settings.analysis_root
    if root is None:
        raise PermanentError("this worker has no analysis root (NOMAL_WORKER_ANALYSIS_ROOT)")
    return root


def _out_dir(ctx: Context) -> tuple[Path, Path]:
    root = _root(ctx)
    out = inside(root, root / ctx.job.params["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    return root, out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, data: bytes | None = None, signals: Signals | None = None) -> None:
    """Write next to the target and rename: a reader never sees half a file."""
    partial = path.with_name(path.name + ".part")
    if signals is not None:
        signals.save(partial)
    else:
        assert data is not None
        partial.write_bytes(data)
    os.replace(partial, path)


def _artifact(root: Path, path: Path, fmt: str, stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "nas_path": str(path.relative_to(root.resolve())),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "format": fmt,
        "schema_version": SCHEMA_VERSION if fmt == "npz" else rallies.MODEL_FORMAT,
        "stats": stats,
    }


def load_signals(root: Path, ref: dict[str, Any]) -> Signals:
    path = inside(root, root / ref["nas_path"])
    if _sha256(path) != ref["sha256"]:
        raise ValueError(f"{path.name}: SHA-256 does not match the artifact")
    return Signals.load(path)


def _proposals(found: list[rallies.Proposal]) -> list[dict[str, Any]]:
    return [{"start_ms": p.start_ms, "end_ms": p.end_ms, "confidence": p.confidence} for p in found]


def find_rallies(
    sig: Signals, model: dict[str, Any] | None, root: Path
) -> tuple[list[rallies.Proposal], str, str | None]:
    """The current finder: the accepted v1 model when there is one and it loads, else v0."""
    if model:
        try:
            v1 = rallies.RallyModel.load(inside(root, root / model["nas_path"]), model["sha256"])
            return v1.predict(sig), "v1", str(model["model_run_id"])
        except (OSError, ValueError, KeyError, PermanentError) as exc:
            log.warning("rally model unusable (%s); using v0", exc)
    return rallies.find_v0(sig), "v0", None


def serve_finder(
    model: dict[str, Any] | None, root: Path
) -> tuple[serve.ServeModel | None, str, str | None]:
    """The current serve finder: the accepted v1 model when there is one and it loads, else
    the rule (v0)."""
    if model:
        try:
            v1 = serve.ServeModel.load(inside(root, root / model["nas_path"]), model["sha256"])
            return v1, "v1", str(model["model_run_id"])
        except (OSError, ValueError, KeyError, PermanentError) as exc:
            log.warning("serve model unusable (%s); using the rule", exc)
    return None, "v0", None


def series(
    sig: Signals,
    finder: tuple[serve.ServeModel | None, str, str | None],
    kinds: tuple[str, ...] = ("serve", "activity"),
) -> dict[str, Any]:
    """The small series the API scores with (nomal/ml/tracks.py)."""
    trk = ball_tracks(sig)
    out: dict[str, Any] = {}
    if "serve" in kinds:
        model, name, run_id = finder
        data = serve.serve_track(sig, model, serve.evidence(sig, trk))
        out["serve"] = {
            "step_ms": tracks.SERVE_STEP_MS,
            "finder": name,
            "model_run_id": run_id,
            "channels": {"p_near": tracks.encode(data)},
        }
    if "activity" in kinds:
        act = serve.activity_track(sig, trk)
        out["activity"] = {
            "step_ms": tracks.ACTIVITY_STEP_MS,
            "channels": {name: tracks.encode(data) for name, data in act.items()},
        }
    return out


def analyse(ctx: Context) -> dict[str, Any]:
    """One video: decode the NAS originals once, find the ball in every frame, the motion
    and the whistles; store the signals; find the rallies with the current finder."""
    lower_thread_priority()
    params = ctx.job.params
    root, out = _out_dir(ctx)
    inputs = [Path(p) for p in raw_inputs(ctx)]
    found_inputs = ctx.api.inputs(ctx.job.id)
    current = found_inputs.get("rally_model")
    settings = ctx.settings
    sig, timing = extract(
        inputs,
        work=ctx.scratch,
        source_fps=float(params.get("fps") or 0),
        duration_ms=int(params["duration_ms"]),
        has_audio=bool(params.get("has_audio", True)),
        cancel=ctx.cancel,
        progress=ctx.report,
        threads=settings.analysis_threads,
        hwaccel=settings.analysis_hwaccel,
        device=settings.vaapi_device,
    )
    if ctx.cancel.is_set():
        raise ffmpeg.Cancelled()
    target = out / f"signals-{ctx.job.id[:8]}.npz"
    _write(target, signals=sig)
    ctx.report(0.99, "finding rallies")
    found, finder, model_run = find_rallies(sig, current, root)
    found_series = series(sig, serve_finder(found_inputs.get("serve_model"), root))
    return {
        "artifact": _artifact(root, target, "npz", summary(sig)),
        "model": {
            "name": SIGNALS_MODEL,
            "version": code_version(),
            "weights_sha256": ball.WEIGHTS_SHA256,
            "params": {
                "analysis_fps": sig.fps,
                "input": f"{WIDTH}x{HEIGHT}",
                "threshold": ball.THRESHOLD,
                "v0": rallies.V0Params().as_dict(),
            },
        },
        "timing": timing,
        "rallies": {"finder": finder, "model_run_id": model_run, "proposals": _proposals(found)},
        "tracks": found_series,
    }


def train_rallies(ctx: Context) -> dict[str, Any]:
    """Learn v1 from every reviewed game, score v0 and v1 on held-out games, and when v1 is
    better propose its rallies for every game nobody has done yet."""
    import sklearn

    lower_thread_priority()
    started = time.perf_counter()
    root, out = _out_dir(ctx)
    found = ctx.api.inputs(ctx.job.id)
    threads = ctx.settings.analysis_threads
    videos: list[rallies.TrainVideo] = []
    serve_videos: list[serve.ServeVideo] = []
    loaded: dict[str, Signals] = {}
    dataset: list[dict[str, Any]] = []
    for n, entry in enumerate(found.get("train", [])):
        ctx.report(0.1 * n / max(len(found["train"]), 1), "loading reviewed games")
        try:
            sig = load_signals(root, entry["features"])
        except (OSError, ValueError, PermanentError) as exc:
            log.warning("skipping %s: %s", entry["video_id"], exc)
            continue
        spans = [(int(s), int(e)) for s, e in entry["rallies"]]
        videos.append(rallies.TrainVideo(entry["video_id"], sig, spans))
        serves = [(int(t), bool(near)) for t, near in entry.get("serves", [])]
        serve_videos.append(serve.ServeVideo(entry["video_id"], serve.evidence(sig), serves))
        loaded[entry["video_id"]] = sig
        dataset.append(
            {"video_id": entry["video_id"], "features_sha256": entry["features"]["sha256"],
             "rallies": len(spans), "serves": len(serves)}
        )  # fmt: skip
    if ctx.cancel.is_set():
        raise ffmpeg.Cancelled()

    p = rallies.V1Params()
    evaluation = rallies.cross_validate(
        videos, p, threads=threads, progress=lambda f: ctx.report(0.1 + 0.6 * f, "held-out games"),
        cancel=ctx.cancel,
    )  # fmt: skip
    if ctx.cancel.is_set():
        raise ffmpeg.Cancelled()
    accepted = bool(evaluation["v1_better"])
    artifact = None
    model = None
    if len(videos) >= 2:
        ctx.report(0.75, "training on every reviewed game")
        model = rallies.fit(videos, p, threads=threads)
        target = out / f"rally-v1-{ctx.job.id[:8]}.pkl"
        _write(target, data=model.dumps())
        artifact = _artifact(
            root, target, "pickle", {"games": len(videos), "features": len(model.names)}
        )
    ctx.report(0.78, "the serve model")
    serve_eval = serve.cross_validate(serve_videos, threads=threads)
    serve_accepted = bool(serve_eval["v1_better"])
    serve_artifact = None
    serve_model = serve.fit(serve_videos, threads=threads) if serve_accepted else None
    if serve_model is None:
        serve_accepted = False
    else:
        target = out / f"serve-v1-{ctx.job.id[:8]}.pkl"
        _write(target, data=serve_model.dumps())
        serve_artifact = _artifact(root, target, "pickle", {"games": len(serve_videos)})
    finder = (
        (serve_model, "v1", None)
        if serve_model is not None
        else serve_finder(found.get("serve_model"), root)
    )

    predictions: dict[str, Any] = {}
    new_series: dict[str, Any] = {}
    others = found.get("others", [])
    everyone = [*found.get("train", []), *others, *found.get("skipped", [])]
    for n, entry in enumerate(everyone):
        if ctx.cancel.is_set():
            raise ffmpeg.Cancelled()
        # Every game gets the new serve model's track; the others what they miss.
        kinds = tuple(
            k for k in ("serve", "activity")
            if (k == "serve" and serve_model is not None) or k not in entry.get("tracks", [])
        )  # fmt: skip
        propose = accepted and model is not None and entry in others
        if not kinds and not propose:
            continue
        ctx.report(0.8 + 0.19 * n / max(len(everyone), 1), "proposing rallies and serves")
        sig_found = loaded.get(entry["video_id"])
        if sig_found is None:
            try:
                sig_found = load_signals(root, entry["features"])
            except (OSError, ValueError, PermanentError) as exc:
                log.warning("skipping %s: %s", entry["video_id"], exc)
                continue
        if propose:
            assert model is not None
            predictions[entry["video_id"]] = {
                "finder": "v1",
                "proposals": _proposals(model.predict(sig_found)),
            }
        if kinds:
            new_series[entry["video_id"]] = series(sig_found, finder, kinds)
    return {
        "model": {
            "name": RALLY_MODEL,
            "version": code_version(),
            "params": {
                "v1": p.as_dict(),
                "v0": rallies.V0Params().as_dict(),
                "sklearn": sklearn.__version__,
                "features": model.names if model else [],
                "serve": {"name": SERVE_MODEL, "features": serve.FEATURES},
            },
        },
        "artifact": artifact,
        "accepted": accepted,
        "timing": {
            "wall_s": round(time.perf_counter() - started, 1),
            "games": len(videos),
            "proposed_for": len(predictions),
            "series_for": len(new_series),
            "threads": threads,
        },
        "evaluation": {"summary": evaluation["summary"], "videos": evaluation["videos"]},
        "dataset": dataset,
        "predictions": predictions,
        "serve": {
            "accepted": serve_accepted,
            "artifact": serve_artifact,
            "evaluation": {"summary": serve_eval["summary"], "videos": serve_eval["videos"]},
        },
        "tracks": new_series,
    }
