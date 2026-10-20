"""Statistics from the tags (PLAN §9.7): box score per player, side-out and break-point.

Pure functions over a video's tags, rallies and sets; nothing is stored, the numbers are
computed when they are read. wieplay/api/stats.py loads the rows and serves JSON and CSV.

Volleyball only. Grades follow DataVolley (taxonomies/volleyball.yaml) with the meanings the
app shows per skill (web/src/i18n/video.en.ts, `grade.*`):

    grade  serve            reception  attack   block                 dig, set, freeball
    #      ace              perfect    kill     point                 perfect
    +      positive         good       good     good                  good
    !      OK               OK         covered  back to the attacker  OK
    -      negative         poor       poor     poor                  poor
    /      overpass forced  overpass   blocked  invasion              overpass
    =      error            error      error    error or touch out    error

What is counted (TA, total attempts, is every tag of the skill, graded or not; a tag without
a grade counts as an attempt that is neither a point nor an error):

- Serve: aces (#), errors (=), in play = TA - aces - errors, efficiency = (aces - errors) / TA,
  rating 0-3 = mean over the graded serves of  # 3, / 3, + 2, ! 2, - 1, = 0  (3: the opponent
  could not pass, 2: they were out of system, 1: they passed perfectly, 0: error).
- Reception: errors (=), perfect % = # / TA, positive % = (# and +) / TA, rating 0-3 = mean over
  the graded receptions of  # 3, + 2, ! 2, - 1, / 0, = 0  (the usual 3-point passing scale:
  an overpass is a 0, like an ace against us).
- Attack: kills (#), errors (=), blocked (/), hitting % = (K - E - B) / TA, kill % = K / TA,
  error % = (E + B) / TA, so hitting % = kill % - error %.
- Block: points (#), touches (+ ! - and ungraded: the ball stayed in play), errors (= and /).
- Dig, set and freeball: the number of tags.
- Points: kills + aces + block points.

A ratio over zero attempts is None (shown as a dash), and so is every number of a skill the row
has no tags of: a 0 always means "tried, and none of these".

Team numbers come from the rallies: points won and lost; side-out % = rallies we received and
won / rallies we received; break-point % = rallies we served and won / rallies we served. Only
rallies with a known server and winner count for those two.

Per set, a tag belongs to the set of its rally (its rally link, or the rally starting within
2 s after it, like the rally filters on the video page); a tag outside every rally belongs to
the set running at its time.

Rotations: a set with a starting rotation (our setter's zone at its first serve, 1-6, the
DataVolley P1-P6) walks it through the rallies: we rotate (setter zone 1 → 6 → 5 → 4 → 3 →
2 → 1) every time we win a rally the opponent served (a side-out). Points, side-out and
break-point per rotation come from the coach's facts only: a rally's winner is the coach's,
else the next rally's known server; where neither is known the walk stops for that set.
Per rotation also our serve, reception and attack numbers (our tags in its rallies, the box
score's formulas; reception efficiency = (perfect and good - errors and overpasses) /
receptions), and, with the set's starting lineup (our six shirt numbers in zones I-VI at the
first serve), who stood where: after k side-outs zone I holds the player who started in zone
I + k. The opponent's rotation is not known and not counted.

Setter analysis: each of our sets (set tags, `E`) with what came of it, from the tags alone:
- the attack: the next tag in the same rally, when it is our attack (else the set had no
  attack of ours: a set error, a free ball over, a dump nobody tagged as an attack);
- the option: the attacker, and the zone the attack started from (its `zone_from`, else the
  set's `zone_to`): 4 left, 3 middle, 2 right, 1 5 6 7 8 9 back row (pipe); unknown without;
- the outcome: kill (#), error (=), blocked (/), in play (anything else);
- the first contact before it: our reception right before the set gives its quality (perfect
  #, good + !, poor - / =); any other tag before it in the rally is transition (after a dig,
  a free ball, a cover); none, an ungraded reception or the opponent's serve right before it
  is unknown;
- the rotation of its rally (above), when the walk knows it.
Distribution per attacker, zone, rotation, reception quality and setter, each with the
attacks' kill % and hitting %. The taxonomy has no tempo, so there is none.

Zone maps (heat maps): per row, its serves and attacks per zone 1-9 where they started
(`zone_from`) and where they went (`zone_to`), with the box score's formulas: attempts, aces or
kills (#), errors (=), blocked (attacks, /), in play, efficiency. Tags without that zone are
counted apart.

A search (wieplay/core/search.py) narrows every number to the tags and rallies it keeps: the box
score and the zone maps count the kept tags, the team numbers the kept rallies, a setter number
the sets whose set or attack is kept; the rotation walk still goes through every rally.

Every number links to its plays: a cell's tags are those matching the row's `filter`, the
column's `codes` and `grades`, and (per set) the set. The web plays them on the video page and
opens a smart reel on the team page. A rotation number's plays are the rallies the walk put in
that rotation (or our tags of the column in them); a setter number's are the sets matching
the line's `filter`.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Literal

from wieplay.core.taxonomy import Taxonomy, load

Side = Literal["us", "them"]
Format = Literal["count", "pct", "rating"]
RowKind = Literal["player", "no_number", "other", "team", "opponent"]
Grade = str | None

# A tag up to this long before a rally belongs to it (the serve toss; web: PRE_ROLL_MS).
RALLY_LEAD_MS = 2000

RATING: dict[str, dict[str, int]] = {
    "serve": {"#": 3, "/": 3, "+": 2, "!": 2, "-": 1, "=": 0},
    "reception": {"#": 3, "+": 2, "!": 2, "-": 1, "/": 0, "=": 0},
}
IN_PLAY: tuple[Grade, ...] = ("+", "!", "-", "/", None)


# --- inputs -----------------------------------------------------------------------------


@dataclass(frozen=True)
class TagIn:
    code: str
    side: Side
    number: int | None
    member_id: int | None
    grade: str | None
    t_ms: int
    segment_id: uuid.UUID | None = None
    id: uuid.UUID | None = None
    zone_from: int | None = None
    zone_to: int | None = None
    # WiePlay's tag, unsure: it counts, marked "to check" (PLAN §10).
    check: bool = False


@dataclass(frozen=True)
class RallyIn:
    id: uuid.UUID
    start_ms: int
    end_ms: int
    period_id: uuid.UUID | None
    serving: Side | None
    won_by: Side | None


@dataclass(frozen=True)
class SetIn:
    id: uuid.UUID
    no: int
    start_ms: int
    end_ms: int | None
    final_us: int | None = None
    final_them: int | None = None
    start_rotation: int | None = None  # our setter's zone at the first serve (1-6)
    # Our six shirt numbers in zones I-VI at the first serve (None: a zone left empty).
    lineup: tuple[int | None, ...] | None = None


# --- columns ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """One number of the box score. Its plays are the tags of `codes` with one of `grades`
    (None: any grade, also none); `None` in `grades` stands for an ungraded tag."""

    key: str
    group: str | None  # serve | reception | attack | block | other; None: on its own
    format: Format
    codes: tuple[str, ...]
    grades: tuple[Grade, ...] | None
    category: str  # the skill whose attempts decide whether the row has this number at all


# (key, group, format, categories, grades)
_SPEC: tuple[tuple[str, str | None, Format, tuple[str, ...], tuple[Grade, ...] | None], ...] = (
    ("points", None, "count", ("attack", "serve", "block"), ("#",)),
    ("serve_ta", "serve", "count", ("serve",), None),
    ("serve_ace", "serve", "count", ("serve",), ("#",)),
    ("serve_err", "serve", "count", ("serve",), ("=",)),
    ("serve_in", "serve", "count", ("serve",), IN_PLAY),
    ("serve_eff", "serve", "pct", ("serve",), None),
    ("serve_rtg", "serve", "rating", ("serve",), None),
    ("rec_ta", "reception", "count", ("reception",), None),
    ("rec_err", "reception", "count", ("reception",), ("=",)),
    ("rec_perfect", "reception", "pct", ("reception",), ("#",)),
    ("rec_positive", "reception", "pct", ("reception",), ("#", "+")),
    ("rec_rtg", "reception", "rating", ("reception",), None),
    ("att_ta", "attack", "count", ("attack",), None),
    ("att_kill", "attack", "count", ("attack",), ("#",)),
    ("att_err", "attack", "count", ("attack",), ("=",)),
    ("att_blocked", "attack", "count", ("attack",), ("/",)),
    ("att_hit", "attack", "pct", ("attack",), None),
    ("att_kill_pct", "attack", "pct", ("attack",), ("#",)),
    ("att_err_pct", "attack", "pct", ("attack",), ("=", "/")),
    ("blk_point", "block", "count", ("block",), ("#",)),
    ("blk_touch", "block", "count", ("block",), ("+", "!", "-", None)),
    ("blk_err", "block", "count", ("block",), ("=", "/")),
    ("dig_ta", "other", "count", ("dig",), None),
    ("set_ta", "other", "count", ("set",), None),
    ("free_ta", "other", "count", ("freeball",), None),
)


def _columns(tax: Taxonomy, spec: Iterable[tuple[Any, ...]]) -> list[Column]:
    code_of = {s.category: s.code for s in tax.skills.values()}
    out = []
    for key, group, fmt, categories, grades in spec:
        if not all(c in code_of for c in categories):
            continue
        codes = tuple(code_of[c] for c in categories)
        out.append(Column(key, group, fmt, codes, grades, categories[0]))
    return out


def columns(tax: Taxonomy) -> list[Column]:
    """The box score's columns for this taxonomy: a skill it lacks has no columns."""
    return _columns(tax, _SPEC)


