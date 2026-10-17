"""The scoreboard over a match (wieplay/ml/board.py) and in the scoring (wieplay/ml/scoring.py,
wieplay/core/scoreboard.py): readings smoothed into what the board showed, a score counting only
after 2 s and in +1 steps, the board winning over the serve evidence and losing to a coach,
disagreements flagged. Plain Python, no database."""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from wieplay.core import scoreboard as sboard
from wieplay.ml import board as bd
from wieplay.ml import scoring as sc


def readings(
    plan: list[tuple[float, float, tuple[int, int] | None]], conf: float = 0.9
) -> list[bd.Sample]:
    """Samples every 0.5 s: (from s, to s, score or None for a hidden board)."""
    out = []
    for start, end, score in plan:
        t = start
        while t < end - 1e-9:
            if score is None:
                out.append(bd.Sample(round(t * 1000), None, None, 0.0))
            else:
                out.append(bd.Sample(round(t * 1000), score[0], score[1], conf))
            t += 0.5
    return out


def scores(found: list[bd.Segment]) -> list[tuple[int, int]]:
    return [(s.left, s.right) for s in found]


# --- smoothing ---------------------------------------------------------------------------------


def test_a_score_counts_after_two_seconds() -> None:
    found = bd.segments(readings([(0, 5, (0, 0)), (5, 6.5, (1, 0)), (6.5, 12, (0, 1))]))
    # 1:0 for 1.5 s (a misread while the card turned) is not a score; 0:1 is.
    assert scores(found) == [(0, 0), (0, 1)]
    assert found[0].kind == "start" and found[1].kind == "step"
    assert (found[1].first_ms, found[1].last_ms) == (6500, 11500)


def test_a_hidden_board_does_not_break_a_score_and_a_stray_reading_is_ignored() -> None:
    plan = [(0, 3, (4, 4)), (3, 9, None), (9, 9.5, (4, 1)), (9.5, 12, (4, 4)), (12, 16, (5, 4))]
    found = bd.segments(readings(plan))
    assert scores(found) == [(4, 4), (5, 4)]
    assert found[0].last_ms == 11500  # one run across the hidden board and the stray reading
    tl = bd.Timeline(found)
    assert tl.at(6000) == found[0]  # hidden, but the same score before and after
    assert tl.at(11_800) is None  # between two scores: the card turned then
    assert tl.at(13_000) == found[1]


def test_misreadings_off_the_plus_one_path_are_dropped() -> None:
    plan = [
        (0, 4, (10, 8)),
        (4, 8, (11, 8)),
        (8, 12, (17, 8)),  # a 1 read as a 7, for 4 s
        (12, 16, (11, 9)),
        (16, 20, (12, 9)),
    ]
    assert scores(bd.segments(readings(plan))) == [(10, 8), (11, 8), (11, 9), (12, 9)]


def test_jumps_while_hidden_and_a_new_set() -> None:
    plan = [
        (0, 4, (20, 18)),
        (4, 60, None),  # hidden for a minute: three points
        (60, 64, (22, 19)),
        (64, 68, (25, 22)),
        (68, 200, None),
        (200, 204, (0, 0)),
        (204, 208, (1, 0)),
    ]
    found = bd.segments(readings(plan))
    assert scores(found) == [(20, 18), (22, 19), (25, 22), (0, 0), (1, 0)]
    assert [s.kind for s in found] == ["start", "jump", "jump", "set", "step"]
    assert [s.set_no for s in found] == [1, 1, 1, 2, 2]


def test_set_counts_name_the_set() -> None:
    samples = [
        bd.Sample(s.t_ms, s.left, s.right, s.confidence, (1, 1))
        for s in readings([(0, 4, (3, 2)), (4, 8, (4, 2))])
    ]
    assert [s.set_no for s in bd.segments(samples)] == [3, 3]


def test_unsure_readings_do_not_count() -> None:
    assert bd.segments(readings([(0, 10, (3, 2))], conf=0.4)) == []


def test_the_track_packs_and_unpacks() -> None:
    found = bd.segments(readings([(0, 4, (0, 0)), (4, 8, (1, 0))]))
    channels = bd.pack(found)
    assert bd.unpack(channels) == found
    with pytest.raises(ValueError):
        bd.unpack({"segments": "bm9uc2Vuc2U="})
    with pytest.raises(ValueError):
        bd.unpack({})


def test_a_noisy_match_reads_back() -> None:
    """A set played out with the board hidden a third of the time and 5 % misreadings."""
    rng = random.Random(3)  # noqa: S311 - test data
    truth: list[tuple[int, int]] = [(0, 0)]
    while max(truth[-1]) < 25:
        a, b = truth[-1]
        truth.append((a + 1, b) if rng.random() < 0.55 else (a, b + 1))
    samples = []
    t = 0
    for score in truth:
        for _ in range(rng.randint(30, 50)):  # 15-25 s per point
            hidden = rng.random() < 0.33
            wrong = rng.random() < 0.05
            if hidden:
                samples.append(bd.Sample(t, None, None, 0.0))
            elif wrong:
                samples.append(bd.Sample(t, rng.randint(0, 30), rng.randint(0, 30), 0.8))
            else:
                samples.append(bd.Sample(t, score[0], score[1], 0.9))
            t += 500
    found = scores(bd.segments(samples))
    assert found == truth


