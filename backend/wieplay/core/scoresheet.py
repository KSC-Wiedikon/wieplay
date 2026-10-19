"""Official scoresheets, API side (PLAN §10.3a; research: docs/research/scoresheets-2026-10.md).

    a coach types the sheet  ──PUT /api/videos/{id}/scoresheet──▶ table `scoresheet` (+ revisions)
    every read: each set decoded and checked (wieplay/ml/sheet.py), a repair suggested
    the scoring: a consistent set's points lined up with the rallies (wieplay/ml/scoring.py),
                 a doubtful one as a hint; the sets' first server and lineups filled in;
                 the sheet's timeouts as `timeout` breaks with their team

Ranked (research §4.1): the coach's facts > the official scoresheet > the scoreboard's log > the
model. A coach's winner or serve is never changed; where it disagrees with the sheet the rally
is flagged ("sheet"). The sheet never overrides the official set score either: a sheet whose set
ends elsewhere is inconsistent (both are shown).

A set read from the photo and not confirmed (SS4, a draft) is evidence too, weaker than a typed
one and the log, stronger than the serves (PLAN §10.3): the reader's probabilities per service
box go into the joint decode (wieplay/ml/scoring.py `BoxCells`); the boxes a coach corrected in
the draft count as typed. Its timeouts and substitutions are "score at this break" evidence like
a typed set's.

What is typed: per set the first server, both starting lineups and every service box by shirt
number and score, timeouts, substitutions, sanctions (no person), "points cancelled", start and
end times. No names, birthdates or licence numbers ever. Opponents by shirt number only.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wieplay.core.models import (
    Period,
    Scoresheet,
    Segment,
    Video,
    VideoRoster,
    WdGame,
    WdMember,
)
from wieplay.ml import scoring as sc
from wieplay.ml import sheet as sh

Side = Literal["us", "them"]
SPORTS = frozenset({"volleyball"})
MAX_SETS = 5
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# --- the typed sheet ---------------------------------------------------------------------------

Score = list[int]  # [the team's own score, the other team's], as written on the sheet


class _Strict(BaseModel):
    """Nothing but the fields below is taken: no names or other notes slip into a sheet."""

    model_config = ConfigDict(extra="forbid")


def _score(value: Score | None) -> Score | None:
    if value is None:
        return None
    if len(value) != 2 or any(isinstance(v, bool) or not 0 <= v <= 99 for v in value):
        raise ValueError("a score is two numbers 0-99")
    return value


class TimeoutIn(_Strict):
    side: Side
    own: int = Field(ge=0, le=99)  # the requesting team's score, written first
    opp: int = Field(ge=0, le=99)


class SubIn(_Strict):
    """A substitution in the column of a starting position: who came in and the score then
    (the substituting team's first), and the score when the starting player came back."""

    side: Side
    position: int = Field(ge=1, le=6)
    number: int | None = Field(default=None, ge=0, le=99)
    at: Score | None = None
    back: Score | None = None

    _check = field_validator("at", "back")(_score)


class SanctionIn(_Strict):
    """A sanction and the score then (the sanctioned team's first); no person is recorded."""

    side: Side
    kind: Literal["W", "P", "E", "D", "DW", "DP"]
    own: int = Field(ge=0, le=99)
    opp: int = Field(ge=0, le=99)


class CancelledIn(_Strict):
    """Rallies played after this score that the sheet does not count (a rotation fault)."""

    us: int = Field(ge=0, le=99)
    them: int = Field(ge=0, le=99)
    rallies: int = Field(default=1, ge=1, le=10)


Column = list[int | None]


class SetSheetIn(_Strict):
    no: int = Field(ge=1, le=MAX_SETS)
    deciding: bool | None = None  # a deciding set (to 15, six boxes); default: set 5
    first_serve: Side | None = None
    lineup_us: list[int | None] | None = Field(default=None, min_length=6, max_length=6)
    lineup_them: list[int | None] | None = Field(default=None, min_length=6, max_length=6)
    # Positions I-VI, each its boxes 1-8 (1-6 in a deciding set): the score written, or null.
    boxes_us: list[Column] = Field(default_factory=list, max_length=6)
    boxes_them: list[Column] = Field(default_factory=list, max_length=6)
    circled_us: list[int] | None = Field(default=None, min_length=2, max_length=2)
    circled_them: list[int] | None = Field(default=None, min_length=2, max_length=2)
    timeouts: list[TimeoutIn] = Field(default_factory=list, max_length=8)
    subs: list[SubIn] = Field(default_factory=list, max_length=12)
    sanctions: list[SanctionIn] = Field(default_factory=list, max_length=10)
    cancelled: list[CancelledIn] = Field(default_factory=list, max_length=5)
    start: str | None = None  # hh:mm, the time of the first serve
    end: str | None = None
    use: bool = True  # this set lines up the scoring ("don't use the sheet for set 3")

    @model_validator(mode="after")
    def _check(self) -> SetSheetIn:
        boxes = sh.boxes_for(self.is_deciding)
        for grid in (self.boxes_us, self.boxes_them):
            for column in grid:
                if len(column) > boxes:
                    raise ValueError(f"a position has at most {boxes} boxes in this set")
                if any(v is not None and (isinstance(v, bool) or not 0 <= v <= 99)
                       for v in column):  # fmt: skip
                    raise ValueError("a box holds a score 0-99")
        for lineup in (self.lineup_us, self.lineup_them):
            if lineup and any(n is not None and (isinstance(n, bool) or not 0 <= n <= 99)
                              for n in lineup):  # fmt: skip
                raise ValueError("a lineup holds shirt numbers 0-99")
        for mark in (self.circled_us, self.circled_them):
            if mark and not (1 <= mark[0] <= 6 and 1 <= mark[1] <= boxes):
                raise ValueError("a circled final is a position 1-6 and a box")
        for t in (self.start, self.end):
            if t is not None and not HHMM.match(t):
                raise ValueError("a time is hh:mm")
        return self

    @property
    def is_deciding(self) -> bool:
        return self.deciding if self.deciding is not None else self.no == MAX_SETS


class SheetIn(_Strict):
    """The whole sheet; `base_version`: the version the browser last saw (0 for a new one)."""

    base_version: int = Field(default=0, ge=0)
    sets: list[SetSheetIn] = Field(default_factory=list, max_length=MAX_SETS)
    use_in_scoring: bool = True

    @model_validator(mode="after")
    def _numbers(self) -> SheetIn:
        numbers = [s.no for s in self.sets]
        if len(numbers) != len(set(numbers)):
            raise ValueError("two sets with the same number")
        self.sets.sort(key=lambda s: s.no)
        return self


def to_set(doc: Mapping[str, Any]) -> sh.SheetSet:
    """A stored (or posted) set as the decoder takes it."""
    s = SetSheetIn.model_validate(doc)
    circled: dict[sh.Side, tuple[int, int] | None] = {
        "us": (s.circled_us[0], s.circled_us[1]) if s.circled_us else None,
        "them": (s.circled_them[0], s.circled_them[1]) if s.circled_them else None,
    }
    return sh.SheetSet(
        no=s.no,
        first_serve=s.first_serve,
        boxes={"us": s.boxes_us, "them": s.boxes_them},
        deciding=s.is_deciding,
        circled=circled,
        lineup={"us": s.lineup_us, "them": s.lineup_them},
        timeouts=[sh.Timeout(t.side, t.own, t.opp) for t in s.timeouts],
        subs=[
            sh.Sub(
                u.side,
                u.position,
                u.number,
                (u.at[0], u.at[1]) if u.at else None,
                (u.back[0], u.back[1]) if u.back else None,
            )
            for u in s.subs
        ],
        sanctions=[sh.Sanction(x.side, x.kind, x.own, x.opp) for x in s.sanctions],
        cancelled=[sh.Cancelled(c.us, c.them, c.rallies) for c in s.cancelled],
    )


# --- checked ------------------------------------------------------------------------------------

Status = Literal["empty", "incomplete", "inconsistent", "consistent"]


@dataclass
class SetCheck:
    """One set of the sheet decoded and checked; `use`: how the scoring takes it."""

    doc: dict[str, Any]
    sheet: sh.SheetSet
    decoded: sh.Decoded
    official: tuple[int, int] | None
    repairs: list[sh.Repair] = field(default_factory=list)
    # Read from the photo and not confirmed yet (SS4): shown, never scored.
    draft: bool = False

    @property
    def no(self) -> int:
        return self.sheet.no

    @property
    def status(self) -> Status:
        if not self.sheet.has_boxes():
            return "empty"
        if self.decoded.consistent:
            return "consistent"
        if self.decoded.errors:
            return "inconsistent"
        return "incomplete"

    @property
    def wanted(self) -> bool:
        return bool(self.doc.get("use", True))

    def use(self, sheet_on: bool) -> Literal["path", "hint", "read"] | None:
        if not sheet_on or not self.wanted:
            return None
        if self.draft:
            return "read"  # its boxes as the reader saw them: soft evidence (PLAN §10.3)
        if self.sheet.first_serve is None:
            return None
        if self.status == "consistent":
            return "path"
        return "hint" if self.decoded.points else None

    @property
    def repair(self) -> sh.Repair | None:
        return sh.clear_repair(self.repairs)


def check(
    sets: Sequence[Mapping[str, Any]],
    official: Sequence[tuple[int, int]] | None,
    drafts: Set[int] = frozenset(),
) -> list[SetCheck]:
    """Every set decoded and checked against the official set scores; a repair searched for
    each set that is not consistent. `drafts`: the sets read from a photo and not confirmed
    (checked and shown like the others, never used by the scoring)."""
    out = []
    for doc in sets:
        s = to_set(doc)
        off = official[s.no - 1] if official and 1 <= s.no <= len(official) else None
        decoded = sh.decode(s, off)
        found = SetCheck(dict(doc), s, decoded, off, draft=s.no in drafts)
        if s.has_boxes() and not decoded.consistent and s.first_serve is not None:
            found.repairs = sh.repairs(s, off)
        out.append(found)
    return out


def trusted(checks: Sequence[SetCheck], official: Sequence[tuple[int, int]] | None) -> bool:
    """The score comes from the sheet (the setup's score step is done): every set with an
    official score is typed, consistent and used; without an official score, every typed set."""
    by_no = {c.no: c for c in checks if c.status != "empty"}
    wanted = range(1, len(official) + 1) if official else sorted(by_no)
    if not wanted:
        return False
    return all(no in by_no and by_no[no].use(True) == "path" for no in wanted)


# --- storage ------------------------------------------------------------------------------------


def drafts_of(row: Scoresheet | None) -> set[int]:
    """The sets read from a photo that wait for a coach's confirmation (SS4)."""
    if row is None or not row.read:
        return set()
    present = {int(doc.get("no", 0)) for doc in row.sets or []}
    return {
        int(no)
        for no, state in (row.read.get("sets") or {}).items()
        if isinstance(state, dict) and state.get("state") == "draft" and int(no) in present
    }


def stored(db: Session, video_id: uuid.UUID, *, deleted: bool = False) -> Scoresheet | None:
    row = db.scalars(select(Scoresheet).where(Scoresheet.video_id == video_id)).first()
    if row is None or (row.deleted_at is not None and not deleted):
        return None
    return row


class Stale(Exception):
    def __init__(self, row: Scoresheet) -> None:
        super().__init__("stale sheet")
        self.row = row


def save(db: Session, video: Video, body: SheetIn, actor: int, now: datetime) -> Scoresheet:
    """Keep the typed sheet. A change based on an older version than the stored one is stale
    (two people typing the same sheet), unless it asks for exactly what is stored."""
    row = stored(db, video.id, deleted=True)
    sets = [s.model_dump(mode="json") for s in body.sets]
    if row is None:
        row = Scoresheet(
            id=uuid.uuid4(), video_id=video.id, created_by=actor, version=0, source="typed"
        )
        db.add(row)
    elif row.deleted_at is None and not row.sets:
        # Photos only so far (SS3): nothing typed to overwrite; the typist entered the sheet.
        row.created_by = actor
        row.created_at = now
    elif row.deleted_at is None and row.version > body.base_version:
        if row.sets == sets and row.use_in_scoring == body.use_in_scoring:
            return row
        raise Stale(row)
    if row.deleted_at is not None:
        row.read = None  # a removed sheet typed anew: its old reading is gone with it
    changed = (
        row.sets != sets
        or row.use_in_scoring != body.use_in_scoring
        or row.deleted_at is not None
        or row.version == 0
    )
    row.sets = sets
    row.use_in_scoring = body.use_in_scoring
    row.game_id = video.game_id
    row.status = "draft" if drafts_of(row) else "entered"
    row.deleted_at = None
    if changed:
        row.updated_by = actor
        row.version = (row.version or 0) + 1
    db.flush()
    return row


def typed(row: Scoresheet | None) -> bool:
    """Something was typed in: a row with only photos (SS3) has no sets yet."""
    return row is not None and bool(row.sets)


def ensure_row(db: Session, video: Video, actor: int) -> Scoresheet:
    """The video's sheet row, for a photo before anything is typed: a new row without sets, or
    a removed one brought back empty (its typed sets stay in its revisions)."""
    row = stored(db, video.id, deleted=True)
    if row is None:
        row = Scoresheet(
            id=uuid.uuid4(),
            video_id=video.id,
            game_id=video.game_id,
            created_by=actor,
            updated_by=actor,
            version=0,
            source="typed",
            sets=[],
        )
        db.add(row)
    elif row.deleted_at is not None:
        row.deleted_at = None
        row.sets = []
        row.read = None
        row.status = "entered"
        row.updated_by = actor
        row.version += 1
    db.flush()
    return row


def remove(db: Session, video: Video, actor: int, now: datetime) -> bool:
    row = stored(db, video.id)
    if row is None:
        return False
    row.deleted_at = now
    row.updated_by = actor
    row.version += 1
    db.flush()
    return True


def official_sets(db: Session, video: Video) -> list[tuple[int, int]] | None:
    from wieplay.core import scoring

    return scoring.official_sets(db, video)


# --- the sets: first server, lineups, starting rotation (research §4.3) ------------------------


def _setters(db: Session, video: Video) -> set[int]:
    return set(
        db.scalars(
            select(VideoRoster.number).where(
                VideoRoster.video_id == video.id,
                VideoRoster.side == "us",
                VideoRoster.position == "setter",
            )
        )
    )


def lineup_of(values: Sequence[int | None] | None) -> list[int | None] | None:
    if not values or all(v is None for v in values):
        return None
    return list(values)


def setter_zone(lineup: Sequence[int | None] | None, setters: set[int]) -> int | None:
    """Our setter's zone at the first serve: the one setter of the roster among the six (the
    sheet's positions I-VI are the zones at the first serve)."""
    if not lineup:
        return None
    found = [zone for zone, n in enumerate(lineup, start=1) if n is not None and n in setters]
    return found[0] if len(found) == 1 else None


@dataclass(frozen=True)
class Conflict:
    """A set's field a coach entered that the sheet says otherwise (flagged, never changed)."""

    set_no: int
    field: str
    coach: Any
    sheet: Any

    def as_json(self) -> dict[str, Any]:
        return {"set": self.set_no, "field": self.field, "coach": self.coach, "sheet": self.sheet}


def fill_periods(
    db: Session, video: Video, checks: Sequence[SetCheck], sheet_on: bool
) -> list[Conflict]:
    """The video's sets (by number) get what a used, consistent sheet set says where a field is
    empty: who served first, our six and theirs, our setter's zone. A different value a coach
    entered is kept and returned as a conflict. A set filled in this way counts as the coach's
    (the scoring no longer replaces it)."""
    periods = {
        p.no: p
        for p in db.scalars(
            select(Period).where(Period.video_id == video.id, Period.deleted_at.is_(None))
        )
    }
    setters = _setters(db, video) if periods else set()
    conflicts: list[Conflict] = []
    for c in checks:
        period = periods.get(c.no)
        if period is None or c.use(sheet_on) != "path":
            continue
        s = c.sheet
        values: dict[str, Any] = {
            "first_serve": s.first_serve,
            "start_lineup": lineup_of(s.lineup.get("us")),
            "start_lineup_them": lineup_of(s.lineup.get("them")),
            "start_rotation": setter_zone(s.lineup.get("us"), setters),
        }
        if period.start_us or period.start_them:
            values["first_serve"] = None  # a set the video joins late: not the first serve
        for name, value in values.items():
            if value is None:
                continue
            have = getattr(period, name)
            if have is None:
                setattr(period, name, value)
            elif have != value:
                conflicts.append(Conflict(c.no, name, have, value))
    db.flush()
    return conflicts


# --- in the scoring (research §4.2) --------------------------------------------------------------


@dataclass(frozen=True)
class SheetUse:
    checks: list[SetCheck]
    on: bool
    # The drafts (SS4): what the reader saw on the sheet's photos, and per set the reading's
    # state (which half is us, what it proposed).
    readings: Any = None
    read: Mapping[str, Any] = field(default_factory=dict)

    def by_no(self) -> dict[int, SetCheck]:
        return {c.no: c for c in self.checks}


def for_scoring(db: Session, video: Video) -> SheetUse | None:
    """The video's sheet as the scoring takes it, or None."""
    if video.sport not in SPORTS:
        return None
    row = stored(db, video.id)
    if row is None or not row.sets:
        return None
    drafts = drafts_of(row)
    readings = None
    if drafts:
        from wieplay.core import sheet_photos, sheet_reading

        readings = sheet_reading.readings_of(sheet_photos.live(db, row))
    return SheetUse(
        check(row.sets, official_sets(db, video), drafts),
        row.use_in_scoring,
        readings,
        dict((row.read or {}).get("sets") or {}),
    )


def path_of(points: Sequence[sh.Point], cancelled: Sequence[sh.Cancelled]) -> sc.SheetPath:
    scores = [(0, 0)] + [p.score() for p in points]
    free: dict[int, int] = {}
    for c in cancelled:
        if (c.us, c.them) in scores:
            k = scores.index((c.us, c.them))
            free[k] = free.get(k, 0) + c.rallies
    return sc.SheetPath(
        tuple(p.winner for p in points),
        tuple(p.server for p in points),
        tuple(p.penalty for p in points),
        free,
    )


def _from(path: sc.SheetPath, start: tuple[int, int]) -> sc.SheetPath | None:
    """The path from this score on (a video that joins the set late), None when the sheet
    never had that score."""
    if start == (0, 0):
        return path
    a = b = 0
    for k, w in enumerate(path.winners):
        if (a, b) == start:
            return sc.SheetPath(
                path.winners[k:],
                path.servers[k:],
                path.penalty[k:],
                {i - k: n for i, n in path.cancelled.items() if i >= k},
            )
        a, b = a + (w == "us"), b + (w == "them")
    return None


def break_notes(s: sh.SheetSet) -> tuple[sc.BreakNote, ...]:
    """A set's timeouts, substitutions (and returns) and sanctions with their scores (us, them):
    "score at this break" evidence for the joint decode."""
    out: list[sc.BreakNote] = []
    for k, t in enumerate(s.timeouts):
        out.append(sc.BreakNote("timeout", t.side, t.score(), index=k))
    for k, u in enumerate(s.subs):
        for kind, score in (("sub", u.at), ("sub_back", u.back)):
            if score is None:
                continue
            us_them = (score[0], score[1]) if u.side == "us" else (score[1], score[0])
            out.append(sc.BreakNote(kind, u.side, us_them, index=k))  # type: ignore[arg-type]
    for k, x in enumerate(s.sanctions):
        out.append(sc.BreakNote("sanction", x.side, x.score(), x.kind, index=k))
    return tuple(out)


def penalties_of(s: sh.SheetSet) -> dict[tuple[int, int], sc.Side]:
    """The penalty points (P, DP) of a set that is not lined up as a path: the score when the
    sanction was given → the team the point went to (a point without a rally)."""
    return {x.score(): sh.other(x.side) for x in s.sanctions if x.kind in sh.POINT_KINDS}


def cells_of(use: SheetUse, c: SetCheck) -> sc.BoxCells | None:
    """A draft set's service boxes as the reader saw them (wieplay/ml/scoring.py `BoxCells`),
    the boxes a coach corrected in the draft as typed; None without a reading."""
    from wieplay.core import sheet_reading
    from wieplay.ml import sheet_read as rd

    readings = use.readings
    if readings is None or not any(k.startswith(f"s{c.no}.") for k in readings.cells):
        return None
    state = use.read.get(str(c.no)) or {}
    us_half = state.get("us_half")
    if us_half not in ("L", "R", "C"):
        return None
    halves = rd.halves_of(us_half)
    deciding = c.sheet.deciding
    box = {
        side: rd.box_cells(readings, c.no, half, deciding, cont=deciding and half == "L")
        for side, half in halves.items()
    }
    fixed = sheet_reading.typed_boxes(c.doc, state)
    return sc.BoxCells(
        boxes={side: {pos: c.alts for pos, c in cells.items()} for side, cells in box.items()},
        fixed={
            side: {pos: v if isinstance(v, int) else None for pos, v in typed.items()}
            for side, typed in fixed.items()
        },
        deciding=deciding,
        first_cost={first: rd.first_cost(readings, c.no, halves, first, box) for first in sh.SIDES},
        weight=sc.W_READ,
    )


def apply(use: SheetUse, set_ins: list[sc.SetIn]) -> list[sc.SetIn]:
    """The sets with the sheet: a consistent set's path (its first server too), a doubtful
    set's winners as a hint, a draft's boxes as read; their timeouts, substitutions and
    sanctions. A coach's first server that differs from the sheet's makes the set a hint (the
    coach ranks first; flagged on the sheet page)."""
    from dataclasses import replace

    by_no = use.by_no()
    out = []
    for st in set_ins:
        c = by_no.get(st.no)
        how = c.use(use.on) if c is not None else None
        if c is None or how is None:
            out.append(st)
            continue
        notes = break_notes(c.sheet)
        if how == "read":
            cells = cells_of(use, c)
            if cells is None:
                out.append(st)
                continue
            first = st.first_serve
            state = use.read.get(str(c.no)) or {}
            proposed = state.get("proposed") or {}
            typed_first = c.doc.get("first_serve")
            if (
                first is None
                and typed_first in sh.SIDES
                and typed_first != proposed.get("first_serve")
            ):
                first = typed_first  # a coach set the draft's first server
            out.append(
                replace(
                    st,
                    cells=cells,
                    notes=notes,
                    penalties=penalties_of(c.sheet),
                    first_serve=first,
                )
            )
            continue
        path = path_of(c.decoded.points, c.sheet.cancelled)
        start = (st.start_us, st.start_them)
        cut = _from(path, start)
        first = cut.servers[0] if cut and cut.servers else None
        coach_agrees = st.first_serve is None or st.first_serve == first
        if how == "path" and cut is not None and first is not None and coach_agrees:
            out.append(replace(st, sheet=cut, first_serve=first, notes=notes))
            continue
        # By score: a late start fits too.
        out.append(replace(st, sheet_hint=path.hint(), penalties=penalties_of(c.sheet)))
    return out


def write(match: sc.MatchOut, rallies: Sequence[Segment]) -> None:
    """What the sheet says about each rally it was lined up with (cleared first)."""
    by_key = {str(r.id): r for r in rallies}
    for rally in rallies:
        rally.sheet_point = rally.sheet_won_by = rally.sheet_serving = None
    for so in match.sets:
        if so.sheet != "path":
            continue
        for out in so.rallies:
            found = by_key.get(out.key)
            if found is not None and out.sheet_point is not None:
                found.sheet_point = out.sheet_point
                found.sheet_won_by = out.won_by
                found.sheet_serving = out.serving


def metrics(use: SheetUse, set_ins: Sequence[sc.SetIn], match: sc.MatchOut) -> dict[str, Any]:
    """What the sheet did in this scoring (research §4.2): per set how it was used, its points
    lined up with a rally, penalties and missed rallies (points without one), rallies without a
    point, disagreements with the coach and the log; the log's and the coach's winners against
    the sheet's."""
    sets: dict[str, Any] = {}
    agree_log = told_log = agree_coach = told_coach = 0
    for st, so in zip(set_ins, match.sets, strict=True):
        c = use.by_no().get(st.no)
        if c is None:
            continue
        aligned = sum(1 for k in so.point_rally if k is not None)
        sheet_rallies = {r.key: r for r in st.rallies}
        if so.sheet == "path":
            for out in so.rallies:
                r = sheet_rallies.get(out.key)
                if r is None or not out.point:
                    continue
                if r.log in ("us", "them") and r.log_p > 0:
                    told_log += 1
                    agree_log += int(r.log == out.won_by)
                if r.won_fact is not None:
                    told_coach += 1
                    agree_coach += int(r.won_fact == out.won_by)
        sets[str(st.no)] = {
            "status": c.status,
            "use": so.sheet,
            "draft": c.draft,
            "points": len(c.decoded.points),
            "aligned": aligned if so.sheet == "path" else None,
            "penalties": so.penalties if so.sheet == "path" else None,
            "missed": so.missed if so.sheet == "path" else None,
            "extra": so.extra,
            "flagged": so.flagged,
            "sheet_flags": sum(1 for r in so.rallies if r.note == "sheet"),
        }
    return {
        "on": use.on,
        "sets": sets,
        "sets_lined_up": sum(1 for s in sets.values() if s["use"] == "path"),
        "sets_hint": sum(1 for s in sets.values() if s["use"] == "hint"),
        "sets_read": sum(1 for s in sets.values() if s["use"] == "read"),
        "vs_log": {"agree": agree_log, "told": told_log},
        "vs_coach": {"agree": agree_coach, "told": told_coach},
    }


# --- timeouts as breaks (research §4.4) ----------------------------------------------------------

IMPORT_SYSTEM = "scoresheet"


def timeout_gaps(
    use: SheetUse, match: sc.MatchOut, rallies: Sequence[Segment]
) -> list[tuple[int, int, str, str]]:
    """(start, end, team, ref) of each of the sheet's timeouts in a lined-up set: the gap after
    the point that made its score (the rally of that point, else the last one before it with a
    rally) up to the next rally."""
    spans = {str(r.id): (r.start_ms, r.end_ms) for r in rallies}
    order = sorted(spans.values())
    out: list[tuple[int, int, str, str]] = []
    by_no = use.by_no()
    for so in match.sets:
        c = by_no.get(so.no)
        if c is None or so.sheet != "path" or not so.point_rally:
            continue
        offset = len(c.decoded.points) - len(so.point_rally)  # a set the video joined late
        for k, (t, at) in enumerate(zip(c.sheet.timeouts, c.decoded.timeout_at, strict=True)):
            if at is None:
                continue
            idx = at - offset  # points of the lined-up path played before the timeout
            before = next(
                (so.point_rally[i] for i in range(min(idx, len(so.point_rally)) - 1, -1, -1)
                 if so.point_rally[i] is not None),
                None,
            )  # fmt: skip
            if before is None:
                continue
            start = spans[before][1]
            later = [s for s, _ in order if s > start]
            if not later:
                continue
            out.append((start, later[0], t.side, f"set{so.no}:to{k + 1}"))
    return out


# --- for the page -------------------------------------------------------------------------------


def previous_lineup(db: Session, video: Video) -> list[int | None] | None:
    """Our first six of the team's last match with a sheet before this one (most sets start the
    same way)."""
    row = db.execute(
        select(Scoresheet.sets)
        .join(Video, Video.id == Scoresheet.video_id)
        .where(
            Video.lineage_id == video.lineage_id,
            Video.id != video.id,
            Video.recorded_on <= video.recorded_on,
            Video.status != "deleted",
            Scoresheet.deleted_at.is_(None),
            func.jsonb_array_length(Scoresheet.sets) > 0,  # not a row with photos only
        )
        .order_by(Video.recorded_on.desc(), Video.created_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    for s in row[0] or []:
        lineup = s.get("lineup_us") if isinstance(s, dict) else None
        if lineup and any(n is not None for n in lineup):
            return list(lineup)
    return None


def member_name(db: Session, member_id: int | None) -> str | None:
    if member_id is None:
        return None
    m = db.get(WdMember, member_id)
    return m.display_name if m else None


def game_of(db: Session, video: Video) -> WdGame | None:
    return db.get(WdGame, video.game_id) if video.game_id is not None else None