# Our numbers per rotation: serve, reception and attack (the box score's, plus reception
# efficiency).
_ROTATION_KEYS = (
    "serve_ta", "serve_ace", "serve_err", "serve_eff",
    "rec_ta", "rec_positive", "rec_eff",
    "att_ta", "att_kill", "att_hit",
)  # fmt: skip
_REC_EFF = ("rec_eff", "reception", "pct", ("reception",), None)


def rotation_columns(tax: Taxonomy) -> list[Column]:
    """The numbers of our tags shown per rotation, in this order."""
    spec = {s[0]: s for s in (*_SPEC, _REC_EFF)}
    return _columns(tax, (spec[key] for key in _ROTATION_KEYS))


# --- a row: counts by (code, grade) ---------------------------------------------------------

Counts = Counter[tuple[str, Grade]]
Places = Counter[tuple[str, int | None, int | None, Grade]]


def count_tags(tags: Iterable[TagIn]) -> Counts:
    return Counter((t.code, t.grade) for t in tags)


def _n(counts: Counts, codes: Sequence[str], grades: Sequence[Grade] | None = None) -> int:
    return sum(
        n
        for (code, grade), n in counts.items()
        if code in codes and (grades is None or grade in grades)
    )


def _ratio(part: float, whole: int) -> float | None:
    return part / whole if whole else None


def value(column: Column, counts: Counts) -> float | int | None:
    """One number of a row. None when the row has no tags of the column's skill(s)."""
    attempts = _n(counts, column.codes)
    if attempts == 0:
        return None
    if column.format == "count":
        return _n(counts, column.codes, column.grades)
    if column.format == "rating":
        scale = RATING[column.category]
        graded = [(scale[g], n) for (c, g), n in counts.items() if c in column.codes and g in scale]
        total = sum(n for _, n in graded)
        return _ratio(sum(r * n for r, n in graded), total)
    code = column.codes
    if column.key == "serve_eff":
        return _ratio(_n(counts, code, ("#",)) - _n(counts, code, ("=",)), attempts)
    if column.key == "att_hit":
        return _ratio(_n(counts, code, ("#",)) - _n(counts, code, ("=", "/")), attempts)
    if column.key == "rec_eff":
        return _ratio(_n(counts, code, ("#", "+")) - _n(counts, code, ("=", "/")), attempts)
    return _ratio(_n(counts, code, column.grades), attempts)


def values(cols: Sequence[Column], counts: Counts) -> dict[str, float | int | None]:
    return {c.key: value(c, counts) for c in cols}


# --- rows and box scores --------------------------------------------------------------------


@dataclass
class Row:
    kind: RowKind
    side: Side
    number: int | None
    member_id: int | None
    name: str | None
    # Which tags are this row's: a key that is present must match (None: must be empty).
    filter: dict[str, Any]
    counts: Counts = field(default_factory=Counter)
    matches: set[uuid.UUID] = field(default_factory=set)  # season: videos with a tag
    # Where its tags started and went: (code, zone from, zone to, grade), for the zone maps.
    places: Places = field(default_factory=Counter)
    to_check: int = 0  # of its tags, WiePlay's still to check

    def add(self, tag: TagIn, video_id: uuid.UUID | None = None) -> None:
        self.counts[(tag.code, tag.grade)] += 1
        self.to_check += int(tag.check)
        self.places[(tag.code, tag.zone_from, tag.zone_to, tag.grade)] += 1
        if video_id is not None:
            self.matches.add(video_id)


@dataclass
class BoxScore:
    players: list[Row]
    team: Row
    opponent: Row

    @property
    def rows(self) -> list[Row]:
        return [*self.players, self.team, self.opponent]


def _team_rows() -> tuple[Row, Row]:
    team = Row("team", "us", None, None, None, {"side": "us"})
    opponent = Row("opponent", "them", None, None, None, {"side": "them"})
    return team, opponent


def video_box(tags: Iterable[TagIn], roster: dict[int, tuple[int | None, str | None]]) -> BoxScore:
    """Our players by shirt number (names from the roster snapshot: number -> member, name),
    our tags without a number, the team and the opponent as one row."""
    team, opponent = _team_rows()
    players: dict[int | None, Row] = {}
    for tag in tags:
        if tag.side == "them":
            opponent.add(tag)
            continue
        team.add(tag)
        row = players.get(tag.number)
        if row is None:
            if tag.number is None:
                row = Row("no_number", "us", None, None, None, {"side": "us", "number": None})
            else:
                member_id, name = roster.get(tag.number, (tag.member_id, None))
                row = Row("player", "us", tag.number, member_id, name,
                          {"side": "us", "number": tag.number})  # fmt: skip
            players[tag.number] = row
        row.add(tag)
    ordered = sorted(players.values(), key=lambda r: (r.number is None, r.number or 0))
    return BoxScore(ordered, team, opponent)


