"""The players job (PLAN §10.5): people on the court of a video, tracked, linked into the
players of this one video, with back crops for the gallery.

    rally windows (asked from the API when the job runs), joined into stretches the tracker
    follows through ─ffmpeg: 10 fps, the court's ROI─▶ YOLO (ONNX: the GPU, else the CPU)
    ─▶ ByteTrack ─▶ masked to the court, side of the net, team per rally ─▶ shirt numbers
    ─▶ chains (the pieces of one person, identities.py) ─▶ people, a confidence each ─▶
        NAS or bucket: players-<job>.npz / tracks.npz (every box, provenance)
        bucket: index.json.gz (boxes per frame, for attributing actions in the API) and
                sheet-<n>.jpg (the gallery's crops), under media/<video>/analysis/

Two setups (WIEPLAY_WORKER_PLAYERS_*):

- the Lenovo's CPU (v1's cost): rally windows only (joined when < 2 s apart), the camera
  files from the NAS, its files on the NAS;
- a GPU worker (the Framework 16, PLAN §8.2): rallies closer than 40 s are one stretch, so
  the tracker follows the players through the dead time of a whole set; the 1080p
  rendition fetched from the bucket through signed URLs (the laptop has no NAS share), its
  files written to the bucket. Between frames it gives way to other users of the GPU
  (gpu_guard.py).

Crops show club members: they stay on the NAS and in the video's bucket prefix, and go when
the video goes (PLAN §5.5). The look of a person (colour histograms) used to link its pieces
lives in memory only, for this job (PLAN §5.7).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from wieplay.ml import bytetrack, identities, people
from wieplay.ml import players as pl
from wieplay.ml.extract import NICE, lower_thread_priority
from wieplay.worker import ffmpeg
from wieplay.worker.analyse import _out_dir, _write, code_version
from wieplay.worker.handlers import Context, PermanentError, raw_inputs

log = logging.getLogger(__name__)
MODEL = "wieplay-players"
VERSION = 2  # players v2: stretches, chains, roster-checked numbers, confidence
DETECT_FPS = 10.0
IMGSZ = 1280
PAD_BEFORE_MS = 1000
PAD_AFTER_MS = 1000
JOIN_GAP_MS = 2000
# ByteTrack as in players v1 (a lost person waited for 1 s, IoU >= 0.2): a looser tracker
# (4 s, IoU >= 0.05) made fewer, longer tracks on 12 rallies of the Legends game (216
# instead of 316, 18.10.2026) but swapped people more often (the numbers read along a card
# agreed 75 % of the time instead of 83 %); the gaps are bridged after tracking instead
# (identities.py: stitching, numbers).
TRACK_BUFFER_S = 1.0
MATCH_COST = 0.8
SECOND_COST = 0.5
UNCONFIRMED_COST = 0.7
# Shirt-number readings per track: a few, more for a track followed long.
OCR_MIN_PER_TRACK = 2
OCR_MAX_PER_TRACK = 12
# The number reader runs on the CPU, on this many threads: its small models with a new input
# size per crop took 37 ms a crop on the laptop's GPU and 13 ms on its CPU (18.10.2026).
OCR_WORKERS = 4
MAIN_S = 30  # a card seen this long on court is a main card in the run's metrics
SIGN_CHUNK = 400
DOWNLOADS = 6


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


def spans_of(windows: list[dict[str, Any]], join_gap_ms: int) -> list[dict[str, Any]]:
    """The stretches the tracker follows without a break: windows closer than `join_gap_ms`
    joined, the dead time between them included. [{start_ms, end_ms, windows: [index]}]"""
    out: list[dict[str, Any]] = []
    for i, w in enumerate(windows):
        if out and w["start_ms"] - out[-1]["end_ms"] <= join_gap_ms:
            out[-1]["end_ms"] = max(out[-1]["end_ms"], w["end_ms"])
            out[-1]["windows"].append(i)
        else:
            out.append({"start_ms": w["start_ms"], "end_ms": w["end_ms"], "windows": [i]})
    return out


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


# --- where the frames come from -------------------------------------------------------------


@dataclass
class Piece:
    """A stretch of the video in one local file: its time 0 is `offset_ms` of the video."""

    path: Path
    offset_ms: int
    start_ms: int
    end_ms: int
    done: Callable[[], None] = lambda: None
    fetch_s: float = 0.0


class NasSource:
    """The camera files on the NAS (the Lenovo)."""

    def __init__(self, parts: list[dict[str, Any]]) -> None:
        self.parts = parts
        self.bytes = 0

    def pieces(self, spans: list[dict[str, Any]]) -> Iterator[tuple[int, Piece]]:
        for si, span in enumerate(spans):
            for path, offset, start, end in pieces(span, self.parts):
                yield si, Piece(Path(path), offset, start, end)

    def close(self) -> None:
        pass


def segments_in(
    segments: list[list[Any]], start_ms: int, end_ms: int
) -> list[tuple[str, int, int]]:
    """The rendition's segments [key, start_ms, duration_ms] that overlap a stretch."""
    out = []
    for key, s, d in segments:
        s, d = int(s), int(d)
        if s < end_ms and s + d > start_ms:
            out.append((str(key), s, d))
    return out


