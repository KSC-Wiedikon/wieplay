"""The joint decode (PLAN §10.3 "One decode, every source"): the scoresheet read from its photo
as soft evidence, timeouts, substitutions and sanctions as "score at this break" evidence, penalty
points, the log's score, the posteriors, and which rallies are sure (`judge`)."""

from __future__ import annotations

import random
from dataclasses import replace

from wieplay.core import scoring as core_scoring
from wieplay.ml import scoring as sc
from wieplay.ml import sheet as sh

# --- a set, its rallies, its sheet ---------------------------------------------------------------


def played(seed: int = 3, first: sc.Side = "us") -> list[sc.Side]:
    return sh.play(random.Random(seed), first, 0.6)  # noqa: S311 - test data


def rallies_for(
    winners: list[sc.Side],
    first: sc.Side,
    *,
    sure: float = 0.6,
    gap_ms: int = 15_000,
    long_after: set[int] | frozenset[int] = frozenset(),
    skip: set[int] | frozenset[int] = frozenset(),
    noise: float = 0.0,
) -> list[sc.RallyObs]:
    """One rally per point, our side near; each rally's serve reading points at its server
    with probability `sure` (at the other end for a share `noise` of them); a long break after
    the points in `long_after`."""
    rng = random.Random(11)  # noqa: S311 - test data
    out: list[sc.RallyObs] = []
    server = first
    t = 0
    for i, won in enumerate(winners):
        t += gap_ms if i - 1 not in long_after else 60_000
        if i not in skip:
            near = (server == "us") != (rng.random() < noise)
            p = sure if near else 1 - sure
            out.append(sc.RallyObs(f"r{i}", t, t + 8_000, p, proposal=0.85))
        t += 8_000
        server = won
    return out


def read_cells(
    winners: list[sc.Side],
    first: sc.Side,
    *,
    misread: dict[tuple[sc.Side, int, int], tuple[int, float]] | None = None,
) -> sc.BoxCells:
    """The sheet's service boxes as a reader would give them: the written value at 0.9, the
    rest blank; `misread`: a box read as another value with that probability (the right one
    second)."""
    grids, _ = sh.encode(first, winners)
    boxes: dict[sc.Side, dict[tuple[int, int], tuple[tuple[sc.Value, float], ...]]] = {}
    for side in sh.SIDES:
        cells: dict[tuple[int, int], tuple[tuple[sc.Value, float], ...]] = {}
        for p in range(1, 7):
            for b in range(1, 9):
                v = grids[side][p - 1][b - 1]
                wrong = (misread or {}).get((side, p, b))
                if wrong is not None:
                    cells[(p, b)] = ((wrong[0], wrong[1]), (v, 1 - wrong[1] - 0.02))
                elif v is None:
                    cells[(p, b)] = ((None, 0.97),)
                else:
                    cells[(p, b)] = ((v, 0.9), (None, 0.05))
        boxes[side] = cells
    return sc.BoxCells(boxes, first_cost={first: 0.0, sh.other(first): 6.0})


def final_of(winners: list[sc.Side]) -> tuple[int, int]:
    return winners.count("us"), winners.count("them")


def decode(st: sc.SetIn, sure: float = sc.SURE) -> sc.SetOut:
    [so] = sc.score_match([st]).sets
    return sc.judge(st, so, extra=core_scoring.note_reasons(st, so), sure=sure)


def right(so: sc.SetOut, winners: list[sc.Side]) -> int:
    return sum(1 for r in so.rallies if r.point and r.won_by == winners[int(r.key[1:])])


# --- the sheet read from its photo: soft evidence -------------------------------------------------


