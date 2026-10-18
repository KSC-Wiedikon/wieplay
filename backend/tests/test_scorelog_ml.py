"""The club scoreboard's log against a video (PLAN §10.10, wieplay/ml/scorelog.py): the shift
between the board's clock and the video's, one per stretch when the video was cut, presses to
rallies, missed rallies and rallies that were no point, the per-set check against the official
score."""

from __future__ import annotations

import random
from typing import Any

import pytest

from wieplay.ml import scorelog as sl

BOARD_T0 = 1_790_961_000_000  # the board's clock (ms epoch)
SHIFT = 860_000 - BOARD_T0  # video ms = board ms + SHIFT


def synth(
    finals: list[tuple[int, int]],
    *,
    seed: int = 7,
    cut_after_set: int | None = None,
    cut_ms: int = 23_000,
    timeouts_every: int = 9,
) -> tuple[dict[str, Any], list[sl.RallySpan], dict[str, str]]:
    """A match as the bridge and the video would have it: rallies of 4-20 s with 12-25 s
    between them (a timeout of ~75 s every few points, a 3 min set break), each press 2-7 s
    after the rally's end; `cut_after_set`: the video lost `cut_ms` in that set break.
    Returns the bridge's payload, the video's rallies and who won each (a | b)."""
    rnd = random.Random(seed)  # noqa: S311 - test data
    events: list[dict[str, Any]] = []
    rallies: list[sl.RallySpan] = []
    truth: dict[str, str] = {}
    t = 900_000  # video ms of the first serve
    cut = 0
    sets = [0, 0]
    for no, (fa, fb) in enumerate(finals, start=1):
        order = ["a"] * fa + ["b"] * fb
        rnd.shuffle(order)
        # The set's last point goes to its winner.
        winner = "a" if fa > fb else "b"
        order.remove(winner)
        order.append(winner)
        a = b = 0
        for k, side in enumerate(order):
            start = t
            end = start + rnd.randint(4_000, 20_000)
            key = f"s{no}r{k}"
            rallies.append(sl.RallySpan(key, start, end))
            truth[key] = side
            a, b = (a + 1, b) if side == "a" else (a, b + 1)
            board = end + rnd.randint(2_000, 7_000) - SHIFT + cut
            events.append(_ev("point", board, no, a, b, sets, side=side))
            t = end + rnd.randint(12_000, 25_000)
            if timeouts_every and k % timeouts_every == timeouts_every - 1 and k < len(order) - 1:
                events.append(_ev("timeout", board + 1_500, no, a, b, sets, side="b"))
                t += 60_000
        sets[0 if fa > fb else 1] += 1
        events.append(_ev("set_end", events[-1]["board_ms"], no, fa, fb, sets))
        t += 180_000
        if cut_after_set == no:
            cut += cut_ms
    payload = {
        "version": 1,
        "source": "live_scores_revisions",
        "channel": "kscw",
        "board_names": {"a": "KSCW H3", "b": "VOLÉRO"},
        "events": events,
        "sets": [{"no": i, "a": fa, "b": fb} for i, (fa, fb) in enumerate(finals, start=1)],
    }
    return payload, rallies, truth


def _ev(kind: str, board: int, no: int, a: int, b: int, sets: list[int], **extra: Any) -> Any:
    return {
        "kind": kind,
        "board_ms": board,
        "server_ms": board + 150,
        "period": no,
        "points_a": a,
        "points_b": b,
        "sets_a": sets[0],
        "sets_b": sets[1],
        **extra,
    }


def winners_right(al: sl.Alignment, truth: dict[str, str]) -> tuple[int, int]:
    said = [(v.key, v.says) for v in al.verdicts.values() if v.says != "none"]
    return sum(truth[k] == s for k, s in said), len(said)


def test_parse_takes_the_bridge_answer_and_refuses_junk() -> None:
    payload, _, _ = synth([(25, 20)])
    log = sl.parse(payload)
    assert log is not None and log.source == "live_scores_revisions"
    assert len(log.points) == 45 and log.finals == {1: (25, 20)}
    assert log.board_names == ("KSCW H3", "VOLÉRO")
    assert sl.parse({"version": 1, "source": None, "events": []}) is None  # no log
    with pytest.raises(ValueError):
        sl.parse({"version": 2, "source": "x", "events": []})
    with pytest.raises(ValueError):
        sl.parse({"version": 1, "source": "x", "events": [{"kind": "goal", "period": 1}]})
    with pytest.raises(ValueError):
        sl.parse({"version": 1, "source": "x", "events": [{"kind": "point", "period": 0}]})


