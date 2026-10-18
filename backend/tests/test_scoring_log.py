"""The club scoreboard's log in the scoring (PLAN §10.10, wieplay/ml/scoring.py): its winners
win over the serve evidence and the scoreboard read in the picture, never over a coach's fact;
its "no point" and the points it saw without a rally shape the score line."""

from __future__ import annotations

from dataclasses import replace

from tests.test_scoring import accuracy, play_set
from wieplay.ml import scoring as sc


def told(rallies: list[sc.RallyObs], winners: list[sc.Side], p: float = 1.0) -> list[sc.RallyObs]:
    return [
        replace(r, log=w, log_p=p, log_missed=(0, 0)) for r, w in zip(rallies, winners, strict=True)
    ]


def test_the_log_wins_over_serves_that_say_the_opposite() -> None:
    rallies, winners, final = play_set(1, "near", "us", seed=5, sure=0.97)
    # The camera read every serve of the middle third the wrong way round.
    third = len(rallies) // 3
    wrong = [
        replace(r, p_near=1 - (r.p_near or 0.5)) if third <= i < 2 * third else r
        for i, r in enumerate(rallies)
    ]
    blind = sc.run_set(sc.SetIn(1, wrong, official=final), "near", "us")
    assert accuracy(blind, winners) < 0.95
    out = sc.run_set(sc.SetIn(1, told(wrong, winners), official=final), "near", "us")
    assert accuracy(out, winners) == 1.0
    assert not any(r.note == "log" for r in out.rallies)  # the path follows the log


def test_the_log_wins_over_the_board_in_the_picture() -> None:
    rallies, winners, final = play_set(1, "near", "us", seed=6, sure=0.9)
    board_wrong = [
        replace(r, board="them" if w == "us" else "us", board_p=1.0) if i == 4 else r
        for i, (r, w) in enumerate(zip(rallies, winners, strict=True))
    ]
    out = sc.run_set(sc.SetIn(1, told(board_wrong, winners), official=final), "near", "us")
    assert out.rallies[4].won_by == winners[4]
    assert out.rallies[4].note == "board"  # the board is overruled, and says so


def test_a_coach_fact_wins_over_the_log_and_the_rally_is_flagged() -> None:
    rallies, winners, final = play_set(1, "far", "them", seed=8, sure=0.97)
    # The coach saw two rallies the other way round (the set still ends where it ended).
    i = next(k for k in range(10, len(winners) - 1) if winners[k] != winners[k + 1])
    with_log = told(rallies, winners)
    with_log[i] = replace(with_log[i], won_fact=winners[i + 1])
    with_log[i + 1] = replace(with_log[i + 1], won_fact=winners[i])
    out = sc.run_set(sc.SetIn(1, with_log, official=final), "far", "them")
    assert [out.rallies[i].won_by, out.rallies[i + 1].won_by] == [winners[i + 1], winners[i]]
    assert out.rallies[i].note == out.rallies[i + 1].note == "log"
    # One fact alone against the log and the official score: the path keeps to the log, the
    # rally is flagged for the coach (whose fact stays the score line's: tagging.rescore).
    alone = told(rallies, winners)
    alone[i] = replace(alone[i], won_fact=winners[i + 1])
    out = sc.run_set(sc.SetIn(1, alone, official=final), "far", "them")
    assert out.rallies[i].note in ("coach", "log")


def test_no_point_and_the_points_without_a_rally() -> None:
    rallies, winners, final = play_set(1, "near", "us", seed=9, sure=0.9)
    # The finder missed rally 7 and proposed a rally that was none after rally 12.
    missing = 7
    extra = sc.RallyObs(
        "extra", rallies[12].end_ms + 2_000, rallies[12].end_ms + 6_000, 0.5, proposal=0.8
    )
    kept = told(rallies, winners)
    gone = kept.pop(missing)
    missed = (1, 0) if winners[missing] == "us" else (0, 1)
    kept[missing] = replace(kept[missing], log_missed=missed)
    kept.insert(12, replace(extra, log="none", log_p=0.6))
    out = sc.run_set(sc.SetIn(1, kept, official=final), "near", "us")
    by_key = {r.key: r for r in out.rallies}
    assert not by_key["extra"].point
    after_gap = by_key[kept[missing].key]
    assert after_gap.missed == missed
    assert out.final == final and out.missed == 1 and out.extra == 1
    assert gone.key not in by_key


def test_points_the_log_saw_after_the_last_rally() -> None:
    rallies, winners, final = play_set(1, "near", "us", seed=4, sure=0.9)
    kept = told(rallies, winners)[:-2]
    last_two = winners[-2:]
    after = (last_two.count("us"), last_two.count("them"))
    out = sc.run_set(sc.SetIn(1, kept, official=final, log_missed_after=after), "near", "us")
    assert out.final == final and out.missed_after == after
    assert all(r.won_by == w for r, w in zip(out.rallies, winners, strict=False))
