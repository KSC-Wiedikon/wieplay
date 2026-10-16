"""Job handlers: ingest, transcode, thumbs, purge, export, import (PLAN §8.4); the analysis
jobs are in analyse.py."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import httpx

from nomal.core import imports
from nomal.core.storage import content_type_for, safe_file_name
from nomal.worker import ffmpeg, importer
from nomal.worker.client import ApiClient, ClaimedJob
from nomal.worker.config import WorkerSettings

log = logging.getLogger(__name__)
CHUNK = 8 * 1024 * 1024


class PermanentError(Exception):
    """Retrying will not help (bad input file, wrong parameters)."""


class RetryLater(Exception):
    """Retrying helps, but not soon (YouTube's bot check)."""

    def __init__(self, message: str, seconds: int) -> None:
        super().__init__(message)
        self.seconds = seconds


@dataclass
class Context:
    settings: WorkerSettings
    api: ApiClient
    job: ClaimedJob
    cancel: threading.Event
    report: Callable[[float, str], None]

    @property
    def scratch(self) -> Path:
        path = self.settings.work_dir / self.job.id
        path.mkdir(parents=True, exist_ok=True)
        return path


def inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise PermanentError(f"path outside {root}: {path}")
    return resolved


def _sha256(path: Path, cancel: threading.Event) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            if cancel.is_set():
                raise ffmpeg.Cancelled()
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path, size: int, ctx: Context, done: list[int], total: int) -> str:
    """Stream an object to disk; returns its SHA-256."""
    digest = hashlib.sha256()
    partial = target.with_suffix(target.suffix + ".part")
    with httpx.stream("GET", url, timeout=httpx.Timeout(120.0, connect=15.0)) as response:
        if response.status_code != 200:
            raise RuntimeError(f"download failed: HTTP {response.status_code}")
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(CHUNK):
                if ctx.cancel.is_set():
                    raise ffmpeg.Cancelled()
                handle.write(chunk)
                digest.update(chunk)
                done[0] += len(chunk)
                ctx.report(0.9 * done[0] / max(total, 1), f"downloading {target.name}")
    if partial.stat().st_size != size:
        raise RuntimeError(f"{target.name}: got {partial.stat().st_size} bytes, expected {size}")
    os.replace(partial, target)
    return digest.hexdigest()


def raw_inputs(ctx: Context) -> list[str]:
    """The job's input files, each confined to the raw originals on the NAS."""
    root = ctx.settings.raw_root
    return [str(inside(root, Path(item))) for item in ctx.job.params["inputs"]]


