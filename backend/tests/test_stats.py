"""M3b: the box score, side-out and break-point from tags (wieplay/core/stats.py, PLAN §9.7).

Every expected number is worked out by hand in the comments.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Any

import pytest

from wieplay.core import stats
from wieplay.core.stats import RallyIn, SetIn, TagIn
from wieplay.core.taxonomy import Taxonomy, load

TAX = load("volleyball")
COLS = stats.columns(TAX)


def t(
    code: str,
    grade: str | None = None,
    *,
    side: Any = "us",
    number: int | None = 7,
    member: int | None = None,
    at: int = 0,
    rally: uuid.UUID | None = None,
) -> TagIn:
    return TagIn(code, side, number, member, grade, at, rally)


def numbers(tags: list[TagIn]) -> dict[str, Any]:
    return stats.values(COLS, stats.count_tags(tags))


def rally(serving: Any, won_by: Any, period: uuid.UUID | None = None) -> RallyIn:
    return RallyIn(uuid.uuid4(), 0, 1, period, serving, won_by)


def test_columns_follow_the_taxonomy() -> None:
    keys = [c.key for c in COLS]
    assert keys[:7] == [
        "points", "serve_ta", "serve_ace", "serve_err", "serve_in", "serve_eff", "serve_rtg",
    ]  # fmt: skip
    assert keys[-3:] == ["dig_ta", "set_ta", "free_ta"]
    by_key = {c.key: c for c in COLS}
    assert by_key["points"].codes == ("A", "S", "B") and by_key["points"].grades == ("#",)
    assert by_key["serve_in"].grades == ("+", "!", "-", "/", None)
    assert by_key["att_err_pct"].grades == ("=", "/")
    assert by_key["att_hit"].grades is None  # all attempts
    # A taxonomy without freeballs and digs has no columns for them.
    lean = Taxonomy(
        TAX.sport, TAX.version, TAX.grades, TAX.zones,
        {k: v for k, v in TAX.skills.items() if k not in ("F", "D")},
    )  # fmt: skip
    assert {"free_ta", "dig_ta"}.isdisjoint(c.key for c in stats.columns(lean))


def test_serve() -> None:
    # 8 serves: 2 aces, 1 error, 5 in play (+ ! - / and one ungraded).
    tags = [t("S", g) for g in ("#", "#", "=", "+", "!", "-", "/", None)]
    n = numbers(tags)
    assert (n["serve_ta"], n["serve_ace"], n["serve_err"], n["serve_in"]) == (8, 2, 1, 5)
    assert n["serve_eff"] == pytest.approx((2 - 1) / 8)  # 0.125
    # Rating over the 7 graded: 3 + 3 + 0 + 2 + 2 + 1 + 3 = 14 -> 2.0
    assert n["serve_rtg"] == pytest.approx(14 / 7)


def test_serve_edge_cases() -> None:
    only_errors = numbers([t("S", "="), t("S", "=")])
    assert only_errors["serve_eff"] == pytest.approx(-1.0)
    assert only_errors["serve_rtg"] == 0.0 and only_errors["serve_in"] == 0
    ungraded = numbers([t("S")])
    assert ungraded["serve_ta"] == 1 and ungraded["serve_in"] == 1
    assert ungraded["serve_eff"] == 0.0
    assert ungraded["serve_rtg"] is None  # nothing graded to rate


def test_reception() -> None:
    # 7 receptions: # + + ! - / =
    tags = [t("R", g) for g in ("#", "+", "+", "!", "-", "/", "=")]
    n = numbers(tags)
    assert (n["rec_ta"], n["rec_err"]) == (7, 1)
    assert n["rec_perfect"] == pytest.approx(1 / 7)
    assert n["rec_positive"] == pytest.approx(3 / 7)  # # + +
    # 3 + 2 + 2 + 2 + 1 + 0 + 0 = 10 -> 10/7
    assert n["rec_rtg"] == pytest.approx(10 / 7)


def test_attack() -> None:
    # 9 attacks: 3 kills, 1 error, 1 blocked, 4 in play (+ ! - and one ungraded).
    tags = [t("A", g) for g in ("#", "#", "#", "=", "/", "+", "!", "-", None)]
    n = numbers(tags)
    assert (n["att_ta"], n["att_kill"], n["att_err"], n["att_blocked"]) == (9, 3, 1, 1)
    assert n["att_hit"] == pytest.approx((3 - 1 - 1) / 9)
    assert n["att_kill_pct"] == pytest.approx(3 / 9)
    assert n["att_err_pct"] == pytest.approx(2 / 9)
    assert n["att_hit"] == pytest.approx(n["att_kill_pct"] - n["att_err_pct"])


def test_attack_hitting_can_be_negative() -> None:
    n = numbers([t("A", "="), t("A", "/"), t("A", "#")])
    assert n["att_hit"] == pytest.approx((1 - 1 - 1) / 3)


def test_block_other_skills_and_points() -> None:
    tags = [t("B", g) for g in ("#", "+", "!", "-", "=", "/", None)]
    tags += [t("D"), t("D", "="), t("E", "#"), t("E"), t("E"), t("F", "+")]
    tags += [t("A", "#"), t("A", "#"), t("S", "#"), t("S", "=")]
    n = numbers(tags)
    assert (n["blk_point"], n["blk_touch"], n["blk_err"]) == (1, 4, 2)
    assert (n["dig_ta"], n["set_ta"], n["free_ta"]) == (2, 3, 1)
    assert n["points"] == 2 + 1 + 1  # kills + aces + block points


def test_no_attempts_is_none_not_zero() -> None:
    n = numbers([t("D", "+")])
    assert n["dig_ta"] == 1
    for key in ("points", "serve_ta", "serve_eff", "serve_rtg", "rec_ta", "rec_positive",
                "att_ta", "att_hit", "att_kill", "blk_point", "set_ta", "free_ta"):  # fmt: skip
        assert n[key] is None, key
    # Tried and none: a real zero.
    tried = numbers([t("A", "+")])
    assert tried["att_kill"] == 0 and tried["att_kill_pct"] == 0.0 and tried["points"] == 0
    assert numbers([]) == {c.key: None for c in COLS}


def test_team_line_side_out_and_break_point() -> None:
    rallies = [
        rally("us", "us"),  # break point won
        rally("us", "them"),  # break point lost
        rally("them", "us"),  # side-out
        rally("them", "them"),  # side-out failed
        rally("them", "us"),  # side-out
        rally(None, "us"),  # unknown server: a point, not in either ratio
        rally("us", None),  # no winner yet: in neither
    ]
    line = stats.team_line(rallies)
    assert (line.rallies, line.won, line.lost) == (7, 4, 2)
    assert (line.side_out_won, line.side_out_of) == (2, 3)
    assert (line.break_point_won, line.break_point_of) == (1, 2)
    assert line.side_out_pct == pytest.approx(2 / 3)
    assert line.break_point_pct == pytest.approx(1 / 2)
    empty = stats.team_line([])
    assert empty.side_out_pct is None and empty.break_point_pct is None and empty.rallies == 0


def test_a_tag_belongs_to_the_set_of_its_rally() -> None:
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    sets = [SetIn(s1, 1, 0, 100_000), SetIn(s2, 2, 100_000, None)]
    ra = RallyIn(uuid.uuid4(), 10_000, 20_000, s1, "us", "us")
    rb = RallyIn(uuid.uuid4(), 100_500, 110_000, s2, "them", "us")
    tags = [
        t("A", at=15_000, rally=ra.id),  # linked: set 1
        t("S", at=99_000),  # unlinked, 1.5 s before rally B (the toss): set 2
        t("D", at=50_000),  # outside every rally: the set running then, 1
        t("D", at=200_000),  # set 2 has no end
        t("D", at=96_000),  # 4.5 s before rally B: too early for it, so set 1
        t("D", at=50_000, rally=uuid.uuid4()),  # linked to a rally we don't know: by time
    ]
    assert stats.set_of_tags(tags, [ra, rb], sets) == [s1, s2, s1, s2, s1, s1]
    assert stats.set_of_tags([t("D", at=5)], [], []) == [None]


def test_set_at_takes_the_latest_started_set() -> None:
    a, b = SetIn(uuid.uuid4(), 1, 0, None), SetIn(uuid.uuid4(), 2, 60_000, 90_000)
    assert stats.set_at([a, b], 70_000) is b
    assert stats.set_at([a, b], 95_000) is a  # set 2 ended; set 1 never did
    assert stats.set_at([b], 10_000) is None


def test_video_box_rows() -> None:
    roster = {7: (101, "Alex Muster"), 3: (102, "Sam Beispiel")}
    tags = [
        t("A", "#", number=7, member=101),
        t("A", "=", number=11),  # not on the roster
        t("R", "+", number=3, member=102),
        t("D", number=None),
        t("S", "=", side="them", number=12),
        t("S", "#", side="them", number=4),
    ]
    box = stats.video_box(tags, roster)
    assert [(r.kind, r.number, r.name) for r in box.players] == [
        ("player", 3, "Sam Beispiel"),
        ("player", 7, "Alex Muster"),
        ("player", 11, None),
        ("no_number", None, None),
    ]
    assert box.players[1].member_id == 101 and box.players[2].member_id is None
    assert box.players[1].filter == {"side": "us", "number": 7}
    assert box.players[3].filter == {"side": "us", "number": None}
    assert box.team.filter == {"side": "us"} and box.opponent.filter == {"side": "them"}
    team = stats.values(COLS, box.team.counts)
    assert (team["att_ta"], team["att_kill"], team["att_err"], team["rec_ta"]) == (2, 1, 1, 1)
    them = stats.values(COLS, box.opponent.counts)
    assert (them["serve_ta"], them["serve_ace"], them["serve_err"]) == (2, 1, 1)


def test_video_stats_per_set() -> None:
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    sets = [SetIn(s2, 2, 60_000, None, 25, 23), SetIn(s1, 1, 0, 60_000, 21, 25)]
    r1 = RallyIn(uuid.uuid4(), 1_000, 9_000, s1, "us", "them")
    r2 = RallyIn(uuid.uuid4(), 61_000, 69_000, s2, "them", "us")
    r3 = RallyIn(uuid.uuid4(), 71_000, 79_000, s2, "us", "us")
    tags = [
        t("S", at=1_000, rally=r1.id),
        t("A", "#", number=3, at=65_000, rally=r2.id),
        t("S", "#", at=71_500, rally=r3.id),
    ]
    out = stats.video_stats(tags, [r1, r2, r3], sets, {})
    assert (out.tags, out.rallies) == (3, 3)
    assert [s.set.no for s in out.sets] == [1, 2]
    one, two = out.sets
    assert [r.number for r in one.box.players] == [7]
    assert [r.number for r in two.box.players] == [3, 7]
    assert (one.team.won, one.team.lost, one.team.break_point_of) == (0, 1, 1)
    assert (two.team.side_out_won, two.team.break_point_won) == (1, 1)
    assert (out.team.won, out.team.lost) == (2, 1)
    assert stats.values(COLS, out.box.team.counts)["points"] == 2


def test_season_sums_counts_and_recomputes_ratios() -> None:
    v1, v2 = uuid.uuid4(), uuid.uuid4()
    first = stats.MatchIn(
        v1, "KSCW H3 - Volley Näfels", date(2026, 10, 11),
        [t("A", "#", number=7, member=101), t("R", "#", number=1, member=104),
         t("S", "=", side="them", number=9)],
        [rally("us", "us"), rally("them", "them")],
        (3, 1),
    )  # fmt: skip
    second = stats.MatchIn(
        v2, "Volero - KSCW H3", date(2026, 9, 27),
        [t("A", "=", number=8, member=101), t("A", "=", number=8, member=101),
         t("A", "+", number=8, member=101), t("A", "#", number=5)],
        [rally("them", "us")],
    )  # fmt: skip
    empty = stats.MatchIn(uuid.uuid4(), "Not tagged yet", date(2026, 10, 14), [], [])
    out = stats.season_stats([first, second, empty], {101: "Alex Muster", 104: "Kim Probe"})
    assert [m.match.title for m in out.matches] == [
        "Volero - KSCW H3", "KSCW H3 - Volley Näfels", "Not tagged yet",
    ]  # fmt: skip
    assert out.tagged == 2
    kim, alex, other = out.box.players
    assert (alex.name, alex.number, len(alex.matches)) == ("Alex Muster", 7, 2)  # latest number
    assert alex.filter == {"side": "us", "member_id": 101}
    assert (kim.number, len(kim.matches)) == (1, 1)
    assert other.kind == "other" and other.filter == {"side": "us", "member_id": None}
    n = stats.values(COLS, alex.counts)
    # 4 attacks over two matches: 1 kill, 2 errors -> kill % 1/4, not the mean of 100 % and 0 %.
    assert (n["att_ta"], n["att_kill"], n["att_err"]) == (4, 1, 2)
    assert n["att_kill_pct"] == pytest.approx(1 / 4)
    assert n["att_hit"] == pytest.approx((1 - 2) / 4)
    assert len(out.box.team.matches) == 2
    assert (out.team.won, out.team.lost, out.team.side_out_won, out.team.side_out_of) == (
        2,
        1,
        1,
        2,
    )
    assert out.team.break_point_pct == pytest.approx(1.0)


def test_season_without_unlinked_tags_has_no_other_row() -> None:
    match = stats.MatchIn(uuid.uuid4(), "M", date(2026, 10, 1), [t("A", member=101)], [])
    out = stats.season_stats([match], {})
    assert [r.kind for r in out.box.players] == ["player"]
    assert out.box.players[0].name is None


def test_csv_cells_and_lines() -> None:
    assert stats.csv_cell(None, "pct") == ""
    assert stats.csv_cell(1 / 3, "pct") == "33.3"
    assert stats.csv_cell(-0.25, "pct") == "-25.0"
    assert stats.csv_cell(10 / 7, "rating") == "1.43"
    assert stats.csv_cell(0, "count") == "0"
    box = stats.video_box(
        [t("A", "#", member=101), t("A", "="), t("S", "#", side="them", number=4)],
        {7: (101, "Alex Müller")},
    )
    line = stats.team_line([rally("them", "us"), rally("us", "them")])
    text = stats.to_csv(COLS, [("Match", box, line)], "en")
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0]
    assert head[:5] == ["Set", "Number", "Player", "Points", "Serve attempts"]
    assert head[-1] == "Break-point %"
    player, team, opponent = (dict(zip(head, r, strict=True)) for r in rows[1:])
    assert (player["Player"], player["Number"], player["Kills"]) == ("Alex Müller", "7", "1")
    assert player["Hitting %"] == "0.0" and player["Kill %"] == "50.0"
    assert player["Serve attempts"] == "" and player["Side-out %"] == ""
    assert (team["Player"], team["Side-out %"], team["Break-point %"]) == ("Team", "100.0", "0.0")
    assert (opponent["Player"], opponent["Aces"]) == ("Opponent", "1")
    german = list(csv.reader(io.StringIO(stats.to_csv(COLS, [("Spiel", box, line)], "de"))))
    assert german[0][:4] == ["Satz", "Nummer", "Spieler", "Punkte"]
    assert german[2][2] == "Team" and german[3][2] == "Gegner"
    season = list(csv.reader(io.StringIO(stats.to_csv(COLS, [("Season", box, line)], "en", True))))
    assert season[0][3] == "Matches"