class BucketSource:
    """The 1080p rendition from the bucket (a GPU worker without the NAS share): per stretch,
    its segments through signed URLs, joined behind the rendition's init segment into one
    local fragmented MP4 (the segments keep their timestamps), the next stretch fetched
    while the current one is decoded."""

    def __init__(self, ctx: Context, rendition: dict[str, Any], scratch: Path) -> None:
        if not rendition or not rendition.get("segments"):
            raise PermanentError("the video has no rendition to read the players from")
        self.ctx = ctx
        self.init_key = str(rendition["init"])
        self.segments: list[list[Any]] = rendition["segments"]
        self.scratch = scratch
        self.bytes = 0
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=DOWNLOADS, thread_name_prefix="fetch")
        self._ahead = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prefetch")

    def _get(self, url: str, target: Path) -> int:
        error = ""
        for _ in range(4):
            if self.ctx.cancel.is_set():
                raise ffmpeg.Cancelled()
            try:
                with httpx.stream("GET", url, timeout=httpx.Timeout(60.0, connect=15.0)) as r:
                    if r.status_code != 200:
                        error = f"HTTP {r.status_code}"
                        continue
                    with target.open("wb") as handle:
                        for chunk in r.iter_bytes(1 << 20):
                            handle.write(chunk)
                size = target.stat().st_size
                with self._lock:
                    self.bytes += size
                return size
            except httpx.TransportError as exc:
                error = str(exc)
        raise RuntimeError(f"download of {target.name} failed: {error}")

    def _fetch(self, si: int, span: dict[str, Any]) -> Piece | None:
        t0 = time.perf_counter()
        segs = segments_in(self.segments, int(span["start_ms"]), int(span["end_ms"]))
        if not segs:
            return None
        folder = self.scratch / f"span-{si}"
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        keys = [self.init_key, *(k for k, _, _ in segs)]
        urls: dict[str, str] = {}
        for i in range(0, len(keys), SIGN_CHUNK):
            urls.update(self.ctx.api.sign(self.ctx.job.id, "get", keys[i : i + SIGN_CHUNK]))
        files = [folder / f"{n:05d}.m4s" for n in range(len(keys))]
        list(self._pool.map(self._get, [urls[k] for k in keys], files))
        joined = folder / "span.mp4"
        with joined.open("wb") as out:
            for f in files:
                with f.open("rb") as handle:
                    shutil.copyfileobj(handle, out, 1 << 22)
                f.unlink()
        first, last = segs[0], segs[-1]
        return Piece(
            joined,
            first[1],
            max(int(span["start_ms"]), first[1]),
            min(int(span["end_ms"]), last[1] + last[2]),
            done=lambda: shutil.rmtree(folder, ignore_errors=True),
            fetch_s=time.perf_counter() - t0,
        )

    def pieces(self, spans: list[dict[str, Any]]) -> Iterator[tuple[int, Piece]]:
        ahead: Future[Piece | None] | None = None
        for si, span in enumerate(spans):
            current = ahead if ahead is not None else self._ahead.submit(self._fetch, si, span)
            ahead = (
                self._ahead.submit(self._fetch, si + 1, spans[si + 1])
                if si + 1 < len(spans)
                else None
            )
            piece = current.result()
            if piece is not None:
                yield si, piece
        if ahead is not None:
            ahead.cancel()

    def close(self) -> None:
        self._ahead.shutdown(wait=True, cancel_futures=True)
        self._pool.shutdown(wait=True, cancel_futures=True)


