"""Official scoresheets, the pure parts (PLAN §10.3a): the service boxes decoded into every point
(round trip on random sets), the checks and the box they doubt, repairs, notes on the path, who
stood where, the sheet's own numbers, and the sheet lined up with the rallies in the scoring.
Made-up numbers only."""

from __future__ import annotations

import json
import os
import random
from collections.abc import Collection
from dataclasses import replace
from pathlib import Path

import pytest

from wieplay.ml import scoring as sc
from wieplay.ml import sheet as sh

N = None


def grid(rows: list[list[int | None]], boxes: int = 8) -> list[list[int | None]]:
    """A team's boxes from rows of the sheet: row b holds box b+1 of positions I-VI."""
    g: list[list[int | None]] = [[None] * boxes for _ in range(6)]
    for b, row in enumerate(rows):
        for p, v in enumerate(row):
            g[p][b] = v
    return g


# The research note's worked example (§3.3): them served first, 25:21 for us, won on reception.
THEM = grid([[2, 3, 5, 8, 10, 12], [13, 14, 16, 17, 20, 21]])
US = grid([[N, 1, 3, 5, 6, 7], [11, 14, 15, 17, 19, 24], [25, N, N, N, N, N]])
SEQUENCE = "TTUTUUTTUUTTTUTTUTTUUUUTUUUTUTTUUTUUTTTUUUUUTU"


def example(**changes: object) -> sh.SheetSet:
    s = sh.SheetSet(
        1,
        "them",
        {"us": US, "them": THEM},
        circled={"us": (1, 3), "them": (6, 2)},
        lineup={"us": [7, 3, 11, 5, 9, 1], "them": [4, 8, 12, 14, 2, 6]},
    )
    return replace(s, **changes)  # type: ignore[arg-type]


def winners(d: sh.Decoded) -> str:
    return "".join("U" if p.winner == "us" else "T" for p in d.points)


# --- decoding ----------------------------------------------------------------------------------


def test_the_worked_example_decodes_to_every_point() -> None:
    d = sh.decode(example(), official=(25, 21))
    assert winners(d) == SEQUENCE
    assert d.final == (25, 21) and d.consistent and d.issues == []
    # The first points: them 2 break points, then our side-out.
    assert [(p.server, p.winner, p.score()) for p in d.points[:4]] == [
        ("them", "them", (0, 1)), ("them", "them", (0, 2)), ("them", "us", (1, 2)),
        ("us", "them", (1, 3)),
    ]  # fmt: skip
    # Our first server is position II (#3): we received first.
    assert d.points[3].position == 2 and d.points[3].rot_us == 1


def test_the_sheet_order_is_the_turn_order() -> None:
    assert sh.cells(first=True)[:7] == [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 1), (1, 2)]
    assert sh.cells(first=False)[:2] == [(2, 1), (3, 1)]  # position I box 1 holds the X
    assert len(sh.cells(first=True, deciding=True)) == 36


@pytest.mark.parametrize("side_out", [0.5, 0.6, 0.7])
def test_random_sets_round_trip_exactly(side_out: float) -> None:
    """Sets played by the rules, written the official way and decoded: the exact points (the
    research note checked 20,000; a smaller run here)."""
    rng = random.Random(int(side_out * 100))  # noqa: S311 - test data
    for k in range(700):
        s, played = sh.synthetic_set(rng, side_out=side_out, deciding=k % 5 == 4)
        d = sh.decode(s)
        assert [p.winner for p in d.points] == played
        assert d.consistent and d.issues == []
        assert d.final == (played.count("us"), played.count("them"))
        # The circled finals are each team's last box.
        for side in sh.SIDES:
            mark = s.circled[side]
            assert mark is not None
            assert s.value(side, *mark) == (d.final[0] if side == "us" else d.final[1])


def test_encode_is_decodes_inverse() -> None:
    d = sh.decode(example())
    grids, circled = sh.encode("them", [p.winner for p in d.points])
    assert grids["us"] == US and grids["them"] == THEM
    assert circled == {"us": (1, 3), "them": (6, 2)}


# --- checks ------------------------------------------------------------------------------------


def edited(side: sh.Side, position: int, box: int, value: int | None) -> sh.SheetSet:
    s = example()
    g = [list(col) for col in s.boxes[side]]
    g[position - 1][box - 1] = value
    return replace(s, boxes={**s.boxes, side: g})


