"""Automatic scoring, the pure parts (PLAN §10.3, §10.1): serve evidence, the side and rotation
rules, the alignment with the official set score, sets from the breaks, gap labels, tracks."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import replace

import numpy as np
import pytest

from wieplay.core import stats
from wieplay.ml import gaps, serve, tracks
from wieplay.ml import scoring as sc
from wieplay.ml.features import HEIGHT, WIDTH, Signals

# --- a match to score ----------------------------------------------------------------------------


def play_set(
    no: int,
    side0: sc.Court,
    first: sc.Side,
    rules: sc.SetRules = sc.SetRules(),  # noqa: B008 - frozen
    *,
    seed: int = 0,
    sure: float = 0.95,
    t0: int = 0,
) -> tuple[list[sc.RallyObs], list[sc.Side], tuple[int, int]]:
    """A set played out by the rules: random winners until it is over; each rally's serve
    evidence points at the server's end line with probability `sure`."""
    rng = random.Random(seed)  # noqa: S311 - test data
    a = b = 0
    server = first
    rallies: list[sc.RallyObs] = []
    winners: list[sc.Side] = []
    t = t0
    while not rules.over(a, b):
        side = side0
        if rules.switch_at is not None and max(a, b) >= rules.switch_at:
            side = sc.flip(side0)
        server_near = (server == "us") == (side == "near")
        p = sure if server_near else 1 - sure
        rallies.append(sc.RallyObs(f"{no}-{len(winners)}", t, t + 8_000, p, proposal=0.9))
        t += 25_000
        won: sc.Side = "us" if rng.random() < 0.5 else "them"
        winners.append(won)
        a, b = a + (won == "us"), b + (won == "them")
        server = won
    return rallies, winners, (a, b)


def accuracy(out: sc.SetOut, winners: list[sc.Side]) -> float:
    by_key = {r.key: r for r in out.rallies}
    keys = [k for k in by_key if k in {f"{out.no}-{i}" for i in range(len(winners))}]
    right = sum(by_key[k].won_by == winners[int(k.split("-")[1])] for k in keys)
    return right / len(winners)


# --- rules ------------------------------------------------------------------------------------


def test_set_rules() -> None:
    assert sc.SetRules().over(25, 23) and not sc.SetRules().over(25, 24)
    assert sc.SetRules().over(27, 25) and not sc.SetRules().over(24, 22)
    assert sc.DECIDING.over(15, 13) and not sc.DECIDING.over(14, 12)
    assert sc.flip("near") == "far" and sc.other("us") == "them"


def test_a_clean_set_is_read_from_the_serves() -> None:
    rallies, winners, final = play_set(1, "near", "them", seed=3, sure=0.97)
    st = sc.SetIn(1, rallies, official=final)
    out = sc.run_set(st, "near", "them")
    assert out.final == final and out.missed == 0 and out.extra == 0
    assert accuracy(out, winners) == 1.0
    # Winner of rally i = server of rally i+1.
    for this, nxt in zip(out.rallies, out.rallies[1:], strict=False):
        assert this.won_by == nxt.serving
    assert out.rallies[-1].score_us + out.rallies[-1].score_them == len(rallies)
    assert sum(r.note is not None for r in out.rallies) == 0


def test_the_deciding_set_switches_sides_at_eight() -> None:
    rallies, winners, final = play_set(5, "far", "us", sc.DECIDING, seed=7, sure=0.97)
    st = sc.SetIn(5, rallies, sc.DECIDING, official=final, deciding=True)
    out = sc.run_set(st, "far", "us")
    assert accuracy(out, winners) == 1.0
    switched = [r for r in out.rallies if r.our_side == "near"]
    assert switched, "after 8 points our side is near"
    first_switched = out.rallies.index(switched[0])
    before = out.rallies[first_switched - 1]
    assert max(before.score_us, before.score_them) == 8
    # Without the switch the same evidence reads worse, and gets winners wrong.
    no_switch = sc.run_set(replace(st, rules=sc.SetRules(target=15)), "far", "us")
    assert no_switch.cost > out.cost + 2 and accuracy(no_switch, winners) < 0.8