# --- frames -----------------------------------------------------------------------------------


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


_END = object()


def _decoded(
    frames: Iterator[np.ndarray],
    prepare: Callable[[np.ndarray], people.Prepared],
    cancel: threading.Event,
    depth: int = 24,
) -> Iterator[tuple[np.ndarray, people.Prepared]]:
    """Frames decoded and made ready for the detector on a thread of their own, so the
    decoder and the CPU work run while the detector runs the previous frame."""
    q: queue.Queue[Any] = queue.Queue(maxsize=depth)
    stop = threading.Event()
    failed: list[BaseException] = []

    def put(item: Any) -> None:
        while not stop.is_set():
            try:
                q.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def work() -> None:
        lower_thread_priority()
        try:
            for frame in frames:
                if stop.is_set() or cancel.is_set():
                    break
                put((frame, prepare(frame)))
        except BaseException as exc:  # handed to the consumer
            failed.append(exc)
        finally:
            close = getattr(frames, "close", None)
            if close is not None:
                close()  # stops ffmpeg
            put(_END)

    thread = threading.Thread(target=work, name="decode", daemon=True)
    thread.start()
    try:
        while (item := q.get()) is not _END:
            yield item
    finally:
        stop.set()
        thread.join(timeout=60)
    if failed:
        raise failed[0]


def tracker_params(fps: float, high: float | None = None) -> bytetrack.TrackerParams:
    """ByteTrack for the players: a lost person waited for TRACK_BUFFER_S; `high` lowers
    the score a box needs to count as sure (far players at a higher input size)."""
    params = bytetrack.TrackerParams(
        buffer=max(10, round(TRACK_BUFFER_S * fps)),
        match_cost=MATCH_COST,
        second_cost=SECOND_COST,
        unconfirmed_cost=UNCONFIRMED_COST,
    )
    if high is not None:
        params.high = high
        params.new_track = high + 0.05
    return params


@dataclass
class Detected:
    col: pl.Collector
    timing: dict[str, Any] = field(default_factory=dict)