def codes(d: sh.Decoded) -> list[tuple[str, str, tuple[str, int, int] | None]]:
    return [
        (i.code, i.severity, (i.box.side, i.box.position, i.box.box) if i.box else None)
        for i in d.issues
    ]


def test_a_box_lower_than_the_score_is_named() -> None:
    # 17 typed 15: they had 17 when they won the serve back (their last box 16, plus the side-out)
    d = sh.decode(edited("them", 4, 2, 15), official=(25, 21))
    assert codes(d)[0] == ("low", "error", ("them", 4, 2))
    assert d.issues[0].params == {"value": 15, "score": 17}
    assert not d.consistent
    assert d.final == (17, 17)  # the decoding so far


def test_a_jump_past_the_end_of_the_set_is_named() -> None:
    d = sh.decode(edited("them", 4, 2, 71), official=(25, 21))  # 17 typed 71
    assert codes(d)[0] == ("over", "error", ("them", 4, 2))


def test_an_empty_box_between_two_is_a_hole() -> None:
    d = sh.decode(edited("them", 3, 1, None))
    assert ("hole", "error", ("them", 3, 1)) in codes(d)
    assert len(d.points) == 7  # decoded up to the hole


def test_a_missing_box_while_typing_is_incomplete_not_an_error() -> None:
    s = example()
    half = grid([[N, 1, 3, 5, 6, 7]])  # only our first row typed so far
    d = sh.decode(replace(s, boxes={"us": half, "them": THEM}))
    assert codes(d) == [("missing", "incomplete", ("us", 1, 2))]
    assert not d.consistent and d.final == (8, 12)


def test_a_reception_win_without_its_final_box_is_complete_with_a_warning() -> None:
    s = replace(edited("us", 1, 3, None), circled={})
    d = sh.decode(s, official=(25, 21))
    assert d.consistent and d.final == (25, 21) and winners(d) == SEQUENCE
    assert codes(d) == [("final_missing", "warning", ("us", 1, 3))]


def test_the_official_score_and_the_circled_finals_are_checked() -> None:
    d = sh.decode(example(), official=(25, 23))
    assert ("official", "error", ("us", 1, 3)) in codes(d)
    d = sh.decode(example(circled={"us": (6, 2), "them": (6, 2)}))
    assert ("circled", "error", ("us", 6, 2)) in codes(d)


def test_a_set_that_is_not_over_is_incomplete() -> None:
    them = grid([[2, 3, 5, 8, 10, 12], [13, 14, 16, 17, 20, 21]])
    us = grid([[N, 1, 3, 5, 6, 7], [11, 14, 15, 17, 19, 22]])
    d = sh.decode(example(boxes={"us": us, "them": them}))
    assert ("not_over", "incomplete", ("them", 6, 2)) in codes(d)


def test_no_first_server_yet() -> None:
    assert codes(sh.decode(example(first_serve=None))) == [("first_serve", "incomplete", None)]
    empty = sh.SheetSet(2, "us", {"us": grid([]), "them": grid([])})
    assert codes(sh.decode(empty)) == [("empty", "incomplete", None)]


# --- repairs ------------------------------------------------------------------------------------


def test_a_swapped_pair_of_digits_is_the_clear_repair() -> None:
    s = edited("them", 4, 2, 71)
    found = sh.repairs(s, official=(25, 21))
    best = sh.clear_repair(found)
    assert best is not None
    assert (best.kind, best.box, best.old, best.new) == (
        "change", sh.BoxRef("them", 4, 2), 71, 17
    )  # fmt: skip
    assert best.final == (25, 21)


def test_a_missing_last_box_is_inserted() -> None:
    us = grid([[N, 1, 3, 5, 6, 7], [11, 14, 15, 17, 19, N]])  # #1's 24 left out
    s = example(boxes={"us": us, "them": THEM}, circled={})
    best = sh.clear_repair(sh.repairs(s, official=(25, 21)))
    assert best is not None and best.box == sh.BoxRef("us", 6, 2) and best.new == 24


def test_an_empty_box_in_the_middle_has_no_clear_repair() -> None:
    """Any value between its neighbours gives the same final: the coach must look."""
    assert sh.clear_repair(sh.repairs(edited("them", 3, 1, None), official=(25, 21))) is None


def test_typo_costs() -> None:
    assert sh.typo_cost(71, 17) == sh.COST_SWAP
    assert sh.typo_cost(14, 15) == sh.COST_NEIGHBOUR  # 4 and 5 are neighbours on the keypad
    assert sh.typo_cost(14, 19) == sh.COST_DIGIT
    assert sh.typo_cost(1, 13) == sh.COST_LENGTH
    assert sh.typo_cost(None, 13) == sh.COST_FILL
    assert sh.typo_cost(3, 30) == sh.COST_LENGTH


