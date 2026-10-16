"""Statistics from the tags (PLAN §9.7): box score per player, side-out and break-point.

Pure functions over a video's tags, rallies and sets; nothing is stored, the numbers are
computed when they are read. nomal/api/stats.py loads the rows and serves JSON and CSV.

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
the set running at its time. Rotation numbers are not computed: sets carry no starting
rotation yet.

Every number links to its plays: a cell's tags are those matching the row's `filter`, the
column's `codes` and `grades`, and (per set) the set. The web plays them on the video page and
opens a smart reel on the team page.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from nomal.core.taxonomy import Taxonomy

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


def columns(tax: Taxonomy) -> list[Column]:
    """The box score's columns for this taxonomy: a skill it lacks has no columns."""
    code_of = {s.category: s.code for s in tax.skills.values()}
    out = []
    for key, group, fmt, categories, grades in _SPEC:
        if not all(c in code_of for c in categories):
            continue
        codes = tuple(code_of[c] for c in categories)
        out.append(Column(key, group, fmt, codes, grades, categories[0]))
    return out


# --- a row: counts by (code, grade) ---------------------------------------------------------

Counts = Counter[tuple[str, Grade]]


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

    def add(self, tag: TagIn, video_id: uuid.UUID | None = None) -> None:
        self.counts[(tag.code, tag.grade)] += 1
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


# --- sets -----------------------------------------------------------------------------------


def set_at(sets: Sequence[SetIn], t_ms: int) -> SetIn | None:
    """The set running at a moment: the last one started at or before it and not ended."""
    found = None
    for s in sorted(sets, key=lambda s: (s.start_ms, s.no)):
        if s.start_ms <= t_ms and (s.end_ms is None or t_ms < s.end_ms):
            found = s
    return found


def set_of_tags(
    tags: Sequence[TagIn], rallies: Sequence[RallyIn], sets: Sequence[SetIn]
) -> list[uuid.UUID | None]:
    """Each tag's set: its rally's (the linked one, or one starting up to 2 s after an
    unlinked tag), else the set running at its time."""
    by_id = {r.id: r for r in rallies}
    ordered = sorted(rallies, key=lambda r: r.start_ms)
    out: list[uuid.UUID | None] = []
    for tag in tags:
        rally = by_id.get(tag.segment_id) if tag.segment_id is not None else None
        if rally is None and tag.segment_id is None:
            rally = next(
                (r for r in ordered if r.start_ms - RALLY_LEAD_MS <= tag.t_ms <= r.end_ms), None
            )
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


@dataclass
class VideoStats:
    box: BoxScore
    team: TeamLine
    sets: list[SetStats]
    tags: int
    rallies: int


def video_stats(
    tags: Sequence[TagIn],
    rallies: Sequence[RallyIn],
    sets: Sequence[SetIn],
    roster: dict[int, tuple[int | None, str | None]],
) -> VideoStats:
    tag_sets = set_of_tags(tags, rallies, sets)
    per_set = []
    for s in sorted(sets, key=lambda s: s.no):
        in_set = [t for t, sid in zip(tags, tag_sets, strict=True) if sid == s.id]
        per_set.append(
            SetStats(
                s, video_box(in_set, roster), team_line(r for r in rallies if r.period_id == s.id)
            )
        )
    return VideoStats(video_box(tags, roster), team_line(rallies), per_set, len(tags), len(rallies))


@dataclass(frozen=True)
class MatchIn:
    video_id: uuid.UUID
    title: str
    recorded_on: date
    tags: Sequence[TagIn]
    rallies: Sequence[RallyIn]
    # The official result in sets [us, them], when the game is known.
    result: tuple[int, int] | None = None


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


def season_stats(matches: Sequence[MatchIn], names: dict[int, str]) -> SeasonStats:
    """Every ready match of a team row: counts are summed, ratios recomputed from the sums."""
    played = sorted(matches, key=lambda m: (m.recorded_on, str(m.video_id)))
    box = season_box([VideoTags(m.video_id, m.tags) for m in played], names)
    total = TeamLine()
    lines = []
    for m in played:
        line = team_line(m.rallies)
        total.add(line)
        lines.append(MatchLine(m, line))
    return SeasonStats(box, total, lines, sum(1 for m in played if m.tags))


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
) -> str:
    """One line per row and scope (the match and each set, or the season). The team line's
    side-out and break-point numbers fill the last columns of the team's row."""
    words = HEADERS.get(lang, HEADERS["en"])
    head = [words["set"], words["number"], words["player"]]
    if with_matches:
        head.append(words["matches"])
    head += [words[c.key] for c in cols] + [words[k] for k in TEAM_KEYS]
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
            writer.writerow(line)
    return out.getvalue()