def detect_and_track(
    ctx: Context,
    source: NasSource | BucketSource,
    windows: list[dict[str, Any]],
    spans: list[dict[str, Any]],
    setups: pl.Setups,
    size: tuple[int, int],
    detector: people.PersonDetector,
    fps: float,
    tracking: bytetrack.TrackerParams | None = None,
    dump: list[tuple[int, int, Any, Any]] | None = None,
) -> Detected:
    """Every stretch, frame by frame: detect, track, collect. The tracker runs through a
    stretch without a break (the dead time between its rallies too); each piece is cropped
    to the court of the camera's setup at its start."""
    width, height = size
    first = setups.at(int(spans[0]["start_ms"]) if spans else 0)
    col = pl.Collector(first, width, height, pl.roi_pixels(first.roi, width, height))
    tracker = bytetrack.ByteTracker(tracking or tracker_params(fps))
    total_ms = sum(s["end_ms"] - s["start_ms"] for s in spans) or 1
    done_ms = 0
    t_detect = t_wait = t_fetch = 0.0
    threads = max(2, min(ctx.settings.analysis_threads, 8))
    current = -1

    def _p(at: float | None = None) -> float:
        return 0.02 + 0.83 * min(1.0, (done_ms if at is None else at) / total_ms)

    for si, piece in source.pieces(spans):
        span = spans[si]
        if si != current:  # a new stretch: nobody is followed across the gap
            tracker.reset()
            current = si
        t_fetch += piece.fetch_s
        geom = setups.at(piece.start_ms)
        crop = pl.roi_pixels(geom.roi, width, height)
        col.view(geom, crop)
        wins = [windows[i] for i in span["windows"]]
        w_idx = list(span["windows"])
        wk = 0
        try:
            frames = _frames(
                piece.path,
                (piece.start_ms - piece.offset_ms) / 1000,
                (piece.end_ms - piece.start_ms) / 1000,
                crop,
                fps,
                threads,
            )
            t0 = time.perf_counter()
            for k, (frame, prep) in enumerate(_decoded(frames, detector.prepare, ctx.cancel)):
                if ctx.cancel.is_set():
                    raise ffmpeg.Cancelled()
                if ctx.gpu is not None:
                    ctx.gpu.yield_while_busy(ctx.cancel, lambda m: ctx.report(_p(), m))
                t1 = time.perf_counter()
                t_wait += t1 - t0
                t_ms = piece.start_ms + round(k * 1000 / fps)
                while wk < len(wins) and wins[wk]["end_ms"] < t_ms:
                    wk += 1
                window = w_idx[wk] if wk < len(wins) and wins[wk]["start_ms"] <= t_ms else -1
                found = detector.infer(prep)
                if dump is not None:  # measuring (scripts/ml/players_eval.py)
                    dump.append((t_ms, si, found.boxes, found.scores))
                assigned = tracker.update(found.boxes, found.scores)
                col.add(t_ms, window, frame, found.boxes, found.scores, assigned.ids, span=si)
                t0 = time.perf_counter()
                t_detect += t0 - t1
                if k % 50 == 0:
                    ctx.report(_p(done_ms + t_ms - piece.start_ms), "finding the players")
        finally:
            piece.done()
        done_ms += piece.end_ms - piece.start_ms

    return Detected(
        col,
        {
            "detect_s": round(t_detect, 1),  # detector, tracker and collecting
            "decode_wait_s": round(t_wait, 1),  # waiting for frames
            "fetch_s": round(t_fetch, 1),
            "frames": len(col.times),
        },
    )


# --- from tracks to people --------------------------------------------------------------------


@dataclass
class Identified:
    tracks: list[pl.Track]  # the pieces, as the tracker gave them
    chains: list[pl.Track]  # people's stretches (kept: seen in play)
    clusters: list[pl.Cluster]
    sheets: pl.Sheets
    index: dict[str, Any]
    stats: dict[str, Any]