# --- notes on the path, who stood where, the sheet's numbers ----------------------------------


def test_timeouts_substitutions_and_penalties_land_on_the_path() -> None:
    plain = sh.decode(example())
    # A score where the next point is ours: them penalised there gives it to us.
    k_us = next(k for k, p in enumerate(plain.points) if p.winner == "us" and k > 20)
    before = plain.scores()[k_us]
    s = example(
        timeouts=[sh.Timeout("them", 8, 6), sh.Timeout("us", 6, 8), sh.Timeout("us", 30, 2)],
        subs=[sh.Sub("us", 2, 12, (10, 12), (17, 17))],
        sanctions=[sh.Sanction("them", "P", before[1], before[0]), sh.Sanction("us", "W", 1, 2)],
    )
    d = sh.decode(s, official=(25, 21))
    # them 8:6 (requesting team first) = 6:8 us:them; the second is the same score from us.
    k = d.index_of((6, 8))
    assert d.timeout_at[:2] == [k, k]
    assert d.timeout_at[2] is None
    assert ("timeout_off", "warning", None) in codes(d)
    assert d.sub_at == [(d.index_of((10, 12)), d.index_of((17, 17)))]
    at = d.index_of(before)
    assert at == k_us and d.points[at].penalty and d.points[at].winner == "us"
    assert sum(p.penalty for p in d.points) == 1


def test_a_penalty_whose_next_point_is_not_the_opponents_is_flagged() -> None:
    s = example(sanctions=[sh.Sanction("us", "P", 0, 0)])  # them won the first point anyway
    d = sh.decode(s)
    assert d.points[0].penalty  # the point went to them: fits
    s = example(sanctions=[sh.Sanction("them", "P", 0, 0)])
    assert ("penalty_off", "warning", None) in codes(sh.decode(s))


def test_who_is_on_court() -> None:
    s = example(subs=[sh.Sub("us", 3, 12, (3, 4), None)])
    d = sh.decode(s)
    assert sh.on_court(s, d, "us", 0) == [7, 3, 11, 5, 9, 1]
    # After our first side-out (point 3) we rotated once: #3 (position II) serves.
    rotated = sh.on_court(s, d, "us", 3)
    assert rotated is not None and rotated[0] == 3
    # From 3:4 on, #12 stands where #11 (position III) would.
    k = d.index_of((3, 4))
    assert k is not None
    later = sh.on_court(s, d, "us", k)
    assert later is not None and 12 in later and 11 not in later
    assert sh.zone_of(1, 1) == 6 and sh.zone_of(3, 0) == 3  # a setter in 1 moves to 6


def test_the_sheets_own_numbers() -> None:
    stats = sh.set_stats(sh.decode(example()).points)
    us, them = stats["us"].team.as_json(), stats["them"].team.as_json()
    # The research note (§3.3): they served 22 (10 break points), we sided out 12 of 22; we
    # served 24 and won 13 of them.
    assert (them["served"], them["break_won"]) == (22, 10)
    assert (us["received"], us["side_out_won"]) == (22, 12)
    assert (us["served"], us["break_won"]) == (24, 13)
    assert us["side_out_pct"] == round(12 / 22, 4)
    assert sum(r.points for r in stats["us"].rotations) == 25
    assert stats["us"].longest_run == 5


# --- in the scoring: lined up with the rallies -------------------------------------------------


def rallies_for(
    played: list[sc.Side],
    first: sc.Side,
    *,
    skip: Collection[int] = (),
    extra: Collection[int] = (),
    sure: float = 0.9,
) -> tuple[list[sc.RallyObs], list[sc.Side]]:
    """Observed rallies of a set (our side near): every point but `skip`, a replay after the
    points in `extra`; returns the servers too."""
    out: list[sc.RallyObs] = []
    servers: list[sc.Side] = []
    server = first
    t = 0
    for i, won in enumerate(played):
        servers.append(server)
        t += 15_000 if i not in skip else 45_000
        if i not in skip:
            p = sure if server == "us" else 1 - sure
            out.append(sc.RallyObs(f"r{i}", t, t + 8_000, p, proposal=0.85))
        t += 8_000
        if i in extra:
            t += 12_000
            out.append(sc.RallyObs(f"x{i}", t, t + 6_000, 0.5, proposal=0.6))
            t += 6_000
        server = won
    return out, servers