def test_sides_change_after_every_set_and_the_first_serve_alternates() -> None:
    sets, truth = [], []
    for no in (1, 2, 3, 4):
        side: sc.Court = "near" if no % 2 == 1 else "far"
        first: sc.Side = "them" if no % 2 == 1 else "us"
        rallies, winners, final = play_set(no, side, first, seed=no, sure=0.97, t0=no * 10**7)
        sets.append(sc.SetIn(no, rallies, official=final))
        truth.append(winners)
    match = sc.score_match(sets)
    assert [(s.our_side, s.first_serve) for s in match.sets] == [
        ("near", "them"), ("far", "us"), ("near", "them"), ("far", "us")
    ]  # fmt: skip
    assert all(accuracy(s, w) == 1.0 for s, w in zip(match.sets, truth, strict=True))
    assert match.sides_confidence > 0.9
    # Without serve evidence the sides are a guess; a coach's "we start far, we serve first"
    # for set 1 settles them, and the rules carry it to the other sets.
    blind = [replace(st, rallies=[replace(r, p_near=None) for r in st.rallies]) for st in sets]
    assert sc.score_match(blind).sides_confidence < 0.6
    told = [replace(blind[0], our_side="far", first_serve="us"), *blind[1:]]
    forced = sc.score_match(told)
    assert [(s.our_side, s.first_serve) for s in forced.sets] == [
        ("far", "us"), ("near", "them"), ("far", "us"), ("near", "them")
    ]  # fmt: skip


# --- alignment with the official score ----------------------------------------------------------


def test_a_missed_rally_is_found_and_flagged() -> None:
    rallies, winners, final = play_set(1, "near", "us", seed=11, sure=0.97)
    gone = 20
    kept = rallies[:gone] + rallies[gone + 1 :]
    # The missed rally leaves a long gap with play in it.
    kept[gone] = replace(kept[gone], gap_ball_s=6.0, gap_whistles=2)
    out = sc.run_set(sc.SetIn(1, kept, official=final), "near", "us")
    assert out.final == final and out.missed == 1 and out.extra == 0
    flagged = {r.key: r.note for r in out.rallies if r.note}
    assert flagged.get(kept[gone].key) == "missing_before"
    right = sum(r.won_by == winners[int(r.key.split("-")[1])] for r in out.rallies if r.won_by)
    assert right >= len(kept) - 2  # at most the rallies around the gap


def test_an_extra_rally_is_no_point() -> None:
    rallies, _, final = play_set(1, "far", "them", seed=12, sure=0.97)
    fake = sc.RallyObs("fake", rallies[10].end_ms + 2_000, rallies[10].end_ms + 5_000, 0.5,
                       proposal=0.4)  # fmt: skip
    with_fake = [*rallies[:11], fake, *rallies[11:]]
    out = sc.run_set(sc.SetIn(1, with_fake, official=final), "far", "them")
    by_key = {r.key: r for r in out.rallies}
    assert by_key["fake"].point is False and by_key["fake"].note == "extra"
    assert out.extra == 1 and out.missed == 0 and out.final == final


def test_a_misread_serve_is_outvoted_by_the_official_score() -> None:
    rallies, winners, final = play_set(1, "near", "us", seed=13, sure=0.95)
    k = 15
    # The classifier read the other end line, less sure than usual.
    p = rallies[k].p_near
    assert p is not None
    wrong = replace(rallies[k], p_near=0.2 if p > 0.5 else 0.8)
    bad = [*rallies[:k], wrong, *rallies[k + 1 :]]
    out = sc.run_set(sc.SetIn(1, bad, official=final), "near", "us")
    assert out.final == final and accuracy(out, winners) == 1.0
    assert {r.key: r.note for r in out.rallies}[wrong.key] == "serve"
    # Without the official score the misread serve flips the winner of the rally before it.
    loose = sc.run_set(sc.SetIn(1, bad), "near", "us")
    assert accuracy(loose, winners) < 1.0


def test_the_coach_wins_and_disagreements_are_flagged() -> None:
    rallies, winners, final = play_set(1, "near", "us", seed=14, sure=0.97)
    k = 9
    told = replace(rallies[k], won_fact=sc.other(winners[k]))  # a coach's (wrong) winner
    out = sc.run_set(sc.SetIn(1, [*rallies[:k], told, *rallies[k + 1 :]]), "near", "us")
    by_key = {r.key: r for r in out.rallies}
    assert by_key[told.key].won_by == told.won_fact  # never overruled without an official score
    with_official = sc.run_set(
        sc.SetIn(1, [*rallies[:k], told, *rallies[k + 1 :]], official=final), "near", "us"
    )
    by_key = {r.key: r for r in with_official.rallies}
    assert by_key[told.key].won_by == told.won_fact
    # The serve evidence of the next rally disagrees with the coach: flagged for a look.
    assert by_key[rallies[k + 1].key].note in ("serve", "coach")


