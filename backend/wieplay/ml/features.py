"""Signals of one video and the per-step features the rally finders use (PLAN §10.1).

The analyse job stores *signals*: per analysed frame the ball (VballNet-Fast) and the motion
in a 3x4 grid, per audio frame (23 ms) the whistle band's level, the tonality and the
strongest pitch, and the whistles found in them. Features are derived from the signals on
every run, so a better feature or whistle rule needs no new pass over the video.

Times: frame i of the signals is at i / fps seconds of the video (the analysis rate divides
the source rate evenly, e.g. 60 → 30, 50 → 25). Steps are STEP_S long.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy.ndimage import median_filter

Arr = npt.NDArray[Any]

SCHEMA_VERSION = 1
STEP_S = 0.5
# Model input (VballNet-Fast): 512x288 RGB.
WIDTH, HEIGHT = 512, 288
# Motion grid: rows far/middle/near court (behind-the-end-line camera), four columns.
GRID_ROWS, GRID_COLS = 3, 4

# Audio (spike S0, s0_whistle.py): 22.05 kHz mono, 2048-point FFT every 512 samples.
SR = 22050
N_FFT = 2048
HOP = 512
BAND = (2000.0, 4500.0)
AUDIO_HOP_S = HOP / SR

# Ball tracks (spike S0, s0_report.py, scaled from 1920 px source to 512 px): a detection
# that sits within STUCK_PX of itself for most of ±2 s is a lamp, a line or a parked ball;
# detections at most two frames apart and closer than LINK_PX per frame are one track; a
# plausible track has at least 8 detections (~0.27 s at 30 fps) and moves (a ball that
# stays within MIN_TRACK_PX is held or parked, not in play).
STUCK_PX = 2.0
STUCK_SHARE = 0.375
LINK_PX = 32.0
MIN_TRACK = 8
MIN_TRACK_PX = 8.0


@dataclass
class Signals:
    """Everything the analyse job keeps of a video (the `features` artifact, .npz)."""

    fps: float
    duration_s: float
    ball_vis: Arr  # uint8 (frames,)
    ball_x: Arr  # float32, model pixels (0..512), -1 when not visible
    ball_y: Arr  # float32, model pixels (0..288), -1 when not visible
    ball_score: Arr  # float16, heatmap peak
    motion: Arr  # float16 (frames, GRID_ROWS, GRID_COLS), mean |Δgrey| per cell, 0..255
    band_db: Arr  # float16 (audio frames,), whistle band energy in dB
    all_db: Arr  # float16, energy above 100 Hz in dB
    tonal_db: Arr  # float16, band peak over band median in dB
    peak_hz: Arr  # uint16, the band's strongest frequency
    whistles: Arr  # float32 (n, 5): t, dur, hz, rise_db, tonal_db
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def frames(self) -> int:
        return len(self.ball_vis)

    @property
    def has_audio(self) -> bool:
        return len(self.band_db) > 0

    def save(self, path: Path) -> None:
        meta = {
            **self.meta,
            "schema_version": SCHEMA_VERSION,
            "fps": self.fps,
            "duration_s": self.duration_s,
            "width": WIDTH,
            "height": HEIGHT,
            "audio_hop_s": AUDIO_HOP_S,
        }
        with path.open("wb") as handle:
            np.savez_compressed(
                handle,
                meta=np.array(json.dumps(meta)),
                ball_vis=self.ball_vis.astype(np.uint8),
                ball_x=self.ball_x.astype(np.float32),
                ball_y=self.ball_y.astype(np.float32),
                ball_score=self.ball_score.astype(np.float16),
                motion=self.motion.astype(np.float16),
                band_db=self.band_db.astype(np.float16),
                all_db=self.all_db.astype(np.float16),
                tonal_db=self.tonal_db.astype(np.float16),
                peak_hz=self.peak_hz.astype(np.uint16),
                whistles=self.whistles.astype(np.float32).reshape(-1, 5),
            )

    @classmethod
    def load(cls, path: Path) -> Signals:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            if meta.get("schema_version") != SCHEMA_VERSION:
                raise ValueError(f"{path.name}: features schema {meta.get('schema_version')}")
            arrays = {k: data[k] for k in data.files if k != "meta"}
        return cls(
            fps=float(meta["fps"]), duration_s=float(meta["duration_s"]), meta=meta, **arrays
        )


# --- Audio ---------------------------------------------------------------------------------


class AudioFrames:
    """Whistle-band numbers per audio frame, fed in chunks (a 2 h match is 600 MB as float32
    samples; this keeps only a few floats per 23 ms)."""

    def __init__(self) -> None:
        self._rest = np.zeros(0, dtype=np.float32)
        self._parts: list[tuple[Arr, Arr, Arr, Arr]] = []
        freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
        self._band = (freqs >= BAND[0]) & (freqs <= BAND[1])
        self._above = freqs > 100
        self._band_freqs = freqs[self._band]
        self._window = np.hanning(N_FFT).astype(np.float32)

    def feed(self, samples: Arr) -> None:
        buf = np.concatenate([self._rest, samples.astype(np.float32, copy=False)])
        if len(buf) < N_FFT:
            self._rest = buf
            return
        n = 1 + (len(buf) - N_FFT) // HOP
        for i0 in range(0, n, 4096):
            idx = np.arange(i0, min(n, i0 + 4096))[:, None] * HOP + np.arange(N_FFT)[None, :]
            spec = np.abs(np.fft.rfft(buf[idx] * self._window, axis=1)) ** 2
            band = spec[:, self._band]
            eps = 1e-12
            e_band = band.sum(1) + eps
            e_all = spec[:, self._above].sum(1) + eps
            peak = band.max(1)
            tonal = 10 * np.log10(peak / (np.median(band, 1) + eps))
            self._parts.append(
                (
                    10 * np.log10(e_band),
                    10 * np.log10(e_all),
                    tonal,
                    self._band_freqs[np.argmax(band, 1)],
                )
            )
        self._rest = buf[n * HOP :]

    def finish(self) -> tuple[Arr, Arr, Arr, Arr]:
        if not self._parts:
            empty = np.zeros(0, dtype=np.float32)
            return empty, empty, empty, np.zeros(0, dtype=np.uint16)
        cols = list(zip(*self._parts, strict=True))
        band_db, all_db, tonal_db, peak_hz = (np.concatenate(c) for c in cols)
        return (
            band_db.astype(np.float32),
            all_db.astype(np.float32),
            tonal_db.astype(np.float32),
            peak_hz.astype(np.uint16),
        )


def detect_whistles(band_db: Arr, all_db: Arr, tonal_db: Arr, peak_hz: Arr) -> Arr:
    """Referee whistles (spike S0): the band holds > 25 % of the energy, its strongest bin
    stands > 18 dB out of the band, and the band is > 10 dB above its 10 s rolling floor;
    runs >= 0.2 s, parts < 0.6 s apart joined, at most 2.5 s. Returns (n, 5): t, dur, hz,
    rise_db, tonal_db."""
    if len(band_db) == 0:
        return np.zeros((0, 5), dtype=np.float32)
    band = band_db.astype(np.float32)
    share = 10 ** ((band - all_db.astype(np.float32)) / 10)
    tonal = tonal_db.astype(np.float32)
    floor = median_filter(band, size=round(10 / AUDIO_HOP_S), mode="nearest")
    rise = band - floor
    hit = (share > 0.25) & (tonal > 18) & (rise > 10)
    # Close 1-frame gaps; runs under 0.2 s are mostly shoe squeaks; a warbling whistle that
    # broke up is joined again when its parts are < 0.6 s apart.
    hit = np.convolve(hit.astype(np.int8), np.ones(3, np.int8), "same") > 0
    edges = np.diff(np.concatenate([[0], hit.astype(np.int8), [0]]))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    dt = AUDIO_HOP_S
    runs: list[list[int]] = []
    for a, b in zip(starts.tolist(), ends.tolist(), strict=True):
        if (b - a) * dt < 0.2:
            continue
        if runs and (a - runs[-1][1]) * dt < 0.6:
            runs[-1][1] = b
        else:
            runs.append([a, b])
    out = []
    for i, j in runs:
        dur = (j - i) * dt
        if dur <= 2.5:
            hz = float(np.median(peak_hz[i:j]))
            # The time of an audio frame is the centre of its FFT window.
            at = i * dt + N_FFT / 2 / SR
            out.append((at, dur, hz, float(rise[i:j].max()), float(tonal[i:j].max())))
    return np.array(out, dtype=np.float32).reshape(-1, 5)


# --- Ball tracks ---------------------------------------------------------------------------


@dataclass
class Tracks:
    in_track: Arr  # bool (frames,): detection belongs to a plausible track
    stuck: Arr  # bool (frames,)
    speed: Arr  # float32 (frames,): frame widths per second along the track, NaN elsewhere
    length_s: Arr  # float32 (frames,): length of the frame's track in seconds, 0 elsewhere


def ball_tracks(sig: Signals) -> Tracks:
    n = sig.frames
    vis = sig.ball_vis.astype(bool)
    det = np.flatnonzero(vis)
    xs, ys = sig.ball_x[det].astype(np.float32), sig.ball_y[det].astype(np.float32)
    stuck = np.zeros(n, dtype=bool)
    span = round(2 * sig.fps)
    need = STUCK_SHARE * 2 * span
    lo_all = np.searchsorted(det, det - span)
    hi_all = np.searchsorted(det, det + span, side="right")
    for k in range(len(det)):
        lo, hi = lo_all[k], hi_all[k]
        if hi - lo <= need:
            continue
        near = np.hypot(xs[lo:hi] - xs[k], ys[lo:hi] - ys[k]) < STUCK_PX
        if near.sum() > need:
            stuck[det[k]] = True
    in_track = np.zeros(n, dtype=bool)
    speed = np.full(n, np.nan, dtype=np.float32)
    length = np.zeros(n, dtype=np.float32)
    good = [k for k in range(len(det)) if not stuck[det[k]]]
    run: list[int] = []
    steps: list[float] = []

    def close() -> None:
        if len(run) >= MIN_TRACK and max(np.ptp(xs[run]), np.ptp(ys[run])) >= MIN_TRACK_PX:
            frames = det[run]
            in_track[frames] = True
            length[frames] = (frames[-1] - frames[0] + 1) / sig.fps
            speed[frames[1:]] = steps
            speed[frames[0]] = steps[0]

    for k in good:
        if run:
            prev = run[-1]
            gap = int(det[k] - det[prev])
            dist = float(np.hypot(xs[k] - xs[prev], ys[k] - ys[prev]))
            if gap <= 2 and dist < LINK_PX * gap:
                run.append(k)
                steps.append(dist / gap * sig.fps / WIDTH)
                continue
            close()
        run, steps = [k], []
    if run:
        close()
    return Tracks(in_track=in_track, stuck=stuck, speed=speed, length_s=length)


# --- Per-step features ---------------------------------------------------------------------


def _step_index(n: int, rate: float) -> Arr:
    """The step of each of n samples taken at `rate` per second."""
    return (np.arange(n) / rate / STEP_S).astype(np.int64)


def _mean_by_step(values: Arr, steps: Arr, n_steps: int, mask: Arr | None = None) -> Arr:
    """Mean of values per step (NaN where a step has none)."""
    keep = steps < n_steps
    if mask is not None:
        keep &= mask
    v = values[keep].astype(np.float64)
    s = steps[keep]
    ok = ~np.isnan(v)
    total = np.bincount(s[ok], weights=v[ok], minlength=n_steps)[:n_steps]
    count = np.bincount(s[ok], minlength=n_steps)[:n_steps]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / np.maximum(count, 1), np.nan)


def _max_by_step(values: Arr, steps: Arr, n_steps: int) -> Arr:
    out = np.full(n_steps, -np.inf)
    keep = steps < n_steps
    np.maximum.at(out, steps[keep], values[keep].astype(np.float64))
    return np.where(np.isinf(out), np.nan, out)


def rolling(x: Arr, window_steps: int, *, offset: int = 0) -> Arr:
    """Mean over `window_steps` steps centred on each step (shifted by `offset`), NaN-aware."""
    v = np.nan_to_num(x.astype(np.float64), nan=0.0)
    c = (~np.isnan(x)).astype(np.float64)
    kernel = np.ones(max(1, window_steps))
    total = np.convolve(v, kernel, "same")
    count = np.convolve(c, kernel, "same")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(count > 0, total / np.maximum(count, 1e-9), np.nan)
    if offset:
        out = np.roll(out, -offset)
        if offset > 0:
            out[-offset:] = np.nan
        else:
            out[:-offset] = np.nan
    return out


def _relative(x: Arr) -> Arr:
    """Divide by the video's median, so halls with more movement compare."""
    med = np.nanmedian(x) if np.any(~np.isnan(x)) else np.nan
    if not np.isfinite(med) or med <= 1e-6:
        return x.astype(np.float64)
    return x / med


