"""The `scoreboard` job (PLAN §10.3, step 5): read the score in the box a coach drew, twice a
second over the whole video, and smooth it into what the board showed and when.

CPU only, on the worker's background lane with the analyses. ffmpeg decodes the NAS originals
and crops the box (decoding is most of the time: a 2 h 1080p60 match is ~15 min with 6 threads);
nomal/ml/scoreboard.py reads each crop in about a millisecond; nomal/ml/board.py smooths.
The readings go to the NAS (npz, next to the video's signals), the segments to the API.

    python -m nomal.worker.scoreboard VIDEO X Y W H [--sets X Y W H] [--kind flip] [--from S --to S]

reads a local file outside the job system (for checks by eye; prints the segments).
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np

from nomal.ml import board as bd
from nomal.ml import scoreboard as sb
from nomal.ml.extract import _Pipe, _read_exact, lower_thread_priority
from nomal.worker import ffmpeg
from nomal.worker.analyse import _out_dir, _sha256, _write, code_version
from nomal.worker.handlers import Context, PermanentError, raw_inputs

log = logging.getLogger(__name__)
MODEL = "nomal-scoreboard"
SCHEMA = 1
EDGE = 0.002  # a box this close to the side of the picture reaches it


def crop_args(
    concat: Path,
    box: tuple[int, int, int, int],
    fps: float,
    *,
    threads: int,
    start_s: float | None = None,
    duration_s: float | None = None,
    concat_input: bool = True,
) -> list[str]:
    """ffmpeg: the box (x, y, w, h in pixels) of every frame at `fps`, as raw gray bytes."""
    x, y, w, h = box
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-threads", str(threads)]
    if start_s is not None:
        args += ["-ss", f"{start_s:.3f}"]
    if concat_input:
        args += [*ffmpeg.input_guard(concat=True), "-f", "concat", "-safe", "0", "-i", str(concat)]
    else:
        args += [*ffmpeg.input_guard(), "-i", str(concat)]
    if duration_s is not None:
        args += ["-t", f"{duration_s:.3f}"]
    args += ["-map", "0:v:0", "-an", "-sn", "-vf", f"fps={fps:g},crop={w}:{h}:{x}:{y},format=gray"]
    args += ["-f", "rawvideo", "pipe:1"]
    return args


def frames(args: list[str], size: tuple[int, int], cancel: threading.Event) -> Iterator[np.ndarray]:
    """The crops ffmpeg writes, one gray array (h, w) at a time."""
    w, h = size
    pipe = _Pipe(args)
    done = False
    try:
        while True:
            if cancel.is_set():
                raise ffmpeg.Cancelled()
            data = _read_exact(pipe.stdout, w * h)
            if len(data) < w * h:
                break
            yield np.frombuffer(data, np.uint8).reshape(h, w)
        done = True
    finally:
        if not done:  # cancelled, failed or abandoned: no ffmpeg left behind
            pipe.stop()
    code = pipe.finish()
    if code != 0:
        raise ffmpeg.FfmpegError("ffmpeg failed: " + " | ".join(list(pipe.tail)[-4:]))


def boxes_of(
    width: int, height: int, rect: sb.Rect, sets: sb.Rect | None
) -> tuple[sb.Rect, tuple[int, int, int, int]]:
    """The crop that holds the score's box and the sets' box, in pixels (even sizes)."""
    outer = sb.union([rect] + ([sets] if sets else []))
    x, y, w, h = outer.pixels(width, height)
    w, h = max(2, w - w % 2), max(2, h - h % 2)
    # The crop as ffmpeg cuts it, back in normalised coordinates (the sub-boxes are cut from it).
    exact = sb.Rect(x / width, y / height, w / width, h / height)
    return exact, (x, y, w, h)


def read_crops(
    crops: Iterator[np.ndarray],
    outer: sb.Rect,
    rect: sb.Rect,
    sets: sb.Rect | None,
    kind: sb.Kind,
    fps: float,
    t0_ms: int = 0,
    progress: Callable[[int], None] | None = None,
    ocr: sb.LineReader | None = None,
) -> list[bd.Sample]:
    """A reading per crop: the score (two numbers) and, with a sets box, the set counts. A
    box at the edge of the picture reads a digit the edge touches (the board stands there)."""
    net = sb.model()
    out: list[bd.Sample] = []
    step = 1000.0 / fps
    edges = (rect.x <= EDGE, rect.x + rect.w >= 1 - EDGE)
    set_edges = (sets.x <= EDGE, sets.x + sets.w >= 1 - EDGE) if sets else (False, False)
    for i, crop in enumerate(crops):
        t = t0_ms + round(i * step)
        score = sb.read(sb.sub_crop(crop, outer, rect), kind, net, True, *edges, ocr=ocr)
        set_counts = None
        if sets is not None:
            found = sb.read(sb.sub_crop(crop, outer, sets), kind, net, True, *set_edges, ocr=ocr)
            if found is not None and found.confidence >= bd.MIN_CONF and max(found.numbers) <= 3:
                set_counts = (found.numbers[0], found.numbers[1])
        if score is None:
            out.append(bd.Sample(t, None, None, 0.0, set_counts))
        else:
            out.append(
                bd.Sample(t, score.numbers[0], score.numbers[1], score.confidence, set_counts)
            )
        if progress is not None and i % 50 == 0:
            progress(t)
    return out


def _rect(value: dict[str, Any] | None) -> sb.Rect | None:
    if not value:
        return None
    return sb.Rect(float(value["x"]), float(value["y"]), float(value["w"]), float(value["h"]))


def save_readings(path: Path, samples: list[bd.Sample]) -> None:
    partial = path.with_name(path.name + ".part.npz")
    sets = [s.sets if s.sets else (-1, -1) for s in samples]
    np.savez_compressed(
        partial,
        t_ms=np.array([s.t_ms for s in samples], np.int64),
        left=np.array([-1 if s.left is None else s.left for s in samples], np.int16),
        right=np.array([-1 if s.right is None else s.right for s in samples], np.int16),
        confidence=np.array([s.confidence for s in samples], np.float32),
        sets=np.array(sets, np.int16).reshape(-1, 2),
        schema=np.array([SCHEMA]),
    )
    _write(path, data=partial.read_bytes())
    partial.unlink()


def run(ctx: Context) -> dict[str, Any]:
    lower_thread_priority()
    started = time.perf_counter()
    params = ctx.job.params
    root, out = _out_dir(ctx)
    rect = _rect(params["rect"])
    if rect is None:
        raise PermanentError("the job has no scoreboard box")
    sets = _rect(params.get("sets"))
    kind: sb.Kind = params.get("kind", "auto")
    if kind not in ("flip", "led", "auto"):
        raise PermanentError(f"unknown board kind {kind}")
    fps = float(params.get("fps") or 2.0)
    width, height = int(params["width"]), int(params["height"])
    outer, box = boxes_of(width, height, rect, sets)
    concat = ffmpeg.concat_list(raw_inputs(ctx), ctx.scratch / "inputs.ffconcat")
    duration_ms = int(params.get("duration_ms") or 0)
    args = crop_args(concat, box, fps, threads=ctx.settings.analysis_threads)
    ctx.report(0.01, "reading the scoreboard")
    ocr = sb.LineReader.find(ctx.settings.players_weights)
    samples = read_crops(
        frames(args, (box[2], box[3]), ctx.cancel),
        outer,
        rect,
        sets,
        kind,
        fps,
        ocr=ocr,
        progress=lambda t: ctx.report(
            0.95 * min(1.0, t / max(duration_ms, 1)), "reading the scoreboard"
        ),
    )
    read_s = time.perf_counter() - started
    found = bd.segments(samples)
    target = out / f"scoreboard-{ctx.job.id[:8]}.npz"
    save_readings(target, samples)
    summary = bd.summary(samples, found)
    return {
        "model": {
            "name": MODEL,
            "version": code_version(),
            "params": {
                "fps": fps,
                "kind": kind,
                "box_px": list(box),
                "weights": sb.WEIGHTS.name,
                "ocr": "PP-OCRv5-rec" if ocr is not None else None,
            },
        },
        "region_id": params["region_id"],
        "artifact": {
            "nas_path": str(target.relative_to(root.resolve())),
            "sha256": _sha256(target),
            "bytes": target.stat().st_size,
            "format": "npz",
            "schema_version": SCHEMA,
            "stats": summary,
        },
        "track": bd.pack(found),
        "summary": summary,
        "timing": {"wall_s": round(time.perf_counter() - started, 1), "read_s": round(read_s, 1)},
    }


def main() -> None:  # pragma: no cover - a tool for checks by eye
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("box", type=float, nargs=4, help="x y w h, 0..1")
    parser.add_argument("--sets", type=float, nargs=4)
    parser.add_argument("--kind", default="auto")
    parser.add_argument("--from", dest="start", type=float)
    parser.add_argument("--to", dest="end", type=float)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--readings", type=Path, help="write every reading here (JSON lines)")
    parser.add_argument("--weights", type=Path, help="the folder with the OCR recogniser")
    parser.add_argument("--no-ocr", action="store_true")
    args = parser.parse_args()
    probe = ffmpeg.probe(args.video)
    rect = sb.Rect(*args.box)
    sets = sb.Rect(*args.sets) if args.sets else None
    outer, box = boxes_of(probe.width, probe.height, rect, sets)
    duration = (args.end - args.start) if args.start is not None and args.end is not None else None
    cmd = crop_args(
        args.video,
        box,
        args.fps,
        threads=args.threads,
        start_s=args.start,
        duration_s=duration,
        concat_input=False,
    )
    started = time.perf_counter()
    t0 = round((args.start or 0) * 1000)
    ocr = None if args.no_ocr else sb.LineReader.find(args.weights)
    samples = read_crops(
        frames(cmd, (box[2], box[3]), threading.Event()),
        outer, rect, sets, args.kind, args.fps, t0, ocr=ocr,
    )  # fmt: skip
    found = bd.segments(samples)
    if args.readings:
        with args.readings.open("w") as handle:
            for s in samples:
                handle.write(json.dumps([s.t_ms, s.left, s.right, s.confidence, s.sets]) + "\n")
    print(
        json.dumps(
            {
                "summary": bd.summary(samples, found),
                "wall_s": round(time.perf_counter() - started, 1),
            }
        )
    )
    for seg in found:
        print(
            f"{seg.first_ms / 1000:8.1f} {seg.last_ms / 1000:8.1f}  set {seg.set_no}  "
            f"{seg.left:2d}:{seg.right:<2d} {seg.kind:5s} {seg.confidence:.2f}"
        )


if __name__ == "__main__":  # pragma: no cover
    main()