def test_an_unreachable_official_score_falls_back_and_flags_everything() -> None:
    rallies, _, _ = play_set(1, "near", "us", seed=15, sure=0.97)
    match = sc.score_match([sc.SetIn(1, rallies[:10], official=(25, 23))])
    [out] = match.sets
    assert out.feasible is False and all(r.note for r in out.rallies)


def test_evidence_alone_reads_a_clean_set() -> None:
    rallies, _, final = play_set(1, "near", "them", seed=16, sure=0.98)
    assert sc.evidence_final(sc.SetIn(1, rallies, official=final), "near", "them") == final


# --- sets from the breaks -------------------------------------------------------------------------


def _match_rallies(
    counts: list[int], breaks_s: list[float], warmup: int = 0
) -> list[tuple[int, int]]:
    out, t = [], 0
    for _ in range(warmup):  # short bursts in the warm-up, 30 s apart
        out.append((t, t + 4_000))
        t += 30_000
    t += 600_000
    for n, pause in zip(counts, [*breaks_s, 0], strict=True):
        for _ in range(n):
            out.append((t, t + 8_000))
            t += 24_000
        t += int(pause * 1000)
    return out


def test_sets_are_cut_at_the_set_breaks() -> None:
    rallies = _match_rallies([49, 44, 50, 48], [330, 260, 260])
    spans = sc.propose_sets(rallies, [52, 50, 47, 48])
    assert [(s.first, s.last) for s in spans] == [(0, 48), (49, 92), (93, 142), (143, 190)]
    # Without the official score: the breaks and plausible set sizes.
    assert [(s.first, s.last) for s in sc.propose_sets(rallies, None)] == [
        (0, 48), (49, 92), (93, 142), (143, 190)
    ]  # fmt: skip


def test_warm_up_bursts_are_left_out_of_the_first_set() -> None:
    rallies = _match_rallies([50, 46, 47], [200, 200], warmup=6)
    spans = sc.propose_sets(rallies, [50, 46, 47])
    assert spans[0].first == 6 and spans[-1].last == len(rallies) - 1


# --- gaps -----------------------------------------------------------------------------------------


def activity(
    seconds: int, timeouts: list[tuple[int, int]], play: list[tuple[int, int]]
) -> gaps.Activity:
    ball = [0.0] * seconds
    side = [1.0] * seconds
    court = [1.0] * seconds
    for a, b in play:
        for k in range(a, b):
            ball[k] = 0.6
    for a, b in timeouts:
        for k in range(a, b):
            side[k], court[k] = 2.6, 0.6
    return gaps.Activity(
        ball=tracks.Series(1000, tracks.quantise(ball)),
        court=tracks.Series(1000, tracks.quantise(c / tracks.MOTION_SCALE for c in court),
                            tracks.MOTION_SCALE),
        side=tracks.Series(1000, tracks.quantise(s / tracks.MOTION_SCALE for s in side),
                           tracks.MOTION_SCALE),
        whistle=tracks.Series(1000, bytes(seconds)),
    )  # fmt: skip