def sheet_set(
    played: list[sc.Side], servers: list[sc.Side], penalty: Collection[int] = ()
) -> sc.SheetPath:
    return sc.SheetPath(
        tuple(played), tuple(servers), tuple(i in penalty for i in range(len(played)))
    )


def one_set(
    rallies: list[sc.RallyObs], path: sc.SheetPath, first: sc.Side, **kw: object
) -> sc.SetOut:
    final = (path.winners.count("us"), path.winners.count("them"))
    st = sc.SetIn(1, rallies, official=final, our_side="near", first_serve=first, sheet=path)
    return sc.score_match([replace(st, **kw)]).sets[0]  # type: ignore[arg-type]


def played_set(seed: int = 3) -> tuple[list[sc.Side], sc.Side]:
    rng = random.Random(seed)  # noqa: S311 - test data
    return sh.play(rng, "us", 0.6), "us"


def test_a_clean_set_takes_every_winner_from_the_sheet() -> None:
    played, first = played_set()
    rallies, servers = rallies_for(played, first, sure=0.6)  # weak serve evidence
    out = one_set(rallies, sheet_set(played, servers), first)
    assert out.sheet == "path"
    assert [r.won_by for r in out.rallies] == played
    assert [r.sheet_point for r in out.rallies] == list(range(1, len(played) + 1))
    assert all(r.note is None for r in out.rallies)
    assert out.final == (played.count("us"), played.count("them"))
    assert out.point_rally == [f"r{i}" for i in range(len(played))]


def test_a_missed_rally_and_a_replay_are_found_and_flagged() -> None:
    played, first = played_set()
    rallies, servers = rallies_for(played, first, skip={10}, extra={20})
    out = one_set(rallies, sheet_set(played, servers), first)
    by_key = {r.key: r for r in out.rallies}
    assert by_key["r11"].note == "missing_before" and by_key["r11"].missed != (0, 0)
    assert by_key["x20"].point is False and by_key["x20"].note == "extra"
    assert all(by_key[f"r{i}"].won_by == w for i, w in enumerate(played) if i != 10)
    assert out.point_rally[10] is None and out.missed == 1 and out.extra == 1


def test_a_penalty_point_needs_no_rally_and_is_not_flagged() -> None:
    played, first = played_set()
    rallies, servers = rallies_for(played, first, skip={12})
    out = one_set(rallies, sheet_set(played, servers, penalty={12}), first)
    by_key = {r.key: r for r in out.rallies}
    assert by_key["r13"].note is None and by_key["r13"].missed != (0, 0)
    assert out.penalties == 1 and out.missed == 0


def test_a_coach_who_disagrees_with_the_sheet_is_flagged() -> None:
    played, first = played_set()
    rallies, servers = rallies_for(played, first)
    wrong: sc.Side = "them" if played[5] == "us" else "us"
    rallies[5] = replace(rallies[5], won_fact=wrong)
    out = one_set(rallies, sheet_set(played, servers), first)
    assert out.rallies[5].note == "sheet" and out.rallies[5].won_by == played[5]


def test_cancelled_points_make_their_rallies_free() -> None:
    played, first = played_set()
    rallies, servers = rallies_for(played, first, extra={20})
    path = replace(sheet_set(played, servers), cancelled={21: 1})
    out = one_set(rallies, path, first)
    by_key = {r.key: r for r in out.rallies}
    assert by_key["x20"].point is False and by_key["x20"].note is None


def test_a_sheet_that_cannot_be_lined_up_becomes_a_hint() -> None:
    played, first = played_set()
    rallies, servers = rallies_for(played, first)
    # Short gaps hold no missed rally: ten more points than rallies cannot be lined up.
    rallies = [
        replace(r, start_ms=k * 13_000, end_ms=k * 13_000 + 8_000) for k, r in enumerate(rallies)
    ]
    longer = played + ["them"] * 10
    path = sc.SheetPath(tuple(longer), tuple(servers + ["them"] * 10), (False,) * len(longer))
    st = sc.SetIn(1, rallies, official=None, our_side="near", first_serve=first, sheet=path)
    out = sc.score_match([st]).sets[0]
    assert out.sheet == "hint"


def test_the_sheet_says_who_served_first() -> None:
    played, first = played_set()
    rallies, servers = rallies_for(played, first)
    path = sheet_set(played, servers)
    assert sc.set_cost(sc.SetIn(1, rallies, sheet=path), "near", "them") == float("inf")