# --- in the scoring ----------------------------------------------------------------------------


def _set(n: int, p_near: float, board: list[str | None]) -> sc.SetIn:
    """n rallies 20 s apart, the serve evidence `p_near` for each, what the board says."""
    rallies = [
        sc.RallyObs(
            key=f"r{i}",
            start_ms=i * 20_000,
            end_ms=i * 20_000 + 8_000,
            p_near=p_near,
            board=board[i] if i < len(board) else None,  # type: ignore[arg-type]
            board_p=0.9 if i < len(board) and board[i] is not None else 0.0,
        )
        for i in range(n)
    ]
    return sc.SetIn(no=1, rallies=rallies, first_serve="us", our_side="near")


def test_the_board_names_winners_from_its_steps() -> None:
    assert sboard.says((3, 2), (4, 2)) == "us"
    assert sboard.says((3, 2), (3, 3)) == "them"
    assert sboard.says((3, 2), (3, 2)) == "none"
    assert sboard.says((3, 2), (5, 2)) is None  # a rally missed in between: nothing said
    assert sboard.says((3, 2), None) is None


def test_the_board_wins_over_the_serve_evidence() -> None:
    # Every serve looks like ours from the near end: the evidence says we win every rally.
    blind = sc.run_set(_set(6, 0.95, []), "near", "us")
    assert [r.won_by for r in blind.rallies[:5]] == ["us"] * 5
    told = ["us", "them", "them", "us", "them"]
    out = sc.run_set(_set(6, 0.95, told), "near", "us")
    assert [r.won_by for r in out.rallies[:5]] == told
    assert all(r.note != "board" for r in out.rallies[:5])


def test_no_change_on_the_board_is_no_point() -> None:
    out = sc.run_set(_set(4, 0.95, ["us", "none", "us"]), "near", "us")
    assert [r.point for r in out.rallies[:3]] == [True, False, True]


def test_a_coach_wins_over_the_board_and_the_rally_is_flagged() -> None:
    st = _set(4, 0.5, ["us", "us", "us"])
    st.rallies[1] = replace(st.rallies[1], won_fact="them")
    out = sc.run_set(st, "near", "us")
    assert out.rallies[1].won_by == "them"  # the coach's
    assert out.rallies[1].note == "board"  # the board said otherwise: a check
    assert [r.won_by for r in out.rallies[:3]] == ["us", "them", "us"]


def test_disagreements_with_the_serves_are_flagged() -> None:
    sets = [_set(6, 0.97, ["us", "them", "them", "us", "them"])]
    match = sc.score_match(sets)
    flagged, counts = sboard.flag_disagreements(sets, match)
    notes = [r.note for r in flagged.sets[0].rallies]
    assert counts["disagree_with_serves"] >= 2
    assert notes.count("board") >= 2
    assert [r.won_by for r in flagged.sets[0].rallies[:5]] == ["us", "them", "them", "us", "them"]


def test_which_side_of_the_board_is_ours_follows_the_official_score() -> None:
    rallies = [
        sc.RallyObs(key=f"r{i}", start_ms=i * 20_000, end_ms=i * 20_000 + 8_000) for i in range(3)
    ]
    st = sc.SetIn(no=1, rallies=rallies, official=(1, 2))
    board = sboard.Board(
        before={"r0": ((0, 0), 0.9), "r1": ((0, 1), 0.9), "r2": ((1, 1), 0.9)},
        finals={1: ((2, 1), 0.9)},
    )
    [oriented] = sboard.orient(board, [st])
    assert board.orientation == "official" and board.left_is_us is False
    # Left is theirs: 0:1 on the board is 1:0 for us.
    assert [r.board for r in oriented.rallies] == ["us", "them", "them"]
    out = sc.score_match([oriented])
    assert [r.won_by for r in out.sets[0].rallies] == ["us", "them", "them"]


def test_without_official_scores_the_better_fit_picks_the_side() -> None:
    # Serves say: we serve r0 and r1 from the near end, they serve r2 and r3.
    rallies = [
        sc.RallyObs(key=f"r{i}", start_ms=i * 20_000, end_ms=i * 20_000 + 8_000, p_near=p)
        for i, p in enumerate([0.97, 0.97, 0.03, 0.03, 0.97])
    ]
    st = sc.SetIn(no=1, rallies=rallies, first_serve="us", our_side="near")
    # Left won r0, right r1 ... in the board's order; left is us, by the serves.
    board = sboard.Board(
        before={f"r{i}": (s, 0.9) for i, s in enumerate([(0, 0), (1, 0), (1, 1), (1, 2)])},
        finals={},
    )
    board.before["r4"] = ((1, 3), 0.9)
    sboard.orient(board, [st])
    assert board.orientation == "fit" and board.left_is_us is True
