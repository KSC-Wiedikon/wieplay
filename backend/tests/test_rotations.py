"""Step 5a/5b: rotations per set and match, the lineup per rotation, and the setter analysis
(wieplay/core/stats.py, PLAN §9.7). Every expected number is worked out by hand."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Any

import pytest

from wieplay.core import stats
from wieplay.core.stats import RallyIn, SetIn, TagIn
from wieplay.core.taxonomy import load

TAX = load("volleyball")
ROT_COLS = stats.rotation_columns(TAX)
CODES = stats.Codes.of(TAX)
ROSTER = {2: (1002, "Jonas Keller"), 7: (1007, "Luca Canepa"), 9: (1009, "David Huber")}


def rally(serving: Any, won_by: Any, *, set_id: uuid.UUID | None = None, at: int = 0) -> RallyIn:
    return RallyIn(uuid.uuid4(), at, at + 8_000, set_id, serving, won_by)


def tag(
    code: str,
    grade: str | None = None,
    *,
    side: Any = "us",
    number: int | None = 7,
    at: int = 0,
    rally_id: uuid.UUID | None = None,
    zone_from: int | None = None,
    zone_to: int | None = None,
    member: int | None = None,
) -> TagIn:
    return TagIn(code, side, number, member, grade, at, rally_id, uuid.uuid4(), zone_from, zone_to)


# --- the rotation walk ---------------------------------------------------------------------------


def test_rotation_columns() -> None:
    assert [c.key for c in ROT_COLS] == [
        "serve_ta", "serve_ace", "serve_err", "serve_eff", "rec_ta", "rec_positive", "rec_eff",
        "att_ta", "att_kill", "att_hit",
    ]  # fmt: skip
    rec_eff = next(c for c in ROT_COLS if c.key == "rec_eff")
    assert (rec_eff.codes, rec_eff.grades, rec_eff.format) == (("R",), None, "pct")
    # (perfect and good - errors and overpasses) / receptions: (2 + 1 - 1 - 1) / 6
    counts = stats.count_tags([tag("R", g) for g in ("#", "#", "+", "!", "=", "/")])
    assert stats.value(rec_eff, counts) == pytest.approx(1 / 6)


def test_side_outs_turn_the_lineup() -> None:
    lineup = (2, 4, 5, 8, 11, 9)  # zones I-VI at the first serve; setter #2 in zone I
    assert stats.lineup_in(lineup, 1, 1) == lineup
    # One side-out (rotation 1 -> 6): the player from zone II serves.
    assert stats.lineup_in(lineup, 1, 6) == (4, 5, 8, 11, 9, 2)
    # The setter is always in the zone named by the rotation.
    for r in stats.ROTATIONS:
        assert stats.lineup_in(lineup, 1, r)[r - 1] == 2
    # Starting in rotation 4 (setter in zone IV): two side-outs later, rotation 2.
    four = (11, 9, 7, 2, 4, 5)
    assert stats.lineup_in(four, 4, 2) == (7, 2, 4, 5, 11, 9)


def test_set_rotations_count_our_tags_in_their_rallies() -> None:
    """Rotation 1, receiving: side-out (#7 passes perfectly, kills) to rotation 6; there #9
    serves an ace, then an error: the opponent serves, we lose (the walk stays in 6)."""
    rallies = [rally("them", "us", at=0), rally("us", "us", at=10_000),
               rally("us", "them", at=20_000), rally("them", "them", at=30_000)]  # fmt: skip
    r1, r2, r3, r4 = (r.id for r in rallies)
    ours = {
        r1: [tag("R", "#", at=1_000, rally_id=r1), tag("A", "#", at=3_000, rally_id=r1)],
        r2: [tag("S", "#", number=9, at=10_200, rally_id=r2)],
        r3: [tag("S", "=", number=9, at=20_200, rally_id=r3)],
        r4: [tag("R", "-", at=31_000, rally_id=r4), tag("A", "/", at=33_000, rally_id=r4)],
    }
    lines = stats.set_rotations(rallies, 1, ours, (2, 4, 5, 8, 11, 9))
    one, six = lines[1], lines[6]
    assert [w.rally.id for w in one.rallies] == [r1]
    assert [(w.rally.id, w.serving, w.won_by) for w in six.rallies] == [
        (r2, "us", "us"), (r3, "us", "them"), (r4, "them", "them"),
    ]  # fmt: skip
    assert (six.team.won, six.team.lost, six.team.break_point_of, six.team.side_out_of) == (
        1, 2, 2, 1,
    )  # fmt: skip
    v1, v6 = stats.values(ROT_COLS, one.counts), stats.values(ROT_COLS, six.counts)
    assert (v1["rec_ta"], v1["rec_eff"], v1["att_hit"], v1["serve_ta"]) == (1, 1.0, 1.0, None)
    assert (v6["serve_ta"], v6["serve_ace"], v6["serve_err"], v6["serve_eff"]) == (2, 1, 1, 0.0)
    assert (v6["rec_positive"], v6["att_ta"], v6["att_hit"]) == (0.0, 1, -1.0)
    assert one.lineup == (2, 4, 5, 8, 11, 9) and six.lineup == (4, 5, 8, 11, 9, 2)
    assert all(not lines[r].rallies for r in (2, 3, 4, 5))


def test_a_five_set_match() -> None:
    """Five sets, each from its own starting rotation (set 5 to 15); the match sums them."""
    starts = {1: 1, 2: 4, 3: 1, 4: 6, 5: 3}
    sets, rallies = [], []
    lineups = {1: (2, 4, 5, 8, 11, 9), 3: (9, 4, 5, 8, 11, 2)}  # sets 1 and 3 have a lineup
    for no, start in starts.items():
        s = SetIn(uuid.uuid4(), no, no * 1_000_000, None, start_rotation=start,
                  lineup=lineups.get(no))  # fmt: skip
        sets.append(s)
        at = s.start_ms
        # Each set: we receive and side out, then win a break point, then lose on our serve.
        for serving, won in (("them", "us"), ("us", "us"), ("us", "them")):
            rallies.append(rally(serving, won, set_id=s.id, at=at))
            at += 10_000
    result = stats.video_stats([], rallies, sets, ROSTER, TAX)
    by_no = {s.set.no: s for s in result.sets}
    for no, start in starts.items():
        lines = by_no[no].rotations
        assert lines is not None
        after = stats.next_rotation(start)
        assert (lines[start].team.rallies, lines[start].team.side_out_won) == (1, 1)
        assert (lines[after].team.rallies, lines[after].team.break_point_won) == (2, 1)
    total = result.rotations
    assert total is not None
    assert sum(line.team.rallies for line in total.values()) == 15
    # Rotation 1 starts sets 1 and 3 (2 rallies); 6 follows them and starts set 4 (4 + 1).
    assert (total[1].team.rallies, total[6].team.rallies, total[6].team.side_out_of) == (2, 5, 1)
    # Rotation 3: set 2 after its side-out (2) and set 5's start (1); then set 5 in 2.
    assert (total[3].team.rallies, total[2].team.rallies) == (3, 2)
    # The lineups of sets 1 and 3 differ in rotation 1: the first set's is shown.
    assert total[1].lineup == (2, 4, 5, 8, 11, 9) and len(total[1].lineups) == 2
    assert total[4].lineup is not None and total[4].lineup[3] == 2


def test_a_set_without_a_starting_rotation_has_no_rotations() -> None:
    s = SetIn(uuid.uuid4(), 1, 0, None)
    result = stats.video_stats([], [rally("us", "us", set_id=s.id)], [s], ROSTER, TAX)
    assert result.rotations is None and result.sets[0].rotations is None


# --- the setter analysis -----------------------------------------------------------------------


def plays_of(tags: list[TagIn], rallies: list[RallyIn], rotation_of: dict[Any, int] | None = None):
    assert CODES is not None
    rally_of = stats.rally_of_tags(tags, rallies)
    return stats.setter_plays(tags, rally_of, [None] * len(tags), rotation_of or {}, CODES)


def test_each_set_finds_its_attack_and_first_contact() -> None:
    a, b, c, d = (rally("them", None, at=n * 20_000) for n in range(4))
    tags = [
        # a: perfect pass, set, #7 kills from zone 4.
        tag("S", "+", side="them", number=12, at=200, rally_id=a.id),
        tag("R", "#", number=1, at=1_500, rally_id=a.id),
        tag("E", None, number=2, at=2_500, rally_id=a.id),
        tag("A", "#", number=7, at=3_000, rally_id=a.id, zone_from=4),
        # b: their serve, our pass not tagged, set to the pipe (zone 8 on the set), blocked;
        # covered (dig), set again: transition, #9 from 2 in play.
        tag("S", "-", side="them", number=12, at=20_200, rally_id=b.id),
        tag("E", "+", number=2, at=21_500, rally_id=b.id, zone_to=8),
        tag("A", "/", number=7, at=22_000, rally_id=b.id),
        tag("B", "#", side="them", number=4, at=22_100, rally_id=b.id),
        tag("D", "+", number=1, at=22_900, rally_id=b.id),
        tag("E", "!", number=2, at=23_500, rally_id=b.id),
        tag("A", "+", number=9, at=24_000, rally_id=b.id, zone_from=2),
        # c: a poor pass, the set goes over (their dig next): no attack of ours.
        tag("R", "-", number=7, at=41_500, rally_id=c.id),
        tag("E", "-", number=2, at=42_500, rally_id=c.id),
        tag("D", "+", side="them", number=6, at=43_500, rally_id=c.id),
        # d: an ungraded pass, a quick from 3 into the net.
        tag("R", None, number=1, at=61_500, rally_id=d.id),
        tag("E", None, number=2, at=62_500, rally_id=d.id),
        tag("A", "=", number=9, at=63_000, rally_id=d.id, zone_from=3),
    ]
    rallies = [a, b, c, d]
    plays = plays_of(tags, rallies, {a.id: 1, b.id: 6, c.id: 6})
    got = [(p.quality, p.zone, p.outcome, p.attack.number if p.attack else None, p.rotation)
           for p in plays]  # fmt: skip
    assert got == [
        ("perfect", "left", "kill", 7, 1),
        ("unknown", "back", "blocked", 7, 6),
        ("transition", "right", "play", 9, 6),
        ("poor", None, None, None, 6),
        ("unknown", "middle", "error", 9, None),
    ]
    assert [p.rally_id for p in plays] == [a.id, b.id, b.id, c.id, d.id]

    who = stats.match_who(ROSTER)
    setter = stats.setter_stats(plays, who)
    assert (setter.sets, setter.attacks) == (5, 4)
    seven, nine = setter.by_attacker
    assert (seven.key, seven.name, seven.attacks, seven.kills, seven.blocked) == (
        "7", "Luca Canepa", 2, 1, 1,
    )  # fmt: skip
    assert seven.kill_pct == 0.5 and seven.hit_pct == 0.0  # (1 - 0 - 1) / 2
    assert seven.filter == {"attacker": 7} and dict(seven.zones) == {"left": 1, "back": 1}
    assert (nine.attacks, nine.errors, nine.hit_pct) == (2, 1, -0.5)
    assert [(z.key, z.attacks) for z in setter.by_zone] == [
        ("left", 1), ("middle", 1), ("right", 1), ("back", 1),
    ]  # fmt: skip
    rotations = {line.key: line for line in setter.by_rotation}
    assert list(rotations) == ["1", "2", "3", "4", "5", "6", "unknown"]
    assert (rotations["6"].sets, rotations["6"].attacks, dict(rotations["6"].zones)) == (
        3, 2, {"back": 1, "right": 1},
    )  # fmt: skip
    assert rotations["2"].sets == 0 and rotations["unknown"].filter == {"rotation": None}
    quality = {line.key: line.sets for line in setter.by_reception}
    assert quality == {"perfect": 1, "poor": 1, "transition": 1, "unknown": 2}
    (only,) = setter.by_setter
    assert (only.number, only.name, only.sets, only.attacks) == (2, "Jonas Keller", 5, 4)
    # A line's plays: the sets whose values match its filter.
    matching = [p for p in plays if all(p.filter_values[k] == v for k, v in seven.filter.items())]
    assert len(matching) == seven.sets


def test_reception_grades_and_tags_without_a_rally() -> None:
    qualities = []
    for grade in ("#", "+", "!", "-", "/"):
        tags = [tag("R", grade, number=1, at=1_000), tag("E", number=2, at=2_000),
                tag("A", "#", at=2_500)]  # fmt: skip
        (play,) = plays_of(tags, [])
        qualities.append(play.quality)
    assert qualities == ["perfect", "good", "good", "poor", "poor"]
    # Without a rally only close neighbours count: a pass 10 s before is not this set's.
    far = [tag("R", "#", number=1, at=0), tag("E", number=2, at=10_000),
           tag("A", "#", at=19_000)]  # fmt: skip
    (play,) = plays_of(far, [])
    assert (play.quality, play.attack) == ("unknown", None)
    # Their sets are not ours.
    assert plays_of([tag("E", side="them", number=5), tag("A", "#", side="them")], []) == []


def test_season_rotations_and_setter() -> None:
    def a_match(day: int, start: int) -> stats.MatchIn:
        s = SetIn(uuid.uuid4(), 1, 0, None, start_rotation=start)
        r = rally("them", "us", set_id=s.id, at=1_000)
        tags = [
            tag("R", "+", number=1, at=1_500, rally_id=r.id, member=1001),
            tag("E", number=2, at=2_500, rally_id=r.id, member=1002),
            tag("A", "#", number=7, at=3_000, rally_id=r.id, zone_from=4, member=1007),
        ]
        return stats.MatchIn(uuid.uuid4(), f"Match {day}", date(2026, 10, day), tags, [r],
                             sets=[s])  # fmt: skip

    names = {1002: "Jonas Keller", 1007: "Luca Canepa"}
    season = stats.season_stats([a_match(4, 1), a_match(11, 3)], names, TAX)
    assert season.rotations is not None
    assert (season.rotations[1].team.side_out_won, season.rotations[3].team.side_out_won) == (1, 1)
    assert season.setter is not None and (season.setter.sets, season.setter.attacks) == (2, 2)
    (attacker,) = season.setter.by_attacker
    assert (attacker.key, attacker.filter, attacker.name) == (
        "m1007", {"attacker_member": 1007}, "Luca Canepa",
    )  # fmt: skip
    assert {q.key: q.sets for q in season.setter.by_reception} == {"good": 2}


# --- CSV ------------------------------------------------------------------------------------------


def test_rotation_and_setter_csv() -> None:
    s = SetIn(uuid.uuid4(), 1, 0, None, start_rotation=1, lineup=(2, 4, 5, 8, 11, None))
    r = rally("them", "us", set_id=s.id, at=1_000)
    tags = [
        tag("R", "#", number=1, at=1_500, rally_id=r.id),
        tag("E", number=2, at=2_500, rally_id=r.id),
        tag("A", "#", number=7, at=3_000, rally_id=r.id, zone_from=4),
    ]
    result = stats.video_stats(tags, [r], [s], ROSTER, TAX)
    text = stats.rotations_csv(ROT_COLS, [("Match", result.rotations), ("1", None)], "en")
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0]
    assert head[:5] == ["Set", "Rotation", "Lineup (zones I-VI)", "Rallies", "Rallies won"]
    lines = [dict(zip(head, row, strict=True)) for row in rows[1:]]
    assert [line["Rotation"] for line in lines] == ["1", "2", "3", "4", "5", "6"]
    one = lines[0]
    assert (one["Lineup (zones I-VI)"], one["Side-out %"], one["Hitting %"]) == (
        "2 4 5 8 11 ?", "100.0", "100.0",
    )  # fmt: skip
    assert (one["Reception efficiency %"], lines[1]["Rallies"], lines[1]["Hitting %"]) == (
        "100.0", "0", "",
    )  # fmt: skip

    text = stats.setter_csv([("Match", result.setter)], "de")
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0]
    assert head[:6] == ["Satz", "Aufteilung", "Option", "Zuspiele", "Angriffe",
                        "Anteil der Angriffe %"]  # fmt: skip
    lines = [dict(zip(head, row, strict=True)) for row in rows[1:]]
    attacker = lines[0]
    assert (attacker["Aufteilung"], attacker["Option"], attacker["Links (4)"]) == (
        "Angreifer", "#7 Luca Canepa", "1",
    )  # fmt: skip
    assert {(line["Aufteilung"], line["Option"]) for line in lines} >= {
        ("Zone", "Links (4)"), ("Rotation", "Rotation 1"), ("Rotation", "Rotation 6"),
        ("Erster Ballkontakt", "Annahme perfekt"), ("Passeur", "#2 Jonas Keller"),
    }  # fmt: skip