def test_a_read_sheet_names_the_winners_the_serves_alone_cannot() -> None:
    winners, first = played(), "us"
    rallies = rallies_for(winners, first, sure=0.8, noise=0.2)  # every fifth serve misread
    plain = sc.SetIn(1, rallies, official=final_of(winners), our_side="near")
    with_sheet = replace(plain, cells=read_cells(winners, first))
    alone = decode(plain)
    read = decode(with_sheet)
    assert read.sheet == "read" and read.first_serve == first
    assert right(read, winners) > right(alone, winners)
    # The boxes it decoded, and how sure it is of them.
    grids, _ = sh.encode(first, winners)
    decoded = {k: v for k, (v, _) in read.boxes.items()}
    assert sum(v == grids[s][p - 1][b - 1] for (s, p, b), v in decoded.items()) >= 0.9 * len(
        decoded
    )
    assert all(0.0 <= prob <= 1.0 for _, prob in read.boxes.values())
    # Right on every rally it is sure of.
    sure = [r for r in read.rallies if r.sure]
    assert all(r.won_by == winners[int(r.key[1:])] for r in sure)
    assert sum(r.sure for r in read.rallies) >= sum(r.sure for r in alone.rallies)


def test_a_misread_box_is_outvoted_and_its_rallies_say_why() -> None:
    winners, first = played(), "us"
    grids, _ = sh.encode(first, winners)
    side, p, b = next(
        (s, p, b)
        for s in sh.SIDES
        for b in (1, 2)
        for p in range(1, 7)
        if grids[s][p - 1][b - 1] not in (None, 0)
    )
    true = grids[side][p - 1][b - 1]
    assert true is not None
    cells = read_cells(winners, first, misread={(side, p, b): (true + 6, 0.93)})
    st = sc.SetIn(1, rallies_for(winners, first, sure=0.8), official=final_of(winners))
    out = decode(replace(st, our_side="near", cells=cells))
    assert right(out, winners) == len(winners)
    assert out.boxes[(side, p, b)][0] == true  # the rules and the video read it right
    flagged = [r for r in out.rallies if any(x.code == "sheet_box" for x in r.reasons)]
    assert flagged and all(r.box == (side, p, b) and not r.sure for r in flagged)
    reason = next(x for x in flagged[0].reasons if x.code == "sheet_box")
    assert reason.params["values"] == [true, true + 6]


def test_a_box_a_coach_typed_into_the_draft_counts_as_typed() -> None:
    winners, first = played(), "us"
    grids, _ = sh.encode(first, winners)
    # A box misread...
    side, p, b = "them", 2, 1
    true = grids[side][p - 1][b - 1]
    assert true is not None
    cells = read_cells(winners, first, misread={(side, p, b): (true + 3, 0.95)})
    # ...but corrected by a coach in the draft: certain, never "unclear".
    typed = replace(cells, fixed={side: {(p, b): true}})
    st = sc.SetIn(1, rallies_for(winners, first, sure=0.7), official=final_of(winners))
    out = decode(replace(st, our_side="near", cells=typed))
    assert out.boxes[(side, p, b)][0] == true
    assert not any(x.code == "sheet_box" for r in out.rallies for x in r.reasons)


# --- timeouts, substitutions, sanctions; penalty points -------------------------------------------


def test_a_timeout_in_a_long_break_fits_one_in_a_short_break_does_not() -> None:
    winners, first = played(), "us"
    k = 10  # after the 11th point
    scores = [(winners[: i + 1].count("us"), winners[: i + 1].count("them")) for i in range(30)]
    note = sc.BreakNote("timeout", "them", scores[k])
    path = sc.SheetPath(tuple(winners), tuple([first, *winners[:-1]]), (False,) * len(winners))
    rallies = rallies_for(winners, first, sure=0.9, long_after={k})
    st = sc.SetIn(
        1, rallies, official=final_of(winners), our_side="near", first_serve=first, sheet=path
    )
    fits = decode(replace(st, notes=(note,)))
    assert not any(x.code == "note" for r in fits.rallies for x in r.reasons)
    # The same timeout two points later: there the break is short.
    elsewhere = replace(note, score=scores[k + 2])
    short = decode(replace(st, notes=(elsewhere,)))
    [flag] = [x for r in short.rallies for x in r.reasons if x.code == "note"]
    assert flag.params["issue"] == "short" and flag.params["score"] == list(scores[k + 2])
    assert short.rallies[k + 2].reasons and not short.rallies[k + 2].sure


