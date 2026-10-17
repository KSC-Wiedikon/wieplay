"""The ball's path from the per-frame detections (PLAN §10.4, post-processing).

    detections (VballNet-Fast, 30 fps) ─▶ stuck spots and spikes removed ─▶ linked into
    tracks ─▶ short gaps filled ─▶ contacts and bounces (direction and speed changes) ─▶
    each piece between contacts smoothed ─▶ a compact track for the browser

Pure numpy, on the worker (the `trajectory` job). Positions are in normalised video
coordinates (0..1, x right, y down, the centre of the model's pixel), like the court
calibration (wieplay/core/court.py).

The compact track (schema 1, gzipped JSON, a few hundred kB for a 2 h match):
    {"schema": 1, "fps": 15.0, "scale": 1000, "frames": n, "duration_s": ...,
     "tracks": [[first sample, x0, y0, dx1, dy1, dx2, dy2, …], …],
     "contacts": [[t_ms, x, y, kind], …], "stats": {...}}
Sample i of the track is at i / fps seconds; x and y are integers in 1/scale of the frame;
inside a track every sample has the ball (gaps end a track). Contact kinds: 0 bounce (the
ball came down and went up slower: the floor), 1 low (came down, went up as fast: a dig or
a reception), 2 high (any other sharp turn: a set, an attack, a block).
"""

from __future__ import annotations

import gzip
import itertools
import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from wieplay.ml.features import HEIGHT, WIDTH, Arr, Signals, ball_tracks

SCHEMA = 1
SCALE = 1000
OUT_FPS = 15.0

# In model pixels (512x288) at 30 fps, as in wieplay/ml/features.py.
SPIKE_PX = 24.0  # a detection this far from both neighbours, which agree, is a shoe or a lamp
LINK_PX = 32.0  # per frame of gap: a hard spike moves ~28 px per frame
MAX_GAP = 5  # frames (0.17 s) bridged inside a track
MIN_TRACK = 8  # detections
MIN_TRACK_PX = 8.0  # a track that moves less is a ball held or lying
# Contacts: the turn between the motion over TURN_K frames before and after a frame.
TURN_K = 2
TURN_DEG = 40.0
MIN_SPEED_PX = 1.5  # per frame; slower than this a turn is noise
SPEED_RATIO = 2.2  # or a change of speed by this factor (a block, a tip)
BOUNCE_RATIO = 0.75  # out/in speed below this after coming down: the floor
NMS_FRAMES = 6
SMOOTH = 5  # frames in the smoothing window (quadratic, Savitzky-Golay)

BOUNCE, LOW, HIGH = 0, 1, 2


@dataclass
class Contact:
    frame: int
    x: float  # normalised
    y: float
    kind: int
    turn_deg: float
    speed_in: float  # model px per frame
    speed_out: float


@dataclass
class Track:
    """The cleaned ball path at the analysis rate. x, y normalised; NaN where no ball."""

    fps: float
    x: Arr
    y: Arr
    track_id: Arr  # int32, -1 outside a track
    filled: Arr  # bool: interpolated over a short gap
    contacts: list[Contact] = field(default_factory=list)

    @property
    def frames(self) -> int:
        return len(self.x)

    def spans(self) -> list[tuple[int, int]]:
        """(first, last) frame of each track."""
        ids = self.track_id
        if not len(ids):
            return []
        edges = np.flatnonzero(np.diff(ids) != 0) + 1
        starts = np.concatenate([[0], edges])
        ends = np.concatenate([edges, [len(ids)]]) - 1
        return [(int(s), int(e)) for s, e in zip(starts, ends, strict=True) if ids[s] >= 0]


def _despike(frames: Arr, xs: Arr, ys: Arr) -> Arr:
    """Keep-mask: drops a detection that is far from both its neighbours while they are
    close to each other (a shoe or a lamp for one frame inside a flight)."""
    keep = np.ones(len(frames), dtype=bool)
    if len(frames) < 3:
        return keep
    gap_prev = frames[1:-1] - frames[:-2]
    gap_next = frames[2:] - frames[1:-1]
    d_prev = np.hypot(xs[1:-1] - xs[:-2], ys[1:-1] - ys[:-2])
    d_next = np.hypot(xs[2:] - xs[1:-1], ys[2:] - ys[1:-1])
    d_skip = np.hypot(xs[2:] - xs[:-2], ys[2:] - ys[:-2])
    close = (gap_prev <= 3) & (gap_next <= 3)
    spike = (
        close
        & (d_prev > SPIKE_PX * gap_prev)
        & (d_next > SPIKE_PX * gap_next)
        & (d_skip < LINK_PX * (gap_prev + gap_next))
    )
    keep[1:-1] = ~spike
    return keep