@dataclass
class StepFeatures:
    names: list[str]
    X: Arr  # float32 (steps, features)
    whistle_rate: float  # per minute
    n_steps: int

    def col(self, name: str) -> Arr:
        out: Arr = self.X[:, self.names.index(name)]
        return out


def n_steps_of(duration_s: float) -> int:
    return max(1, int(np.ceil(duration_s / STEP_S)))


def step_features(sig: Signals) -> StepFeatures:
    n_steps = n_steps_of(sig.duration_s)
    fsteps = _step_index(sig.frames, sig.fps)
    tracks = ball_tracks(sig)
    vis = sig.ball_vis.astype(bool)
    cols: dict[str, Arr] = {}
    cols["ball_det"] = _mean_by_step(vis & ~tracks.stuck, fsteps, n_steps)
    cols["ball_trk"] = _mean_by_step(tracks.in_track, fsteps, n_steps)
    cols["ball_speed"] = _mean_by_step(tracks.speed, fsteps, n_steps, tracks.in_track)
    y = np.where(tracks.in_track, sig.ball_y / HEIGHT, np.nan)
    cols["ball_y"] = _mean_by_step(y, fsteps, n_steps)
    cols["ball_ymin"] = -_max_by_step(np.nan_to_num(-y, nan=-np.inf), fsteps, n_steps)
    cols["ball_len"] = _mean_by_step(tracks.length_s, fsteps, n_steps, tracks.in_track)
    for name in ("ball_det", "ball_trk"):
        cols[name] = np.nan_to_num(cols[name], nan=0.0)

    motion = sig.motion.astype(np.float32)
    if motion.size:
        total = motion.mean(axis=(1, 2))
        cols["motion"] = _relative(_mean_by_step(total, fsteps, n_steps))
        for r in range(GRID_ROWS):
            cols[f"motion_r{r}"] = _relative(
                _mean_by_step(motion[:, r, :].mean(1), fsteps, n_steps)
            )
        cols["motion_mid"] = _relative(
            _mean_by_step(motion[:, :, 1:3].mean((1, 2)), fsteps, n_steps)
        )
        cols["motion_side"] = _relative(
            _mean_by_step(motion[:, :, [0, GRID_COLS - 1]].mean((1, 2)), fsteps, n_steps)
        )
    else:
        for name in ("motion", "motion_r0", "motion_r1", "motion_r2", "motion_mid", "motion_side"):
            cols[name] = np.full(n_steps, np.nan)

    if sig.has_audio:
        asteps = _step_index(len(sig.all_db), 1 / AUDIO_HOP_S)
        level = _mean_by_step(sig.all_db.astype(np.float32), asteps, n_steps)
        cols["audio_db"] = level - np.nanmedian(level)
        band = sig.band_db.astype(np.float32)
        floor = median_filter(band, size=round(10 / AUDIO_HOP_S), mode="nearest")
        cols["band_rise"] = _max_by_step(band - floor, asteps, n_steps)
    else:
        cols["audio_db"] = np.full(n_steps, np.nan)
        cols["band_rise"] = np.full(n_steps, np.nan)

    t = (np.arange(n_steps) + 0.5) * STEP_S
    whistles = sig.whistles.reshape(-1, 5)
    on = np.zeros(n_steps)
    since = np.full(n_steps, 60.0)
    until = np.full(n_steps, 60.0)
    if len(whistles):
        w_start = whistles[:, 0].astype(np.float64)
        w_end = w_start + whistles[:, 1]
        for s, e in zip(w_start, w_end, strict=True):
            on[int(s / STEP_S) : int(e / STEP_S) + 1] = 1
        # Time since the last whistle ended / until the next one starts.
        k = np.searchsorted(w_end, t, side="right") - 1
        since = np.where(k >= 0, np.minimum(60.0, t - w_end[np.maximum(k, 0)]), 60.0)
        j = np.searchsorted(w_start, t, side="left")
        until = np.where(
            j < len(w_start), np.minimum(60.0, w_start[np.minimum(j, len(w_start) - 1)] - t), 60.0
        )
    minutes = max(sig.duration_s / 60, 1e-6)
    rate = len(whistles) / minutes
    cols["whistle_on"] = on[:n_steps]
    cols["whistle_since"] = np.maximum(since, 0.0)
    cols["whistle_until"] = np.maximum(until, 0.0)
    cols["whistle_rate"] = np.full(n_steps, rate)

    # Context: what happens around each step (a rally is seconds of activity after quiet).
    per_s = round(1 / STEP_S)
    for name in ("ball_trk", "ball_det", "motion", "audio_db"):
        base = cols[name]
        for seconds in (2, 6, 20):
            cols[f"{name}_c{seconds}"] = rolling(base, seconds * per_s)
        cols[f"{name}_past3"] = rolling(base, 3 * per_s, offset=-(3 * per_s) // 2 - 1)
        cols[f"{name}_next3"] = rolling(base, 3 * per_s, offset=(3 * per_s) // 2 + 1)

    names = list(cols)
    X = np.stack([np.asarray(cols[k], dtype=np.float32)[:n_steps] for k in names], axis=1)
    return StepFeatures(names=names, X=X, whistle_rate=rate, n_steps=n_steps)
