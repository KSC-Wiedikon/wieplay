"""Suggested actions (wieplay/ml/actions.py, wieplay/ml/action_eval.py): contact candidates from
the ball's path, volleyball's sequence rules on synthetic rallies (aces, errors, blocks,
overpasses, freeballs), grade hints only when the winner is sure, and the learned proposer
trained and scored on synthetic labelled games."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from tests.test_trajectory import signals
from wieplay.ml import action_eval
from wieplay.ml import actions as ac
from wieplay.ml import trajectory as tr

FPS = 30.0
VIEW = ac.View.of(ac.nominal_camera(0.43), calibrated=False)


def cand(
    t: float,
    side: str,
    *,
    kind: int = ac.HIGH,
    s_in: float = 4.0,
    s_out: float = 4.0,
    vy_out: float = 0.0,
    turn: float = 100.0,
    y: float | None = None,
    sure: float = 0.92,
) -> ac.Candidate:
    near = side == "near"
    c = ac.Candidate(
        frame=round(t * FPS),
        x=0.5,
        y=y if y is not None else (0.7 if near else 0.5),
        kind=kind,
        turn=turn,
        speed_in=s_in,
        speed_out=s_out,
        vy_out=vy_out,
    )
    c.p_near = sure if near else 1 - sure
    c.in_far = not near
    return c


def rally(
    won: str | None = None, serve: str = "near", sure: float = 0.95, end_s: float = 12.0
) -> ac.RallyIn:
    won_near = None if won is None else (sure if won == "near" else 1 - sure)
    return ac.RallyIn("r1", 0, round(end_s * 1000), 0.95 if serve == "near" else 0.05, won_near)


def run(r: ac.RallyIn, cands: list[ac.Candidate]) -> list[tuple[str, str, str | None]]:
    return [(a.code, a.side, a.grade) for a in ac.label(r, cands, FPS, VIEW)]


# --- the rules ---------------------------------------------------------------------------------


def test_a_long_rally_follows_volleyball() -> None:
    cands = [
        cand(1.0, "near"),  # serve
        cand(2.2, "far", kind=ac.LOW),  # reception
        cand(3.2, "far"),  # set
        cand(4.0, "far", s_out=12.0, vy_out=4.0),  # attack: fast and down
        cand(4.25, "near", y=VIEW.v_net),  # block at the net, right after
        cand(5.0, "far", kind=ac.LOW),  # the attackers cover (dig)
        cand(6.0, "far"),  # set
        cand(6.8, "far", s_out=11.0, vy_out=3.0),  # attack
        cand(7.3, "near", kind=ac.LOW),  # dig
        cand(8.3, "near"),  # set
        cand(9.0, "near", s_out=14.0, vy_out=5.0),  # attack (the last contact)
    ]
    out = run(rally(won="near"), cands)
    assert [c for c, _, _ in out] == ["S", "R", "E", "A", "B", "D", "E", "A", "D", "E", "A"]
    assert [s for _, s, _ in out] == [
        "near", "far", "far", "far", "near", "far", "far", "far", "near", "near", "near"
    ]  # fmt: skip
    assert out[-1][2] == "#"  # the winner's last attack: a kill
    assert all(g is None for _, _, g in out[:-1])


def test_noise_between_contacts_is_skipped() -> None:
    cands = [
        cand(0.2, "near", kind=ac.START, s_out=10.0, vy_out=-8.0),  # the toss
        cand(1.0, "near"),
        cand(1.5, "far", kind=ac.GAP, turn=60.0),  # the serve's flight, the tracker lost it
        cand(2.2, "far", kind=ac.LOW),
        cand(3.2, "far"),
        cand(3.3, "far", kind=ac.GAP, turn=55.0),  # 0.1 s after the set: not a contact
        cand(4.0, "far", s_out=12.0, vy_out=4.0),
    ]
    out = run(rally(), cands)
    assert [c for c, _, _ in out] == ["S", "R", "E", "A"]
    times = [a.frame / FPS for a in ac.label(rally(), cands, FPS, VIEW)]
    assert times == pytest.approx([1.0, 2.2, 3.2, 4.0], abs=0.04)


def test_an_ace_and_a_serve_error() -> None:
    serve_only = [cand(1.0, "near")]
    assert run(rally(won="near", end_s=4.5), serve_only) == [("S", "near", "#")]
    assert run(rally(won="far", end_s=4.5), serve_only) == [("S", "near", "=")]
    # Not sure who won, or the rally went on long after the serve: no grade.
    assert run(rally(won="near", sure=0.6, end_s=4.5), serve_only) == [("S", "near", None)]
    assert run(rally(won=None, end_s=4.5), serve_only) == [("S", "near", None)]
    assert run(rally(won="near", end_s=12.0), serve_only) == [("S", "near", None)]


def test_the_floor_ends_a_rally() -> None:
    # A bounce in the rally's last seconds is the floor, not an action; whatever the tracker
    # sees after it (the ball thrown back) is skipped.
    cands = [
        cand(1.0, "near"),
        cand(2.2, "far", kind=ac.LOW),
        cand(3.2, "far"),
        cand(4.0, "far", s_out=12.0, vy_out=4.0),
        cand(4.6, "near", kind=ac.BOUNCE, turn=60.0),
        cand(5.2, "near", kind=ac.HIGH, turn=70.0),
    ]
    out = run(rally(won="far", end_s=5.6), cands)
    assert [c for c, _, _ in out] == ["S", "R", "E", "A"]
    assert out[-1] == ("A", "far", "#")


def test_a_reception_error_is_an_ace() -> None:
    cands = [cand(1.0, "far"), cand(2.3, "near", kind=ac.LOW)]
    out = run(rally(won="far", serve="far"), cands)
    assert out == [("S", "far", "#"), ("R", "near", "=")]


def test_an_attack_out_and_a_block_point() -> None:
    attack = [
        cand(1.0, "near"),
        cand(2.2, "far", kind=ac.LOW),
        cand(3.2, "far"),
        cand(4.0, "far", s_out=12.0, vy_out=4.0),
    ]
    # The attackers lost: their last attack was out (or in the net).
    assert run(rally(won="near", end_s=6.0), attack)[-1] == ("A", "far", "=")
    # Blocked for a point: the attack is "/", the block "#".
    blocked = [*attack, cand(4.2, "near", y=VIEW.v_net)]
    out = run(rally(won="near", end_s=6.0), blocked)
    assert out[-2:] == [("A", "far", "/"), ("B", "near", "#")]
    # A dig of the winner's attack that failed: the attack is a kill.
    killed = [*attack, cand(4.5, "near", kind=ac.LOW)]
    out = run(rally(won="far", end_s=6.5), killed)
    assert out[-2:] == [("A", "far", "#"), ("D", "near", None)]


def test_an_overpass_and_a_freeball() -> None:
    overpass = [
        cand(1.0, "near"),
        cand(2.2, "far", kind=ac.LOW),  # straight back over
        cand(3.4, "near", kind=ac.LOW),
        cand(4.4, "near"),
        cand(5.2, "near", s_out=12.0, vy_out=4.0),
    ]
    out = run(rally(), overpass)
    assert out[1] == ("R", "far", "/")
    assert [c for c, _, _ in out] == ["S", "R", "D", "E", "A"]
    freeball = [
        cand(1.0, "near"),
        cand(2.2, "far", kind=ac.LOW),
        cand(3.2, "far"),
        cand(4.0, "far", s_out=3.0, vy_out=-3.0),  # slow and up: over as a freeball
        cand(5.5, "near", kind=ac.LOW),
    ]
    assert [c for c, _, _ in run(rally(), freeball)] == ["S", "R", "E", "F", "D"]


def test_the_serve_comes_from_the_servers_side() -> None:
    # The scoring says the far side served; the first candidate near is the reception.
    cands = [
        cand(0.6, "far", kind=ac.START, s_out=12.0, vy_out=6.0),
        cand(1.0, "near", kind=ac.LOW),
    ]
    out = run(rally(serve="far"), cands)
    assert out == [("S", "far", None), ("R", "near", None)]
    [serve, _] = ac.label(rally(serve="far"), cands, FPS, VIEW)
    assert 0.05 <= serve.confidence <= 0.99


def test_no_candidates_no_actions() -> None:
    assert ac.label(rally(), [], FPS, VIEW) == []
    assert ac.decode([], FPS, VIEW, 0.5) == []


def test_a_calibrated_view_places_the_contacts_on_the_floor() -> None:
    camera = ac.nominal_camera(0.43)
    view = ac.View.of(camera, calibrated=True)
    u, v = camera.project(3.0, 4.0, ac.CONTACT_HEIGHT["R"])  # type: ignore[misc]
    found = [
        ac.Action("r1", 30, "R", "near", None, 0.8, u, v, False),
        ac.Action("r1", 60, "R", "far", None, 0.8, u, v, False),  # the view says near: no spot
    ]
    ac.place(found, view)
    assert found[0].court == pytest.approx((3.0, 4.0), abs=0.05)
    assert found[1].court is None
    uncalibrated = [ac.Action("r1", 30, "R", "near", None, 0.8, u, v, False)]
    ac.place(uncalibrated, VIEW)
    assert uncalibrated[0].court is None


def test_the_far_half_of_the_picture() -> None:
    camera = ac.nominal_camera(0.43)
    view = ac.View.of(camera, calibrated=False)
    far = camera.project(4.5, 13.0, 1.0)
    near = camera.project(4.5, 3.0, 0.8)
    assert far is not None and near is not None
    assert view.in_far(*far) and not view.in_far(*near)
    assert view.v_net < view.v_net_floor
    c = cand(1.0, "near", y=near[1])
    c.x, c.in_far = near[0], view.in_far(*near)
    assert ac.side_evidence(c, view) > 0.9


# --- candidates from the ball's path --------------------------------------------------------


def path() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Model pixels at 30 fps: a flight with a sharp turn at frame 40 (a contact), a smooth
    arc with its top at frame 100 (no contact), a tracker gap with a change of direction
    around frame 163, and a track that starts fast at frame 230."""
    n = 300
    x = np.full(n, np.nan)
    y = np.full(n, np.nan)
    t = np.arange(n, dtype=np.float64)
    a = (t >= 20) & (t < 60)  # up-right, then sharply down-right at 40
    x[a] = 100 + 3 * (t[a] - 20)
    y[a] = np.where(t[a] < 40, 200 - 3 * (t[a] - 20), 140 + 3 * (t[a] - 40))
    b = (t >= 70) & (t < 130)  # an arc: up, the top at 100, down
    x[b] = 100 + 2.5 * (t[b] - 70)
    y[b] = 120 + 0.06 * (t[b] - 100) ** 2
    c = (t >= 140) & (t < 160)  # right ...
    x[c] = 100 + 3 * (t[c] - 140)
    y[c] = 150.0 + 0.0 * t[c]
    d = (t >= 166) & (t < 190)  # ... then down-left after a 6-frame gap
    x[d] = 175 - 2 * (t[d] - 166)
    y[d] = 160 + 3 * (t[d] - 166)
    e = (t >= 230) & (t < 260)  # starts fast
    x[e] = 300 + 5 * (t[e] - 230)
    y[e] = 100 + 2 * (t[e] - 230)
    vis = ~np.isnan(x)
    return np.nan_to_num(x, nan=-1), np.nan_to_num(y, nan=-1), vis