@dataclass(frozen=True)
class VideoTags:
    """One match of a season: its id and its tags in time order."""

    video_id: uuid.UUID
    tags: Sequence[TagIn]


def season_box(videos: Sequence[VideoTags], names: dict[int, str]) -> BoxScore:
    """Our players by member across the season's matches (a shirt number can change; the
    number shown is the latest), tags of numbers not on a roster as one row, the team and
    the opponent. Videos in the order they were played."""
    team, opponent = _team_rows()
    players: dict[int, Row] = {}
    other = Row("other", "us", None, None, None, {"side": "us", "member_id": None})
    for video in videos:
        for tag in video.tags:
            if tag.side == "them":
                opponent.add(tag, video.video_id)
                continue
            team.add(tag, video.video_id)
            if tag.member_id is None:
                other.add(tag, video.video_id)
                continue
            row = players.get(tag.member_id)
            if row is None:
                row = Row("player", "us", tag.number, tag.member_id, names.get(tag.member_id),
                          {"side": "us", "member_id": tag.member_id})  # fmt: skip
                players[tag.member_id] = row
            if tag.number is not None:
                row.number = tag.number
            row.add(tag, video.video_id)
    ordered = sorted(
        players.values(), key=lambda r: (r.number is None, r.number or 0, r.name or "")
    )
    if other.counts:
        ordered.append(other)
    return BoxScore(ordered, team, opponent)


# --- team numbers from the rallies ----------------------------------------------------------


@dataclass
class TeamLine:
    rallies: int = 0
    won: int = 0
    lost: int = 0
    side_out_won: int = 0
    side_out_of: int = 0
    break_point_won: int = 0
    break_point_of: int = 0

    def add(self, other: TeamLine) -> None:
        for name in vars(self):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    @property
    def side_out_pct(self) -> float | None:
        return _ratio(self.side_out_won, self.side_out_of)

    @property
    def break_point_pct(self) -> float | None:
        return _ratio(self.break_point_won, self.break_point_of)


def team_line(rallies: Iterable[RallyIn]) -> TeamLine:
    line = TeamLine()
    for rally in rallies:
        line.rallies += 1
        if rally.won_by == "us":
            line.won += 1
        elif rally.won_by == "them":
            line.lost += 1
        if rally.won_by is None or rally.serving is None:
            continue
        if rally.serving == "them":
            line.side_out_of += 1
            line.side_out_won += int(rally.won_by == "us")
        else:
            line.break_point_of += 1
            line.break_point_won += int(rally.won_by == "us")
    return line


# Which rallies each team number is made of (the web plays them): a present key must match;
# won_by "any" means decided.
TEAM_FILTERS: dict[str, dict[str, str]] = {
    "won": {"won_by": "us"},
    "lost": {"won_by": "them"},
    "side_out_won": {"serving": "them", "won_by": "us"},
    "side_out_of": {"serving": "them", "won_by": "any"},
    "break_point_won": {"serving": "us", "won_by": "us"},
    "break_point_of": {"serving": "us", "won_by": "any"},
}


# --- rotations ------------------------------------------------------------------------------

ROTATIONS = (1, 2, 3, 4, 5, 6)


def next_rotation(rotation: int) -> int:
    """After a side-out the setter moves one zone on: 1 → 6 → 5 → 4 → 3 → 2 → 1."""
    return 6 if rotation == 1 else rotation - 1


@dataclass(frozen=True)
class Walked:
    """A rally of a set as the rotation walk saw it: its server, winner and our rotation."""

    rally: RallyIn
    serving: Side
    won_by: Side
    rotation: int


def walk(rallies: Sequence[RallyIn], start_rotation: int) -> list[Walked]:
    """A set's rallies (time order) as far as our rotation is known, walking from its starting
    rotation (module docstring): a winner nobody entered is the next rally's server; where
    neither is known the walk stops."""
    out = []
    rotation = start_rotation
    for n, rally in enumerate(rallies):
        won = rally.won_by
        if won is None and n + 1 < len(rallies):
            won = rallies[n + 1].serving
        if won is None or rally.serving is None:
            break  # from here on the rotation is not known
        out.append(Walked(rally, rally.serving, won, rotation))
        if rally.serving == "them" and won == "us":
            rotation = next_rotation(rotation)
    return out


def rotation_lines(rallies: Sequence[RallyIn], start_rotation: int) -> dict[int, TeamLine]:
    """The team numbers of one set per rotation. Rallies in time order."""
    lines = {r: TeamLine() for r in ROTATIONS}
    for w in walk(rallies, start_rotation):
        lines[w.rotation].add(team_line([replace(w.rally, won_by=w.won_by)]))
    return lines


def lineup_in(
    lineup: Sequence[int | None], start_rotation: int, rotation: int
) -> tuple[int | None, ...]:
    """Who stands in zones I-VI in a rotation, from the lineup at the first serve: after k
    side-outs (k = start - rotation, mod 6) zone I holds who started in zone I + k."""
    k = (start_rotation - rotation) % 6
    return tuple(lineup[(i + k) % 6] for i in range(6))


@dataclass
class RotationLine:
    """One rotation: the team numbers, our tags in its rallies, the rallies themselves (with
    the server and winner the walk used) and the lineups seen in it (one per set)."""

    team: TeamLine = field(default_factory=TeamLine)
    counts: Counts = field(default_factory=Counter)
    rallies: list[Walked] = field(default_factory=list)
    lineups: Counter[tuple[int | None, ...]] = field(default_factory=Counter)

    def add(self, other: RotationLine) -> None:
        self.team.add(other.team)
        self.counts.update(other.counts)
        self.rallies.extend(other.rallies)
        self.lineups.update(other.lineups)

    @property
    def lineup(self) -> tuple[int | None, ...] | None:
        """The lineup of this rotation; the first set's among the most common when sets differ."""
        if not self.lineups:
            return None
        return self.lineups.most_common(1)[0][0]


Rotations = dict[int, RotationLine]


def empty_rotations() -> Rotations:
    return {r: RotationLine() for r in ROTATIONS}


def set_rotations(
    rallies: Sequence[RallyIn],
    start_rotation: int,
    ours: dict[uuid.UUID, list[TagIn]],
    lineup: Sequence[int | None] | None = None,
    keep: Collection[uuid.UUID] | None = None,
) -> Rotations:
    """One set per rotation (rallies in time order; `ours`: our tags per rally). With `keep`
    (a search) only those rallies count; the walk still goes through all of them."""
    lines = empty_rotations()
    for w in walk(rallies, start_rotation):
        if keep is not None and w.rally.id not in keep:
            continue
        line = lines[w.rotation]
        line.team.add(team_line([replace(w.rally, won_by=w.won_by)]))
        line.counts.update(count_tags(ours.get(w.rally.id, ())))
        line.rallies.append(w)
    if lineup is not None and len(lineup) == 6:
        for r in ROTATIONS:
            lines[r].lineups[lineup_in(lineup, start_rotation, r)] += 1
    return lines