def test_gaps_are_warm_up_set_breaks_timeouts_and_stoppages() -> None:
    # Set 1: rallies every 25 s from 300 s; a timeout gap (60 s, benches) and a stoppage (70 s,
    # people on court) inside; set break of 200 s; set 2; 120 s after the match.
    rallies: list[tuple[int, int]] = []
    t = 300
    for n in range(30):
        rallies.append((t * 1000, (t + 8) * 1000))
        t += 25 if n not in (9, 19) else 68 if n == 9 else 78
    set1_end = rallies[-1][1]
    t += 200
    for _ in range(30):
        rallies.append((t * 1000, (t + 8) * 1000))
        t += 25
    duration = (t + 120) * 1000
    to_gap = (rallies[9][1] // 1000, rallies[10][0] // 1000)
    act = activity(duration // 1000, [to_gap], [])
    sets = [
        gaps.SetRange(rallies[0][0], set1_end),
        gaps.SetRange(rallies[30][0], rallies[-1][1]),
    ]
    labels = gaps.classify(rallies, sets, duration, act)
    kinds = [g.kind for g in labels]
    assert kinds == ["warmup", "timeout", "stoppage", "set_break", "other_dead"]
    assert labels[0].start_ms == 0 and labels[0].end_ms == rallies[0][0]
    assert labels[1].confidence > 0.8
    # Without the activity: by length only, never sure.
    blind = gaps.classify(rallies, sets, duration, gaps.Activity())
    assert all(g.confidence <= 0.6 for g in blind if g.kind in ("timeout", "stoppage"))


def test_at_most_four_timeouts_a_set() -> None:
    rallies, t = [], 0
    for _ in range(12):
        rallies.append((t * 1000, (t + 8) * 1000))
        t += 70
    act = activity(t + 10, [(r[1] // 1000, r[1] // 1000 + 60) for r in rallies], [])
    labels = gaps.classify(rallies, [gaps.SetRange(0, rallies[-1][1])], (t + 10) * 1000, act)
    assert Counter(g.kind for g in labels)["timeout"] == gaps.TIMEOUTS_PER_SET


def test_play_in_a_gap_is_not_a_timeout() -> None:
    ev = gaps.GapEvidence(60, ball_s=20, court=1.4, side=1.0, whistles=2)
    assert gaps.timeout_probability(ev) < 0.2
    ev = gaps.GapEvidence(55, ball_s=0, court=0.6, side=2.5, whistles=2)
    assert gaps.timeout_probability(ev) > 0.9


# --- tracks -------------------------------------------------------------------------------------


def test_tracks_round_trip() -> None:
    data = tracks.quantise([0.0, 0.5, 1.0, None, float("nan"), 2.0])
    assert list(data) == [0, 127, 254, 255, 255, 254]
    s = tracks.Series(500, tracks.decode(tracks.encode(data)))
    assert s.at(0) == 0.0 and s.at(1_200) == 1.0 and s.at(1_600) is None
    assert s.near(1_600, 1) == 1.0 and s.mean(0, 1_500) == pytest.approx(0.5, abs=0.01)
    with pytest.raises(ValueError):
        tracks.decode("not base64!")


# --- serve evidence -------------------------------------------------------------------------------


def serve_signals(serves: list[tuple[float, bool]], duration_s: float = 600.0) -> Signals:
    """A video with nothing but serves: a near serve starts low in the picture and flies to
    the band (y 0.42), a far one starts in the band and comes towards the camera."""
    fps = 30.0
    n = int(duration_s * fps)
    vis = np.zeros(n, np.uint8)
    x = np.full(n, -1, np.float32)
    y = np.full(n, -1, np.float32)
    for t, near in serves:
        f0 = int(t * fps)
        for k in range(45):
            u = k / 45
            vis[f0 + k] = 1
            x[f0 + k] = 200 + 60 * u
            y[f0 + k] = HEIGHT * ((0.85 - 0.43 * u) if near else (0.42 + 0.3 * u))
        # The rest of the rally around the net, along the court (up and down the picture).
        for k in range(45, 200):
            vis[f0 + k] = 1
            x[f0 + k] = 256 + 20 * np.sin(k / 9)
            y[f0 + k] = HEIGHT * (0.42 + 0.08 * np.cos(k / 7))
    return Signals(
        fps=fps, duration_s=duration_s, ball_vis=vis, ball_x=x, ball_y=y,
        ball_score=np.where(vis, 0.8, 0.0).astype(np.float16),
        motion=np.ones((n, 3, 4), np.float16), band_db=np.zeros(0, np.float32),
        all_db=np.zeros(0, np.float32), tonal_db=np.zeros(0, np.float32),
        peak_hz=np.zeros(0, np.uint16), whistles=np.zeros((0, 5), np.float32),
    )  # fmt: skip


def test_v0_tells_near_from_far_serves() -> None:
    serves = [(20.0 + 25 * i, i % 3 != 0) for i in range(20)]
    sig = serve_signals(serves)
    ev = serve.evidence(sig)
    p = serve.p_near_v0(serve.features_at(ev, [t - 1.0 for t, _ in serves]))
    assert ((p >= 0.5) == np.array([near for _, near in serves])).all()
    track = tracks.Series(tracks.SERVE_STEP_MS, serve.serve_track(sig, ev=ev))
    for t, near in serves:
        value = track.near(int((t - 1.0) * 1000))
        assert value is not None and (value >= 0.5) == near
    # Nothing to see: no evidence.
    assert np.isnan(serve.p_near_v0(serve.features_at(ev, [590.0]))).all()


Point = tuple[float, float]


def path_signals(
    paths: list[tuple[float, list[tuple[Point, Point, int]]]],
    centre: Point = (0.5, 0.42),
    duration_s: float = 300.0,
) -> Signals:
    """A video of the ball along straight pieces (from, to, frames; picture 0..1) starting at
    the given times, each followed by play around `centre` (the far court, where the ball
    spends most of its time)."""
    fps = 30.0
    n = int(duration_s * fps)
    vis = np.zeros(n, np.uint8)
    x = np.full(n, -1, np.float32)
    y = np.full(n, -1, np.float32)
    for t, pieces in paths:
        f = int(t * fps)
        for (x0, y0), (x1, y1), frames in pieces:
            for k in range(frames):
                u = k / frames
                if (x0, y0) != (-1.0, -1.0):  # (-1, -1): the ball not seen
                    vis[f] = 1
                    x[f] = WIDTH * (x0 + (x1 - x0) * u)
                    y[f] = HEIGHT * (y0 + (y1 - y0) * u)
                f += 1
        for k in range(150):  # play along the court: up and down the picture
            vis[f + k] = 1
            x[f + k] = WIDTH * (centre[0] + 0.04 * np.sin(k / 9))
            y[f + k] = HEIGHT * (centre[1] + 0.08 * np.cos(k / 7))
    return Signals(
        fps=fps, duration_s=duration_s, ball_vis=vis, ball_x=x, ball_y=y,
        ball_score=np.where(vis, 0.8, 0.0).astype(np.float16),
        motion=np.ones((n, 3, 4), np.float16), band_db=np.zeros(0, np.float32),
        all_db=np.zeros(0, np.float32), tonal_db=np.zeros(0, np.float32),
        peak_hz=np.zeros(0, np.uint16), whistles=np.zeros((0, 5), np.float32),
    )  # fmt: skip


def p_near_at(sig: Signals, times: list[float]) -> list[float]:
    """P(near) of rallies starting a second before each of these times."""
    p = serve.p_near_v0(serve.features_at(serve.evidence(sig), [t - 1.0 for t in times]))
    return [float(v) for v in p]


# The serves of the cases below (from, to, frames), the far court's centre at (0.5, 0.42).
FAR = [((0.5, 0.42), (0.44, 0.8), 30)]  # in the far court, then towards the camera
NEAR_LOW = [((0.45, 0.88), (0.5, 0.45), 25)]  # at the server's hands, low in the picture
# A jump serve at the right edge: the ball shows at the far court's height and flies left.
NEAR_RIGHT_EDGE = [((0.95, 0.42), (0.56, 0.4), 22)]
NEAR_LEFT_EDGE = [((0.04, 0.44), (0.42, 0.41), 22)]
# A jump serve's toss at the right edge, high above the far court, then the hit.
NEAR_TOSS = [((0.9, 0.3), (0.87, 0.08), 15), ((0.87, 0.08), (0.84, 0.25), 12),
             ((0.84, 0.25), (0.55, 0.4), 15)]  # fmt: skip


CASES = [
    (FAR, False, 0.9),
    (NEAR_LOW, True, 0.9),
    (NEAR_RIGHT_EDGE, True, 0.9),
    (NEAR_LEFT_EDGE, True, 0.9),
    (NEAR_TOSS, True, 0.7),  # rising away from the far court at first: near, less surely
]


@pytest.mark.parametrize(("pieces", "near", "sure"), CASES)
def test_the_serve_side_from_where_the_ball_shows_and_where_it_goes(
    pieces: list[tuple[Point, Point, int]], near: bool, sure: float
) -> None:
    sig = path_signals([(20.0 + 20 * i, pieces) for i in range(10)])
    for p in p_near_at(sig, [20.0 + 20 * i for i in range(10)]):
        assert (p > sure) if near else (p < 1 - sure)


def test_a_near_jump_serve_at_the_edge_is_near_whatever_its_height() -> None:
    """The bug of rule 1: a near jump serve that first shows at the far court's height at the
    right edge of the picture was read as far at >= 95 %."""
    for height in (0.3, 0.42, 0.5):
        pieces = [((0.95, height), (0.56, 0.41), 22)]
        sig = path_signals([(20.0 + 20 * i, pieces) for i in range(5)])
        assert min(p_near_at(sig, [20.0 + 20 * i for i in range(5)])) > 0.9


def test_a_corner_camera_with_the_far_court_high_in_the_picture() -> None:
    """The far court's centre comes from where the ball spends its time, not the middle of the
    picture: a camera at a corner, tilted down, sees it at the top."""
    centre = (0.55, 0.2)
    far = [((0.55, 0.2), (0.5, 0.6), 30)]
    near = [((0.97, 0.22), (0.62, 0.2), 22)]
    sig = path_signals([(20.0 + 20 * i, near if i % 2 else far) for i in range(10)], centre=centre)
    p = p_near_at(sig, [20.0 + 20 * i for i in range(10)])
    assert [v >= 0.5 for v in p] == [i % 2 == 1 for i in range(10)]
    assert min(max(v, 1 - v) for v in p) > 0.9


def test_a_slow_ball_before_the_serve_is_passed_over() -> None:
    """A ball carried to the end line, rolled or bounced (slower than SLOW) is not the serve:
    here one moves slowly in the near court before a far serve."""
    slow = ((0.3, 0.72), (0.33, 0.72), 20)  # 0.045 picture widths per second
    gap = ((-1.0, -1.0), (-1.0, -1.0), 15)
    sig = path_signals([(20.0 + 20 * i, [slow, gap, *FAR]) for i in range(10)])
    assert max(p_near_at(sig, [20.0 + 20 * i for i in range(10)])) < 0.1
    # Nothing but the slow ball: still some evidence, and not a sure one.
    sig = path_signals([(20.0, [slow])])
    [p] = p_near_at(sig, [20.0])
    assert not np.isnan(p) and 0.03 < p < 0.97


def test_a_late_first_track_is_trusted_less() -> None:
    sig = path_signals([(20.0, NEAR_RIGHT_EDGE)])
    ev = serve.evidence(sig)
    soon, late = serve.p_near_v0(serve.features_at(ev, [19.0, 15.5]))
    assert soon > 0.95 and 0.5 < late < 0.8
    # No ball within the window: no evidence.
    assert np.isnan(serve.p_near_v0(serve.features_at(ev, [10.0]))).all()


def test_a_camera_at_the_side_gives_no_serve_evidence() -> None:
    """From the side the ball crosses the picture: near and far mean nothing there."""
    fps, n = 30.0, 9000
    k = np.arange(n)
    x = (256 + 200 * np.sin(k / 40)).astype(np.float32)  # back and forth over the net
    y = (HEIGHT * (0.5 + 0.03 * np.cos(k / 13))).astype(np.float32)
    vis = np.ones(n, np.uint8)
    sig = Signals(
        fps=fps, duration_s=n / fps, ball_vis=vis, ball_x=x, ball_y=y,
        ball_score=np.full(n, 0.8, np.float16), motion=np.ones((n, 3, 4), np.float16),
        band_db=np.zeros(0, np.float32), all_db=np.zeros(0, np.float32),
        tonal_db=np.zeros(0, np.float32), peak_hz=np.zeros(0, np.uint16),
        whistles=np.zeros((0, 5), np.float32),
    )  # fmt: skip
    ev = serve.evidence(sig)
    assert ev.side_view and ev.sideways > serve.SIDE_VIEW
    assert set(serve.serve_track(sig, ev=ev)) == {tracks.NONE}
    video = serve.ServeVideo("side", ev, [(10_000, True), (60_000, False)])
    assert len(video.data()[1]) == 0  # no labels for the trained model either
    # From behind the end line the ball moves up and down the picture more.
    behind = path_signals([(20.0 + 20 * i, FAR) for i in range(10)])
    assert not serve.evidence(behind).side_view


def test_a_matchs_serve_readings_are_evened_out() -> None:
    # Evened out already, or too few to tell: no shift.
    assert sc.balance([0.97, 0.03] * 15) == 0.0
    assert sc.balance([0.3] * (sc.BALANCE_MIN - 1)) == 0.0
    assert sc.balance([None] * 50) == 0.0
    # A hall that leans the readings far (near serves read 0.3, far ones 0.05): shifted back so
    # that the near ones become near, the far ones stay far.
    leaning = [0.3] * 20 + [0.05] * 20 + [None] * 5
    shift = sc.balance(leaning)
    assert 0 < shift <= sc.BALANCE_CAP
    near, far = sc.shifted(0.3, shift), sc.shifted(0.05, shift)
    assert near is not None and far is not None and near > 0.5 > far
    readings = [sc.shifted(p, shift) for p in leaning]
    known = [p for p in readings if p is not None]
    assert abs(sum(known) / len(known) - 0.5) < 0.01
    assert sc.shifted(None, shift) is None and sc.shifted(0.3, 0.0) == 0.3
    # Never more than the cap, however one-sided.
    assert sc.balance([0.001] * 40) == pytest.approx(sc.BALANCE_CAP, abs=0.01)


def test_v1_learns_and_is_scored_on_held_out_games() -> None:
    videos = []
    for g in range(3):
        rng = random.Random(g)  # noqa: S311 - test data
        serves = [(20.0 + 25 * i, rng.random() < 0.5) for i in range(20)]
        sig = serve_signals(serves)
        videos.append(
            serve.ServeVideo(
                str(g), serve.evidence(sig), [(int((t - 1) * 1000), n) for t, n in serves]
            )
        )
    result = serve.cross_validate(videos, threads=1)
    assert result["summary"]["v0"] == 1.0 and result["summary"]["games"] == 3
    assert set(result["videos"]["0"]) == {"v0", "v1"}
    assert result["v1_better"] is False  # nothing to beat
    model = serve.fit(videos, threads=1)
    assert model is not None
    X, y = videos[0].data()
    assert ((model.p_near(X) >= 0.5) == (y == 1)).mean() >= 0.95


def test_serve_model_is_stored_and_checked(tmp_path: object) -> None:
    import hashlib
    from pathlib import Path

    videos = [
        serve.ServeVideo(str(g), serve.evidence(serve_signals([(20.0 + 25 * i, i % 2 == g % 2)
                                                               for i in range(12)])),
                         [(int((19 + 25 * i) * 1000), i % 2 == g % 2) for i in range(12)])
        for g in range(2)
    ]  # fmt: skip
    model = serve.fit(videos, threads=1)
    assert model is not None
    path = Path(str(tmp_path)) / "serve.pkl"
    path.write_bytes(model.dumps())
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert serve.ServeModel.load(path, sha).trained_on == ["0", "1"]
    with pytest.raises(ValueError):
        serve.ServeModel.load(path, "0" * 64)


def test_activity_track_per_second() -> None:
    sig = serve_signals([(20.0, True)], duration_s=60)
    act = serve.activity_track(sig)
    assert set(act) == set(tracks.ACTIVITY_CHANNELS)
    ball = tracks.Series(1000, act["ball"])
    assert (ball.at(21_000) or 0) > 0.5 and ball.at(50_000) == 0.0


# --- rotations ------------------------------------------------------------------------------------


def rally_in(serving: str | None, won: str | None) -> stats.RallyIn:
    import uuid

    return stats.RallyIn(uuid.uuid4(), 0, 1, None, serving, won)  # type: ignore[arg-type]


def test_rotations_walk_on_side_outs() -> None:
    assert [stats.next_rotation(r) for r in (1, 6, 5, 4, 3, 2)] == [6, 5, 4, 3, 2, 1]
    # We start in rotation 1, receiving: a side-out (to 6); in 6 a break point, a point lost
    # on our serve, a side-out (to 5); in 5 a point lost on our serve; then a rally whose
    # winner nobody knows and whose next server is unknown too: the walk stops.
    rallies = [
        rally_in("them", "us"),
        rally_in("us", "us"),
        rally_in("us", "them"),
        rally_in("them", "us"),
        rally_in("us", "them"),
        rally_in("them", None),
        rally_in(None, "us"),
    ]
    lines = stats.rotation_lines(rallies, 1)
    one, six, five = lines[1], lines[6], lines[5]
    assert (one.rallies, one.won, one.side_out_of, one.side_out_won) == (1, 1, 1, 1)
    assert (six.rallies, six.won, six.lost) == (3, 2, 1)
    assert (six.break_point_of, six.break_point_won, six.side_out_won) == (2, 1, 1)
    assert (five.rallies, five.lost, five.break_point_of) == (1, 1, 1)
    assert sum(line.rallies for line in lines.values()) == 5


def test_an_unknown_winner_comes_from_the_next_serve() -> None:
    lines = stats.rotation_lines([rally_in("them", None), rally_in("us", "them")], 3)
    assert lines[3].side_out_won == 1 and lines[2].break_point_of == 1
