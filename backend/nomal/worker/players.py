"""The players job (PLAN §10.5): people on the court in a video's rally windows, tracked,
grouped into players, with back crops for the gallery.

    rally windows (asked from the API when the job runs) ─ffmpeg: 10 fps, the court's ROI─▶
    YOLO (CPU, ONNX) ─▶ ByteTrack ─▶ masked to the court, side of the net, team per rally
    ─▶ clusters, shirt-number suggestions ─▶
        NAS:    players-<job>.npz (every box), the contact sheets (provenance)
        bucket: index.json.gz (boxes per frame, for attributing actions in the API) and
                sheet-<n>.jpg (the gallery's crops), under media/<video>/analysis/

Runs on the Lenovo's background lane by default (caps analyse + nas); a worker with a GPU
can take it with other caps (PLAN §8.3) and NOMAL_WORKER_PLAYERS_PROVIDERS. Crops show
club members: they stay on the NAS and in the video's bucket prefix, and go when the video
goes (PLAN §5.5).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from nomal.ml import bytetrack, people
from nomal.ml import players as pl
from nomal.ml.extract import NICE, lower_thread_priority
from nomal.worker import ffmpeg
from nomal.worker.analyse import _out_dir, _write, code_version
from nomal.worker.handlers import Context, PermanentError, raw_inputs

log = logging.getLogger(__name__)
MODEL = "nomal-players"
DETECT_FPS = 10.0
IMGSZ = 1280
PAD_BEFORE_MS = 1000
PAD_AFTER_MS = 1000
JOIN_GAP_MS = 2000


def _nice() -> None:
    import os

    os.nice(NICE)


def windows_from(rallies: list[dict[str, Any]], duration_ms: int) -> list[dict[str, Any]]:
    """Rally spans padded and joined: [{start_ms, end_ms, near_team}]. Joined windows keep
    the near team only when their rallies agree."""
    spans = sorted(
        (
            max(0, int(r["start_ms"]) - PAD_BEFORE_MS),
            min(duration_ms, int(r["end_ms"]) + PAD_AFTER_MS),
            r.get("near_team"),
        )
        for r in rallies
        if int(r["end_ms"]) > int(r["start_ms"])
    )
    out: list[dict[str, Any]] = []
    for s, e, team in spans:
        if out and s - out[-1]["end_ms"] <= JOIN_GAP_MS:
            last = out[-1]
            last["end_ms"] = max(last["end_ms"], e)
            if last["near_team"] != team:
                last["near_team"] = None
        else:
            out.append({"start_ms": s, "end_ms": e, "near_team": team})
    return [w for w in out if w["end_ms"] - w["start_ms"] >= 500]


def pieces(win: dict[str, Any], parts: list[dict[str, Any]]) -> list[tuple[str, int, int, int]]:
    """A window cut per camera file: (path, offset_ms, start_ms, end_ms) in the video's
    timeline. Seeking inside the concat demuxer decodes from the start of the video (minutes
    per seek on a 2 h match), so each piece seeks in its own file, as the export does."""
    out = []
    for part in parts:
        offset, length = int(part["offset_ms"] or 0), int(part["duration_ms"] or 0)
        start = max(int(win["start_ms"]), offset)
        end = min(int(win["end_ms"]), offset + length) if length else int(win["end_ms"])
        if end - start >= 200:
            out.append((str(part["path"]), offset, start, end))
    return out


def _frames(
    source: Path,
    start_s: float,
    duration_s: float,
    crop: tuple[int, int, int, int],
    fps: float,
    threads: int,
) -> Iterator[np.ndarray]:
    x, y, w, h = crop
    args = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-threads", str(threads),
        "-ss", f"{start_s:.3f}", *ffmpeg.input_guard(), "-i", str(source),
        "-t", f"{duration_s:.3f}", "-map", "0:v:0", "-an", "-sn",
        "-vf", f"fps={fps:.6g},crop={w}:{h}:{x}:{y}",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
    ]  # fmt: skip
    proc = subprocess.Popen(  # noqa: S603 - argument list, ffmpeg from PATH
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=_nice
    )
    size = w * h * 3
    assert proc.stdout is not None
    try:
        while True:
            data = proc.stdout.read(size)
            if len(data) < size:
                break
            yield np.frombuffer(data, np.uint8).reshape(h, w, 3)
    finally:
        if proc.poll() is None:
            proc.kill()
        _, err = proc.communicate()
        if proc.returncode not in (0, -9) and err:
            log.warning("ffmpeg %s at %.1f s: %s", source.name, start_s, err.decode()[-300:])


def _upload(ctx: Context, urls: dict[str, str], key: str, data: bytes, content_type: str) -> None:
    error = ""
    for _ in range(4):
        try:
            response = httpx.put(
                urls[key], content=data, headers={"Content-Type": content_type},
                timeout=httpx.Timeout(60.0, connect=15.0),
            )  # fmt: skip
            if response.status_code in (200, 201, 204):
                return
            error = f"HTTP {response.status_code}"
        except httpx.TransportError as exc:
            error = str(exc)
    raise RuntimeError(f"upload of {key} failed: {error}")


def detect_and_track(
    ctx: Context,
    parts: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    setups: pl.Setups,
    size: tuple[int, int],
    detector: people.PersonDetector,
    fps: float,
) -> tuple[pl.Collector, dict[str, float]]:
    """Every window, frame by frame: detect, track, collect. `parts` are the camera files
    with their place in the timeline (paths already checked by the caller); each piece is
    cropped to the court of the camera's setup at its start."""
    width, height = size
    first = setups.at(int(windows[0]["start_ms"]) if windows else 0)
    col = pl.Collector(first, width, height, pl.roi_pixels(first.roi, width, height))
    tracker = bytetrack.ByteTracker()
    total_ms = sum(w["end_ms"] - w["start_ms"] for w in windows) or 1
    done_ms = 0
    t_detect = t_decode = 0.0
    for wi, win in enumerate(windows):
        for path, offset, start, end in pieces(win, parts):
            geom = setups.at(start)
            crop = pl.roi_pixels(geom.roi, width, height)
            col.view(geom, crop)
            tracker.reset()
            t0 = time.perf_counter()
            frames = _frames(
                Path(path), (start - offset) / 1000, (end - start) / 1000, crop, fps, 2
            )
            for k, frame in enumerate(frames):
                if ctx.cancel.is_set():
                    raise ffmpeg.Cancelled()
                t1 = time.perf_counter()
                t_decode += t1 - t0
                found = detector.detect(frame)
                assigned = tracker.update(found.boxes, found.scores)
                col.add(start + round(k * 1000 / fps), wi, frame, found.boxes, found.scores,
                        assigned.ids)  # fmt: skip
                t0 = time.perf_counter()
                t_detect += t0 - t1
        done_ms += win["end_ms"] - win["start_ms"]
        ctx.report(0.02 + 0.88 * done_ms / total_ms, "finding the players")
    return col, {"decode_s": round(t_decode, 1), "detect_s": round(t_detect, 1)}