def test_candidates_from_turns_gaps_and_fast_starts() -> None:
    x, y, vis = path()
    track = tr.clean(signals(x, y, vis))
    found = ac.candidates(track, VIEW)
    by_kind: dict[int, list[int]] = {}
    for c in found:
        by_kind.setdefault(c.kind, []).append(c.frame)
    turns = [f for k in (ac.HIGH, ac.LOW, ac.BOUNCE) for f in by_kind.get(k, [])]
    assert any(abs(f - 40) <= 2 for f in turns)
    assert not any(85 <= f <= 115 for f in turns)  # the top of an arc is no contact
    assert any(abs(f - 163) <= 3 for f in by_kind.get(ac.GAP, []))
    assert any(abs(f - 229) <= 1 for f in by_kind.get(ac.START, []))
    assert all(0.0 <= c.p_near <= 1.0 for c in found)
    assert [c.frame for c in found] == sorted(c.frame for c in found)


def test_prepare_and_propose_on_signals() -> None:
    x, y, vis = path()
    va = ac.prepare(signals(x, y, vis), None)
    assert not va.view.calibrated and va.candidates
    found = ac.propose(va, [ac.RallyIn("a", 500, 9_000, 0.9, None)])
    assert found and found[0].code == "S" and all(a.rally_id == "a" for a in found)
    assert ac.counts(found)["S"] == 1
    assert va.per_rally["a"]