def add_rotations(total: Rotations, more: Rotations) -> None:
    for r in ROTATIONS:
        total[r].add(more[r])


# --- setter analysis --------------------------------------------------------------------------

# Where an attack started (DataVolley zones on the attacking half): front row 4 3 2, the rest
# is the back row (8: the pipe).
ZONE_OF: dict[int, str] = {
    4: "left", 3: "middle", 2: "right", 1: "back", 5: "back", 6: "back", 7: "back", 8: "back",
    9: "back",
}  # fmt: skip
ZONES = ("left", "middle", "right", "back")
UNKNOWN = "unknown"
# The first contact before a set: our reception's grade, else transition or unknown.
QUALITY_OF: dict[str, str] = {"#": "perfect", "+": "good", "!": "good",
                              "-": "poor", "/": "poor", "=": "poor"}  # fmt: skip
QUALITIES = ("perfect", "good", "poor", "transition", UNKNOWN)
OUTCOME_OF: dict[str, str] = {"#": "kill", "=": "error", "/": "blocked"}
# Tags without a rally: a set and the tag after (or before) it belong together this close.
LOOSE_GAP_MS = 8000


@dataclass(frozen=True)
class Codes:
    """The taxonomy's codes of the skills the setter analysis reads."""

    serve: str
    reception: str
    set: str
    attack: str

    @staticmethod
    def of(tax: Taxonomy) -> Codes | None:
        code_of = {s.category: s.code for s in tax.skills.values()}
        try:
            return Codes(code_of["serve"], code_of["reception"], code_of["set"], code_of["attack"])
        except KeyError:
            return None


@dataclass(frozen=True)
class SetPlay:
    """One of our sets and what came of it (module docstring)."""

    set: TagIn
    attack: TagIn | None
    rally_id: uuid.UUID | None
    set_id: uuid.UUID | None  # the match's set (period) it was played in
    rotation: int | None
    quality: str
    zone: str | None  # left | middle | right | back; None: no attack, or no zone tagged
    outcome: str | None  # kill | error | blocked | play; None: no attack

    @property
    def filter_values(self) -> dict[str, Any]:
        """What a line's `filter` is matched against."""
        attack = self.attack
        return {
            "attacker": attack.number if attack else None,
            "attacker_member": attack.member_id if attack else None,
            "setter": self.set.number,
            "setter_member": self.set.member_id,
            "zone": self.zone,
            "rotation": self.rotation,
            "quality": self.quality,
            "outcome": self.outcome,
            "attacked": attack is not None,
        }


def _zone(attack: TagIn, set_tag: TagIn) -> str | None:
    zone = attack.zone_from if attack.zone_from is not None else set_tag.zone_to
    return ZONE_OF.get(zone) if zone is not None else None


def setter_plays(
    tags: Sequence[TagIn],
    rally_of: Sequence[RallyIn | None],
    set_of: Sequence[uuid.UUID | None],
    rotation_of: dict[uuid.UUID, int],
    codes: Codes,
) -> list[SetPlay]:
    """Our sets in time order with the attack after and the contact before each (tags in time
    order; per tag its rally, its set, and per rally our rotation where the walk knows it)."""
    by_rally: dict[uuid.UUID | None, list[int]] = defaultdict(list)
    for i, rally in enumerate(rally_of):
        by_rally[rally.id if rally else None].append(i)
    out: list[SetPlay] = []
    for rally_id, seq in by_rally.items():
        seq.sort(key=lambda i: tags[i].t_ms)
        for at, i in enumerate(seq):
            tag = tags[i]
            if tag.code != codes.set or tag.side != "us":
                continue
            after = tags[seq[at + 1]] if at + 1 < len(seq) else None
            before = tags[seq[at - 1]] if at > 0 else None
            if rally_id is None:  # no rally: only a close neighbour counts
                if after is not None and after.t_ms - tag.t_ms > LOOSE_GAP_MS:
                    after = None
                if before is not None and tag.t_ms - before.t_ms > LOOSE_GAP_MS:
                    before = None
            attack = after if after and after.code == codes.attack and after.side == "us" else None
            if before is None:
                quality = UNKNOWN
            elif before.side == "us" and before.code == codes.reception:
                quality = QUALITY_OF.get(before.grade or "", UNKNOWN)
            elif before.side == "them" and before.code == codes.serve:
                quality = UNKNOWN  # our reception was not tagged
            else:
                quality = "transition"
            out.append(
                SetPlay(
                    set=tag,
                    attack=attack,
                    rally_id=rally_id,
                    set_id=set_of[i],
                    rotation=rotation_of.get(rally_id) if rally_id else None,
                    quality=quality,
                    zone=_zone(attack, tag) if attack else None,
                    outcome=OUTCOME_OF.get(attack.grade or "", "play") if attack else None,
                )
            )
    out.sort(key=lambda p: p.set.t_ms)
    return out


@dataclass
class SetterLine:
    """One option of the distribution (an attacker, a zone, a rotation, a reception quality or
    a setter): our sets in it, the attacks after them, where they went and what came of them.
    Its plays are the sets matching `filter`."""

    key: str
    filter: dict[str, Any]
    number: int | None = None
    member_id: int | None = None
    name: str | None = None
    sets: int = 0
    attacks: int = 0
    kills: int = 0
    errors: int = 0
    blocked: int = 0
    zones: Counter[str] = field(default_factory=Counter)  # attacks per zone (or "unknown")

    def add(self, play: SetPlay) -> None:
        self.sets += 1
        if play.attack is None:
            return
        self.attacks += 1
        self.kills += play.outcome == "kill"
        self.errors += play.outcome == "error"
        self.blocked += play.outcome == "blocked"
        self.zones[play.zone or UNKNOWN] += 1

    @property
    def kill_pct(self) -> float | None:
        return _ratio(self.kills, self.attacks)

    @property
    def hit_pct(self) -> float | None:
        return _ratio(self.kills - self.errors - self.blocked, self.attacks)


@dataclass
class SetterStats:
    plays: list[SetPlay]
    sets: int
    attacks: int  # sets followed by our attack
    by_attacker: list[SetterLine]
    by_zone: list[SetterLine]
    by_rotation: list[SetterLine]
    by_reception: list[SetterLine]
    by_setter: list[SetterLine]


# Who an attacker or setter is: (key, filter, number, member, name) of a tag.
Person = tuple[str, dict[str, Any], int | None, int | None, str | None]
Who = Callable[[TagIn, str], Person]


def match_who(roster: dict[int, tuple[int | None, str | None]]) -> Who:
    """A match: our players by shirt number, names from the roster snapshot."""

    def who(tag: TagIn, role: str) -> Person:
        if tag.number is None:
            return "none", {role: None}, None, None, None
        member, name = roster.get(tag.number, (tag.member_id, None))
        return str(tag.number), {role: tag.number}, tag.number, member, name

    return who


