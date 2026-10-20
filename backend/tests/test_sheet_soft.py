"""The typed scoresheet as strong evidence, not a fact (PLAN §10.3, §10.3a, 20.10.2026): a
scorer's slip that still adds up is outvoted where the board log and the video agree against it,
the rest of the set follows the sheet; the sheet wins against the video alone; a coach's fact
always wins; where the sheet and the log disagree the rally is never sure; the boxes to change so
the set says what the decode found, and applying them."""

from __future__ import annotations

from dataclasses import replace

from wieplay.core import scoresheet as ss
from wieplay.core import scoring as core_scoring
from wieplay.ml import scoring as sc
from wieplay.ml import sheet as sh

# --- a set, its rallies, a sheet with a slip in it --------------------------------------------

# A made-up set the other team served first, as a scorer wrote it (U: our point, T: theirs) and as
# it was played: at 3:2 the scorer missed our side-out, wrote their next three points under the
# server before it, and caught up at 7:6 (the slip PLAN §10.3 describes; the rest is made up).
# Both add up and end 23:25; the boxes differ in two of theirs.
SHEET = "UUTUT" + "TTTUTUUU" + "T" + "UT" * 15 + "TUTT"
SLIP = (5, 8, 9, 12)  # the points whose winner the scorer has otherwise
FIRST: sc.Side = "them"


def as_sides(text: str) -> list[sc.Side]:
    return ["us" if c == "U" else "them" for c in text]


def played_and_written() -> tuple[list[sc.Side], list[sc.Side]]:
    sheet = as_sides(SHEET)
    real = [sc.other(w) if i in SLIP else w for i, w in enumerate(sheet)]
    return real, sheet


def scores(winners: list[sc.Side]) -> list[tuple[int, int]]:
    out = [(0, 0)]
    for w in winners:
        a, b = out[-1]
        out.append((a + (w == "us"), b + (w == "them")))
    return out


def path(winners: list[sc.Side], first: sc.Side) -> sc.SheetPath:
    servers = [first, *winners[:-1]]
    return sc.SheetPath(tuple(winners), tuple(servers), (False,) * len(winners))


def rallies(
    winners: list[sc.Side], first: sc.Side = FIRST, *, sure: float | None, log: float = 0.0
) -> list[sc.RallyObs]:
    """One rally per point, our side near; the serve read at `sure` for the real server (None:
    no reading); the board log naming each rally's winner and score at weight `log` (0: none)."""
    out = []
    server = first
    after = scores(winners)
    for i, won in enumerate(winners):
        t = i * 25_000
        p = None if sure is None else (sure if server == "us" else 1 - sure)
        r = sc.RallyObs(f"r{i}", t, t + 8_000, p, proposal=0.85)
        if log:
            r = replace(r, log=won, log_p=log, log_score=after[i + 1])
        out.append(r)
        server = won
    return out


def decode(st: sc.SetIn) -> sc.SetOut:
    [so] = sc.score_match([st]).sets
    return sc.judge(st, so, extra=core_scoring.note_reasons(st, so))


def a_set(real: list[sc.Side], sheet: list[sc.Side], obs: list[sc.RallyObs]) -> sc.SetIn:
    final = (real.count("us"), real.count("them"))
    return sc.SetIn(
        4, obs, official=final, our_side="near", first_serve=FIRST, sheet=path(sheet, FIRST)
    )


def differ(a: list[sc.Side], b: list[sc.Side]) -> set[int]:
    """The points after which the score of a and b differs, or whose winner does."""
    sa, sb = scores(a), scores(b)
    return {i for i in range(len(a)) if a[i] != b[i] or sa[i + 1] != sb[i + 1]}


# --- the decode ----------------------------------------------------------------------------------


def test_a_misplaced_run_is_outvoted_by_the_log_and_the_video() -> None:
    real, sheet = played_and_written()
    st = a_set(real, sheet, rallies(real, sure=0.97, log=0.9))
    out = decode(st)
    # The score line is the real one, everywhere.
    assert [r.won_by for r in out.rallies] == real
    assert [(r.score_us, r.score_them) for r in out.rallies] == scores(real)[1:]
    stretch = differ(real, sheet)
    for i, r in enumerate(out.rallies):
        if i in stretch:
            # Where the sheet and the log disagree: never sure, and the reason says who says what.
            assert not r.sure and r.sheet_point is None
            reason = r.reasons[0]
            assert reason.code == "sheet_log"
            assert reason.params["log"] == real[i] and reason.params["sheet"] == sheet[i]
            assert reason.params["video"] == "log" and reason.params["follows"] == "log"
        else:
            # The rest of the set keeps to the sheet.
            assert r.sheet_point == i + 1 and r.sheet_won == sheet[i]
    assert stretch == set(range(5, 13))  # the four points and the ones between them
    assert out.winners == tuple(real)
    assert sc.departure_sources(st, out) == ["log", "video"]


def test_the_sheet_wins_against_the_video_alone() -> None:
    real, sheet = played_and_written()
    out = decode(a_set(real, sheet, rallies(real, sure=0.97)))
    # The serves are read right at 97 %, and still the sheet's points stand: every point of the
    # path is the sheet's (the rallies may be lined up with them otherwise).
    assert out.winners == tuple(sheet)
    assert all(r.sheet_point is not None for r in out.rallies if r.point)
    assert sc.departure_sources(a_set(real, sheet, rallies(real, sure=0.97)), out) == []
    # Where the video is sure of something else, the rally needs the coach.
    stretch = out.rallies[5:13]
    assert all(not r.sure for r in stretch)
    assert any(
        x.code == "video" and x.params == {"vs": "sheet"} for r in stretch for x in r.reasons
    )


