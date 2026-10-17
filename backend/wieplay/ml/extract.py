"""Decode a video once and compute its signals: ball, motion, audio (the analyse job).

    python -m wieplay.ml.extract MEDIA [MEDIA…] OUT.npz [--threads 6] [--hwaccel vaapi]

The input is the NAS originals, joined like the transcode joins them (split camera files,
PLAN §6.4), not the 1080p rendition: the originals are on the worker's NAS mount while the
renditions are in object storage, and the rendition is capped at 50 fps, so a 60 fps camera
would reach the model unevenly resampled. ffmpeg picks every n-th frame (60 → 30, 50 → 25)
and scales to 512x288 RGB, on the iGPU with VAAPI when it works, else on the CPU.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import numpy as np

from wieplay.ml import ball
from wieplay.ml.features import (
    GRID_COLS,
    GRID_ROWS,
    HEIGHT,
    SR,
    WIDTH,
    Arr,
    AudioFrames,
    Signals,
    detect_whistles,
)
from wieplay.worker.ffmpeg import Cancelled, FfmpegError, concat_list, input_guard

log = logging.getLogger(__name__)

TARGET_FPS = 30.0
FRAME_BYTES = WIDTH * HEIGHT * 3
BATCH_GROUPS = 8  # 24 frames per inference call
AUDIO_CHUNK_S = 30
NICE = 15  # below the transcodes (nice 10): analysis never slows playback down

Progress = Callable[[float, str], None]


def analysis_fps(source_fps: float) -> float:
    """Every n-th frame, so the frames stay evenly spaced: 60 → 30, 50 → 25, 25 → 25."""
    if source_fps <= 0:
        return TARGET_FPS
    return source_fps / max(1, round(source_fps / TARGET_FPS))


def _nice() -> None:
    os.nice(NICE)


def lower_thread_priority() -> None:
    """Lower this thread's priority (Linux: nice is per thread; threads it starts, such as
    ONNX Runtime's pool, inherit it)."""
    with contextlib.suppress(OSError, AttributeError):
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), NICE)


def video_args(concat: Path, fps: float, *, hwaccel: str, device: str, threads: int) -> list[str]:
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-threads", str(threads)]
    if hwaccel == "vaapi":
        args += ["-hwaccel", "vaapi", "-hwaccel_device", device, "-hwaccel_output_format", "vaapi"]
        scale = f"scale_vaapi=w={WIDTH}:h={HEIGHT}:format=nv12,hwdownload,format=nv12"
    else:
        scale = f"scale={WIDTH}:{HEIGHT}:flags=area"
    args += [*input_guard(concat=True), "-f", "concat", "-safe", "0", "-i", str(concat)]
    args += ["-map", "0:v:0", "-an", "-sn", "-vf", f"fps={fps:.6g},{scale}"]
    args += ["-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    return args


def audio_args(concat: Path) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
        *input_guard(concat=True), "-f", "concat", "-safe", "0", "-i", str(concat),
        "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1",
    ]  # fmt: skip


class _Pipe:
    """An ffmpeg process whose stdout we read; stderr kept for the error message."""

    def __init__(self, args: list[str]) -> None:
        self.proc = subprocess.Popen(  # noqa: S603 - argument list, ffmpeg from PATH
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=_nice
        )
        self.tail: deque[str] = deque(maxlen=20)
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.tail.append(line.decode(errors="replace").rstrip())

    @property
    def stdout(self) -> IO[bytes]:
        assert self.proc.stdout is not None
        return self.proc.stdout

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()

    def finish(self) -> int:
        code = self.proc.wait()
        self._reader.join(timeout=5)
        return code