def season_who(names: dict[int, str]) -> Who:
    """A season: our players by member (the latest shirt number shown), the rest as one."""

    def who(tag: TagIn, role: str) -> Person:
        if tag.member_id is None:
            return "other", {f"{role}_member": None}, None, None, None
        member = tag.member_id
        return f"m{member}", {f"{role}_member": member}, tag.number, member, names.get(member)

    return who


def setter_stats(plays: Sequence[SetPlay], who: Who) -> SetterStats:
    attackers: dict[str, SetterLine] = {}
    setters: dict[str, SetterLine] = {}
    zones = {z: SetterLine(z, {"attacked": True, "zone": z}) for z in ZONES}
    no_zone = SetterLine(UNKNOWN, {"attacked": True, "zone": None})
    rotations = {r: SetterLine(str(r), {"rotation": r}) for r in ROTATIONS}
    no_rotation = SetterLine(UNKNOWN, {"rotation": None})
    qualities = {q: SetterLine(q, {"quality": q}) for q in QUALITIES}

    def line(lines: dict[str, SetterLine], tag: TagIn, role: str) -> SetterLine:
        key, filt, number, member, name = who(tag, role)
        if key not in lines:
            lines[key] = SetterLine(key, filt, number, member, name)
        found = lines[key]
        if tag.number is not None:
            found.number = tag.number  # a season: the latest shirt number
        return found

    for play in plays:
        if play.attack is not None:
            line(attackers, play.attack, "attacker").add(play)
            (zones[play.zone] if play.zone else no_zone).add(play)
        line(setters, play.set, "setter").add(play)
        (rotations[play.rotation] if play.rotation else no_rotation).add(play)
        qualities[play.quality].add(play)

    def ordered(lines: Iterable[SetterLine]) -> list[SetterLine]:
        return sorted(lines, key=lambda x: (-x.attacks, -x.sets, x.number is None, x.number or 0))

    known_rotation = any(p.rotation for p in plays)
    return SetterStats(
        plays=list(plays),
        sets=len(plays),
        attacks=sum(1 for p in plays if p.attack is not None),
        by_attacker=ordered(attackers.values()),
        by_zone=[z for z in (*zones.values(), no_zone) if z.sets],
        by_rotation=[
            *(rotations.values() if known_rotation else ()),
            *((no_rotation,) if no_rotation.sets else ()),
        ],
        by_reception=[q for q in qualities.values() if q.sets],
        by_setter=ordered(setters.values()),
    )


# --- zones (heat maps of serves and attacks) ---------------------------------------------------

# The skills drawn as zone maps, and the two ends of a tag: where it started and where it went
# (DataVolley zones 1-9, each on the half of the team it is on).
ZONE_SKILLS = ("serve", "attack")
ZONE_ENDS = ("from", "to")
ZONE_NUMBERS = (1, 2, 3, 4, 5, 6, 7, 8, 9)


@dataclass(frozen=True)
class ZoneCell:
    """A row's serves or attacks from (or to) one zone; zone None: no zone tagged.

    points: aces or kills (#); errors (=); blocked (attacks only: /); the rest is in play.
    Efficiency: serve (aces - errors) / TA, attack (kills - errors - blocked) / TA, as in the
    box score."""

    zone: int | None
    ta: int
    points: int
    errors: int
    blocked: int

    @property
    def in_play(self) -> int:
        return self.ta - self.points - self.errors - self.blocked

    @property
    def point_pct(self) -> float | None:
        return _ratio(self.points, self.ta)

    @property
    def eff(self) -> float | None:
        return _ratio(self.points - self.errors - self.blocked, self.ta)


def zone_cells(places: Places, code: str, category: str, end: str) -> list[ZoneCell]:
    """A row's tags of one skill per zone at one end ("from" or "to"), zones in order and the
    tags without that zone last; zones nobody played are left out."""
    totals: dict[int | None, Counter[Grade]] = defaultdict(Counter)
    for (c, zone_from, zone_to, grade), n in places.items():
        if c != code:
            continue
        zone = zone_from if end == "from" else zone_to
        totals[zone if zone in ZONE_NUMBERS else None][grade] += n
    out = []
    for zone in (*ZONE_NUMBERS, None):
        grades = totals.get(zone)
        if not grades:
            continue
        blocked = grades["/"] if category == "attack" else 0
        out.append(ZoneCell(zone, sum(grades.values()), grades["#"], grades["="], blocked))
    return out


def zone_codes(tax: Taxonomy) -> dict[str, str]:
    """The taxonomy's code per zone skill (serve -> S, attack -> A)."""
    code_of = {s.category: s.code for s in tax.skills.values()}
    return {c: code_of[c] for c in ZONE_SKILLS if c in code_of}


def has_zone_tags(row: Row, codes: Mapping[str, str]) -> bool:
    return any(c in codes.values() for c, _ in row.counts)


# --- sets -----------------------------------------------------------------------------------


def set_at(sets: Sequence[SetIn], t_ms: int) -> SetIn | None:
    """The set running at a moment: the last one started at or before it and not ended."""
    found = None
    for s in sorted(sets, key=lambda s: (s.start_ms, s.no)):
        if s.start_ms <= t_ms and (s.end_ms is None or t_ms < s.end_ms):
            found = s
    return found


def rally_of_tags(tags: Sequence[TagIn], rallies: Sequence[RallyIn]) -> list[RallyIn | None]:
    """Each tag's rally: the linked one, or one starting up to 2 s after an unlinked tag."""
    by_id = {r.id: r for r in rallies}
    ordered = sorted(rallies, key=lambda r: r.start_ms)
    out: list[RallyIn | None] = []
    for tag in tags:
        rally = by_id.get(tag.segment_id) if tag.segment_id is not None else None
        if rally is None and tag.segment_id is None:
            rally = next(
                (r for r in ordered if r.start_ms - RALLY_LEAD_MS <= tag.t_ms <= r.end_ms), None
            )
        out.append(rally)
    return out


def set_of_tags(
    tags: Sequence[TagIn],
    rallies: Sequence[RallyIn],
    sets: Sequence[SetIn],
    rally_of: Sequence[RallyIn | None] | None = None,
) -> list[uuid.UUID | None]:
    """Each tag's set: its rally's (rally_of_tags), else the set running at its time."""
    if rally_of is None:
        rally_of = rally_of_tags(tags, rallies)
    out: list[uuid.UUID | None] = []
    for tag, rally in zip(tags, rally_of, strict=True):
        if rally is not None and rally.period_id is not None:
            out.append(rally.period_id)
        else:
            found = set_at(sets, tag.t_ms)
            out.append(found.id if found else None)
    return out


# --- a match and a season --------------------------------------------------------------------


@dataclass
class SetStats:
    set: SetIn
    box: BoxScore
    team: TeamLine
    rotations: Rotations | None = None  # with a starting rotation
    setter: SetterStats | None = None