def _savgol(values: Arr, window: int) -> Arr:
    """Quadratic Savitzky-Golay smoothing; the ends use the fit of the first/last window."""
    n = len(values)
    if n < window or window < 3:
        return values.copy()
    half = window // 2
    t = np.arange(-half, half + 1, dtype=np.float64)
    basis = np.stack([np.ones_like(t), t, t * t], axis=1)
    proj = np.linalg.pinv(basis)  # (3, window): coefficients from the window's values
    out = values.astype(np.float64).copy()
    windows = np.lib.stride_tricks.sliding_window_view(values.astype(np.float64), window)
    coeffs = windows @ proj.T  # (n - window + 1, 3)
    out[half : n - half] = coeffs[:, 0]
    for i in range(half):  # the ends from the first and last fits
        out[i] = coeffs[0] @ [1.0, i - half, (i - half) ** 2]
        j = n - half + i
        k = i + 1
        out[j] = coeffs[-1] @ [1.0, k, k * k]
    return out


def _contacts(x: Arr, y: Arr) -> list[tuple[int, int, float, float, float]]:
    """Frames inside one track (model px, gap-filled) where the ball turns sharply or
    changes speed: (frame, kind, turn, speed in, speed out), after non-maximum suppression."""
    n = len(x)
    k = TURN_K
    if n < 2 * k + 1:
        return []
    i = np.arange(k, n - k)
    vin = np.stack([x[i] - x[i - k], y[i] - y[i - k]], axis=1) / k
    vout = np.stack([x[i + k] - x[i], y[i + k] - y[i]], axis=1) / k
    sin_, sout = np.hypot(*vin.T), np.hypot(*vout.T)
    cos = (vin * vout).sum(1) / np.maximum(sin_ * sout, 1e-9)
    turn = np.degrees(np.arccos(np.clip(cos, -1, 1)))
    ratio = (sout + 0.5) / (sin_ + 0.5)
    moving = np.minimum(sin_, sout) >= MIN_SPEED_PX
    fast = np.maximum(sin_, sout) >= 2 * MIN_SPEED_PX
    hit = (moving & (turn >= TURN_DEG)) | (
        fast & ((ratio >= SPEED_RATIO) | (ratio <= 1 / SPEED_RATIO))
    )
    score = turn / 180 + np.abs(np.log(ratio))
    found: list[tuple[int, int, float, float, float]] = []
    taken = np.zeros(n, dtype=bool)
    for j in np.argsort(-score):
        if not hit[j]:
            continue
        f = int(i[j])
        if taken[max(0, f - NMS_FRAMES) : f + NMS_FRAMES + 1].any():
            continue
        taken[f] = True
        # Image y grows downwards: down then up is the lowest point of a flight.
        came_down, goes_up = vin[j, 1] > 0.3, vout[j, 1] < -0.3
        if came_down and goes_up:
            kind = BOUNCE if sout[j] < BOUNCE_RATIO * sin_[j] else LOW
        else:
            kind = HIGH
        found.append((f, kind, float(turn[j]), float(sin_[j]), float(sout[j])))
    return sorted(found)