def _read_exact(stream: IO[bytes], size: int) -> bytes:
    chunks, got = [], 0
    while got < size:
        chunk = stream.read(size - got)
        if not chunk:
            break
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def read_audio(
    concat: Path, duration_s: float, cancel: threading.Event, progress: Progress
) -> AudioFrames:
    frames = AudioFrames()
    pipe = _Pipe(audio_args(concat))
    chunk = AUDIO_CHUNK_S * SR * 4
    done = 0.0
    try:
        while data := _read_exact(pipe.stdout, chunk):
            if cancel.is_set():
                raise Cancelled()
            frames.feed(np.frombuffer(data[: len(data) // 4 * 4], dtype=np.float32))
            done += len(data) / 4 / SR
            progress(min(1.0, done / max(duration_s, 1.0)), "listening for whistles")
    except BaseException:
        pipe.stop()
        raise
    if pipe.finish() != 0:
        raise FfmpegError("audio decode failed: " + " | ".join(list(pipe.tail)[-4:]))
    return frames


def _batches(pipe: _Pipe, frames_per_batch: int, cancel: threading.Event) -> Iterator[Arr]:
    """Batches of frames, read ahead in a thread so decoding overlaps the model."""
    out: queue.Queue[Arr | BaseException | None] = queue.Queue(maxsize=4)

    def reader() -> None:
        try:
            while not cancel.is_set():
                data = _read_exact(pipe.stdout, frames_per_batch * FRAME_BYTES)
                n = len(data) // FRAME_BYTES
                if n == 0:
                    break
                out.put(
                    np.frombuffer(data[: n * FRAME_BYTES], np.uint8).reshape(n, HEIGHT, WIDTH, 3)
                )
        except BaseException as exc:  # handed to the consumer
            out.put(exc)
        out.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    while True:
        item = out.get()
        if item is None:
            break
        if isinstance(item, BaseException):
            raise item
        yield item
    thread.join(timeout=5)


def motion_grid(frames: Arr, previous: Arr | None) -> tuple[Arr, Arr]:
    """Mean |Δgrey| per grid cell against the previous frame, on every 4th pixel (128x72:
    enough for how much moves, and cheap next to the model). Returns (n, GRID_ROWS,
    GRID_COLS) and the last grey frame."""
    n = len(frames)
    small = frames[:, ::4, ::4, :].astype(np.float32)
    grey = small @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    before = np.concatenate([grey[:1] if previous is None else previous[None], grey[:-1]])
    diff = np.abs(grey - before)
    h, w = diff.shape[1] // GRID_ROWS, diff.shape[2] // GRID_COLS
    cells = diff[:, : h * GRID_ROWS, : w * GRID_COLS].reshape(n, GRID_ROWS, h, GRID_COLS, w)
    return cells.mean(axis=(2, 4)), grey[-1]


@dataclass
class VideoPass:
    vis: list[Arr] = field(default_factory=list)
    x: list[Arr] = field(default_factory=list)
    y: list[Arr] = field(default_factory=list)
    score: list[Arr] = field(default_factory=list)
    motion: list[Arr] = field(default_factory=list)
    frames: int = 0
    infer_s: float = 0.0


def read_video(
    concat: Path,
    fps: float,
    expected_frames: int,
    tracker: ball.BallTracker,
    *,
    hwaccel: str,
    device: str,
    threads: int,
    cancel: threading.Event,
    progress: Progress,
) -> VideoPass:
    result = VideoPass()
    pipe = _Pipe(video_args(concat, fps, hwaccel=hwaccel, device=device, threads=threads))
    previous: Arr | None = None
    try:
        for frames in _batches(pipe, ball.GROUP * BATCH_GROUPS, cancel):
            if cancel.is_set():
                raise Cancelled()
            t = time.perf_counter()
            det = tracker.detect(frames)
            result.infer_s += time.perf_counter() - t
            grid, previous = motion_grid(frames, previous)
            result.vis.append(det.vis)
            result.x.append(det.x)
            result.y.append(det.y)
            result.score.append(det.score)
            result.motion.append(grid.astype(np.float16))
            result.frames += len(frames)
            progress(min(1.0, result.frames / max(expected_frames, 1)), "finding the ball")
        if cancel.is_set():
            raise Cancelled()
    except BaseException:
        pipe.stop()
        raise
    code = pipe.finish()
    if code != 0:
        raise FfmpegError(f"video decode ({hwaccel}) failed: " + " | ".join(list(pipe.tail)[-4:]))
    return result


def extract(
    inputs: Sequence[Path],
    *,
    work: Path,
    source_fps: float,
    duration_ms: int,
    has_audio: bool,
    cancel: threading.Event,
    progress: Progress,
    threads: int = 4,
    hwaccel: str = "vaapi",
    device: str = "/dev/dri/renderD128",
) -> tuple[Signals, dict[str, Any]]:
    """The signals of one video and how long each part took."""
    lower_thread_priority()
    started = time.perf_counter()
    work.mkdir(parents=True, exist_ok=True)
    concat = concat_list([str(p) for p in inputs], work / "analyse.ffconcat")
    duration_s = duration_ms / 1000
    fps = analysis_fps(source_fps)

    t = time.perf_counter()
    if has_audio:
        audio = read_audio(concat, duration_s, cancel, lambda p, m: progress(0.1 * p, m))
        band_db, all_db, tonal_db, peak_hz = audio.finish()
    else:
        band_db = all_db = tonal_db = np.zeros(0, np.float32)
        peak_hz = np.zeros(0, np.uint16)
    whistles = detect_whistles(band_db, all_db, tonal_db, peak_hz)
    audio_s = time.perf_counter() - t

    t = time.perf_counter()
    tracker = ball.BallTracker(threads=threads)
    expected = round(duration_s * fps)

    def video_progress(p: float, message: str) -> None:
        progress(0.1 + 0.88 * p, message)

    used = hwaccel
    try:
        result = read_video(
            concat, fps, expected, tracker, hwaccel=hwaccel, device=device, threads=threads,
            cancel=cancel, progress=video_progress,
        )  # fmt: skip
    except FfmpegError:
        if hwaccel == "none":
            raise
        log.warning("VAAPI decode failed; decoding on the CPU")
        used = "none"
        result = read_video(
            concat, fps, expected, tracker, hwaccel="none", device=device, threads=threads,
            cancel=cancel, progress=video_progress,
        )  # fmt: skip
    video_s = time.perf_counter() - t

    def cat(parts: list[Arr], dtype: Any, shape: tuple[int, ...] = ()) -> Arr:
        return np.concatenate(parts).astype(dtype) if parts else np.zeros((0, *shape), dtype)

    signals = Signals(
        fps=fps,
        duration_s=duration_s,
        ball_vis=cat(result.vis, np.uint8),
        ball_x=cat(result.x, np.float32),
        ball_y=cat(result.y, np.float32),
        ball_score=cat(result.score, np.float16),
        motion=cat(result.motion, np.float16, (GRID_ROWS, GRID_COLS)),
        band_db=band_db,
        all_db=all_db,
        tonal_db=tonal_db,
        peak_hz=peak_hz,
        whistles=whistles,
        meta={
            "ball_model": ball.MODEL_NAME,
            "ball_weights_sha256": ball.WEIGHTS_SHA256,
            "source_fps": source_fps,
            "hwaccel": used,
        },
    )
    wall = time.perf_counter() - started
    timing = {
        "wall_s": round(wall, 1),
        "audio_s": round(audio_s, 1),
        "video_s": round(video_s, 1),
        "infer_s": round(result.infer_s, 1),
        "frames": result.frames,
        "frames_per_s": round(result.frames / video_s, 1) if video_s else None,
        "hwaccel": used,
        "threads": threads,
    }
    return signals, timing


def summary(sig: Signals) -> dict[str, Any]:
    """A few numbers for the model run and the admin page."""
    from wieplay.ml.features import ball_tracks

    tracks = ball_tracks(sig)
    n = max(sig.frames, 1)
    minutes = max(sig.duration_s / 60, 1e-6)
    hz = sig.whistles[:, 2] if len(sig.whistles) else np.zeros(0)
    return {
        "frames": sig.frames,
        "fps": round(sig.fps, 3),
        "ball_detected": round(float(sig.ball_vis.sum()) / n, 4),
        "ball_in_track": round(float(tracks.in_track.sum()) / n, 4),
        "ball_stuck": round(float(tracks.stuck.sum()) / n, 4),
        "whistles": len(sig.whistles),
        "whistles_per_min": round(len(sig.whistles) / minutes, 2),
        "whistle_median_hz": int(np.median(hz)) if len(hz) else None,
        "has_audio": sig.has_audio,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("media", nargs="+", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--hwaccel", choices=["vaapi", "none"], default="vaapi")
    ap.add_argument("--device", default="/dev/dri/renderD128")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from wieplay.worker.ffmpeg import probe

    probes = [probe(p) for p in a.media]
    last = [0.0]

    def report(p: float, message: str) -> None:
        if p - last[0] >= 0.02:
            last[0] = p
            log.info("%3.0f%% %s", 100 * p, message)

    sig, timing = extract(
        a.media,
        work=a.out.parent / (a.out.stem + ".work"),
        source_fps=float(probes[0].fps),
        duration_ms=sum(p.duration_ms for p in probes),
        has_audio=all(p.has_audio for p in probes),
        cancel=threading.Event(),
        progress=report,
        threads=a.threads,
        hwaccel=a.hwaccel,
        device=a.device,
    )
    sig.save(a.out)
    print(json.dumps({"timing": timing, "summary": summary(sig)}))


if __name__ == "__main__":
    main()