@dataclass
class VideoStats:
    box: BoxScore
    team: TeamLine
    sets: list[SetStats]
    tags: int
    rallies: int
    rotations: Rotations | None = None  # summed over the sets that have them
    setter: SetterStats | None = None  # None: a taxonomy without sets or attacks


def set_rallies(rallies: Sequence[RallyIn], s: SetIn) -> list[RallyIn]:
    """A set's rallies in time order."""
    return sorted((r for r in rallies if r.period_id == s.id), key=lambda r: r.start_ms)


def rotation_of_rallies(rallies: Sequence[RallyIn], sets: Sequence[SetIn]) -> dict[uuid.UUID, int]:
    """Our rotation in each rally where the walk knows it (sets with a starting rotation)."""
    out: dict[uuid.UUID, int] = {}
    for s in sets:
        if s.start_rotation in ROTATIONS:
            assert s.start_rotation is not None
            walked = walk(set_rallies(rallies, s), s.start_rotation)
            out.update((w.rally.id, w.rotation) for w in walked)
    return out


@dataclass(frozen=True)
class Selection:
    """What a search keeps of a match (wieplay/core/search.py): the tags (their positions in
    the match's tag list) and the rallies whose numbers count."""

    tags: frozenset[int]
    rallies: frozenset[uuid.UUID]


# A search over one match: its tags (time order), rallies and sets -> what counts.
Selector = Callable[[Sequence[TagIn], Sequence[RallyIn], Sequence[SetIn]], Selection]


@dataclass
class _Walked:
    """A match's rotations and sets, worked out once for its stats and its season's."""

    per_set: list[tuple[SetIn, list[RallyIn], Rotations | None]]
    total: Rotations | None
    rally_of: list[RallyIn | None]
    set_of: list[uuid.UUID | None]
    rotation_of: dict[uuid.UUID, int]


def _walk_match(
    tags: Sequence[TagIn],
    rallies: Sequence[RallyIn],
    sets: Sequence[SetIn],
    selection: Selection | None = None,
) -> _Walked:
    """With a selection, the rotations count only its tags and rallies; the walk itself and
    the rotation of every rally still come from all of them."""
    rally_of = rally_of_tags(tags, rallies)
    set_of = set_of_tags(tags, rallies, sets, rally_of)
    ours: dict[uuid.UUID, list[TagIn]] = defaultdict(list)
    for i, (tag, rally) in enumerate(zip(tags, rally_of, strict=True)):
        if rally is not None and tag.side == "us" and (selection is None or i in selection.tags):
            ours[rally.id].append(tag)
    keep = selection.rallies if selection is not None else None
    per_set: list[tuple[SetIn, list[RallyIn], Rotations | None]] = []
    total: Rotations | None = None
    rotation_of: dict[uuid.UUID, int] = {}
    for s in sorted(sets, key=lambda s: s.no):
        played = set_rallies(rallies, s)
        lines = None
        if s.start_rotation in ROTATIONS:
            assert s.start_rotation is not None
            rotation_of.update((w.rally.id, w.rotation) for w in walk(played, s.start_rotation))
            lines = set_rotations(played, s.start_rotation, ours, s.lineup, keep)
            if total is None:
                total = empty_rotations()
            add_rotations(total, lines)
        if keep is not None:
            played = [r for r in played if r.id in keep]
        per_set.append((s, played, lines))
    return _Walked(per_set, total, rally_of, set_of, rotation_of)


def _kept_plays(plays: list[SetPlay], picked: set[int]) -> list[SetPlay]:
    """A search keeps a set when it keeps the set's tag or the attack after it (by identity:
    the plays hold the match's own tag objects)."""
    return [p for p in plays if id(p.set) in picked or (p.attack and id(p.attack) in picked)]


def video_stats(
    tags: Sequence[TagIn],
    rallies: Sequence[RallyIn],
    sets: Sequence[SetIn],
    roster: dict[int, tuple[int | None, str | None]],
    tax: Taxonomy | None = None,
    select: Selector | None = None,
) -> VideoStats:
    """A match's numbers; with `select` (a search) only the tags and rallies it keeps."""
    selection = select(tags, rallies, sets) if select is not None else None
    walked = _walk_match(tags, rallies, sets, selection)
    codes = Codes.of(tax or load("volleyball"))
    plays = (
        setter_plays(tags, walked.rally_of, walked.set_of, walked.rotation_of, codes)
        if codes
        else None
    )
    counted = list(range(len(tags))) if selection is None else sorted(selection.tags)
    chosen = [tags[i] for i in counted]
    if plays is not None and selection is not None:
        plays = _kept_plays(plays, {id(t) for t in chosen})
    kept = rallies if selection is None else [r for r in rallies if r.id in selection.rallies]
    who = match_who(roster)
    per_set = []
    for s, played, lines in walked.per_set:
        in_set = [tags[i] for i in counted if walked.set_of[i] == s.id]
        setter = (
            setter_stats([p for p in plays if p.set_id == s.id], who) if plays is not None else None
        )
        per_set.append(SetStats(s, video_box(in_set, roster), team_line(played), lines, setter))
    return VideoStats(
        video_box(chosen, roster),
        team_line(kept),
        per_set,
        len(chosen),
        len(kept),
        walked.total,
        setter_stats(plays, who) if plays is not None else None,
    )


@dataclass(frozen=True)
class MatchIn:
    video_id: uuid.UUID
    title: str
    recorded_on: date
    tags: Sequence[TagIn]
    rallies: Sequence[RallyIn]
    # The official result in sets [us, them], when the game is known.
    result: tuple[int, int] | None = None
    sets: Sequence[SetIn] = ()


@dataclass
class MatchLine:
    match: MatchIn
    team: TeamLine


@dataclass
class SeasonStats:
    box: BoxScore
    team: TeamLine
    matches: list[MatchLine]
    tagged: int  # matches with at least one tag
    rotations: Rotations | None = None  # summed over the matches' sets that have them
    setter: SetterStats | None = None


def season_stats(
    matches: Sequence[MatchIn],
    names: dict[int, str],
    tax: Taxonomy | None = None,
    select: Selector | None = None,
) -> SeasonStats:
    """Every ready match of a team row: counts are summed, ratios recomputed from the sums.
    With `select` (a search) each match counts only the tags and rallies it keeps; `tagged`
    still counts the matches with any tag."""
    played = sorted(matches, key=lambda m: (m.recorded_on, str(m.video_id)))
    codes = Codes.of(tax or load("volleyball"))
    total = TeamLine()
    lines = []
    rotations: Rotations | None = None
    plays: list[SetPlay] = []
    counted: list[VideoTags] = []
    for m in played:
        selection = select(m.tags, m.rallies, m.sets) if select is not None else None
        chosen = list(m.tags) if selection is None else [m.tags[i] for i in sorted(selection.tags)]
        counted.append(VideoTags(m.video_id, chosen))
        kept = (
            m.rallies if selection is None else [r for r in m.rallies if r.id in selection.rallies]
        )
        line = team_line(kept)
        total.add(line)
        lines.append(MatchLine(m, line))
        walked = _walk_match(m.tags, m.rallies, m.sets, selection)
        if walked.total is not None:
            if rotations is None:
                rotations = empty_rotations()
            add_rotations(rotations, walked.total)
        if codes:
            found = setter_plays(m.tags, walked.rally_of, walked.set_of, walked.rotation_of, codes)
            plays += found if selection is None else _kept_plays(found, {id(t) for t in chosen})
    box = season_box(counted, names)
    setter = setter_stats(plays, season_who(names)) if codes else None
    return SeasonStats(box, total, lines, sum(1 for m in played if m.tags), rotations, setter)


