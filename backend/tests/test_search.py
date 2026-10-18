"""The search over tags and rallies (wieplay/core/search.py) and what it does to the stats and
the zone maps (wieplay/core/stats.py), PLAN §9.7. Every expected number is worked out by hand."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from wieplay.core import stats
from wieplay.core.search import (
    PlayerRef,
    TagSearch,
    outcome_of,
    select_match,
    selector,
    tag_terms_hold,
)
from wieplay.core.stats import RallyIn, SetIn, TagIn
from wieplay.core.taxonomy import load

TAX = load("volleyball")
COLS = stats.columns(TAX)
S1, S2 = uuid.uuid4(), uuid.uuid4()


def tag(
    code: str,
    grade: str | None = None,
    *,
    side: Any = "us",
    number: int | None = 7,
    at: int = 0,
    rally: RallyIn | None = None,
    zone_from: int | None = None,
    zone_to: int | None = None,
    member: int | None = None,
) -> TagIn:
    rally_id = rally.id if rally else None
    return TagIn(code, side, number, member, grade, at, rally_id, uuid.uuid4(), zone_from, zone_to)


def rally(serving: Any, won_by: Any, at: int, set_id: uuid.UUID | None = S1) -> RallyIn:
    return RallyIn(uuid.uuid4(), at, at + 8_000, set_id, serving, won_by)


# A match: set 1 starts in rotation 1 (we serve first), set 2 has no starting rotation.
#   r1 (set 1, rotation 1): we serve (#7, to zone 5), they pass, #3 kills from zone 4: won
#   r2 (set 1, rotation 1): we serve (#7 error): lost  -> side-out for them
#   r3 (set 1, rotation 1): they serve (#12 ace to zone 1): lost
#   r4 (set 2): they serve, #1 passes poorly, #7 attacks from 2, blocked: lost
R1, R2, R3 = rally("us", "us", 1_000), rally("us", "them", 11_000), rally("them", "them", 21_000)
R4 = rally("them", "them", 61_000, S2)
SETS = [SetIn(S1, 1, 0, 60_000, start_rotation=1), SetIn(S2, 2, 60_000, None)]
TAGS = [
    tag("S", "+", at=1_100, rally=R1, zone_from=1, zone_to=5),
    tag("R", "-", side="them", number=4, at=2_500, rally=R1),
    tag("A", "#", number=3, at=6_000, rally=R1, zone_from=4, zone_to=1),
    tag("S", "=", at=11_100, rally=R2, zone_from=1),
    tag("S", "#", side="them", number=12, at=21_100, rally=R3, zone_to=1),
    tag("R", "-", number=1, at=62_000, rally=R4),
    tag("A", "/", at=64_000, rally=R4, zone_from=2),
]
RALLIES = [R1, R2, R3, R4]


def kept(search: TagSearch) -> tuple[list[int], set[uuid.UUID]]:
    found = select_match(search, TAX, TAGS, RALLIES, SETS)
    return sorted(found.tags), set(found.rallies)


def test_empty_and_invalid_searches() -> None:
    assert (
        TagSearch().is_empty()
        and selector(TagSearch(), TAX) is None
        and selector(None, TAX) is None
    )
    assert TagSearch(sets=[3]).has_rally_terms() and not TagSearch(sets=[3]).has_tag_terms()
    with pytest.raises(ValidationError):
        TagSearch(rotations=[7])
    with pytest.raises(ValidationError):
        TagSearch(zones_from=[0])
    with pytest.raises(ValidationError):
        TagSearch(players=[PlayerRef(side="us", number=100)])


def test_outcomes_follow_the_taxonomy() -> None:
    assert outcome_of(tag("A", "#"), TAX) == "point"
    assert outcome_of(tag("A", "/"), TAX) == "error"  # blocked: the attacker's side loses
    assert outcome_of(tag("S", "/"), TAX) == "play"  # an overpass forced: the rally goes on
    assert outcome_of(tag("B", "#"), TAX) == "point"
    assert outcome_of(tag("R"), TAX) == "play"


def test_tag_terms() -> None:
    seven_attack = TagSearch(players=[PlayerRef(side="us", number=7)], codes=["A"])
    assert tag_terms_hold(TAGS[6], seven_attack, TAX)
    assert not tag_terms_hold(TAGS[2], seven_attack, TAX)  # #3
    # Their #7 is not our #7.
    assert not tag_terms_hold(tag("A", side="them"), seven_attack, TAX)
    assert tag_terms_hold(TAGS[4], TagSearch(sides=["them"], grades=["#"]), TAX)
    assert tag_terms_hold(tag("A"), TagSearch(grades=[None]), TAX)  # ungraded
    assert not tag_terms_hold(tag("A", "#"), TagSearch(grades=[None]), TAX)
    assert tag_terms_hold(TAGS[0], TagSearch(zones_to=[5]), TAX)
    assert not tag_terms_hold(TAGS[0], TagSearch(zones_from=[5]), TAX)
    assert tag_terms_hold(TAGS[0], TagSearch(zones=[5]), TAX)  # from or to
    assert tag_terms_hold(TAGS[2], TagSearch(outcomes=["point"]), TAX)


def test_a_search_keeps_tags_and_rallies() -> None:
    # "#3 attack #": the kill, and the rally it was in.
    tags, rallies = kept(
        TagSearch(players=[PlayerRef(side="us", number=3)], codes=["A"], grades=["#"])
    )
    assert (tags, rallies) == ([2], {R1.id})
    # "set 2": everything in set 2.
    assert kept(TagSearch(sets=[2])) == ([5, 6], {R4.id})
    # "rotation 1, lost": the rallies of set 1 we lost (set 2 has no rotation).
    assert kept(TagSearch(rotations=[1], won_by=["them"])) == ([3, 4], {R2.id, R3.id})
    # "side-out" (they served) and a reception: our poor pass of r4 only.
    assert kept(TagSearch(serving=["them"], codes=["R"], sides=["us"])) == ([5], {R4.id})
    # Tag terms nobody matches keep no rally either.
    assert kept(TagSearch(codes=["F"])) == ([], set())


def test_the_rotation_follows_side_outs() -> None:
    # Set 1 from rotation 1: we lose r2 on our serve (no side-out), r3 on theirs. Had we won
    # r3 (a side-out), the next rally would be rotation 6.
    r5 = rally("them", "us", 31_000)
    r6 = rally("us", "us", 41_000)
    serve = tag("S", "#", at=41_100, rally=r6)
    found = select_match(TagSearch(rotations=[6]), TAX, [*TAGS, serve], [*RALLIES, r5, r6], SETS)
    assert set(found.rallies) == {r6.id} and found.tags == {7}


def test_stats_count_what_the_search_keeps() -> None:
    whole = stats.video_stats(TAGS, RALLIES, SETS, {})
    assert (whole.tags, whole.rallies, whole.team.lost) == (7, 4, 3)
    # "set 1": the box score and the team line of set 1 only; the walk still knows rotation 1.
    out = stats.video_stats(TAGS, RALLIES, SETS, {}, TAX, selector(TagSearch(sets=[1]), TAX))
    assert (out.tags, out.rallies) == (5, 3)
    assert (out.team.won, out.team.lost, out.team.side_out_of) == (1, 2, 1)
    team = stats.values(COLS, out.box.team.counts)
    assert (team["serve_ta"], team["att_ta"], team["rec_ta"]) == (2, 1, None)
    one, two = out.sets
    assert one.team.rallies == 3 and two.team.rallies == 0 and two.box.players == []
    assert out.rotations is not None and out.rotations[1].team.rallies == 3
    # "#7": our #7's tags, and the rallies they are in.
    seven = stats.video_stats(
        TAGS,
        RALLIES,
        SETS,
        {},
        TAX,
        selector(TagSearch(players=[PlayerRef(side="us", number=7)]), TAX),
    )
    assert [r.number for r in seven.box.players] == [7] and seven.rallies == 3
    assert stats.values(COLS, seven.box.opponent.counts)["serve_ta"] is None


def test_the_setter_analysis_keeps_a_set_by_its_set_or_its_attack() -> None:
    set_tag = tag("E", "+", number=2, at=5_000, rally=R1)
    tags = sorted([*TAGS, set_tag], key=lambda t: t.t_ms)
    whole = stats.video_stats(tags, RALLIES, SETS, {})
    assert whole.setter is not None and whole.setter.sets == 1
    kill = selector(TagSearch(codes=["A"], grades=["#"]), TAX)
    kept_by_attack = stats.video_stats(tags, RALLIES, SETS, {}, TAX, kill)
    assert kept_by_attack.setter is not None and kept_by_attack.setter.attacks == 1
    other = selector(TagSearch(codes=["S"]), TAX)
    dropped = stats.video_stats(tags, RALLIES, SETS, {}, TAX, other)
    assert dropped.setter is not None and dropped.setter.sets == 0


def test_season_with_a_search() -> None:
    match = stats.MatchIn(uuid.uuid4(), "M", date(2026, 10, 1), TAGS, RALLIES, None, SETS)
    out = stats.season_stats([match], {}, TAX, selector(TagSearch(sets=[2]), TAX))
    assert out.tagged == 1  # the match has tags, whatever the search keeps
    assert out.team.rallies == 1 and out.matches[0].team.rallies == 1
    assert stats.values(COLS, out.box.team.counts)["att_ta"] == 1


# --- zone maps -------------------------------------------------------------------------------


def test_zone_cells() -> None:
    box = stats.video_box(
        [
            tag("S", "#", zone_from=1, zone_to=5),
            tag("S", "=", zone_from=1, zone_to=5),
            tag("S", "+", zone_from=6, zone_to=1),
            tag("S", zone_from=6),
            tag("A", "#", zone_from=4, zone_to=6),
            tag("A", "/", zone_from=4),
            tag("A", "=", zone_from=4),
            tag("A", "+"),
        ],
        {},
    )
    seven = box.players[0]
    to = stats.zone_cells(seven.places, "S", "serve", "to")
    assert [(c.zone, c.ta, c.points, c.errors) for c in to] == [
        (1, 1, 0, 0),
        (5, 2, 1, 1),
        (None, 1, 0, 0),
    ]
    five = to[1]
    assert five.eff == 0.0 and five.point_pct == 0.5 and five.in_play == 0 and five.blocked == 0
    start = stats.zone_cells(seven.places, "S", "serve", "from")
    assert [(c.zone, c.ta) for c in start] == [(1, 2), (6, 2)]
    attacks = stats.zone_cells(seven.places, "A", "attack", "from")
    four = attacks[0]
    # Zone 4: a kill, a blocked, an error -> hitting % (1 - 1 - 1) / 3.
    assert (four.zone, four.ta, four.points, four.errors, four.blocked) == (4, 3, 1, 1, 1)
    assert four.eff == pytest.approx(-1 / 3) and four.in_play == 0
    assert (attacks[1].zone, attacks[1].ta, attacks[1].in_play) == (None, 1, 1)
    assert stats.zone_codes(TAX) == {"serve": "S", "attack": "A"}
    assert stats.has_zone_tags(seven, {"serve": "S"})
    assert not stats.has_zone_tags(box.opponent, {"serve": "S", "attack": "A"})


def test_zones_csv() -> None:
    tags = [tag("S", "#", zone_to=5), tag("A", "=", side="them", number=4, zone_from=2)]
    box = stats.video_box(tags, {7: (101, "Alex Muster")})
    text = stats.zones_csv([("Match", box)], {"serve": "S", "attack": "A"}, "en",
                           {"team": "KSCW H3", "opponent": "Volero"})  # fmt: skip
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0]
    assert head[:7] == ["Set", "Number", "Player", "Skill", "Zones", "Zone", "Attempts"]
    lines = [dict(zip(head, r, strict=True)) for r in rows[1:]]
    seven_to = next(r for r in lines if r["Player"] == "Alex Muster" and r["Zones"] == "To")
    assert (seven_to["Skill"], seven_to["Zone"], seven_to["Points (ace, kill)"]) == (
        "Serve",
        "5",
        "1",
    )
    assert seven_to["Efficiency %"] == "100.0"
    seven_from = next(r for r in lines if r["Player"] == "Alex Muster" and r["Zones"] == "From")
    assert seven_from["Zone"] == "Zone not tagged"
    volero = next(r for r in lines if r["Player"] == "Volero" and r["Zones"] == "From")
    assert (volero["Skill"], volero["Zone"], volero["Errors"]) == ("Attack", "2", "1")
    german = list(csv.reader(io.StringIO(stats.zones_csv([("Spiel", box)], {"serve": "S"}, "de"))))
    assert german[0][3:6] == ["Aktion", "Zonen", "Zone"] and german[1][4] == "Von"