def clean(sig: Signals) -> Track:
    """The ball path of a video's signals: plausible tracks only, gaps up to MAX_GAP
    frames filled, contacts found, each piece between contacts smoothed."""
    n = sig.frames
    x_out = np.full(n, np.nan, dtype=np.float64)
    y_out = np.full(n, np.nan, dtype=np.float64)
    ids = np.full(n, -1, dtype=np.int32)
    filled = np.zeros(n, dtype=bool)
    track = Track(fps=sig.fps, x=x_out, y=y_out, track_id=ids, filled=filled)
    if n == 0:
        return track
    stuck = ball_tracks(sig).stuck
    det = np.flatnonzero(sig.ball_vis.astype(bool) & ~stuck)
    xs = sig.ball_x[det].astype(np.float64)
    ys = sig.ball_y[det].astype(np.float64)
    keep = _despike(det, xs, ys)
    det, xs, ys = det[keep], xs[keep], ys[keep]

    # Link: consecutive detections close enough for the frames between them.
    runs: list[tuple[int, int]] = []
    start = 0
    for k in range(1, len(det) + 1):
        if k < len(det):
            gap = int(det[k] - det[k - 1])
            if gap <= MAX_GAP and np.hypot(xs[k] - xs[k - 1], ys[k] - ys[k - 1]) < LINK_PX * gap:
                continue
        runs.append((start, k))
        start = k

    next_id = 0
    for a, b in runs:
        if b - a < MIN_TRACK:
            continue
        rx, ry, rf = xs[a:b], ys[a:b], det[a:b]
        if max(np.ptp(rx), np.ptp(ry)) < MIN_TRACK_PX:
            continue
        frames = np.arange(rf[0], rf[-1] + 1)
        px = np.interp(frames, rf, rx)
        py = np.interp(frames, rf, ry)
        found = _contacts(px, py)
        # Smooth each piece between contacts on its own, so a contact stays a corner.
        cuts = [0, *[c[0] for c in found], len(frames) - 1]
        sx, sy = px.copy(), py.copy()
        for c0, c1 in itertools.pairwise(cuts):
            if c1 - c0 + 1 >= SMOOTH:
                sx[c0 : c1 + 1] = _savgol(px[c0 : c1 + 1], SMOOTH)
                sy[c0 : c1 + 1] = _savgol(py[c0 : c1 + 1], SMOOTH)
        for f, kind, turn, s_in, s_out in found:
            track.contacts.append(
                Contact(
                    frame=int(frames[f]),
                    x=float((px[f] + 0.5) / WIDTH),
                    y=float((py[f] + 0.5) / HEIGHT),
                    kind=kind,
                    turn_deg=turn,
                    speed_in=s_in,
                    speed_out=s_out,
                )
            )
        x_out[frames] = (sx + 0.5) / WIDTH
        y_out[frames] = (sy + 0.5) / HEIGHT
        ids[frames] = next_id
        seen = np.zeros(len(frames), dtype=bool)
        seen[rf - rf[0]] = True
        filled[frames] = ~seen
        next_id += 1
    track.contacts.sort(key=lambda c: c.frame)
    return track


def encode(
    track: Track, out_fps: float = OUT_FPS, duration_s: float | None = None
) -> dict[str, Any]:
    """The compact track for the browser (module docstring), at about `out_fps`."""
    step = max(1, round(track.fps / out_fps)) if track.fps > 0 else 1
    fps = track.fps / step if track.fps > 0 else out_fps
    tracks: list[list[int]] = []
    samples = 0
    for first, last in track.spans():
        s0 = -(-first // step)  # the first sample at or after the track's first frame
        frames = np.arange(s0 * step, last + 1, step)
        if len(frames) < 2:
            continue
        qx = np.rint(track.x[frames] * SCALE).astype(np.int64)
        qy = np.rint(track.y[frames] * SCALE).astype(np.int64)
        dx = np.diff(qx, prepend=0)
        dy = np.diff(qy, prepend=0)
        flat = np.empty(2 * len(frames), dtype=np.int64)
        flat[0::2], flat[1::2] = dx, dy
        tracks.append([int(s0), *flat.tolist()])
        samples += len(frames)
    contacts = [
        [
            round(c.frame / track.fps * 1000),
            round(c.x * SCALE),
            round(c.y * SCALE),
            c.kind,
        ]
        for c in track.contacts
    ]
    in_track = int((track.track_id >= 0).sum())
    return {
        "schema": SCHEMA,
        "fps": round(fps, 6),
        "scale": SCALE,
        "frames": -(-track.frames // step),
        "duration_s": round(duration_s if duration_s is not None else track.frames / track.fps, 3)
        if track.fps > 0
        else 0.0,
        "tracks": tracks,
        "contacts": contacts,
        "stats": {
            "analysis_fps": round(track.fps, 6),
            "tracks": len(tracks),
            "samples": samples,
            "contacts": len(contacts),
            "bounces": sum(1 for c in track.contacts if c.kind == BOUNCE),
            "in_track": round(in_track / max(track.frames, 1), 4),
            "filled": round(float(track.filled.sum()) / max(in_track, 1), 4),
        },
    }


def decode(payload: dict[str, Any]) -> tuple[Arr, Arr]:
    """The positions of an encoded track per sample (NaN where no ball): for checks and
    renders; the browser has its own (web/src/lib/ballTrack.ts)."""
    n = int(payload["frames"])
    scale = float(payload["scale"])
    x = np.full(n, np.nan)
    y = np.full(n, np.nan)
    for item in payload["tracks"]:
        s0, deltas = int(item[0]), np.asarray(item[1:], dtype=np.int64)
        qx = np.cumsum(deltas[0::2])
        qy = np.cumsum(deltas[1::2])
        end = min(n, s0 + len(qx))
        x[s0:end] = qx[: end - s0] / scale
        y[s0:end] = qy[: end - s0] / scale
    return x, y


def to_bytes(payload: dict[str, Any]) -> bytes:
    """Gzipped JSON, as stored and served (mtime 0: the same track gives the same bytes)."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return gzip.compress(raw, compresslevel=9, mtime=0)