def test_without_the_video_the_log_and_the_sheet_ask_the_coach() -> None:
    real, sheet = played_and_written()
    out = decode(a_set(real, sheet, rallies(real, sure=None, log=0.9)))
    flagged = {
        i for i, r in enumerate(out.rallies) if any(x.code == "sheet_log" for x in r.reasons)
    }
    assert flagged and flagged <= differ(real, sheet)
    for i in flagged:
        reason = next(x for x in out.rallies[i].reasons if x.code == "sheet_log")
        assert reason.params["video"] is None  # nothing to tell them apart
        assert not out.rallies[i].sure
    assert all(not out.rallies[i].sure for i in differ(real, sheet))
    # Outside the stretch the two records agree: there the rallies are sure.
    assert all(r.sure for r in out.rallies[:5] + out.rallies[14:])


def test_a_coachs_fact_always_wins() -> None:
    real, _ = played_and_written()
    k = 20
    wrong: sc.Side = sc.other(real[k])
    # The sheet, the log and the video all say real[k]; the coach says otherwise.
    obs = rallies(real, sure=0.97, log=1.0)
    obs[k] = replace(obs[k], won_fact=wrong)
    out = decode(a_set(real, real, obs))
    assert out.rallies[k].point and out.rallies[k].won_by == wrong and out.rallies[k].sure
    # Its neighbours, where the score line now leaves the sheet and the log, need the coach.
    assert any(not r.sure for r in out.rallies[k - 2 : k + 4] if r.key != f"r{k}")


def test_a_sheet_that_agrees_with_everything_is_followed_as_before() -> None:
    real, _ = played_and_written()
    st = a_set(real, real, rallies(real, sure=0.7, log=0.9))
    out = decode(st)
    assert [r.won_by for r in out.rallies] == real
    assert all(r.sheet_point == i + 1 for i, r in enumerate(out.rallies))
    assert not any(x.code in ("sheet_log", "sheet") for r in out.rallies for x in r.reasons)
    assert sc.sheet_disputes(st, out) == []
    assert all(r.sure for r in out.rallies[1:])


# --- the boxes to change, and changing them ------------------------------------------------------


def doc_of(winners: list[sc.Side], first: sc.Side, no: int = 1) -> dict[str, object]:
    grids, circled = sh.encode(first, winners)
    return ss.SetSheetIn.model_validate(
        {
            "no": no,
            "first_serve": first,
            "boxes_us": grids["us"],
            "boxes_them": grids["them"],
            "circled_us": list(circled["us"] or []) or None,
            "circled_them": list(circled["them"] or []) or None,
        }
    ).model_dump(mode="json")


def test_the_correction_is_the_boxes_the_decode_found() -> None:
    real, sheet = played_and_written()
    final = (real.count("us"), real.count("them"))
    doc = doc_of(sheet, FIRST, no=4)
    [check] = ss.check([doc], [(25, 20), (20, 25), (25, 20), final])
    assert check.status == "consistent"
    plain = sc.SetIn(4, rallies(real, sure=0.97, log=0.9), official=final, our_side="near")
    [st] = ss.apply(ss.SheetUse([check], True), [plain])
    out = decode(st)
    found = ss.correction_for(check, st, out)
    assert found is not None and found["sources"] == ["log", "video"]
    # Exactly their box III.1 is 2, not 5, and IV.1 is 5, not 6: the scorer's slip.
    expected = {("them", 3, 1, 5, 2), ("them", 4, 1, 6, 5)}
    assert {
        (b["box"]["side"], b["box"]["position"], b["box"]["box"], b["old"], b["new"])
        for b in found["boxes"]
    } == expected
    assert found["from"] == [3, 2] and found["to"] == [7, 6]
    # Applied: the set says the real points; each box changed is noted with the paper's value.
    [fixed] = ss.apply_correction([doc], 4, found)
    [again] = ss.check([fixed], [(25, 20), (20, 25), (25, 20), final])
    assert again.status == "consistent"
    assert [p.winner for p in again.decoded.points] == real
    notes = fixed["corrections"]
    assert len(notes) == len(expected)
    assert {(n["side"], n["position"], n["box"], n["paper"], n["value"]) for n in notes} == expected
    assert all(n["by"] == ["log", "video"] for n in notes)
    ss.SetSheetIn.model_validate(fixed)  # still a sheet the API takes
    # The suggestion is for that set as it was: once changed, it is stale.
    assert ss.correction_now(again, found) is None
    assert ss.correction_now(check, found) == found


def test_a_stale_correction_is_refused() -> None:
    _, sheet = played_and_written()
    doc = doc_of(sheet, FIRST)
    [check] = ss.check([doc], None)
    found = {"boxes": [sh.Repair("change", sh.BoxRef("us", 1, 1), 1, 2, 1.0, (25, 20)).as_json()],
             "digest": "0" * 16, "sources": ["log"]}  # fmt: skip
    try:
        ss.apply_correction([doc], 1, found)
    except ss.CorrectionStale:
        pass
    else:
        raise AssertionError("a correction for another set was applied")
    assert ss.correction_now(check, found) is None


def test_no_correction_where_the_decode_keeps_to_the_sheet() -> None:
    real, _ = played_and_written()
    final = (real.count("us"), real.count("them"))
    [check] = ss.check([doc_of(real, FIRST)], [final])
    plain = sc.SetIn(1, rallies(real, sure=0.97, log=0.9), official=final, our_side="near")
    [st] = ss.apply(ss.SheetUse([check], True), [plain])
    assert ss.correction_for(check, st, decode(st)) is None
    assert sh.correction(check.sheet, real) == []