# --- CSV ------------------------------------------------------------------------------------

HEADERS: dict[str, dict[str, str]] = {
    "en": {
        "set": "Set", "number": "Number", "player": "Player", "matches": "Matches",
        "points": "Points",
        "serve_ta": "Serve attempts", "serve_ace": "Aces", "serve_err": "Serve errors",
        "serve_in": "Serves in play", "serve_eff": "Serve efficiency %",
        "serve_rtg": "Serve rating (0-3)",
        "rec_ta": "Reception attempts", "rec_err": "Reception errors",
        "rec_perfect": "Perfect reception %", "rec_positive": "Positive reception %",
        "rec_rtg": "Reception rating (0-3)",
        "att_ta": "Attack attempts", "att_kill": "Kills", "att_err": "Attack errors",
        "att_blocked": "Attacks blocked", "att_hit": "Hitting %", "att_kill_pct": "Kill %",
        "att_err_pct": "Attack error %",
        "blk_point": "Block points", "blk_touch": "Block touches", "blk_err": "Block errors",
        "dig_ta": "Digs", "set_ta": "Sets", "free_ta": "Freeballs",
        "won": "Rallies won", "lost": "Rallies lost", "side_out_won": "Side-outs",
        "side_out_of": "Rallies received", "side_out_pct": "Side-out %",
        "break_point_won": "Break points", "break_point_of": "Rallies served",
        "break_point_pct": "Break-point %",
        "all": "Match", "season": "Season", "team": "Team", "opponent": "Opponent",
        "no_number": "No number", "other": "Not on the roster",
        # Rotations and the setter analysis (?part=rotations, ?part=setter).
        "rotation": "Rotation", "lineup": "Lineup (zones I-VI)", "rallies": "Rallies",
        "rec_eff": "Reception efficiency %",
        "breakdown": "Breakdown", "option": "Option", "sets": "Sets", "attacks": "Attacks",
        "share": "Share of attacks %", "zone_left": "Left (4)", "zone_middle": "Middle (3)",
        "zone_right": "Right (2)", "zone_back": "Back row", "zone_unknown": "Zone not tagged",
        "kills": "Kills", "errors": "Errors", "blocked": "Blocked", "kill_pct": "Kill %",
        "hit_pct": "Hitting %",
        "to_check": "To check",
        "by_attacker": "Attacker", "by_zone": "Zone", "by_rotation": "Rotation",
        "by_reception": "First contact", "by_setter": "Setter",
        "rotation_no": "Rotation {n}", "rotation_unknown": "Rotation not known",
        "quality_perfect": "Perfect reception", "quality_good": "Good reception",
        "quality_poor": "Poor reception", "quality_transition": "Transition",
        "quality_unknown": "Not known",
        # The zone maps (?part=zones).
        "skill": "Skill", "zone_end": "Zones", "zone": "Zone", "attempts": "Attempts",
        "zone_points": "Points (ace, kill)", "in_play": "In play",
        "zone_point_pct": "Ace or kill %", "efficiency": "Efficiency %",
        "skill_serve": "Serve", "skill_attack": "Attack", "end_from": "From", "end_to": "To",
    },
    "de": {
        "set": "Satz", "number": "Nummer", "player": "Spieler", "matches": "Spiele",
        "points": "Punkte",
        "serve_ta": "Aufschläge", "serve_ace": "Asse", "serve_err": "Aufschlagfehler",
        "serve_in": "Aufschläge im Spiel", "serve_eff": "Aufschlag-Effizienz %",
        "serve_rtg": "Aufschlag-Wertung (0-3)",
        "rec_ta": "Annahmen", "rec_err": "Annahmefehler", "rec_perfect": "Annahme perfekt %",
        "rec_positive": "Annahme positiv %", "rec_rtg": "Annahme-Wertung (0-3)",
        "att_ta": "Angriffe", "att_kill": "Angriffspunkte", "att_err": "Angriffsfehler",
        "att_blocked": "Angriffe geblockt", "att_hit": "Angriffseffizienz %",
        "att_kill_pct": "Angriffspunkte %", "att_err_pct": "Angriffsfehler %",
        "blk_point": "Blockpunkte", "blk_touch": "Blockberührungen", "blk_err": "Blockfehler",
        "dig_ta": "Abwehr", "set_ta": "Zuspiele", "free_ta": "Freeballs",
        "won": "Ballwechsel gewonnen", "lost": "Ballwechsel verloren", "side_out_won": "Side-outs",
        "side_out_of": "Ballwechsel in Annahme", "side_out_pct": "Side-out %",
        "break_point_won": "Breakpunkte", "break_point_of": "Ballwechsel mit Aufschlag",
        "break_point_pct": "Breakpunkt %",
        "all": "Spiel", "season": "Saison", "team": "Team", "opponent": "Gegner",
        "no_number": "Ohne Nummer", "other": "Nicht im Kader",
        "rotation": "Rotation", "lineup": "Aufstellung (Zonen I-VI)", "rallies": "Ballwechsel",
        "rec_eff": "Annahme-Effizienz %",
        "breakdown": "Aufteilung", "option": "Option", "sets": "Zuspiele", "attacks": "Angriffe",
        "share": "Anteil der Angriffe %", "zone_left": "Links (4)", "zone_middle": "Mitte (3)",
        "zone_right": "Rechts (2)", "zone_back": "Hinten", "zone_unknown": "Zone nicht erfasst",
        "kills": "Punkte", "errors": "Fehler", "blocked": "Geblockt", "kill_pct": "Punkte %",
        "hit_pct": "Angriffseffizienz %",
        "to_check": "Noch zu prüfen",
        "by_attacker": "Angreifer", "by_zone": "Zone", "by_rotation": "Rotation",
        "by_reception": "Erster Ballkontakt", "by_setter": "Passeur",
        "rotation_no": "Rotation {n}", "rotation_unknown": "Rotation unbekannt",
        "quality_perfect": "Annahme perfekt", "quality_good": "Annahme gut",
        "quality_poor": "Annahme schlecht", "quality_transition": "Transition",
        "quality_unknown": "Unbekannt",
        "skill": "Aktion", "zone_end": "Zonen", "zone": "Zone", "attempts": "Versuche",
        "zone_points": "Punkte (Ass, Angriffspunkt)", "in_play": "Im Spiel",
        "zone_point_pct": "Ass- oder Punkte-%", "efficiency": "Effizienz %",
        "skill_serve": "Aufschlag", "skill_attack": "Angriff", "end_from": "Von", "end_to": "Nach",
    },
}  # fmt: skip