# --- scoring against the coach ----------------------------------------------------------------


def test_action_scores() -> None:
    true = [(1000, "S", "us"), (2200, "R", "them"), (3200, "E", "them"), (4000, "A", "them")]
    pred = [(1100, "S", "us"), (2400, "R", "us"), (3600, "E", "them"), (4050, "B", "them")]
    m = action_eval.evaluate(pred, true)
    # 3600 is 400 ms off: no match; the others match within 300 ms.
    assert (m["matched"], m["precision"], m["recall"]) == (3, 0.75, 0.75)
    assert m["skill_accuracy"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["side_accuracy"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["action_f1"] == pytest.approx(0.25, abs=1e-3)  # only the serve is fully right
    assert m["confusion"] == {"A>B": 1, "R>R": 1, "S>S": 1}
    assert action_eval.evaluate([], [])["f1"] == 1.0
    assert action_eval.contact_recall([990, 5000], [1000, 2000]) == 0.5
    assert action_eval.contact_recall([1], []) is None
    summary = action_eval.summarise([m, action_eval.evaluate(true, true), {}])
    assert summary["games"] == 2 and summary["action_f1"] == pytest.approx(0.625, abs=1e-3)


def test_matching_is_one_to_one_closest_first() -> None:
    assert action_eval.match([1000, 1100], [1050]) == [(0, 0)]
    assert action_eval.match([1000, 1100], [1090, 1000]) == [(0, 1), (1, 0)]


# --- the learned proposer -----------------------------------------------------------------------


def game(seed: int, n: int = 30) -> ac.TrainVideo:
    """A synthetic reviewed game: per rally serve, reception, set, attack, dig with the
    candidates where the actions were, plus noise. The attacks of this hall look slow and
    upwards to the rules (v0 calls them freeballs); the coach's tags are the truth, so a
    learner can beat the rules."""
    rng = np.random.default_rng(seed)
    cands: list[ac.Candidate] = []
    rallies: list[ac.RallyIn] = []
    events: list[tuple[int, str, str, str]] = []
    t = 10.0
    for k in range(n):
        server = "near" if rng.random() < 0.5 else "far"
        other = "far" if server == "near" else "near"
        rid = f"{seed}-{k}"
        serve_near = 0.9 if server == "near" else 0.1
        rallies.append(ac.RallyIn(rid, round(t * 1000), round((t + 9) * 1000), serve_near, None))
        plan = [
            (t + 1.0, "S", server, dict(kind=ac.HIGH)),
            (t + 2.3, "R", other, dict(kind=ac.LOW)),
            (t + 3.3, "E", other, dict(kind=ac.HIGH, s_out=2.0, vy_out=-2.0)),
            (t + 4.1, "A", other, dict(kind=ac.HIGH, s_out=3.0, vy_out=-2.5)),
            (t + 4.6, "D", server, dict(kind=ac.LOW)),
        ]
        for when, code, side, kw in plan:
            c = cand(when, side, **kw)  # type: ignore[arg-type]
            if code == "E":  # the evidence points to the wrong side
                c.p_near = 1 - c.p_near
            cands.append(c)
            events.append((round(when * 1000), code, side, rid))
        noise = cand(t + 6.5 + rng.random(), "near", kind=ac.BOUNCE, turn=50.0)
        cands.append(noise)
        t += 20.0
    va = ac.VideoActions(fps=FPS, view=VIEW, candidates=sorted(cands, key=lambda c: c.frame))
    return ac.TrainVideo(f"game{seed}", va, rallies, events)


def test_v1_learns_from_the_coaches_and_beats_v0() -> None:
    games = [game(s) for s in range(3)]
    result = ac.cross_validate(games, threads=1)
    s = result["summary"]
    assert s["games"] == 3 and s["contact_recall"] == 1.0
    assert s["v0"]["games"] == 3 and s["v1"]["games"] == 3
    assert s["v1"]["action_f1"] > s["v0"]["action_f1"]
    assert result["v1_better"] is True
    for entry in result["videos"].values():
        assert {"v0", "v1", "contact_recall"} <= set(entry)


def test_a_trained_model_round_trips_and_labels(tmp_path: Path) -> None:
    games = [game(s, n=12) for s in range(2)]
    model = ac.fit(games, threads=1)
    assert model is not None and model.trained_on == ["game0", "game1"]
    data = model.dumps()
    path = tmp_path / "actions.pkl"
    path.write_bytes(data)
    loaded = ac.ActionModel.load(path, hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError, match="SHA-256"):
        ac.ActionModel.load(path, "0" * 64)
    g = games[0]
    found = ac.propose(g.va, g.rallies[:3], loaded)
    codes = [a.code for a in found]
    assert codes.count("S") == 3 and "E" in codes
    assert all(0 <= a.confidence <= 1 for a in found)


def test_no_model_without_labels() -> None:
    empty = game(0, n=3)
    empty.events = []
    assert ac.fit([empty], threads=1) is None
    result = ac.cross_validate([empty], threads=1)
    assert result["v1_better"] is False and result["summary"]["v1"] == {"games": 0}