def test_a_timeout_whose_score_the_set_never_had_is_flagged_where_it_would_be() -> None:
    winners, first = played(), "us"
    st = sc.SetIn(
        1,
        rallies_for(winners, first, sure=0.7),
        official=final_of(winners),
        our_side="near",
        cells=read_cells(winners, first),
    )
    never = next(
        (a, b)
        for a in range(5, 20)
        for b in range(5, 20)
        if (a, b) not in {(winners[:i].count("us"), winners[:i].count("them")) for i in range(60)}
    )
    out = decode(replace(st, notes=(sc.BreakNote("timeout", "us", never),)))
    [flagged] = [r for r in out.rallies if any(x.code == "note" for x in r.reasons)]
    reason = next(x for x in flagged.reasons if x.code == "note")
    assert reason.params["issue"] == "off_path"
    assert sum(reason.params["here"]) >= sum(never)


def test_a_penalty_point_comes_without_a_rally() -> None:
    winners, first = played(), "us"
    k = 12  # a red card for us at this score: the point goes to them, no rally
    winners = [*winners[:k], "them", *winners[k + 1 :]]
    # A legal set again: play it out from there.
    a, b = winners[:k].count("us"), winners[:k].count("them")
    rng = random.Random(5)  # noqa: S311 - test data
    rest: list[sc.Side] = []
    score = [a, b + 1]
    serving: sc.Side = "them"
    while not sh.is_over(score[0], score[1], 25):
        won: sc.Side = sh.other(serving) if rng.random() < 0.6 else serving
        rest.append(won)
        score[0 if won == "us" else 1] += 1
        serving = won
    winners = [*winners[: k + 1], *rest]
    cells = read_cells(winners, first)
    rallies = rallies_for(winners, first, sure=0.8, skip={k})
    st = sc.SetIn(
        1,
        rallies,
        official=final_of(winners),
        our_side="near",
        cells=cells,
        penalties={(a, b): "them"},
        notes=(sc.BreakNote("sanction", "us", (a, b), "P"),),
    )
    out = decode(st)
    after = next(r for r in out.rallies if r.key == f"r{k + 1}")
    assert after.missed == (0, 1)  # the penalty point before it
    assert after.note is None  # no rally missed there: the penalty has none by the rules
    assert not any(x.code == "note" for x in after.reasons)
    assert right(out, winners) == len(rallies)


# --- the log's score, the coach's facts, the threshold -------------------------------------------


def test_the_logs_score_pins_a_set_the_video_joins_late() -> None:
    winners, first = played(), "us"
    rallies = rallies_for(winners, first, sure=0.95)
    # The first three points have no rally; the log knows the score after each press.
    late = rallies[3:]
    scores = [(winners[: i + 1].count("us"), winners[: i + 1].count("them")) for i in range(60)]
    told = [
        replace(r, log=winners[int(r.key[1:])], log_p=1.0, log_score=scores[int(r.key[1:])])
        for r in late
    ]
    # The log saw those three points without a rally of their own.
    told[0] = replace(told[0], log_missed=scores[2])
    st = sc.SetIn(1, told, official=final_of(winners), our_side="near", first_serve=first)
    out = decode(st)
    assert [(r.score_us, r.score_them) for r in out.rallies] == [
        scores[int(r.key[1:])] for r in late
    ]
    assert out.rallies[0].missed != (0, 0)
    assert any(x.code == "missing_before" for x in out.rallies[0].reasons)
    assert all(r.sure for r in out.rallies[1:])