TEAM_KEYS = (
    "won", "lost", "side_out_won", "side_out_of", "side_out_pct",
    "break_point_won", "break_point_of", "break_point_pct",
)  # fmt: skip


def csv_cell(number: float | int | None, fmt: Format) -> str:
    """Percent with one decimal (31.2), a rating with two (2.15), counts as they are."""
    if number is None:
        return ""
    if fmt == "pct":
        return f"{number * 100:.1f}"
    if fmt == "rating":
        return f"{number:.2f}"
    return str(number)


def _team_cells(line: TeamLine) -> list[str]:
    out = []
    for key in TEAM_KEYS:
        number = getattr(line, key)
        out.append(csv_cell(number, "pct" if key.endswith("_pct") else "count"))
    return out


def to_csv(
    cols: Sequence[Column],
    scopes: Sequence[tuple[str, BoxScore, TeamLine]],
    lang: str = "en",
    with_matches: bool = False,
    labels: Mapping[str, str] | None = None,
) -> str:
    """One line per row and scope (the match and each set, or the season). The team line's
    side-out and break-point numbers fill the last columns of the team's row. `labels`: the
    names of rows without one by kind (a match: "team" and "opponent" by the teams' names)."""
    words = {**HEADERS.get(lang, HEADERS["en"]), **(labels or {})}
    head = [words["set"], words["number"], words["player"]]
    if with_matches:
        head.append(words["matches"])
    head += [words[c.key] for c in cols] + [words[k] for k in TEAM_KEYS]
    head.append(words["to_check"])
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(head)
    for scope, box, team in scopes:
        for row in box.rows:
            name = row.name or (words[row.kind] if row.kind != "player" else "")
            line = [scope, "" if row.number is None else str(row.number), name]
            if with_matches:
                line.append(str(len(row.matches)))
            nums = values(cols, row.counts)
            line += [csv_cell(nums[c.key], c.format) for c in cols]
            line += _team_cells(team) if row.kind == "team" else [""] * len(TEAM_KEYS)
            # How many of the row's tags WiePlay is unsure of (PLAN §10: they count, marked).
            line.append(str(row.to_check))
            writer.writerow(line)
    return out.getvalue()


ROTATION_TEAM_KEYS = (
    "rallies", "won", "lost", "side_out_of", "side_out_won", "side_out_pct",
    "break_point_of", "break_point_won", "break_point_pct",
)  # fmt: skip


def _lineup_cell(lineup: Sequence[int | None] | None) -> str:
    if lineup is None:
        return ""
    return " ".join("?" if n is None else str(n) for n in lineup)


def rotations_csv(
    cols: Sequence[Column], scopes: Sequence[tuple[str, Rotations | None]], lang: str = "en"
) -> str:
    """One line per rotation and scope (the match and each set, or the season): the team
    numbers, the lineup in it and our serve, reception and attack numbers."""
    words = HEADERS.get(lang, HEADERS["en"])
    head = [words["set"], words["rotation"], words["lineup"]]
    head += [words[k] for k in ROTATION_TEAM_KEYS] + [words[c.key] for c in cols]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(head)
    for scope, lines in scopes:
        if lines is None:
            continue
        for r in ROTATIONS:
            line = lines[r]
            row = [scope, str(r), _lineup_cell(line.lineup)]
            for key in ROTATION_TEAM_KEYS:
                number = getattr(line.team, key)
                row.append(csv_cell(number, "pct" if key.endswith("_pct") else "count"))
            nums = values(cols, line.counts)
            row += [csv_cell(nums[c.key], c.format) for c in cols]
            writer.writerow(row)
    return out.getvalue()


def _option(words: dict[str, str], group: str, line: SetterLine) -> str:
    if group in ("by_attacker", "by_setter"):
        if line.number is None:
            return words["no_number"] if line.key == "none" else words["other"]
        return " ".join(x for x in (f"#{line.number}", line.name) if x)
    if group == "by_zone":
        return words[f"zone_{line.key}"]
    if group == "by_rotation":
        if line.key == UNKNOWN:
            return words["rotation_unknown"]
        return words["rotation_no"].format(n=line.key)
    return words[f"quality_{line.key}"]


def zones_csv(
    scopes: Sequence[tuple[str, BoxScore]],
    codes: Mapping[str, str],
    lang: str = "en",
    labels: Mapping[str, str] | None = None,
) -> str:
    """The zone maps as a table: per scope (the match and each set, or the season), row (a
    player, the team, the opponent), skill and end (from, to) one line per zone."""
    words = {**HEADERS.get(lang, HEADERS["en"]), **(labels or {})}
    head = [words["set"], words["number"], words["player"], words["skill"], words["zone_end"],
            words["zone"], words["attempts"], words["zone_points"], words["errors"],
            words["blocked"], words["in_play"], words["zone_point_pct"],
            words["efficiency"]]  # fmt: skip
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(head)
    for scope, box in scopes:
        for row in box.rows:
            name = row.name or (words[row.kind] if row.kind != "player" else "")
            for category, code in codes.items():
                for end in ZONE_ENDS:
                    for cell in zone_cells(row.places, code, category, end):
                        writer.writerow(
                            [
                                scope,
                                "" if row.number is None else str(row.number),
                                name,
                                words[f"skill_{category}"],
                                words[f"end_{end}"],
                                words["zone_unknown"] if cell.zone is None else str(cell.zone),
                                str(cell.ta),
                                str(cell.points),
                                str(cell.errors),
                                str(cell.blocked),
                                str(cell.in_play),
                                csv_cell(cell.point_pct, "pct"),
                                csv_cell(cell.eff, "pct"),
                            ]
                        )
    return out.getvalue()


SETTER_GROUPS = ("by_attacker", "by_zone", "by_rotation", "by_reception", "by_setter")


def setter_csv(scopes: Sequence[tuple[str, SetterStats | None]], lang: str = "en") -> str:
    """The setter analysis: per scope and breakdown one line per option, with its sets, its
    attacks' share, zones and outcomes."""
    words = HEADERS.get(lang, HEADERS["en"])
    zone_keys = (*ZONES, UNKNOWN)
    head = [words["set"], words["breakdown"], words["option"], words["sets"], words["attacks"],
            words["share"], *(words[f"zone_{z}"] for z in zone_keys), words["kills"],
            words["errors"], words["blocked"], words["kill_pct"], words["hit_pct"]]  # fmt: skip
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(head)
    for scope, setter in scopes:
        if setter is None:
            continue
        for group in SETTER_GROUPS:
            for line in getattr(setter, group):
                writer.writerow(
                    [
                        scope,
                        words[group],
                        _option(words, group, line),
                        str(line.sets),
                        str(line.attacks),
                        csv_cell(_ratio(line.attacks, setter.attacks), "pct"),
                        *(str(line.zones.get(z, 0)) for z in zone_keys),
                        str(line.kills),
                        str(line.errors),
                        str(line.blocked),
                        csv_cell(line.kill_pct, "pct"),
                        csv_cell(line.hit_pct, "pct"),
                    ]
                )
    return out.getvalue()