def ocr_budget(t: pl.Track) -> int:
    """Readings for a track: two, one more per 5 s it was followed, at most twelve."""
    return int(min(OCR_MAX_PER_TRACK, OCR_MIN_PER_TRACK + t.detections // 50))


def identify(
    col: pl.Collector,
    windows: list[dict[str, Any]],
    reader: people.NumberReader | None,
    roster: dict[str, list[int]],
    fps: float,
    stitch: identities.StitchParams | None = None,
    calibrated: bool = True,
) -> Identified:
    """Tracks → teams → numbers → chains → people (identities.py), then the gallery's
    sheets and the API's index. Without a court calibration every card is to be checked:
    the sides of the net come from foot heights then, and on two uncalibrated games
    (18.10.2026) whole stretches of one team landed on the other's cards."""
    tracks = pl.finish_tracks(col)
    near = [w["near_team"] for w in windows]
    sides = identities.smooth_sides(near)
    teams = identities.assign_teams(tracks, sides)
    allowed = {team: set(nums) for team, nums in roster.items() if nums}
    ocr_read = 0
    if reader is not None:
        ocr_read = pl.read_numbers(tracks, reader, ocr_budget, allowed or None, OCR_WORKERS)
    # How often the reader reads a number our roster has (before the roster drops the
    # rest): a precision check that needs no coach (18 numbers of 99 on H3's roster).
    ours = [n for t in tracks if t.team == "us" for _, n, _ in t.reads]
    on_roster = (
        round(sum(1 for n in ours if n in allowed["us"]) / len(ours), 3)
        if ours and "us" in allowed
        else None
    )
    tracks, cuts = identities.split_swaps(tracks, col, teams, allowed or None)
    tracks, met = identities.split_encounters(tracks, col, teams, allowed or None)
    numbered_tracks = sum(1 for t in tracks if t.number is not None)
    chains = identities.chains_of(tracks, stitch)
    standing = 0
    for c in chains:
        if not c.referee and identities.still(c, col):
            c.referee = True  # stands still through the rallies: not playing
            standing += 1
    kept = identities.assign_numbers([c for c in chains if identities.in_play(c)])
    clusters = pl.cluster_tracks(kept)
    absorbed = identities.absorb(clusters)
    identities.set_confidence(clusters, fps, sure=calibrated)
    sheets = pl.contact_sheets(clusters)
    index = pl.encode_index(col, kept)
    players = [c for c in clusters if not c.referee]
    stats = {
        "version": VERSION,
        "calibrated": calibrated,
        "tracks": len(tracks),
        "swaps_cut": cuts,
        "encounter_cuts": met,
        "sides_smoothed": sum(1 for a, b in zip(near, sides, strict=True) if a != b),
        "standing_still": standing,
        "absorbed": absorbed,
        "chains": len(chains),
        "chains_in_play": len(kept),
        "numbers_read_tracks": numbered_tracks,
        "numbers_read": sum(1 for c in kept if c.number is not None),
        "ocr_crops": ocr_read,
        "readings": sum(len(t.reads) for t in tracks),
        "readings_ours_on_roster": on_roster,
        "clusters": len(clusters),
        "clusters_us": sum(1 for c in players if c.team == "us"),
        "clusters_them": sum(1 for c in players if c.team == "them"),
        "clusters_numbered": sum(1 for c in players if c.number is not None),
        "clusters_uncertain": sum(1 for c in players if c.confidence < identities.UNCERTAIN),
        # Cards the job is sure of (confidence >= 0.5): the people; and their share of the
        # players' detections (the rest is on cards to check).
        "confident_us": sum(
            1 for c in players if c.team == "us" and c.confidence >= identities.UNCERTAIN
        ),
        "confident_them": sum(
            1 for c in players if c.team == "them" and c.confidence >= identities.UNCERTAIN
        ),
        "confident_share": round(
            sum(c.detections for c in players if c.confidence >= identities.UNCERTAIN)
            / max(1, sum(c.detections for c in players)),
            3,
        ),
        # Cards seen at least MAIN_S on court: the people a coach names (the rest are brief).
        "main_us": sum(1 for c in players if c.team == "us" and c.detections >= MAIN_S * fps),
        "main_them": sum(1 for c in players if c.team == "them" and c.detections >= MAIN_S * fps),
        **teams.info,
    }
    return Identified(tracks, kept, clusters, sheets, index, stats)


# --- outputs ------------------------------------------------------------------------------


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


def tracks_npz(col: pl.Collector, windows: list[dict[str, Any]], meta: dict[str, Any]) -> bytes:
    """Every box (provenance, later training): no pictures, only boxes and times."""
    rows = np.array(col.rows, dtype=np.float64).reshape(-1, 6)
    buf = io.BytesIO()
    np.savez_compressed(
        buf,
        t_ms=np.asarray(col.times, dtype=np.int64),
        window=np.asarray(col.windows, dtype=np.int32),
        span=np.asarray(col.spans, dtype=np.int32),
        frame=rows[:, 0].astype(np.int32),
        track=rows[:, 1].astype(np.int32),
        box=rows[:, 2:].astype(np.float32),
        meta=np.frombuffer(json.dumps({"windows": windows, **meta}).encode(), np.uint8),
    )
    return buf.getvalue()


def run(ctx: Context) -> dict[str, Any]:
    lower_thread_priority()
    started = time.perf_counter()
    params = ctx.job.params
    settings = ctx.settings
    found = ctx.api.inputs(ctx.job.id)
    windows = windows_from(found.get("rallies", []), int(params["duration_ms"]))
    if not windows:
        raise PermanentError("no rallies to look at yet")
    join_gap_ms = round(float(settings.players_join_gap_s) * 1000)
    spans = spans_of(windows, join_gap_ms)
    bucket = settings.players_source == "bucket"
    rendition = found.get("rendition") or {}
    if bucket and rendition.get("width") and rendition.get("height"):
        width, height = int(rendition["width"]), int(rendition["height"])
    else:
        width, height = int(params["width"]), int(params["height"])
    setups = pl.Setups(found.get("setups") or [], found.get("calibration"), width / height)
    providers = settings.players_providers or None
    threads = settings.analysis_threads
    weights = settings.players_weights
    fps = float(params.get("detect_fps") or settings.players_detect_fps or DETECT_FPS)
    detector = people.PersonDetector(
        weights / people.DETECTOR_FILE if weights else None,
        imgsz=int(params.get("imgsz") or IMGSZ), threads=threads, providers=providers,
        gpu_mem_limit_mib=settings.gpu_mem_limit_mib,
    )  # fmt: skip
    if bucket:
        source: NasSource | BucketSource = BucketSource(ctx, rendition, ctx.scratch / "video")
    else:
        paths = raw_inputs(ctx)  # each confined to the raw originals on the NAS
        parts = [{**p, "path": path} for p, path in zip(params["parts"], paths, strict=True)]
        source = NasSource(parts)
    try:
        detected = detect_and_track(
            ctx, source, windows, spans, setups, (width, height), detector, fps
        )
    finally:
        source.close()
    if ctx.cancel.is_set():
        raise ffmpeg.Cancelled()
    timing = dict(detected.timing)
    timing["detect_wall_s"] = round(time.perf_counter() - started, 1)
    sha_detector, used = detector.sha256, detector.providers
    del detector  # its memory on the GPU goes before the number reader takes some

    t = time.perf_counter()
    ctx.report(0.86, "reading shirt numbers")
    reader = None
    try:
        reader = people.NumberReader(
            weights / people.OCR_DET_FILE if weights else None,
            weights / people.OCR_REC_FILE if weights else None,
            threads=2,
        )  # fmt: skip
    except (OSError, ValueError, RuntimeError) as exc:  # no OCR weights: the coach types
        log.warning("no shirt-number reader: %s", exc)
    roster = {k: [int(n) for n in v] for k, v in (found.get("roster") or {}).items()}
    out = identify(detected.col, windows, reader, roster, fps, calibrated=setups.calibrated)
    timing["identify_s"] = round(time.perf_counter() - t, 1)
    if ctx.gpu is not None:
        timing["gpu_paused_s"] = round(ctx.gpu.paused_s, 1)

    ctx.report(0.95, "saving")
    col = detected.col
    prefix = str(params["prefix"])
    meta = {
        "spans": spans,
        "geometry": setups.as_dict(),
        "source": settings.players_source,
    }
    npz = tracks_npz(col, windows, meta)
    index_bytes = gzip.compress(json.dumps(out.index, separators=(",", ":")).encode(), mtime=0)
    sheet_keys = [f"{prefix}sheet-{n}.jpg" for n in range(len(out.sheets.pages))]
    index_key = f"{prefix}index.json.gz"
    keys = [index_key, *sheet_keys]
    tracks_artifact: dict[str, Any] = {
        "sha256": hashlib.sha256(npz).hexdigest(),
        "bytes": len(npz),
    }
    index_info: dict[str, Any] = {
        "key": index_key,
        "sha256": hashlib.sha256(index_bytes).hexdigest(),
        "bytes": len(index_bytes),
    }
    sheets_info: dict[str, Any] = {
        "keys": sheet_keys,
        "cell": [pl.CELL_W, pl.CELL_H],
        "cols": pl.SHEET_COLS,
        "rows": pl.SHEET_ROWS,
    }
    if bucket:
        # No NAS here: the boxes go to the bucket with the rest (PLAN §8.2).
        tracks_key = f"{prefix}tracks.npz"
        keys.append(tracks_key)
        tracks_artifact["key"] = tracks_key
    else:
        # NAS: every box (provenance, later training), the sheets and the index too.
        root, folder = _out_dir(ctx)
        tag = ctx.job.id[:8]
        tracks_path = folder / f"players-{tag}.npz"
        _write(tracks_path, data=npz)
        sheet_dir = folder / f"players-{tag}"
        sheet_dir.mkdir(exist_ok=True)
        for n, page in enumerate(out.sheets.pages):
            _write(sheet_dir / f"sheet-{n}.jpg", data=page)
        index_path = sheet_dir / "index.json.gz"
        _write(index_path, data=index_bytes)
        tracks_artifact["nas_path"] = str(tracks_path.relative_to(root.resolve()))
        index_info["nas_path"] = str(index_path.relative_to(root.resolve()))
        sheets_info["nas_dir"] = str(sheet_dir.relative_to(root.resolve()))
    urls = ctx.api.sign(ctx.job.id, "put", keys)
    ctx.report(0.97, "uploading")
    _upload(ctx, urls, index_key, index_bytes, "application/gzip")
    for key, page in zip(sheet_keys, out.sheets.pages, strict=True):
        _upload(ctx, urls, key, page, "image/jpeg")
    if bucket:
        _upload(ctx, urls, tracks_artifact["key"], npz, "application/octet-stream")

    track_info = pl.track_rows(out.chains, col.times, out.sheets)
    wall = time.perf_counter() - started
    timing.update(
        {
            "wall_s": round(wall, 1),
            "frames_per_s": round(len(col.times) / max(wall, 1e-6), 2),
            "threads": threads,
            "providers": used,
            "bytes_fetched": source.bytes,
        }
    )
    if "CUDAExecutionProvider" in used:
        # The GPU-hour ledger's entry (PLAN §8.3, §10.9): the job's time on the card.
        timing["gpu_s"] = round(wall - float(timing.get("gpu_paused_s", 0.0)), 1)
    return {
        "model": {
            "name": MODEL,
            "version": code_version(),
            "weights_sha256": sha_detector,
            "params": {
                "players": VERSION,
                "detector": people.DETECTOR_NAME,
                "imgsz": int(params.get("imgsz") or IMGSZ),
                "conf": 0.15,
                "detect_fps": fps,
                "join_gap_s": join_gap_ms / 1000,
                "tracker": tracker_params(fps).as_dict(),
                "stitch": identities.StitchParams().as_dict(),
                "ocr": reader.sha256 if reader is not None else None,
                "roster_checked": sorted(k for k, v in roster.items() if v),
                "merge_distance": pl.MERGE_DISTANCE,
                "geometry": setups.as_dict(),
                "providers": used,
                "source": settings.players_source,
            },
        },
        "tracks_artifact": tracks_artifact,
        "index": index_info,
        "sheets": sheets_info,
        "clusters": [
            {
                "key": c.key,
                "team": c.team,
                "referee": c.referee,
                "number": c.number,
                "number_conf": c.number_conf,
                "confidence": c.confidence,
                "crops": out.sheets.cluster_crops.get(c.key, []),
                "tracks": [track_info[t.track_id] for t in c.tracks],
            }
            for c in out.clusters
        ],
        "stats": {
            **out.stats,
            "windows": len(windows),
            "spans": len(spans),
            "window_s": round(sum(w["end_ms"] - w["start_ms"] for w in windows) / 1000, 1),
            "span_s": round(sum(s["end_ms"] - s["start_ms"] for s in spans) / 1000, 1),
            "frames": len(col.times),
            "detections": col.detections,
            "masked": col.masked,
        },
        "timing": timing,
    }
