"""The trajectory job (PLAN §10.4, §10.6): the ball's path from a video's signals, and the
court found in its picture. Seconds of CPU; queued after each analyse job, on the worker's
background lane.

    signals (.npz on the NAS) ─▶ smoothed track, contacts, bounces ─▶ gzipped JSON on the
    NAS (provenance) and in the bucket (the browser's copy, served by the API)
    a few frames of the originals ─median─▶ the court's lines ─▶ a proposed calibration
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import time
from typing import Any

import httpx
import numpy as np

from nomal.ml import court_detect, trajectory
from nomal.ml.extract import NICE, lower_thread_priority
from nomal.worker import ffmpeg
from nomal.worker.analyse import _out_dir, _write, code_version, load_signals
from nomal.worker.handlers import Context, raw_inputs

log = logging.getLogger(__name__)
MODEL = "nomal-trajectory"
COURT_FRAMES = 15
COURT_SIZE = (1280, 720)


def _nice() -> None:
    os.nice(NICE)


def _grab(parts: ffmpeg.Parts, at_s: float, size: tuple[int, int]) -> np.ndarray | None:
    w, h = size
    source, offset = parts.locate(at_s)  # seek in the camera file, not the concat demuxer
    args = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
        "-ss", f"{offset:.2f}", *ffmpeg.input_guard(), "-i", str(source),
        "-frames:v", "1", "-an", "-vf", f"scale={w}:{h}:flags=area",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
    ]  # fmt: skip
    out = subprocess.run(  # noqa: S603 - argument list, ffmpeg from PATH
        args, capture_output=True, timeout=120, check=False, preexec_fn=_nice
    )
    if out.returncode != 0 or len(out.stdout) < w * h * 3:
        return None
    return np.frombuffer(out.stdout[: w * h * 3], np.uint8).reshape(h, w, 3)


def median_frame(ctx: Context, duration_ms: int, frames: int = COURT_FRAMES) -> np.ndarray | None:
    """The hall without its players: the median of frames spread over the video."""
    parts = ffmpeg.Parts(raw_inputs(ctx))
    duration_s = max(duration_ms / 1000, 1.0)
    grabbed = []
    for i in range(frames):
        if ctx.cancel.is_set():
            raise ffmpeg.Cancelled()
        at = duration_s * (0.08 + 0.84 * i / max(frames - 1, 1))
        frame = _grab(parts, at, COURT_SIZE)
        if frame is not None:
            grabbed.append(frame)
        ctx.report(0.4 + 0.4 * (i + 1) / frames, "finding the court")
    if len(grabbed) < 3:
        return None
    median: np.ndarray = np.median(np.stack(grabbed), axis=0).astype(np.uint8)
    return median


def _upload(ctx: Context, key: str, data: bytes) -> None:
    url = ctx.api.sign(ctx.job.id, "put", [key])[key]
    error = ""
    for _ in range(4):
        try:
            response = httpx.put(
                url,
                content=data,
                headers={"Content-Type": "application/gzip"},
                timeout=httpx.Timeout(60.0, connect=15.0),
            )
            if response.status_code in (200, 201, 204):
                return
            error = f"HTTP {response.status_code}"
        except httpx.TransportError as exc:
            error = str(exc)
    raise RuntimeError(f"upload of the ball track failed: {error}")


def run(ctx: Context) -> dict[str, Any]:
    lower_thread_priority()
    started = time.perf_counter()
    params = ctx.job.params
    root, out = _out_dir(ctx)
    sig = load_signals(root, params["features"])
    ctx.report(0.1, "smoothing the ball's path")
    track = trajectory.clean(sig)
    payload = trajectory.encode(track, duration_s=sig.duration_s)
    data = trajectory.to_bytes(payload)
    target = out / f"ball-track-{ctx.job.id[:8]}.json.gz"
    _write(target, data=data)
    track_s = time.perf_counter() - started
    ctx.report(0.3, "uploading the ball's path")
    _upload(ctx, params["track_key"], data)

    t = time.perf_counter()
    guess = None
    try:
        # Jobs queued since the court job exists (nomal/worker/court.py) leave the court to it.
        frame = (
            median_frame(ctx, int(params.get("duration_ms") or sig.duration_s * 1000))
            if params.get("court", True)
            else None
        )
        if frame is not None:
            ctx.report(0.85, "finding the court")
            guess = court_detect.detect(frame)
    except (ffmpeg.FfmpegError, OSError, subprocess.SubprocessError, ValueError) as exc:
        log.warning("court finder failed: %s", exc)
    court_s = time.perf_counter() - t
    if ctx.cancel.is_set():
        raise ffmpeg.Cancelled()
    return {
        "model": {
            "name": MODEL,
            "version": code_version(),
            "params": {
                "track_schema": trajectory.SCHEMA,
                "out_fps": trajectory.OUT_FPS,
                "max_gap_frames": trajectory.MAX_GAP,
                "turn_deg": trajectory.TURN_DEG,
                "court_frames": COURT_FRAMES,
                "court_size": list(COURT_SIZE),
            },
        },
        "track": {
            "nas_path": str(target.relative_to(root.resolve())),
            "key": params["track_key"],
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "schema_version": trajectory.SCHEMA,
            "stats": {**payload["stats"], "fps": payload["fps"]},
        },
        "court": (
            {
                "points": {k: list(v) for k, v in guess.points.items()},
                "confidence": guess.confidence,
                "stats": guess.stats,
            }
            if guess is not None
            else None
        ),
        "timing": {
            "wall_s": round(time.perf_counter() - started, 1),
            "track_s": round(track_s, 2),
            "court_s": round(court_s, 1),
        },
    }
