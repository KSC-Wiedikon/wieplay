"""Rally finder (PLAN §10.1): metrics, signals → features → rallies (v0), learning (v1)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.analysis_helpers import league_rallies, synthetic_signals, to_ms
from wieplay.ml import metrics, rallies
from wieplay.ml.features import (
    AUDIO_HOP_S,
    SR,
    AudioFrames,
    Signals,
    ball_tracks,
    detect_whistles,
    step_features,
)

# --- metrics -------------------------------------------------------------------------------


def test_boundary_f1_counts_boundaries_within_a_second() -> None:
    true = [(10_000, 20_000), (40_000, 48_000)]
    assert metrics.evaluate(true, true)["boundary"]["f1"] == 1.0
    near = [(10_900, 19_100), (39_100, 48_900)]
    assert metrics.evaluate(near, true)["boundary"]["f1"] == 1.0
    far = [(11_500, 20_000), (40_000, 49_500)]  # one start and one end 1.5 s off
    m = metrics.evaluate(far, true)
    assert m["boundary"] == {"precision": 0.5, "recall": 0.5, "f1": 0.5}
    assert (m["start"]["f1"], m["end"]["f1"]) == (0.5, 0.5)
    assert m["rally"]["f1"] == 1.0  # both still overlap their rally with IoU >= 0.5


def test_extra_and_missing_rallies() -> None:
    true = [(0, 10_000), (20_000, 30_000), (40_000, 50_000)]
    pred = [(0, 10_000), (20_000, 30_000), (60_000, 65_000), (70_000, 75_000)]
    m = metrics.evaluate(pred, true)
    assert (m["rallies_true"], m["rallies_pred"]) == (3, 4)
    assert m["boundary"]["precision"] == 0.5 and m["boundary"]["recall"] == pytest.approx(0.6667)
    assert m["rally"]["recall"] == pytest.approx(0.6667)
    assert m["live_iou"] == pytest.approx(20 / 40)
    # Each true boundary is matched once: two proposals ending at the same time count once.
    assert (
        metrics.evaluate([(0, 10_000), (5_000, 10_000)], [(0, 10_000)])["end"]["precision"] == 0.5
    )
    assert metrics.evaluate([], [])["boundary"]["f1"] == 1.0
    assert metrics.evaluate([], true)["boundary"]["f1"] == 0.0


def test_summary_averages_over_games() -> None:
    a = metrics.evaluate([(0, 10_000)], [(0, 10_000)])
    b = metrics.evaluate([(0, 10_000)], [(5_000, 20_000)])
    s = metrics.summarise([a, b])
    assert s["videos"] == 2 and s["boundary_f1"] == 0.5
    assert s["boundary_f1_pooled"] == 0.5
    assert metrics.summarise([]) == {"videos": 0}


# --- signals and features ------------------------------------------------------------------


def test_whistles_from_a_tone_in_noise() -> None:
    rng = np.random.default_rng(1)
    t = np.arange(SR * 30) / SR
    x = rng.normal(0, 0.01, len(t)).astype(np.float32)
    for start in (5.0, 12.0, 20.0):  # 0.5 s at 3.8 kHz
        on = (t >= start) & (t < start + 0.5)
        x[on] += 0.3 * np.sin(2 * np.pi * 3800 * t[on]).astype(np.float32)
    blip = (t >= 25) & (t < 25.03)  # a squeak
    x[blip] += 0.3 * np.sin(2 * np.pi * 3800 * t[blip]).astype(np.float32)
    frames = AudioFrames()
    for i in range(0, len(x), 12_345):  # chunks that do not line up with the hop
        frames.feed(x[i : i + 12_345])
    band, total, tonal, peak = frames.finish()
    assert len(band) == 1 + (len(x) - 2048) // 512
    found = detect_whistles(band, total, tonal, peak)
    assert [round(float(w[0])) for w in found] == [5, 12, 20]  # the squeak is too short
    assert all(abs(float(w[0]) - start) < 0.1 for w, start in zip(found, (5, 12, 20), strict=True))
    assert all(0.45 < float(w[1]) < 0.7 for w in found)  # the FFT window adds a little
    assert all(3700 <= w[2] <= 3900 for w in found)
    assert detect_whistles(*(np.zeros(0),) * 4).shape == (0, 5)


def test_ball_tracks_ignore_stuck_spots_and_strays() -> None:
    sig = synthetic_signals([(10.0, 20.0)], 40.0, seed=3)
    tracks = ball_tracks(sig)
    lamp = (sig.ball_x == 500.0) & (sig.ball_y == 12.0)
    assert tracks.stuck[lamp].mean() > 0.9 and not tracks.in_track[lamp].any()
    t = np.arange(sig.frames) / sig.fps
    flying = (t >= 11) & (t < 19) & sig.ball_vis.astype(bool) & ~lamp
    assert tracks.in_track[flying].mean() > 0.9
    stray = sig.ball_vis.astype(bool) & ~lamp & ((t < 10) | (t > 20))
    assert tracks.in_track[stray].mean() < 0.05


def test_step_features_shape_and_names() -> None:
    sig = synthetic_signals([(10.0, 20.0)], 40.0, seed=3)
    f = step_features(sig)
    assert f.X.shape == (80, len(f.names)) and f.n_steps == 80
    assert {"ball_trk", "motion", "audio_db", "whistle_since", "ball_trk_next3"} <= set(f.names)
    trk = f.col("ball_trk")
    assert trk[25:35].mean() > 0.6 and trk[:15].mean() < 0.1
    silent = step_features(synthetic_signals([(10.0, 20.0)], 40.0, audio=False, whistles=False))
    assert np.isnan(silent.col("audio_db")).all() and silent.whistle_rate == 0
    assert silent.names == f.names  # the same columns, so one model serves both


def test_signals_round_trip(tmp_path: Path) -> None:
    sig = synthetic_signals([(10.0, 20.0)], 40.0)
    sig.save(tmp_path / "s.npz")
    back = Signals.load(tmp_path / "s.npz")
    assert back.fps == 30.0 and back.frames == sig.frames
    assert np.array_equal(back.ball_vis, sig.ball_vis)
    assert np.allclose(back.whistles, sig.whistles)
    assert back.meta["synthetic"] is True and back.meta["schema_version"] == 1


# --- v0 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("referee", [True, False])
def test_v0_finds_the_rallies_with_and_without_whistles(referee: bool) -> None:
    true, duration = league_rallies(30, seed=1)
    sig = synthetic_signals(true, duration, whistles=referee, audio=referee, seed=2)
    found = rallies.find_v0(sig)
    m = metrics.evaluate([p.span for p in found], to_ms(true))
    assert m["rally"]["f1"] >= 0.95
    assert m["boundary"]["f1"] >= 0.9
    assert all(0 < p.confidence <= 1 for p in found)
    if referee:  # ended by a whistle: more confident
        assert min(p.confidence for p in found) >= 0.5


def test_v0_ignores_warmup_and_short_bursts() -> None:
    sig = synthetic_signals([(10.0, 160.0), (200.0, 201.5), (230.0, 245.0)], 300.0, seed=4)
    found = rallies.find_v0(sig)
    # 150 s of play is a warm-up, 1.5 s is a ball thrown back: only the real rally stays.
    assert [(round(p.start_ms / 1000), round(p.end_ms / 1000)) for p in found] == [(230, 245)]


def test_v0_with_a_referee_ignores_squeaks_and_balls_thrown_back() -> None:
    """What the first real match showed: a shoe squeak must not split a rally, and the ball
    thrown back to the server after the whistle is no rally."""
    true, duration = league_rallies(20, seed=8)
    sig = synthetic_signals(true, duration, seed=9, throws=True, squeaks=True)
    found = rallies.find_v0(sig)
    m = metrics.evaluate([p.span for p in found], to_ms(true))
    assert len(found) == 20 and m["rally"]["f1"] == 1.0
    assert m["end"]["f1"] == 1.0  # at the end whistle
    # Without the whistles the throws are too short to pass as rallies.
    silent = synthetic_signals(true, duration, seed=9, throws=True, whistles=False, audio=False)
    assert len(rallies.find_v0(silent)) == 20


# --- v1 ------------------------------------------------------------------------------------


def test_hysteresis_turns_probabilities_into_rallies() -> None:
    prob = np.zeros(100)
    prob[10:30] = 0.9
    prob[18:20] = 0.4  # dips above `off` do not split a rally
    prob[50:53] = 0.9  # 1.5 s: too short
    prob[60:80] = 0.8
    prob[69] = 0.0  # a single-step hole is smoothed over
    found = rallies.segments_from_prob(prob)
    assert [(p.start_ms, p.end_ms) for p in found] == [(5_000, 15_000), (30_000, 40_000)]
    assert found[0].confidence == pytest.approx(0.85)


def two_games(seed: int = 0) -> list[rallies.TrainVideo]:
    out = []
    for k, referee in enumerate((True, False)):
        true, duration = league_rallies(25, seed=seed + k)
        sig = synthetic_signals(true, duration, whistles=referee, audio=referee, seed=seed + 7 + k)
        out.append(rallies.TrainVideo(f"game-{k}", sig, to_ms(true)))
    return out


def test_leave_one_game_out_scores_v0_and_v1() -> None:
    games = two_games()
    report = rallies.cross_validate(games, threads=2)
    assert set(report["videos"]) == {"game-0", "game-1"}
    for entry in report["videos"].values():
        assert entry["v0"]["rallies_true"] == 25 and entry["v1"]["rallies_true"] == 25
    assert report["summary"]["v1"]["videos"] == 2
    assert report["summary"]["v1"]["boundary_f1"] >= 0.8
    assert report["v1_better"] == (
        report["summary"]["v1"]["boundary_f1"] > report["summary"]["v0"]["boundary_f1"]
    )
    # One game cannot be held out against anything: v0 only, v1 never preferred.
    single = rallies.cross_validate(games[:1])
    assert single["summary"]["v1"] == {"videos": 0} and single["v1_better"] is False


def test_a_trained_model_is_stored_and_checked(tmp_path: Path) -> None:
    games = two_games(seed=3)
    model = rallies.fit(games, threads=2)
    data = model.dumps()
    path = tmp_path / "rally.pkl"
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    back = rallies.RallyModel.load(path, sha)
    true, duration = league_rallies(20, seed=40)
    found = back.predict(synthetic_signals(true, duration, seed=41))
    assert metrics.evaluate([p.span for p in found], to_ms(true))["rally"]["f1"] >= 0.9
    assert back.trained_on == ["game-0", "game-1"]
    with pytest.raises(ValueError, match="SHA-256"):
        rallies.RallyModel.load(path, "0" * 64)


def test_rejected_proposals_weigh_more_and_each_game_the_same() -> None:
    """Hard negatives: the dead time inside a proposal a coach rejected weighs
    `hard_negative` times an ordinary step; the part a kept rally covers does not."""
    rallies_ms = [(1_000, 2_000)]
    rejected = [(1_500, 3_000)]  # half of it is the kept rally's
    w = rallies.game_weights(10, rallies_ms, rejected, 3.0)
    assert w.sum() == pytest.approx(1.0)
    ordinary = w[0]
    assert w.tolist() == pytest.approx([ordinary] * 4 + [3 * ordinary] * 2 + [ordinary] * 4)
    assert ordinary == pytest.approx(1 / 14)  # 8 ordinary steps + 2 at 3x
    # No rejections (or a weight of 1): every step the same, as before.
    assert rallies.game_weights(10, rallies_ms, [], 3.0).tolist() == pytest.approx([0.1] * 10)
    assert rallies.game_weights(10, rallies_ms, rejected, 1.0).tolist() == pytest.approx([0.1] * 10)


def test_training_uses_the_hard_negative_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    from sklearn.ensemble import HistGradientBoostingClassifier

    seen: dict[str, np.ndarray] = {}
    original = HistGradientBoostingClassifier.fit

    def spy(self: Any, X: Any, y: Any, **kw: Any) -> Any:
        seen["w"] = np.asarray(kw["sample_weight"])
        return original(self, X, y, **kw)

    monkeypatch.setattr(HistGradientBoostingClassifier, "fit", spy)
    a, b = two_games(seed=5)
    end = a.rallies[0][1]
    a.rejected = [(end + 3_000, end + 8_000)]  # ten steps of dead time after rally 1
    rallies.fit([a, b], rallies.V1Params(max_iter=5), threads=1)
    n_a = len(a.data()[0])
    w_a, w_b = seen["w"][:n_a], seen["w"][n_a:]
    assert w_a.sum() == pytest.approx(w_b.sum())  # each game counts the same
    hard = rallies.labels_for(n_a, a.rejected) == 1
    assert hard.sum() == 10
    assert np.allclose(w_a[hard], 3 * w_a[0]) and np.allclose(w_a[~hard], w_a[0])
    assert np.allclose(w_b, w_b[0])  # no rejections there


def test_whistle_context_tells_serve_whistles_from_end_whistles() -> None:
    """The owner's case (02.10.2026): after the end whistle the ball is thrown back over the
    net. That ball follows a whistle that ended play; a rally's ball follows a serve
    whistle that came after dead time."""
    true = [(10.0, 20.0), (40.0, 52.0)]
    sig = synthetic_signals(true, 70.0, seed=3, throws=True, squeaks=True)
    f = step_features(sig)
    ctx = rallies.whistle_context(sig, f, rallies.V0Params(min_serves=1))
    last_play, serve_since = ctx[:, 0], ctx[:, 1]
    assert np.isnan(last_play[:12]).all() and (serve_since[:12] == 60).all()  # none yet
    in_rally = slice(24, 36)  # 12-18 s: after the serve whistle at 7-7.5 s
    assert (last_play[in_rally] == 0).all()  # the squeak at 15 s is no whistle
    assert serve_since[in_rally].min() > 4 and serve_since[in_rally].max() < 11
    thrown = slice(42, 46)  # 21-23 s: the ball goes back after the end whistle at 19.7 s
    assert (last_play[thrown] > 0.3).all() and (serve_since[thrown] > 13).all()
    X, names = rallies.design(sig, f)
    assert names[-3:] == ["v0", *rallies.WHISTLE_CONTEXT] and X.shape == (f.n_steps, len(names))
    silent = synthetic_signals(true, 70.0, whistles=False, audio=False)
    quiet = rallies.whistle_context(silent, step_features(silent))
    assert np.isnan(quiet[:, 0]).all() and (quiet[:, 1] == 60).all()


def test_a_model_loads_with_params_of_another_version(tmp_path: Path) -> None:
    import pickle

    model = rallies.fit(two_games(seed=6), rallies.V1Params(max_iter=5), threads=1)
    stored = pickle.loads(model.dumps())  # noqa: S301 - our own bytes
    stored["params"].pop("hard_negative")  # trained before hard negatives
    stored["params"]["later_knob"] = 1  # or by a newer version
    data = pickle.dumps(stored)
    path = tmp_path / "rally.pkl"
    path.write_bytes(data)
    back = rallies.RallyModel.load(path, hashlib.sha256(data).hexdigest())
    assert back.params.hard_negative == rallies.V1Params().hard_negative
    assert back.params.max_iter == 5


def test_labels_cover_the_steps_inside_rallies() -> None:
    y = rallies.labels_for(10, [(1_000, 2_000), (3_200, 3_900)])
    assert y.tolist() == [0, 0, 1, 1, 0, 0, 1, 1, 0, 0]  # step centres 3.25 s, 3.75 s
    assert pytest.approx(512 / 22050) == AUDIO_HOP_S


# --- per team (PLAN §10.5b) ------------------------------------------------------------------


def team_games(seed: int = 0) -> tuple[list[rallies.TrainVideo], dict[str, str]]:
    """Team "a" plays in a hall where the ball goes back to the server after every rally and
    nobody whistles; team "b" in halls with a referee."""
    out, teams = [], {}
    for k in range(5):
        hall_a = k < 3
        true, duration = league_rallies(14, seed=seed + k)
        sig = synthetic_signals(
            true, duration, whistles=not hall_a, audio=not hall_a, throws=hall_a, seed=seed + 20 + k
        )
        vid = f"game-{k}"
        out.append(rallies.TrainVideo(vid, sig, to_ms(true)))
        teams[vid] = "a" if hall_a else "b"
    return out, teams


def test_a_team_s_own_variant_is_weighed_on_its_held_out_games() -> None:
    games, teams = team_games()
    p = rallies.V1Params(max_iter=30)
    found = rallies.per_team(games, teams, p, threads=2)
    a, b = found["a"], found["b"]
    assert a["games"] == 3 and a["held_out"] == 3 and b["games"] == 2
    assert set(a["boundary_f1"]) == {"pooled", "weighted", "calibrated"}
    assert a["threshold"] in rallies.THRESHOLDS and a["boost"] == rallies.TEAM_BOOST
    for entry in (a, b):
        best = max(entry["boundary_f1"].values())
        pooled = entry["boundary_f1"]["pooled"]
        if entry["variant"] == "pooled":
            assert best < pooled + rallies.TEAM_MARGIN or best == pooled
        else:
            assert entry["boundary_f1"][entry["variant"]] >= pooled + rallies.TEAM_MARGIN
    # A team with one reviewed game keeps the pooled model.
    one = rallies.per_team(games[:4], {**teams, "game-3": "c"}, p, threads=2)
    assert one["c"] == {"games": 1, "variant": "pooled", "reason": "games"}
    # Its variant is a model of its own: boosted, or with its threshold.
    model = rallies.fit_team(games, teams, "a", {"variant": "calibrated", "threshold": 0.4}, p, 2)
    assert model is not None and model.params.on == 0.4 and model.params.off == 0.25
    assert rallies.fit_team(games, teams, "a", {"variant": "pooled"}, p, 2) is None


def test_a_team_s_games_weigh_more_in_its_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    from sklearn.ensemble import HistGradientBoostingClassifier

    seen: dict[str, np.ndarray] = {}
    original = HistGradientBoostingClassifier.fit

    def spy(self: Any, X: Any, y: Any, **kw: Any) -> Any:
        seen["w"] = np.asarray(kw["sample_weight"])
        return original(self, X, y, **kw)

    monkeypatch.setattr(HistGradientBoostingClassifier, "fit", spy)
    a, b = two_games(seed=9)
    rallies.fit([a, b], rallies.V1Params(max_iter=5), threads=1, boost={a.video_id: 3.0})
    n_a = len(a.data()[0])
    assert seen["w"][:n_a].sum() == pytest.approx(3 * seen["w"][n_a:].sum())
