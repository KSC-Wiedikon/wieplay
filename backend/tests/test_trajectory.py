"""The ball's path (wieplay/ml/trajectory.py): outliers out, short gaps filled, contacts and
bounces found, the compact track round-trips."""

from __future__ import annotations

import gzip
import json

import numpy as np
import pytest

from wieplay.ml import trajectory as tr
from wieplay.ml.features import HEIGHT, WIDTH, Signals

FPS = 30.0


def signals(x: np.ndarray, y: np.ndarray, vis: np.ndarray) -> Signals:
    n = len(x)
    return Signals(
        fps=FPS,
        duration_s=n / FPS,
        ball_vis=vis.astype(np.uint8),
        ball_x=np.where(vis, x, -1).astype(np.float32),
        ball_y=np.where(vis, y, -1).astype(np.float32),
        ball_score=np.where(vis, 0.9, 0.1).astype(np.float16),
        motion=np.zeros((n, 3, 4), np.float16),
        band_db=np.zeros(0, np.float32),
        all_db=np.zeros(0, np.float32),
        tonal_db=np.zeros(0, np.float32),
        peak_hz=np.zeros(0, np.uint16),
        whistles=np.zeros((0, 5), np.float32),
    )


def flight(n_before: int = 30, n_after: int = 30, bounce_at: int = 60) -> tuple[np.ndarray, ...]:
    """A ball falling onto the floor and bouncing up slower: down to y = 250 at frame
    `bounce_at`, then up again (model pixels, image y grows downwards)."""
    n = bounce_at + n_after + 20
    t = np.arange(n, dtype=np.float64)
    x = 100 + 2.0 * t
    y = np.full(n, np.nan)
    down = t <= bounce_at
    # Falling faster and faster until it hits the floor; it comes off at half the speed.
    s = bounce_at - t[down]
    y[down] = 250 - 5.0 * s + 0.03 * s**2
    u = t[~down] - bounce_at
    y[~down] = 250 - 2.5 * u + 0.03 * u**2
    vis = np.zeros(n, dtype=bool)
    vis[bounce_at - n_before : bounce_at + n_after] = True
    return x, y, vis


def test_a_clean_flight_comes_out_smooth_with_its_bounce() -> None:
    x, y, vis = flight()
    track = tr.clean(signals(x, y, vis))
    [(first, last)] = track.spans()
    assert (first, last) == (30, 89)
    err = np.hypot(track.x[first : last + 1] * WIDTH - 0.5 - x[first : last + 1],
                   track.y[first : last + 1] * HEIGHT - 0.5 - y[first : last + 1])  # fmt: skip
    assert err.max() < 1.5
    bounces = [c for c in track.contacts if c.kind == tr.BOUNCE]
    assert len(bounces) == 1 and abs(bounces[0].frame - 60) <= 2
    assert bounces[0].speed_out < bounces[0].speed_in


def test_spikes_and_stuck_spots_are_removed_and_short_gaps_filled() -> None:
    x, y, vis = flight()
    rng = np.random.default_rng(3)
    xs, ys = x.copy(), y.copy()
    # A shoe for one frame in the middle of the flight.
    xs[45], ys[45] = 480.0, 20.0
    # A two-frame hole, then a six-frame hole (longer than MAX_GAP: the track splits).
    vis = vis.copy()
    vis[50:52] = False
    vis[70:76] = False
    # A lamp the tracker sees all the time, in another corner.
    n = len(x)
    lamp = np.zeros(n, dtype=bool)
    lamp[:] = True
    lamp[vis] = False
    xs[lamp] = 505.0 + rng.normal(0, 0.2, lamp.sum())
    ys[lamp] = 8.0 + rng.normal(0, 0.2, lamp.sum())
    track = tr.clean(signals(xs, ys, vis | lamp))
    spans = track.spans()
    assert spans == [(30, 69), (76, 89)]
    assert track.filled[50] and track.filled[51] and not track.filled[49]
    assert (
        abs(track.x[45] * WIDTH - 0.5 - x[45]) < 2 and abs(track.y[45] * HEIGHT - 0.5 - y[45]) < 2
    )
    assert np.isnan(track.x[:30]).all()  # the lamp is nowhere in the track


def test_a_fast_return_after_coming_down_is_a_low_contact() -> None:
    n = 80
    t = np.arange(n, dtype=np.float64)
    x = 200 + 2.0 * t
    y = np.where(t <= 40, 120 + 3.0 * t, 240 - 5.0 * (t - 40))  # down, then up faster: a dig
    vis = np.ones(n, dtype=bool)
    track = tr.clean(signals(x, y, vis))
    kinds = [(c.frame, c.kind) for c in track.contacts]
    assert any(abs(f - 40) <= 2 and k == tr.LOW for f, k in kinds)


def test_held_balls_and_short_blips_are_no_track() -> None:
    n = 120
    x = np.full(n, 250.0)
    y = np.full(n, 140.0) + np.random.default_rng(1).normal(0, 0.5, n)
    vis = np.zeros(n, dtype=bool)
    vis[10:60] = True  # held still: moves less than MIN_TRACK_PX
    vis[80:85] = True  # five frames
    x[80:85] = np.arange(5) * 10.0
    track = tr.clean(signals(x, y, vis))
    assert track.spans() == [] and track.contacts == []


def test_the_compact_track_round_trips() -> None:
    x, y, vis = flight()
    track = tr.clean(signals(x, y, vis))
    payload = tr.encode(track)
    assert payload["fps"] == 15.0 and payload["scale"] == tr.SCALE
    assert payload["frames"] == (len(x) + 1) // 2
    [item] = payload["tracks"]
    assert item[0] == 15  # sample 15 = frame 30
    dx, dy = tr.decode(payload)
    frames = np.arange(30, 90, 2)
    assert np.nanmax(np.abs(dx[frames // 2] - track.x[frames])) <= 0.5 / tr.SCALE + 1e-9
    assert np.nanmax(np.abs(dy[frames // 2] - track.y[frames])) <= 0.5 / tr.SCALE + 1e-9
    assert np.isnan(dx[:15]).all()
    [[t_ms, cx, cy, kind]] = [c for c in payload["contacts"] if c[3] == tr.BOUNCE]
    assert abs(t_ms - 2000) <= 70 and kind == tr.BOUNCE
    assert 0 < cx < tr.SCALE and 0 < cy < tr.SCALE
    data = tr.to_bytes(payload)
    assert data == tr.to_bytes(payload)  # same track, same bytes (ETag)
    assert json.loads(gzip.decompress(data)) == payload


@pytest.mark.parametrize("fps", [25.0, 30.0])
def test_sample_times_follow_the_analysis_rate(fps: float) -> None:
    x, y, vis = flight()
    sig = signals(x, y, vis)
    sig.fps = fps
    payload = tr.encode(tr.clean(sig))
    assert payload["fps"] == pytest.approx(fps / 2)
    assert payload["stats"]["analysis_fps"] == pytest.approx(fps)


def test_an_empty_video_has_an_empty_track() -> None:
    empty = np.zeros(0)
    payload = tr.encode(tr.clean(signals(empty, empty, empty.astype(bool))))
    assert payload["tracks"] == [] and payload["contacts"] == [] and payload["frames"] == 0