def ingest(ctx: Context) -> dict[str, Any]:
    """Uploaded or inbox files → originals on the NAS, probed and checksummed."""
    params = ctx.job.params
    raw_root = ctx.settings.raw_root
    raw_dir = inside(raw_root, raw_root / params["raw_subdir"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(params["files"], key=lambda f: f["part_no"])
    total = sum(int(f["bytes"]) for f in files)
    done = [0]
    staged = [f["object_key"] for f in files if f.get("object_key")]
    urls = ctx.api.sign(ctx.job.id, "get", staged) if staged else {}

    results: list[dict[str, Any]] = []
    probes: list[ffmpeg.Probe] = []
    for f in files:
        target = raw_dir / f"{int(f['part_no']):02d}_{safe_file_name(f['name'])}"
        if f.get("inbox_path"):
            if ctx.settings.inbox_root is None:
                raise PermanentError("inbox file but no inbox configured on this worker")
            source = inside(ctx.settings.inbox_root, ctx.settings.inbox_root / f["inbox_path"])
            if source.exists():
                shutil.move(source, target)  # same share: a rename, no copy
            elif not target.exists():
                raise PermanentError(f"inbox file is gone: {f['inbox_path']}")
            ctx.report(0.5 * (len(results) + 1) / len(files), f"checksumming {target.name}")
            sha = _sha256(target, ctx.cancel)
        elif target.exists() and target.stat().st_size == int(f["bytes"]):
            sha = _sha256(target, ctx.cancel)  # a previous attempt got this far
            done[0] += int(f["bytes"])
        else:
            sha = _download(urls[f["object_key"]], target, int(f["bytes"]), ctx, done, total)
        probe = ffmpeg.probe(target)
        probes.append(probe)
        results.append(
            {
                "file_id": f["id"],
                "sha256": sha,
                "nas_path": str(target),
                "duration_ms": probe.duration_ms,
                "probe": {
                    "width": probe.width,
                    "height": probe.height,
                    "fps": f"{probe.fps.numerator}/{probe.fps.denominator}",
                    "has_audio": probe.has_audio,
                    **probe.capture,
                },
            }
        )

    first = probes[0]
    for other in probes[1:]:
        if (other.width, other.height, other.video_codec) != (
            first.width,
            first.height,
            first.video_codec,
        ):
            raise PermanentError("the files of one video differ in resolution or codec")
    if staged:
        ctx.api.sign(ctx.job.id, "delete", staged)  # staging no longer needed
    fps = Fraction(first.fps)
    return {
        "files": results,
        "duration_ms": sum(p.duration_ms for p in probes),
        "width": first.width,
        "height": first.height,
        "fps_num": fps.numerator,
        "fps_den": fps.denominator,
        "has_audio": all(p.has_audio for p in probes),
        "capture": first.capture,
    }


def _upload_dir(ctx: Context, directory: Path, prefix: str, names: list[str], base: float) -> int:
    """PUT every file to its signed URL; returns the bytes sent."""
    keys = [prefix + name for name in names]
    urls = ctx.api.sign(ctx.job.id, "put", keys)
    sent = [0]
    lock = threading.Lock()

    def put(name: str) -> None:
        if ctx.cancel.is_set():
            raise ffmpeg.Cancelled()
        path = directory / name
        data = path.read_bytes()
        for attempt in range(4):
            try:
                response = httpx.put(
                    urls[prefix + name],
                    content=data,
                    headers={"Content-Type": content_type_for(name)},
                    timeout=httpx.Timeout(120.0, connect=15.0),
                )
                if response.status_code in (200, 201, 204):
                    break
                error = f"HTTP {response.status_code}"
            except httpx.TransportError as exc:
                error = str(exc)
            if attempt == 3:
                raise RuntimeError(f"upload of {name} failed: {error}")
        with lock:
            sent[0] += len(data)
            ctx.report(base + (1 - base) * sent[0] / max(total, 1), "uploading")

    total = sum((directory / n).stat().st_size for n in names)
    with ThreadPoolExecutor(max_workers=ctx.settings.upload_concurrency) as pool:
        list(pool.map(put, names))
    return total


def transcode(ctx: Context) -> dict[str, Any]:
    params = ctx.job.params
    spec = params["rendition"]
    out = ctx.scratch / spec["name"]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    concat = ffmpeg.concat_list(raw_inputs(ctx), ctx.scratch / "inputs.ffconcat")
    src = params["source"]
    height = int(spec["height"])
    width = ffmpeg.even(src["width"] * height / src["height"])
    fps = float(params["fps"])

    def encode(encoder: str) -> None:
        args = ffmpeg.hls_args(
            concat=concat,
            out_width=width,
            out_height=height,
            fps=fps,
            video_kbps=int(spec["video_kbps"]),
            max_kbps=int(spec["max_kbps"]),
            audio_kbps=int(spec["audio_kbps"]),
            has_audio=bool(params["has_audio"]),
            segment_seconds=int(params["segment_seconds"]),
            encoder=encoder,
            threads=ctx.settings.threads,
            preset=ctx.settings.x264_preset,
            vaapi_device=ctx.settings.vaapi_device,
        )
        ffmpeg.run(
            args,
            duration_ms=int(params["duration_ms"]),
            on_progress=lambda p: ctx.report(0.85 * p, f"encoding {spec['name']} ({encoder})"),
            cancel=ctx.cancel,
            cwd=out,
        )

    try:
        encode(ctx.settings.encoder)
    except ffmpeg.FfmpegError:
        if ctx.settings.encoder != "vaapi":
            raise
        log.warning("VAAPI encode failed; falling back to x264")
        for item in out.iterdir():
            item.unlink()
        encode("x264")

    playlist = (out / "index.m3u8").read_text()
    names = sorted(p.name for p in out.iterdir() if p.name != "index.m3u8")
    segments = [n for n in names if n.startswith("seg_")]
    if not segments or "init.mp4" not in names:
        raise RuntimeError("ffmpeg produced no segments")
    sent = _upload_dir(ctx, out, params["prefix"], names, 0.85)
    return {
        "name": spec["name"],
        "codecs": ffmpeg.codecs_of(out / "init.mp4", bool(params["has_audio"])),
        "width": width,
        "height": height,
        "fps": fps,
        "bandwidth": ffmpeg.peak_bandwidth(out, playlist),
        "segment_seconds": int(params["segment_seconds"]),
        "segment_count": len(segments),
        "bytes": sent,
        "playlist": playlist,
    }


def thumbs(ctx: Context) -> dict[str, Any]:
    params = ctx.job.params
    out = ctx.scratch / "thumbs"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    concat = ffmpeg.concat_list(raw_inputs(ctx), ctx.scratch / "inputs.ffconcat")
    src = params["source"]
    tile_w = int(params["tile_w"])
    tile_h = ffmpeg.even(tile_w * src["height"] / src["width"])
    interval, cols, rows = int(params["interval_s"]), int(params["cols"]), int(params["rows"])
    duration_ms = int(params["duration_ms"])
    ffmpeg.run(
        ffmpeg.thumbs_args(
            concat=concat, interval_s=interval, tile_w=tile_w, tile_h=tile_h, cols=cols, rows=rows
        ),
        duration_ms=duration_ms,
        on_progress=lambda p: ctx.report(0.7 * p, "thumbnails"),
        cancel=ctx.cancel,
        cwd=out,
    )
    first = raw_inputs(ctx)[0]
    first_ms = ffmpeg.probe(Path(first)).duration_ms
    ffmpeg.run(
        ffmpeg.poster_args(source=Path(first), at_s=min(60.0, first_ms / 1000 * 0.1)),
        duration_ms=1,
        on_progress=lambda p: None,
        cancel=ctx.cancel,
        cwd=out,
    )
    names = sorted(p.name for p in out.iterdir())
    sheets = [n for n in names if n.startswith("sprite_")]
    _upload_dir(ctx, out, params["prefix"], names, 0.7)
    count = math.ceil(duration_ms / 1000 / interval)
    return {
        "interval_s": interval,
        "cols": cols,
        "rows": rows,
        "tile_w": tile_w,
        "tile_h": tile_h,
        "count": min(count, len(sheets) * cols * rows),
        "sheets": len(sheets),
        "poster": "poster.jpg" in names,
    }


def purge(ctx: Context) -> dict[str, Any]:
    """Delete a deleted video's originals and analysis signals from the NAS (PLAN §5.5)."""
    removed = 0
    analysis_root = ctx.settings.analysis_root
    analysis_dir = ctx.job.params.get("analysis_subdir")
    if analysis_root is not None and analysis_dir:
        folder = inside(analysis_root, analysis_root / analysis_dir)
        if folder != analysis_root.resolve() and folder.is_dir():
            removed += sum(1 for p in folder.rglob("*") if p.is_file())
            shutil.rmtree(folder)
    root = ctx.settings.raw_root
    subdir = ctx.job.params.get("raw_subdir")
    if subdir:
        # The whole folder: it may also hold files of an ingest that never finished.
        folder = inside(root, root / subdir)
        if folder != root.resolve() and folder.is_dir():
            removed += sum(1 for p in folder.rglob("*") if p.is_file())
            shutil.rmtree(folder)
    for raw in ctx.job.params.get("nas_paths", []):
        path = inside(root, Path(raw))
        if path.exists():
            path.unlink()
            removed += 1
        parent = path.parent
        while parent != root.resolve() and parent.is_relative_to(root.resolve()):
            try:
                parent.rmdir()  # only when empty
            except OSError:
                break
            parent = parent.parent
    return {"removed": removed}


def import_video(ctx: Context) -> dict[str, Any]:
    """A YouTube or Twitch link → the video's raw folder on the NAS (PLAN §7.2.2). The
    ingest that the API queues next finds the file there, checksums and probes it."""
    params = ctx.job.params
    # The API built this address from a parsed id; parse it again before fetching anything.
    try:
        source = imports.parse_link(params["url"])
    except imports.NotImportable as exc:
        raise PermanentError(str(exc)) from exc
    if source.url != params["url"] or source.platform != params["platform"]:
        raise PermanentError("unexpected import address")
    raw_root = ctx.settings.raw_root
    raw_dir = inside(raw_root, raw_root / params["raw_subdir"])
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"01_{safe_file_name(params['file_name'])}"
    work = ctx.scratch
    try:
        path, info = importer.fetch(
            source.url,
            source.platform,
            params.get("channels", []),
            work,
            max_seconds=int(params["max_seconds"]),
            max_bytes=int(params["max_bytes"]),
            progress=lambda f: ctx.report(0.95 * f, "downloading"),
            cancel=ctx.cancel,
        )
        ctx.report(0.97, "copying to the NAS")
        partial = target.with_name(target.name + ".part")
        shutil.copyfile(path, partial)
        os.replace(partial, target)
    except importer.Refused as exc:
        raise PermanentError(str(exc)) from exc
    except importer.RateLimited as exc:
        raise RetryLater(str(exc), exc.seconds) from exc
    except importer.Stopped as exc:
        raise ffmpeg.Cancelled() from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"file_name": params["file_name"], "bytes": target.stat().st_size, "info": info}


def export(ctx: Context) -> dict[str, Any]:
    """A watermarked MP4 of a clip, reel or condensed match (PLAN §7.7)."""
    params = ctx.job.params
    out = ctx.scratch / "export"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    watermark = out / "watermark.txt"
    watermark.write_text(str(params["watermark"])[:200])
    pieces = params["pieces"]
    total_ms = sum(int(p["end_ms"]) - int(p["start_ms"]) for p in pieces)
    done_ms = 0
    parts: list[str] = []
    root = ctx.settings.raw_root
    for piece in pieces:
        # A piece in the video's timeline may span two camera files: cut per file.
        for part in piece["parts"]:
            offset, length = int(part["offset_ms"]), int(part["duration_ms"])
            start = max(int(piece["start_ms"]), offset)
            end = min(int(piece["end_ms"]), offset + length)
            if end - start < 40:  # less than two frames of this file
                continue
            source = inside(root, Path(part["path"]))
            name = f"piece_{len(parts):03d}.mp4"
            base, cut_ms = done_ms, end - start

            def report(p: float, base: int = base, cut_ms: int = cut_ms) -> None:
                ctx.report(0.9 * (base + p * cut_ms) / max(total_ms, 1), "rendering")

            ffmpeg.run(
                ffmpeg.export_piece_args(
                    source=source,
                    start_s=(start - offset) / 1000,
                    duration_s=cut_ms / 1000,
                    has_audio=bool(piece.get("has_audio", True)),
                    watermark_file=watermark,
                    font_file=ctx.settings.font_file,
                    output=name,
                ),
                duration_ms=cut_ms,
                on_progress=report,
                cancel=ctx.cancel,
                cwd=out,
            )
            parts.append(name)
            done_ms += cut_ms
    if not parts:
        raise PermanentError("nothing to render")
    parts_list = out / "parts.ffconcat"
    parts_list.write_text("ffconcat version 1.0\n" + "".join(f"file '{name}'\n" for name in parts))
    ffmpeg.run(
        ffmpeg.join_args(parts_list=parts_list, output="export.mp4"),
        duration_ms=total_ms,
        on_progress=lambda p: ctx.report(0.9 + 0.05 * p, "joining"),
        cancel=ctx.cancel,
        cwd=out,
    )
    result = out / "export.mp4"
    size = result.stat().st_size
    url = ctx.api.sign(ctx.job.id, "put", [params["key"]])[params["key"]]
    ctx.report(0.95, "uploading")
    with result.open("rb") as handle:
        response = httpx.put(
            url,
            content=handle,
            headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
            timeout=httpx.Timeout(600.0, connect=15.0),
        )
    if response.status_code not in (200, 201, 204):
        raise RuntimeError(f"export upload failed: HTTP {response.status_code}")
    return {"bytes": size, "duration_ms": total_ms}


def analyse(ctx: Context) -> dict[str, Any]:
    from nomal.worker import analyse as analysis  # numpy, onnxruntime: the worker image only

    return analysis.analyse(ctx)


def train_rallies(ctx: Context) -> dict[str, Any]:
    from nomal.worker import analyse as analysis

    return analysis.train_rallies(ctx)


def trajectory(ctx: Context) -> dict[str, Any]:
    from nomal.worker import trajectory as job  # numpy: the worker image only

    return job.run(ctx)


HANDLERS: dict[str, Callable[[Context], dict[str, Any]]] = {
    "import": import_video,
    "ingest": ingest,
    "transcode": transcode,
    "thumbs": thumbs,
    "purge": purge,
    "export": export,
    "analyse": analyse,
    "train_rallies": train_rallies,
    "trajectory": trajectory,
}
# Long CPU jobs that run on a worker's background lane (runner.py), next to the media jobs.
BACKGROUND_KINDS = ("analyse", "train_rallies", "trajectory")
