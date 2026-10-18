"""Search over tags and rallies (PLAN §9.7: the search bar and saved filters).

The web turns what a person types ("#3 attack #", "set 3, rotation 2, reception -") into a
`TagSearch` (web/src/lib/search.ts, which also keeps the words); the server takes that form for
the stats (only what it keeps counts, wieplay/core/stats.py) and the smart reels (only what it
keeps plays, wieplay/core/reels.py). Terms of one kind are alternatives, kinds together must
all hold:

- tag terms: the side, the player (side and shirt number), the skill, the grade (None: a tag
  without one), the zone it started from, went to, or either, and its outcome: "point" when
  the tag won the rally for its side (an ace, a kill, a block point), "error" when it lost it,
  "play" otherwise (the taxonomy's `ends_rally_on`);
- rally terms: the set (of the tag's rally, else the set running at its time), our rotation in
  the rally (the stats' rotation walk), who won the rally, who served (a break point: we
  served; a side-out: they served).

A tag matches when its own terms and its rally's terms hold. A rally matches when its terms
hold and, with tag terms, at least one of its tags matches them (like the rally filters on the
video page).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from wieplay.core import stats
from wieplay.core.taxonomy import Taxonomy

Side = Literal["us", "them"]
Outcome = Literal["point", "error", "play"]
Zone = Annotated[int, Field(ge=1, le=9)]
SetNo = Annotated[int, Field(ge=1, le=9)]
Rotation = Annotated[int, Field(ge=1, le=6)]


class PlayerRef(BaseModel):
    side: Side
    number: int = Field(ge=0, le=99)


class TagSearch(BaseModel):
    """What a search keeps; an empty list puts no condition on its kind."""

    sides: list[Side] = Field(default_factory=list, max_length=2)
    players: list[PlayerRef] = Field(default_factory=list, max_length=30)
    codes: list[str] = Field(default_factory=list, max_length=10)
    # None stands for a tag without a grade.
    grades: list[str | None] = Field(default_factory=list, max_length=7)
    zones_from: list[Zone] = Field(default_factory=list, max_length=9)
    zones_to: list[Zone] = Field(default_factory=list, max_length=9)
    zones: list[Zone] = Field(default_factory=list, max_length=9)  # from or to
    outcomes: list[Outcome] = Field(default_factory=list, max_length=3)
    sets: list[SetNo] = Field(default_factory=list, max_length=9)
    rotations: list[Rotation] = Field(default_factory=list, max_length=6)
    won_by: list[Side] = Field(default_factory=list, max_length=2)
    serving: list[Side] = Field(default_factory=list, max_length=2)

    def has_tag_terms(self) -> bool:
        return bool(
            self.sides or self.players or self.codes or self.grades or self.zones_from
            or self.zones_to or self.zones or self.outcomes
        )  # fmt: skip

    def has_rally_terms(self) -> bool:
        return bool(self.sets or self.rotations or self.won_by or self.serving)

    def is_empty(self) -> bool:
        return not self.has_tag_terms() and not self.has_rally_terms()


def outcome_of(tag: stats.TagIn, tax: Taxonomy) -> Outcome:
    """Point for the tagged side, its error, or neither (the rally went on)."""
    skill = tax.skills.get(tag.code)
    ends = skill.ends_rally_on.get(tag.grade) if skill is not None and tag.grade else None
    if ends == "self":
        return "point"
    return "error" if ends == "other" else "play"


def tag_terms_hold(tag: stats.TagIn, search: TagSearch, tax: Taxonomy) -> bool:
    if search.sides and tag.side not in search.sides:
        return False
    if search.players and not any(
        p.side == tag.side and p.number == tag.number for p in search.players
    ):
        return False
    if search.codes and tag.code not in search.codes:
        return False
    if search.grades and tag.grade not in search.grades:
        return False
    if search.zones_from and tag.zone_from not in search.zones_from:
        return False
    if search.zones_to and tag.zone_to not in search.zones_to:
        return False
    if search.zones and tag.zone_from not in search.zones and tag.zone_to not in search.zones:
        return False
    return not search.outcomes or outcome_of(tag, tax) in search.outcomes


def rally_terms_hold(
    rally: stats.RallyIn | None, set_no: int | None, rotation: int | None, search: TagSearch
) -> bool:
    """The rally terms for a rally (or a tag outside every rally: only its set is known)."""
    if search.sets and set_no not in search.sets:
        return False
    if search.rotations and rotation not in search.rotations:
        return False
    if search.won_by and (rally is None or rally.won_by not in search.won_by):
        return False
    return not search.serving or (rally is not None and rally.serving in search.serving)


def select_match(
    search: TagSearch,
    tax: Taxonomy,
    tags: Sequence[stats.TagIn],
    rallies: Sequence[stats.RallyIn],
    sets: Sequence[stats.SetIn],
) -> stats.Selection:
    """What the search keeps of one match: tags (positions in `tags`, time order) and rallies."""
    rally_of = stats.rally_of_tags(tags, rallies)
    set_of = stats.set_of_tags(tags, rallies, sets, rally_of)
    numbers = {s.id: s.no for s in sets}
    rotation_of = stats.rotation_of_rallies(rallies, sets) if search.rotations else {}

    def rally_set(rally: stats.RallyIn) -> int | None:
        if rally.period_id is not None:
            return numbers.get(rally.period_id)
        found = stats.set_at(sets, rally.start_ms)
        return found.no if found else None

    hits = [tag_terms_hold(tag, search, tax) for tag in tags]
    kept_tags = set()
    for i, rally in enumerate(rally_of):
        if not hits[i]:
            continue
        set_id = set_of[i]
        rotation = rotation_of.get(rally.id) if rally is not None else None
        if rally_terms_hold(rally, numbers.get(set_id) if set_id else None, rotation, search):
            kept_tags.add(i)
    with_hit = {rally.id for rally, hit in zip(rally_of, hits, strict=True) if hit and rally}
    kept_rallies = {
        r.id
        for r in rallies
        if rally_terms_hold(r, rally_set(r), rotation_of.get(r.id), search)
        and (not search.has_tag_terms() or r.id in with_hit)
    }
    return stats.Selection(frozenset(kept_tags), frozenset(kept_rallies))


def selector(search: TagSearch | None, tax: Taxonomy) -> stats.Selector | None:
    """The stats' selector for a search; None when it keeps everything."""
    if search is None or search.is_empty():
        return None
    return lambda tags, rallies, sets: select_match(search, tax, tags, rallies, sets)