def test_a_coach_decision_flows_back_and_its_neighbours_become_sure() -> None:
    winners, first = played(seed=8), "them"
    rallies = rallies_for(winners, first, sure=0.95)
    scores = [(winners[: i + 1].count("us"), winners[: i + 1].count("them")) for i in range(60)]
    k = 14
    # The log names every rally but two; the serve of the second reads the other end.
    told = [
        replace(r, log=winners[i], log_p=0.9, log_score=scores[i]) if i not in (k - 1, k) else r
        for i, r in enumerate(rallies)
    ]
    wrong = told[k].p_near
    assert wrong is not None
    told[k] = replace(told[k], p_near=1 - wrong)
    st = sc.SetIn(1, told, official=final_of(winners), our_side="near", first_serve=first)
    before = {r.key: r for r in decode(st).rallies}
    assert not before[f"r{k - 1}"].sure and not before[f"r{k}"].sure
    assert before[f"r{k}"].reasons[0].code == "video"
    # The coach says who won the first of the two: a fact; the decode runs again.
    decided = list(told)
    decided[k - 1] = replace(decided[k - 1], won_fact=winners[k - 1])
    after = {r.key: r for r in decode(replace(st, rallies=decided)).rallies}
    assert after[f"r{k - 1}"].sure and after[f"r{k - 1}"].won_by == winners[k - 1]
    # Its neighbour, which the coach did not touch, is sure now, and right.
    assert after[f"r{k}"].sure and after[f"r{k}"].won_by == winners[k]
    assert all(r.won_by == winners[int(r.key[1:])] for r in after.values() if r.sure)


def test_sure_means_the_posterior_and_no_source_against() -> None:
    winners, first = played(), "us"
    st = sc.SetIn(
        1,
        rallies_for(winners, first, sure=0.9),
        official=final_of(winners),
        our_side="near",
        cells=read_cells(winners, first),
    )
    [so] = sc.score_match([st]).sets
    loose = sc.judge(st, replace(so, rallies=list(so.rallies)), sure=0.5)
    strict = sc.judge(st, replace(so, rallies=list(so.rallies)), sure=0.999999)
    n_loose = sum(r.sure for r in loose.rallies)
    n_strict = sum(r.sure for r in strict.rallies)
    assert n_loose > n_strict
    for r in strict.rallies:
        assert r.sure == (not r.reasons and r.p_answer * so.sides_share >= 0.999999)
        if not r.sure:
            assert r.reasons  # always a reason why
    # A source against it: the log says the other team won.
    k = 20
    against: sc.Side = sc.other(winners[k])
    told = list(st.rallies)
    told[k] = replace(told[k], log=against, log_p=0.6)
    out = decode(replace(st, rallies=told), sure=0.5)
    r = out.rallies[k]
    assert not r.sure and r.reasons[0].code == "log" and r.reasons[0].params == {"team": against}


def test_no_point_on_the_log_needs_the_coach_until_they_accept_it_as_a_replay() -> None:
    winners, first = played(), "us"
    rallies = rallies_for(winners, first, sure=0.95)
    replay = sc.RallyObs("x", rallies[9].end_ms + 9_000, rallies[9].end_ms + 13_000, 0.5,
                         proposal=0.6, log="none", log_p=0.9)  # fmt: skip
    st = sc.SetIn(
        1, [*rallies[:10], replay, *rallies[10:]], official=final_of(winners), our_side="near"
    )
    out = decode(st)
    x = next(r for r in out.rallies if r.key == "x")
    assert not x.point and not x.sure and x.reasons[0].code == "log_none"
    accepted = decode(
        replace(st, rallies=[replace(r, proposal=None) if r.key == "x" else r for r in st.rallies])
    )
    assert next(r for r in accepted.rallies if r.key == "x").sure


def test_posteriors_add_up() -> None:
    winners, first = played(), "us"
    st = sc.SetIn(
        1, rallies_for(winners, first, sure=0.8), official=final_of(winners), our_side="near"
    )
    out = decode(st)
    for r in out.rallies:
        assert abs(r.p_us + r.p_them + r.p_none - 1.0) < 0.02
        assert r.p_answer <= max(r.p_us, r.p_them, r.p_none) + 1e-6