# --- the browser's decoder: the same answers (web/src/lib/scoresheet.ts) ----------------------

FIXTURE = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "scoresheet.fixture.json"


def _doc(s: sh.SheetSet) -> dict[str, object]:
    """A set as the API takes it (wieplay/core/scoresheet.py SetSheetIn)."""
    n = sh.boxes_for(s.deciding)

    def grid(side: sh.Side) -> list[list[int | None]]:
        g = s.boxes.get(side) or []
        return [[(g[p][b] if p < len(g) and b < len(g[p]) else None) for b in range(n)]
                for p in range(6)]  # fmt: skip

    return {
        "no": s.no,
        "deciding": s.deciding,
        "first_serve": s.first_serve,
        "lineup_us": list(s.lineup.get("us") or []) or None,
        "lineup_them": list(s.lineup.get("them") or []) or None,
        "boxes_us": grid("us"),
        "boxes_them": grid("them"),
        "circled_us": list(c) if (c := s.circled.get("us")) else None,
        "circled_them": list(c) if (c := s.circled.get("them")) else None,
        "timeouts": [{"side": t.side, "own": t.own, "opp": t.opp} for t in s.timeouts],
        "subs": [
            {
                "side": u.side,
                "position": u.position,
                "number": u.number_in,
                "at": list(u.at) if u.at else None,
                "back": list(u.back) if u.back else None,
            }
            for u in s.subs
        ],
        "sanctions": [
            {"side": x.side, "kind": x.kind, "own": x.own, "opp": x.opp} for x in s.sanctions
        ],
        "cancelled": [],
        "start": None,
        "end": None,
        "use": True,
    }


def fixture_cases() -> list[dict[str, object]]:
    rng = random.Random(7)  # noqa: S311 - test data
    cases: list[tuple[str, sh.SheetSet, tuple[int, int] | None]] = []
    for k in range(30):
        s, played = sh.synthetic_set(rng, no=5 if k % 6 == 5 else 1 + k % 4, deciding=k % 6 == 5)
        cases.append((f"random {k}", s, (played.count("us"), played.count("them"))))
    cases += [
        ("worked example", example(), (25, 21)),
        ("low box", edited("them", 4, 2, 15), (25, 21)),
        ("over", edited("them", 4, 2, 71), (25, 21)),
        ("hole", edited("them", 3, 1, None), (25, 21)),
        ("final missing", replace(edited("us", 1, 3, None), circled={}), (25, 21)),
        ("official differs", example(), (25, 23)),
        ("circled elsewhere", example(circled={"us": (6, 2), "them": (6, 2)}), None),
        (
            "typing",
            replace(example(), boxes={"us": grid([[N, 1, 3, 5, 6, 7]]), "them": THEM}),
            None,
        ),
        ("no first server", example(first_serve=None), None),
        (
            "notes",
            example(
                timeouts=[
                    sh.Timeout("them", 8, 6),
                    sh.Timeout("us", 6, 8),
                    sh.Timeout("us", 30, 2),
                ],
                subs=[sh.Sub("us", 2, 12, (10, 12), (17, 17)), sh.Sub("us", 3, 7, (5, 5), None)],
                sanctions=[sh.Sanction("them", "P", 0, 0), sh.Sanction("us", "P", 0, 0)],
            ),
            (25, 21),
        ),
    ]
    out: list[dict[str, object]] = []
    for name, s, official in cases:
        d = sh.decode(s, official)
        out.append(
            {
                "name": name,
                "set": _doc(s),
                "official": list(official) if official else None,
                "expect": {
                    "winners": winners(d),
                    "penalties": [k for k, p in enumerate(d.points) if p.penalty],
                    "final": list(d.final),
                    "complete": d.complete,
                    "consistent": d.consistent,
                    "issues": [
                        [i.code, i.severity, i.box.as_json() if i.box else None] for i in d.issues
                    ],
                },
            }
        )
    return out


def test_the_browsers_decoder_fixture_is_current() -> None:
    """The fixture web/src/lib/scoresheet.test.ts checks the browser's decoder against. To
    renew it: WIEPLAY_WRITE_SHEET_FIXTURE=1 uv run pytest tests/test_scoresheet.py."""
    cases = fixture_cases()
    text = "[\n" + ",\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n]\n"
    if os.environ.get("WIEPLAY_WRITE_SHEET_FIXTURE"):
        FIXTURE.write_text(text)
    assert FIXTURE.exists() and json.loads(FIXTURE.read_text()) == cases