def test_one_shift_lines_every_press_up_with_its_rally() -> None:
    payload, rallies, truth = synth([(25, 20), (23, 25), (25, 18)])
    log = sl.parse(payload)
    assert log is not None
    al = sl.align(log, rallies)
    assert al.shift_ms is not None and abs(al.shift_ms - SHIFT) <= 4_000
    assert not any(s.own for s in al.stretches)
    assert al.matched == al.points == 136
    right, said = winners_right(al, truth)
    assert right == said == 136
    assert all(s.anchors >= 1 for s in al.stretches)  # timeouts and set ends in long breaks
    v = al.verdicts["s2r0"]
    assert v.period == 2 and v.score is not None and sum(v.score) == 1 and v.p == 1.0
    assert not al.missed and not al.missed_after


def test_a_cut_in_the_video_gets_its_own_shift() -> None:
    payload, rallies, truth = synth([(25, 20), (23, 25), (25, 18), (25, 22)], cut_after_set=2)
    log = sl.parse(payload)
    assert log is not None
    al = sl.align(log, rallies)
    shifts = {s.period: s.shift_ms for s in al.stretches}
    assert abs((shifts[1] - shifts[3]) - 23_000) <= 4_000
    assert abs(shifts[1] - shifts[2]) <= 1_000 and abs(shifts[3] - shifts[4]) <= 1_000
    right, said = winners_right(al, truth)
    assert right == said == al.points


def test_missed_rallies_and_rallies_that_were_no_point() -> None:
    payload, rallies, truth = synth([(25, 20)])
    log = sl.parse(payload)
    assert log is not None
    # The finder missed rally 10 and proposed a "rally" in the gap after rally 20.
    kept = [r for r in rallies if r.key != "s1r10"]
    r20 = next(r for r in rallies if r.key == "s1r20")
    nxt = next(r for r in rallies if r.key == "s1r21")
    fake = sl.RallySpan("fake", r20.end_ms + 9_000, min(r20.end_ms + 12_000, nxt.start_ms - 1))
    al = sl.align(log, [*kept, fake])
    side = truth["s1r10"]
    assert al.missed["s1r11"] == ((1, 0) if side == "a" else (0, 1))
    assert al.verdicts["fake"].says == "none" and al.verdicts["fake"].p == sl.NONE_P
    right, said = winners_right(al, truth)
    assert right == said == 44


def test_untimed_points_say_nothing_about_the_rallies_around_them() -> None:
    payload, rallies, _ = synth([(25, 20)])
    events = payload["events"]
    # The board was offline for points 5 and 6: one jump, no times.
    i = next(
        k
        for k, e in enumerate(events)
        if e["kind"] == "point" and e["points_a"] + e["points_b"] == 5
    )
    jump = {**events[i + 1], "kind": "jump", "delta": [1, 1], "side": None}
    payload["events"] = [*events[:i], jump, *events[i + 2 :]]
    log = sl.parse(payload)
    assert log is not None
    al = sl.align(log, rallies)
    # The two rallies without a press are not called "no point".
    assert sum(1 for v in al.verdicts.values() if v.says == "none") == 0
    assert al.matched == al.points == 43


def test_a_set_must_end_on_the_official_score() -> None:
    payload, rallies, _ = synth([(25, 20), (23, 25)])
    log = sl.parse(payload)
    assert log is not None
    al = sl.align(log, rallies)
    sets = sl.trust(log, {1: (25, 20), 2: (25, 23)}, al)
    assert sets[1]["trusted"] and sets[1]["agrees"] and sets[1]["matched_share"] == 1.0
    assert not sets[2]["trusted"] and not sets[2]["agrees"]
    assert sets[2]["final"] == [23, 25] and sets[2]["official"] == [25, 23]
    # Without official scores nothing is trusted.
    assert not any(s["trusted"] for s in sl.trust(log, None, al).values())


def test_a_set_whose_presses_found_no_rallies_is_not_trusted() -> None:
    payload, rallies, _ = synth([(25, 20), (23, 25)], seed=3)
    log = sl.parse(payload)
    assert log is not None
    # The finder found set 2's rallies only now and then.
    sparse = [r for k, r in enumerate(rallies) if not r.key.startswith("s2") or k % 3 == 0]
    al = sl.align(log, sparse)
    sets = sl.trust(log, {1: (25, 20), 2: (23, 25)}, al)
    assert sets[1]["trusted"] and sets[1]["fit_share"] == 1.0
    assert sets[2]["agrees"] and not sets[2]["trusted"]
    assert sets[2]["matched_share"] < sl.MIN_TRUSTED_MATCHED


def test_fit_weight_prefers_a_press_a_few_seconds_after_the_end() -> None:
    assert sl.fit_weight(4_000) == 1.0
    assert sl.fit_weight(0) == 0.8
    assert sl.fit_weight(-5_000) == 0.0
    assert sl.fit_weight(-5_000, inside=True) == 0.6
    assert 0.3 < sl.fit_weight(18_000) < 0.8
    assert sl.fit_weight(30_000) == 0.1
    assert sl.fit_weight(60_000) == 0.0
    assert sl.weight_at(5_000, 0, 12_000) == 0.6  # pressed inside the rally as the video has it
    assert sl.weight_at(10_000, 0, 12_000) == 0.8  # pressed close to its end