def run(ctx: Context) -> dict[str, Any]:
    lower_thread_priority()
    started = time.perf_counter()
    params = ctx.job.params
    root, out = _out_dir(ctx)
    found = ctx.api.inputs(ctx.job.id)
    windows = windows_from(found.get("rallies", []), int(params["duration_ms"]))
    if not windows:
        raise PermanentError("no rallies to look at yet")
    width, height = int(params["width"]), int(params["height"])
    setups = pl.Setups(found.get("setups") or [], found.get("calibration"), width / height)
    settings = ctx.settings
    providers = settings.players_providers or None
    threads = settings.analysis_threads
    weights = settings.players_weights
    detector = people.PersonDetector(
        weights / people.DETECTOR_FILE if weights else None,
        imgsz=int(params.get("imgsz") or IMGSZ), threads=threads, providers=providers,
    )  # fmt: skip
    fps = float(params.get("detect_fps") or DETECT_FPS)
    paths = raw_inputs(ctx)  # each confined to the raw originals on the NAS
    parts = [{**part, "path": path} for part, path in zip(params["parts"], paths, strict=True)]
    col, timing = detect_and_track(ctx, parts, windows, setups, (width, height), detector, fps)
    if ctx.cancel.is_set():
        raise ffmpeg.Cancelled()

    t = time.perf_counter()
    tracks = pl.finish_tracks(col)
    ctx.report(0.91, "reading shirt numbers")
    reader = None
    ocr_read = 0
    try:
        reader = people.NumberReader(
            weights / people.OCR_DET_FILE if weights else None,
            weights / people.OCR_REC_FILE if weights else None,
            threads=min(threads, 2), providers=providers,
        )  # fmt: skip
    except (OSError, ValueError, RuntimeError) as exc:  # no OCR weights: the coach types
        log.warning("no shirt-number reader: %s", exc)
    if reader is not None:
        ocr_read = pl.read_numbers(tracks, reader)
    timing["ocr_s"] = round(time.perf_counter() - t, 1)
    teams = pl.assign_teams(tracks, [w["near_team"] for w in windows])
    clusters = pl.cluster_tracks(tracks)
    ctx.report(0.95, "contact sheets")
    sheets = pl.contact_sheets(clusters)
    index = pl.encode_index(col, tracks)
    timing["post_s"] = round(time.perf_counter() - t, 1)

    # NAS: every box (provenance, later training) and the sheets.
    tag = ctx.job.id[:8]
    rows = np.array(col.rows, dtype=np.float64).reshape(-1, 6)
    buf = io.BytesIO()
    np.savez_compressed(
        buf,
        t_ms=np.asarray(col.times, dtype=np.int64),
        window=np.asarray(col.windows, dtype=np.int32),
        frame=rows[:, 0].astype(np.int32),
        track=rows[:, 1].astype(np.int32),
        box=rows[:, 2:].astype(np.float32),
        meta=np.frombuffer(
            json.dumps({"windows": windows, "geometry": setups.as_dict()}).encode(), np.uint8
        ),
    )
    tracks_path = out / f"players-{tag}.npz"
    _write(tracks_path, data=buf.getvalue())
    sheet_dir = out / f"players-{tag}"
    sheet_dir.mkdir(exist_ok=True)
    for n, page in enumerate(sheets.pages):
        _write(sheet_dir / f"sheet-{n}.jpg", data=page)

    # Bucket: what the API and the browser read (the index also on the NAS).
    prefix = str(params["prefix"])
    index_bytes = gzip.compress(json.dumps(index, separators=(",", ":")).encode(), mtime=0)
    index_path = sheet_dir / "index.json.gz"
    _write(index_path, data=index_bytes)
    keys = [f"{prefix}index.json.gz", *(f"{prefix}sheet-{n}.jpg" for n in range(len(sheets.pages)))]
    urls = ctx.api.sign(ctx.job.id, "put", keys)
    ctx.report(0.97, "uploading")
    _upload(ctx, urls, keys[0], index_bytes, "application/gzip")
    for n, page in enumerate(sheets.pages):
        _upload(ctx, urls, keys[1 + n], page, "image/jpeg")

    track_info = pl.track_rows(tracks, col.times, sheets)
    detections = sum(t.detections for t in tracks)
    return {
        "model": {
            "name": MODEL,
            "version": code_version(),
            "weights_sha256": detector.sha256,
            "params": {
                "detector": people.DETECTOR_NAME,
                "imgsz": detector.imgsz,
                "conf": detector.conf,
                "detect_fps": fps,
                "tracker": bytetrack.TrackerParams().as_dict(),
                "ocr": reader.sha256 if reader is not None else None,
                "merge_distance": pl.MERGE_DISTANCE,
                "geometry": setups.as_dict(),
                "providers": providers or ["CPUExecutionProvider"],
            },
        },
        "tracks_artifact": {
            "nas_path": str(tracks_path.relative_to(root.resolve())),
            "sha256": hashlib.sha256(tracks_path.read_bytes()).hexdigest(),
            "bytes": tracks_path.stat().st_size,
        },
        "index": {
            "key": keys[0],
            "nas_path": str(index_path.relative_to(root.resolve())),
            "sha256": hashlib.sha256(index_bytes).hexdigest(),
            "bytes": len(index_bytes),
        },
        "sheets": {
            "keys": keys[1:],
            "nas_dir": str(sheet_dir.relative_to(root.resolve())),
            "cell": [pl.CELL_W, pl.CELL_H],
            "cols": pl.SHEET_COLS,
            "rows": pl.SHEET_ROWS,
        },
        "clusters": [
            {
                "key": c.key,
                "team": c.team,
                "referee": c.referee,
                "number": c.number,
                "number_conf": c.number_conf,
                "crops": sheets.cluster_crops.get(c.key, []),
                "tracks": [track_info[t.track_id] for t in c.tracks],
            }
            for c in clusters
        ],
        "stats": {
            "windows": len(windows),
            "window_s": round(sum(w["end_ms"] - w["start_ms"] for w in windows) / 1000, 1),
            "frames": len(col.times),
            "detections": col.detections,
            "masked": col.masked,
            "tracked": detections,
            "tracks": len(tracks),
            "clusters": len(clusters),
            "clusters_us": sum(1 for c in clusters if c.team == "us" and not c.referee),
            "clusters_them": sum(1 for c in clusters if c.team == "them" and not c.referee),
            "numbers_read": sum(1 for t in tracks if t.number is not None),
            "ocr_crops": ocr_read,
            **teams,
        },
        "timing": {
            **timing,
            "wall_s": round(time.perf_counter() - started, 1),
            "frames_per_s": round(len(col.times) / max(time.perf_counter() - started, 1e-6), 2),
            "threads": threads,
        },
    }
