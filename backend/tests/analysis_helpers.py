"""Synthetic analysis signals: rallies with a ball in flight, movement and whistles."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from nomal.ml.features import AUDIO_HOP_S, GRID_COLS, GRID_ROWS, Signals


def synthetic_signals(
    rallies_s: Sequence[tuple[float, float]],
    duration_s: float,
    *,
    fps: float = 30.0,
    whistles: bool = True,
    audio: bool = True,
    seed: int = 0,
    throws: bool = False,
    squeaks: bool = False,
) -> Signals:
    """A video whose rallies are `rallies_s` (seconds): during a rally the ball flies (seen in
    ~85 % of frames, along a smooth path, from 1 s after the start to 1 s before the end)
    and people move more; between rallies a few stray detections; a lamp the tracker sees
    all the time; a serve whistle 3 s before and an end whistle 0.3 s before the end of
    each rally (when `whistles`)."""
    rng = np.random.default_rng(seed)
    n = round(duration_s * fps)
    t = np.arange(n) / fps
    in_rally = np.zeros(n, dtype=bool)
    flying = np.zeros(n, dtype=bool)
    for s, e in rallies_s:
        in_rally |= (t >= s) & (t < e)
        # As a coach tags: the rally opens a moment before the toss and closes about when
        # the referee whistles, a moment after the ball is dead.
        flying |= (t >= s + 1.0) & (t < e - 1.0)
        if throws:  # after the whistle, the ball goes back to the server
            flying |= (t >= e + 1.0) & (t < e + 3.0)
    vis = (flying & (rng.random(n) < 0.85)).astype(np.uint8)
    x = np.where(vis, 256 + 200 * np.sin(t * 1.3), -1).astype(np.float32)
    y = np.where(vis, 140 + 100 * np.cos(t * 2.1), -1).astype(np.float32)
    stray = (~in_rally) & (rng.random(n) < 0.03)
    vis[stray] = 1
    x[stray] = rng.uniform(0, 512, stray.sum())
    y[stray] = rng.uniform(0, 288, stray.sum())
    lamp = (~vis.astype(bool)) & (rng.random(n) < 0.6)
    vis[lamp] = 1
    x[lamp], y[lamp] = 500.0, 12.0
    motion = rng.normal(3.0, 0.5, (n, GRID_ROWS, GRID_COLS)) + 3.0 * in_rally[:, None, None]
    events: list[tuple[float, float, float, float, float]] = []
    if whistles:
        for s, e in rallies_s:
            events.append((s - 3.0, 0.5, 3800.0, 20.0, 40.0))
            events.append((e - 0.3, 0.6, 3800.0, 22.0, 45.0))
            if squeaks:  # shoes in the middle of the rally
                events.append(((s + e) / 2, 0.2, 2500.0, 12.0, 25.0))
    m = round(duration_s / AUDIO_HOP_S) if audio else 0
    ta = np.arange(m) * AUDIO_HOP_S
    band = rng.normal(-60, 1.5, m)
    for w in events:
        band[(ta >= w[0]) & (ta < w[0] + w[1])] += 25
    events.sort()
    return Signals(
        fps=fps,
        duration_s=duration_s,
        ball_vis=vis,
        ball_x=x,
        ball_y=y,
        ball_score=np.where(vis, 0.8, 0.1).astype(np.float16),
        motion=np.clip(motion, 0, 255).astype(np.float16),
        band_db=band.astype(np.float32),
        all_db=rng.normal(-40, 1.0, m).astype(np.float32),
        tonal_db=rng.normal(6, 1.0, m).astype(np.float32),
        peak_hz=np.full(m, 3000, dtype=np.uint16),
        whistles=np.array(events, dtype=np.float32).reshape(-1, 5),
        meta={"synthetic": True},
    )


def league_rallies(
    n: int, *, start_s: float = 60.0, seed: int = 0
) -> tuple[list[tuple[float, float]], float]:
    """n rallies of 5-20 s with 12-25 s of dead time between them; the video's length."""
    rng = np.random.default_rng(seed)
    out, at = [], start_s
    for _ in range(n):
        length = float(rng.uniform(5, 20))
        out.append((round(at, 2), round(at + length, 2)))
        at += length + float(rng.uniform(12, 25))
    return out, at + 30.0


def to_ms(spans: Sequence[tuple[float, float]]) -> list[tuple[int, int]]:
    return [(round(s * 1000), round(e * 1000)) for s, e in spans]
