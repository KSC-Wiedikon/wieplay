"""Tags, rallies, sets and the score (PLAN §6.6, §6.7, §9.4).

Writes are idempotent upserts by client-generated id: the browser's outbox can resend any
change after a network loss (PLAN §9.6). Deletes are soft. Every change is recorded by the
database trigger `wieplay_write_revision`, attributed through the transaction setting
`wieplay.actor` (set_actor).
"""

from __future__ import annotations

import uuid
from bisect import bisect_left
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, delete, func, select, text
from sqlalchemy.orm import Session

from wieplay.core.models import (
    Event,
    Period,
    Segment,
    Video,
    VideoRoster,
    WdTeam,
)
from wieplay.core.taxonomy import Taxonomy

Side = Literal["us", "them"]
# What a tag's review says (PLAN §10, since 20.10.2026 "WiePlay assigns everything"):
# - auto: WiePlay's, sure; it counts and nobody needs to look at it;
# - check: WiePlay's, unsure; it counts too, marked "to check";
# - confirmed / corrected: a person's answer (it stays when WiePlay runs again);
# - rejected: a person said no (soft-deleted, a hard negative for training);
# - merged: a rally a coach folded into another one (merged_into); neither a rally nor a
#   "not a rally" for training, the surviving one carries the label;
# - unreviewed: before 20.10.2026 a model's open suggestion (migration 0029 turned those into
#   auto or check); since then only on a person's own tags (source manual or import), where
#   the review means nothing. From a client on a model's tag it means "back to WiePlay's".
Review = Literal["auto", "check", "unreviewed", "confirmed", "corrected", "rejected", "merged"]
# WiePlay's own review states: nobody answered the tag yet (a rerun may replace it).
MODEL_OWN = ("auto", "check", "unreviewed")
HUMAN_REVIEWS = ("confirmed", "corrected")
GONE = ("rejected", "merged")
# A break or a set WiePlay labelled is sure from this confidence on (gaps.py: a set break
# 0.9, a warm-up over two minutes 0.95; propose_sets: 0.95 after a long break).
GAP_SURE = 0.9
SET_SURE = 0.9
GAP_KINDS = ("warmup", "set_break", "timeout", "stoppage", "substitution", "other_dead")
# A serve tag may sit slightly before the rally marker (the toss).
SERVE_SLACK_MS = 2000
# substitution: a coach's label only (the review's "Sub"); the model calls such a break a stoppage.
SegmentKind = Literal[
    "rally",
    "possession",
    "timeout",
    "set_break",
    "warmup",
    "stoppage",
    "substitution",
    "other_dead",
]


MAX_MEDIA_MS = 24 * 3600 * 1000  # no video is longer than a day
MAX_FRAME = 10_000_000
# Per video, to keep every write bounded (a long match has well under 2000 tags).
MAX_EVENTS, MAX_SEGMENTS, MAX_PERIODS = 5000, 2000, 9


class _Change(BaseModel):
    """Every change names the version it was based on: 0 for a new item, else the version
    the browser last saw. A change based on an older version than the server's is
    skipped and the current item returned, so a late retry never overwrites newer work
    (or undeletes a tag)."""

    id: uuid.UUID
    base_version: int = Field(default=0, ge=0)
    deleted: bool = False


class EventIn(_Change):
    code: str = Field(min_length=1, max_length=8)
    t_ms: int = Field(ge=0, le=MAX_MEDIA_MS)
    frame: int | None = Field(default=None, ge=0, le=MAX_FRAME)
    time_precision: Literal["live", "adjusted", "frame"] = "live"
    side: Side
    number: int | None = Field(default=None, ge=0, le=99)
    attributes: dict[str, Any] = Field(default_factory=dict)
    segment_id: uuid.UUID | None = None
    clip_pre_ms: int | None = Field(default=None, ge=0, le=60_000)
    clip_post_ms: int | None = Field(default=None, ge=0, le=60_000)
    note: str | None = Field(default=None, max_length=500)
    review: Review = "unreviewed"


class SegmentIn(_Change):
    kind: SegmentKind
    start_ms: int = Field(ge=0, le=MAX_MEDIA_MS)
    end_ms: int = Field(ge=0, le=MAX_MEDIA_MS)
    # Who served is derived (serve tag, the serve called in review, previous winner, the set's
    # first server). `serving_manual`: the serving team a coach called; left out, it stays.
    won_by: Side | None = None
    serving_manual: Side | None = None
    review: Review = "unreviewed"
    # With review "merged": the segment of the same video it was merged into.
    merged_into: uuid.UUID | None = None

    @model_validator(mode="after")
    def _order(self) -> SegmentIn:
        if self.end_ms <= self.start_ms:
            raise ValueError("a segment ends after it starts")
        return self


class PeriodIn(_Change):
    no: int = Field(ge=1, le=9)
    start_ms: int = Field(ge=0, le=MAX_MEDIA_MS)
    end_ms: int | None = Field(default=None, ge=0, le=MAX_MEDIA_MS)
    start_us: int = Field(default=0, ge=0, le=99)
    start_them: int = Field(default=0, ge=0, le=99)
    first_serve: Side | None = None
    our_court_side_at_start: Literal["near", "far"] | None = None
    side_switch_at_score: int | None = Field(default=None, ge=1, le=99)
    # Left out, these stay as they are: our setter's zone at the first serve, and the answer
    # to a set the model proposed.
    start_rotation: int | None = Field(default=None, ge=1, le=6)
    # Left out, it stays too: our six shirt numbers in zones I-VI at the first serve (null in
    # a zone: not entered).
    start_lineup: list[int | None] | None = Field(default=None, min_length=6, max_length=6)
    review: Review | None = None

    @model_validator(mode="after")
    def _order(self) -> PeriodIn:
        if self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("a set or quarter ends after it starts")
        if self.start_lineup is not None:
            numbers = [n for n in self.start_lineup if n is not None]
            if any(isinstance(n, bool) or not 0 <= n <= 99 for n in numbers):
                raise ValueError("a lineup holds shirt numbers 0-99")
            if len(numbers) != len(set(numbers)):
                raise ValueError("a player stands in one zone of the lineup")
            if not numbers:
                self.start_lineup = None  # nothing entered
        return self


class TaggingError(ValueError):
    pass


def stands(sure_only: bool = False) -> Any:
    """Segments that count (with the live filter of the caller): everything but what a person
    rejected or merged (PLAN §10: WiePlay's rallies and breaks count from the start, the
    unsure ones marked "to check"). `sure_only`: without those still to check (a rally whose
    winner alone is unsure still counts; its winner is left out, stats_input.rally_in)."""
    out: Any = Segment.review.not_in(GONE)
    if sure_only:
        out = out & (Segment.review != "check")
    return out


def event_stands(sure_only: bool = False) -> Any:
    """Tags that count (with the live filter of the caller): everything but what a person
    rejected (PLAN §10.4a). `sure_only`: without the ones still to check, nor WiePlay's
    guesses it did not ask about (core/actions.choose_checks)."""
    out: Any = Event.review.not_in(GONE)
    if sure_only:
        guess = (Event.source == "model") & (Event.review == "auto") & Event.model_sure.is_(False)
        out = out & (Event.review != "check") & ~guess
    return out


def event_fact() -> Any:
    """Tags a person stands behind: made by hand or imported, or WiePlay's that a person
    accepted or corrected. What the automatic scoring takes as the coach's (a serve tag names
    the server): WiePlay's own tags are its output, never its input."""
    return Event.source.in_(("manual", "import")) | Event.review.in_(HUMAN_REVIEWS)


def period_stands(sure_only: bool = False) -> Any:
    out: Any = Period.review.not_in(GONE)
    if sure_only:
        out = out & (Period.review != "check")
    return out


def rally_to_check() -> Any:
    """A live rally that waits for a person (with the live filter of the caller): WiePlay's
    unsure rally, or any rally whose winner the scoring is unsure of and nobody gave
    (a person's own rally too), unless a person answered it."""
    return (
        (Segment.kind == "rally")
        & Segment.review.not_in(GONE)
        & (
            (Segment.review == "check")
            | (
                Segment.score_sure.is_(False)
                & Segment.won_by.is_(None)
                & Segment.review.not_in(HUMAN_REVIEWS)
            )
        )
    )


def gap_to_check() -> Any:
    return Segment.kind.in_(GAP_KINDS) & (Segment.source == "model") & (Segment.review == "check")


def event_to_check() -> Any:
    return (Event.source == "model") & (Event.review == "check")


def period_to_check() -> Any:
    return (Period.source == "model") & (Period.review == "check")


TO_CHECK_KINDS = ("rallies", "breaks", "sets", "actions")


def to_check_counts(db: Session, video_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    """Per video, what still waits for a person ("N still to check"): rallies, breaks, sets and
    actions (the players are the identify queue's, core/identify.py)."""
    out: dict[uuid.UUID, dict[str, int]] = {v: dict.fromkeys(TO_CHECK_KINDS, 0) for v in video_ids}
    if not video_ids:
        return out
    queries = (
        ("rallies", Segment, rally_to_check()),
        ("breaks", Segment, gap_to_check()),
        ("sets", Period, period_to_check()),
        ("actions", Event, event_to_check()),
    )
    for key, model, where in queries:
        rows = db.execute(
            select(model.video_id, func.count())
            .where(model.video_id.in_(video_ids), model.deleted_at.is_(None), where)
            .group_by(model.video_id)
        ).all()
        for video_id, n in rows:
            out[video_id][key] = int(n)
    return out


def to_check_total(db: Session, video_ids: list[uuid.UUID]) -> int:
    return sum(sum(c.values()) for c in to_check_counts(db, video_ids).values())


def model_state(sure: bool | None) -> str:
    """WiePlay's review state for a tag of its own: auto when sure, else to check."""
    return "auto" if sure else "check"


def keep_model_state(row: Segment | Event | Period, sure: bool | None) -> None:
    """Follow WiePlay's sureness on a tag nobody answered (a person's answer stays). Not a
    person's change: the revision trigger takes it as derived (migration 0029)."""
    if row.source == "model" and row.review in MODEL_OWN and row.deleted_at is None:
        row.review = model_state(sure)


IN_USE = "this id is already in use"


def set_actor(db: Session, member_id: int) -> None:
    """Attribute this transaction's revisions to the member (read by the trigger)."""
    db.execute(text("SELECT set_config('wieplay.actor', :actor, true)"), {"actor": str(member_id)})


def ensure_roster(db: Session, video: Video) -> list[VideoRoster]:
    """The video's roster snapshot; on first use, taken from the team's roster now."""
    rows = list(db.scalars(select(VideoRoster).where(VideoRoster.video_id == video.id)))
    if rows:
        return rows
    team = db.get(WdTeam, video.team_id)
    assert team is not None
    from wieplay.core import team_memory

    # The roster's numbers; one member per number (a guest or staff member sharing a player's
    # number gives way), and the coaches' fixes on the team page (PLAN §10.5b).
    for link in team_memory.roster_links(db, team):
        rows.append(
            VideoRoster(
                video_id=video.id,
                side="us",
                number=link.number,
                member_id=link.member_id,
                position=link.position,
                is_libero=link.libero,
            )
        )
    db.add_all(rows)
    db.flush()
    return rows


def _member_for(db: Session, video: Video, side: str, number: int | None) -> int | None:
    if side != "us" or number is None:
        return None
    return db.scalar(
        select(VideoRoster.member_id).where(
            VideoRoster.video_id == video.id,
            VideoRoster.side == "us",
            VideoRoster.number == number,
        )
    )


def _check_attributes(tax: Taxonomy, code: str, attributes: dict[str, Any]) -> dict[str, Any]:
    allowed = {"grade", "zone_from", "zone_to"} if tax.zones else {"grade"}
    if set(attributes) - allowed:
        raise TaggingError(f"unknown attributes: {sorted(set(attributes) - allowed)}")
    grade = attributes.get("grade")
    if grade is not None and grade not in tax.grades:
        raise TaggingError(f"unknown grade {grade!r}")
    if grade is not None and not tax.skills[code].graded:
        raise TaggingError(f"{code} takes no grade")
    for key in ("zone_from", "zone_to"):
        zone = attributes.get(key)
        if zone is not None and (isinstance(zone, bool) or zone not in tax.zones):
            raise TaggingError(f"{key} must be one of {list(tax.zones)}")
    return {k: v for k, v in attributes.items() if v is not None}


def _snapshot(row: Event | Segment | Period, fields: tuple[str, ...]) -> tuple[Any, ...]:
    return (*(getattr(row, f, None) for f in fields), row.deleted_at is not None)


# What a tagger sets. member_id (from the roster) and segment_id (the rally by time) are
# derived by the server.
EVENT_FIELDS = (
    "code", "t_ms", "frame", "time_precision", "side", "number", "attributes",
    "clip_pre_ms", "clip_post_ms", "note", "review",
)  # fmt: skip
SEGMENT_FIELDS = ("kind", "start_ms", "end_ms", "won_by", "review", "merged_into",
                  "serving_manual")  # fmt: skip
PERIOD_FIELDS = (
    "no", "start_ms", "end_ms", "start_us", "start_them", "first_serve",
    "our_court_side_at_start", "side_switch_at_score", "start_rotation", "start_lineup",
    "review",
)  # fmt: skip
# Fields a client may leave out to keep them as they are.
KEPT_WHEN_ABSENT = ("serving_manual", "start_rotation", "start_lineup", "review")


def _bump(
    row: Event | Segment | Period, before: tuple[Any, ...] | None, after: tuple[Any, ...]
) -> None:
    """A resent change that changes nothing keeps its version (and writes no revision)."""
    if before != after:
        row.version = (row.version or 0) + 1


def _stamp(row: Event | Segment | Period, deleted: bool, now: datetime) -> None:
    if deleted and row.deleted_at is None:
        row.deleted_at = now
    elif not deleted:
        row.deleted_at = None


def upsert_event(
    db: Session, video: Video, tax: Taxonomy, item: EventIn, actor: int, now: datetime
) -> Event:
    if item.code not in tax.skills:
        raise TaggingError(f"unknown skill {item.code!r}")
    if video.duration_ms is not None and item.t_ms > video.duration_ms + 1000:
        raise TaggingError("tag time beyond the end of the video")
    attributes = _check_attributes(tax, item.code, item.attributes)
    member_id = _member_for(db, video, item.side, item.number)
    with db.no_autoflush:  # one INSERT with the finished row, not a half-built one
        return _apply_event(db, video, tax, item, attributes, member_id, actor, now)


class Stale(Exception):
    """The change was based on an older version; the row is left as it is."""

    def __init__(self, row: Event | Segment | Period) -> None:
        super().__init__("stale change")
        self.row = row


def _check_base(
    row: Event | Segment | Period | None,
    video: Video,
    item: _Change,
    intended: tuple[Any, ...],
    fields: tuple[str, ...],
) -> bool:
    """False when there is nothing to do. A change based on an older version is stale,
    unless it asks for exactly what the item already is (a resend after a lost answer)."""
    if row is None:
        return True
    if row.video_id != video.id:
        raise TaggingError(IN_USE)
    if (row.version or 0) > item.base_version:
        if _snapshot(row, fields) == intended:
            return False
        raise Stale(row)
    return True


def _intended(item: _Change, fields: tuple[str, ...], **overrides: Any) -> tuple[Any, ...]:
    return (*(overrides.get(f, getattr(item, f, None)) for f in fields), item.deleted)


def _kept(item: _Change, row: Segment | Period | None) -> dict[str, Any]:
    """The values of fields the client left out: the row's (nothing for a new row)."""
    return {
        f: getattr(row, f, None) if row is not None else None
        for f in KEPT_WHEN_ABSENT
        if f not in item.model_fields_set and hasattr(item, f)
    }


def _apply_event(
    db: Session,
    video: Video,
    tax: Taxonomy,
    item: EventIn,
    attributes: dict[str, Any],
    member_id: int | None,
    actor: int,
    now: datetime,
) -> Event:
    row = db.get(Event, item.id)
    if not _check_base(
        row, video, item, _intended(item, EVENT_FIELDS, attributes=attributes), EVENT_FIELDS
    ):
        assert row is not None
        return row
    before = None if row is None else _snapshot(row, EVENT_FIELDS)
    if row is None:
        row = Event(id=item.id, video_id=video.id, version=0, created_by=actor, source="manual")
        db.add(row)
    review = _model_event_review(row, item, attributes)
    row.sport, row.taxonomy_version = tax.sport, tax.version
    row.code, row.t_ms, row.frame, row.time_precision = (
        item.code,
        item.t_ms,
        item.frame,
        item.time_precision,
    )
    row.side, row.number = item.side, item.number
    row.member_id = member_id
    row.attributes = attributes
    row.clip_pre_ms, row.clip_post_ms, row.note = item.clip_pre_ms, item.clip_post_ms, item.note
    row.review = review
    _stamp(row, item.deleted, now)
    after = _snapshot(row, EVENT_FIELDS)
    if before != after:
        row.updated_by = actor
    _bump(row, before, after)
    return row


def _answer(row_review: str, asked: str, edited: bool, own: str) -> str:
    """The review state of WiePlay's tag after a person's change (PLAN §11: corrections are
    data; the revision tables keep before and after). `asked`: the state the client sent,
    `own`: WiePlay's state for the tag (auto or check), `edited`: the person changed what
    WiePlay said.

    A client that names another state than the tag has gets it: accept ("confirmed"),
    correct ("corrected"), reopen (auto, check or the older "unreviewed": WiePlay's own state
    again, the undo of an answer). Confirming while changing it is a correction. A client
    that passes the state through unchanged (one that does not know about reviews, like the
    tagging page) still leaves a trace: an edit corrects. WiePlay's two states count as one
    here: a client that saw "auto" before a scoring made it "check" answers nothing."""
    if edited and asked == "confirmed":
        return "corrected"
    explicit = asked != row_review and not (asked in MODEL_OWN and row_review in MODEL_OWN)
    if explicit:
        return own if asked in MODEL_OWN else asked
    if edited and asked in MODEL_OWN:
        return "corrected"
    return own if asked in MODEL_OWN else asked


def _model_event_review(row: Event, item: EventIn, attributes: dict[str, Any]) -> str:
    """Review state of WiePlay's action after a person's change (`_answer`): a delete rejects
    it (kept as a hard negative), undoing that reopens it; another skill, side, grade, zone or
    time corrects it. Naming the player of an action WiePlay left without one (the coach's `7`
    in the review) is information it asked for, not a correction. Manual and imported tags
    keep what the client sends."""
    if row.source != "model":
        return item.review
    own = model_state(row.model_sure)
    if item.deleted:
        return "rejected"
    if row.deleted_at is not None:  # undo of a rejection
        return own if item.review in ("rejected", *MODEL_OWN) else item.review
    edited = (row.code, row.side, row.t_ms, row.attributes or {}) != (
        item.code,
        item.side,
        item.t_ms,
        attributes,
    ) or (row.number is not None and row.number != item.number)
    return _answer(row.review, item.review, edited, own)


def upsert_segment(
    db: Session, video: Video, item: SegmentIn, actor: int, now: datetime
) -> Segment:
    row = db.get(Segment, item.id)
    kept = _kept(item, row)
    if not _check_base(row, video, item, _intended(item, SEGMENT_FIELDS, **kept), SEGMENT_FIELDS):
        assert row is not None
        return row
    if video.duration_ms is not None and item.start_ms > video.duration_ms + 1000:
        raise TaggingError("segment beyond the end of the video")
    before = None if row is None else _snapshot(row, SEGMENT_FIELDS)
    if row is None:
        row = Segment(id=item.id, video_id=video.id, version=0, created_by=actor, source="manual")
        db.add(row)
    if item.merged_into is not None:
        target = db.get(Segment, item.merged_into)
        if target is None or target.video_id != video.id or target.id == item.id:
            raise TaggingError("merged_into must be another segment of this video")
    review = _model_review(row, item)
    row.kind, row.start_ms, row.end_ms = item.kind, item.start_ms, item.end_ms
    row.won_by, row.review = item.won_by, review
    row.serving_manual = kept.get("serving_manual", item.serving_manual)
    row.merged_into = item.merged_into if review == "merged" else None
    _stamp(row, item.deleted, now)
    after = _snapshot(row, SEGMENT_FIELDS)
    if before != after:
        row.updated_by = actor
    _bump(row, before, after)
    return row


def segment_sure(row: Segment) -> bool:
    """Whether WiePlay is sure of its rally (the joint decode, PLAN §10.3) or break label."""
    if row.kind == "rally":
        return bool(row.score_sure)
    return row.confidence is not None and row.confidence >= GAP_SURE


def _model_review(row: Segment, item: SegmentIn) -> str:
    """Review state of WiePlay's rally or break after a person's change (`_answer`): moving a
    boundary, another kind or another winner corrects it. A delete is a rejection
    ("rejected", kept as a hard negative), unless the client says "merged" (merged into
    another rally: no label either way). Undoing a delete reopens it. Manual and imported
    segments keep whatever the client sends."""
    if row.source != "model":
        return item.review
    own = model_state(segment_sure(row))
    if item.deleted:
        return "merged" if item.review == "merged" else "rejected"
    if row.deleted_at is not None:  # undo of a rejection or a merge
        return own if item.review in ("rejected", "merged", *MODEL_OWN) else item.review
    # Taking WiePlay's winner for a rally nobody gave one ("Enter" in To check) is no change.
    took = row.won_by is None and item.won_by is not None and item.won_by == row.model_won_by
    edited = (row.kind, row.start_ms, row.end_ms) != (item.kind, item.start_ms, item.end_ms) or (
        row.won_by != item.won_by and not took
    )
    return _answer(row.review, item.review, edited, own)


def upsert_period(db: Session, video: Video, item: PeriodIn, actor: int, now: datetime) -> Period:
    with db.no_autoflush:
        return _apply_period(db, video, item, actor, now)


def _apply_period(db: Session, video: Video, item: PeriodIn, actor: int, now: datetime) -> Period:
    row = db.get(Period, item.id)
    kept = _kept(item, row)
    if row is None and kept.get("review") is None:
        kept["review"] = "unreviewed"  # a person's own set: the review means nothing
    if not _check_base(row, video, item, _intended(item, PERIOD_FIELDS, **kept), PERIOD_FIELDS):
        assert row is not None
        return row
    if video.duration_ms is not None and item.start_ms > video.duration_ms + 1000:
        raise TaggingError("set or quarter beyond the end of the video")
    before = None if row is None else _snapshot(row, PERIOD_FIELDS)
    if row is None:
        # A coach's own set replaces the sets the scoring proposed that nobody answered.
        db.execute(delete(Period).where(Period.video_id == video.id, untouched_model_sets()))
        row = Period(id=item.id, video_id=video.id, version=0, created_by=actor)
        db.add(row)
    review = _period_review(row, item, kept.get("review", item.review) or "unreviewed")
    for key in (
        "no",
        "start_ms",
        "end_ms",
        "start_us",
        "start_them",
        "first_serve",
        "our_court_side_at_start",
        "side_switch_at_score",
    ):
        setattr(row, key, getattr(item, key))
    row.start_rotation = kept.get("start_rotation", item.start_rotation)
    row.start_lineup = kept.get("start_lineup", item.start_lineup)
    row.review = review
    _stamp(row, item.deleted, now)
    _bump(row, before, _snapshot(row, PERIOD_FIELDS))
    return row


PERIOD_EDIT = ("no", "start_ms", "end_ms", "start_us", "start_them")
# What a coach fills in on a set (the model never does).
PERIOD_COACH_FIELDS = (
    "first_serve", "our_court_side_at_start", "side_switch_at_score", "start_rotation",
    "start_lineup",
)  # fmt: skip


def untouched_model_sets() -> Any:
    """Sets the scoring proposed that nobody answered or filled in: replaced freely."""
    return (
        (Period.source == "model")
        & Period.review.in_(MODEL_OWN)
        & Period.deleted_at.is_(None)
        & (Period.start_us == 0)
        & (Period.start_them == 0)
        & and_(*(getattr(Period, f).is_(None) for f in PERIOD_COACH_FIELDS))
    )


def period_sure(row: Period) -> bool:
    return row.confidence is not None and row.confidence >= SET_SURE


def _period_review(row: Period, item: PeriodIn, asked: str) -> str:
    """Review state of a set WiePlay proposed after a person's change (`_answer`): a delete
    rejects it, moving it or changing its number or starting score corrects it; filling in who
    served first or the sides does not (that is information WiePlay asks for). A person's own
    set keeps what the client sends."""
    if row.source != "model":
        return asked
    own = model_state(period_sure(row))
    if item.deleted:
        return "rejected"
    if row.deleted_at is not None:  # undo of a rejection
        return own if asked in ("rejected", *MODEL_OWN) else asked
    edited = any(getattr(row, f) != getattr(item, f) for f in PERIOD_EDIT)
    return _answer(row.review, asked, edited, own)


def rescore(db: Session, video: Video) -> None:
    """Score line from the rallies: per set, from its starting score; the rally winner
    serves next (PLAN §6.6: scores live on rallies). Basketball: from the made shots."""
    db.flush()
    if video.sport == "basketball":
        _rescore_basketball(db, video)
        return
    periods = list(
        db.scalars(
            select(Period)
            .where(Period.video_id == video.id, Period.deleted_at.is_(None))
            .order_by(Period.start_ms, Period.no)
        )
    )
    rallies = list(
        db.scalars(
            select(Segment)
            .where(
                Segment.video_id == video.id,
                Segment.kind == "rally",
                Segment.deleted_at.is_(None),
            )
            .order_by(Segment.start_ms)
        )
    )

    def period_of(rally: Segment) -> Period | None:
        found = None
        for period in periods:
            if period.start_ms <= rally.start_ms and (
                period.end_ms is None or rally.start_ms < period.end_ms
            ):
                found = period
        return found

    _link_events(db, video, rallies)

    by_period: dict[uuid.UUID | None, list[Segment]] = {}
    for rally in rallies:
        period = period_of(rally)
        rally.period_id = period.id if period else None
        by_period.setdefault(rally.period_id, []).append(rally)

    # Who served: a serve tag in the rally wins, then the serve a coach called in review; else
    # the previous rally's winner; else the set's first server. Facts only (the model's server
    # is model_serving); derived on every rescore, so a corrected rally fixes the rest.
    serves = list(
        db.execute(
            select(Event.t_ms, Event.side)
            .where(
                Event.video_id == video.id,
                Event.code == "S",
                Event.deleted_at.is_(None),
                event_fact(),  # WiePlay's serve is its output, not a fact
            )
            .order_by(Event.t_ms)
        ).all()
    )
    serve_times = [t for t, _ in serves]

    def called(rally: Segment) -> str | None:
        at = bisect_left(serve_times, rally.start_ms - SERVE_SLACK_MS)
        if at < len(serves) and serves[at][0] <= rally.end_ms:
            return str(serves[at][1])
        return rally.serving_manual

    for rally in by_period.get(None, []):
        rally.score_us_after = rally.score_them_after = None
        rally.score_source = None
        rally.serving = called(rally)

    for period in periods:
        us, them = period.start_us, period.start_them
        serving = period.first_serve
        played = by_period.get(period.id, [])
        calls = [called(r) for r in played]
        # A set the official scoresheet was lined up with (PLAN §10.3a): its points without a
        # rally (penalties, missed rallies) and its winners come from the sheet.
        sheet_set = any(r.sheet_point is not None for r in played)
        from_model = from_sheet = False
        for n, rally in enumerate(played):
            # Points the model thinks were played before it without a rally (one it missed).
            if rally.model_missed_us or rally.model_missed_them:
                us += rally.model_missed_us or 0
                them += rally.model_missed_them or 0
                if sheet_set:
                    from_sheet = True
                else:
                    from_model = True
            rally.serving = calls[n] or serving
            # The winner: the coach's; else the next rally's server (a fact); else the sheet's;
            # else the model's.
            winner = rally.won_by
            if winner is None and n + 1 < len(played):
                winner = calls[n + 1]
            fact = winner is not None
            if winner is None and rally.sheet_won_by is not None:
                winner, from_sheet = rally.sheet_won_by, True
            if winner is None and rally.model_won_by is not None:
                winner, from_model = rally.model_won_by, True
            if winner == "us":
                us += 1
            elif winner == "them":
                them += 1
            if winner is None:
                rally.score_us_after = rally.score_them_after = None
                rally.score_source = None
            else:
                rally.score_us_after, rally.score_them_after = us, them
                rally.score_source = "model" if from_model else "sheet" if from_sheet else "tags"
            serving = winner if fact else None
        us += period.model_missed_us or 0
        them += period.model_missed_them or 0
        period.final_us, period.final_them = (us, them) if played else (None, None)


def now_utc() -> datetime:
    return datetime.now(UTC)


def check_limits(
    db: Session, video: Video, event_ids: list[uuid.UUID], segment_ids: list[uuid.UUID]
) -> None:
    """Refuse a batch that would take a video past the per-video limits (new items only)."""

    def count(model: type[Event] | type[Segment], ids: list[uuid.UUID]) -> tuple[int, int]:
        have = db.scalar(select(func.count()).select_from(model).where(model.video_id == video.id))
        known = db.scalar(select(func.count()).select_from(model).where(model.id.in_(ids)))
        return int(have or 0), len(ids) - int(known or 0)

    have_events, new_events = count(Event, event_ids)
    have_segments, new_segments = count(Segment, segment_ids)
    if have_events + new_events > MAX_EVENTS or have_segments + new_segments > MAX_SEGMENTS:
        raise TaggingError("too many tags or segments for one video")


def check_periods(db: Session, video: Video) -> None:
    db.flush()
    numbers = list(
        db.scalars(
            select(Period.no).where(Period.video_id == video.id, Period.deleted_at.is_(None))
        )
    )
    if len(numbers) != len(set(numbers)):
        raise TaggingError("two sets or quarters with the same number")
    if len(numbers) > MAX_PERIODS:
        raise TaggingError("too many sets")


def _link_events(db: Session, video: Video, rallies: list[Segment]) -> None:
    """Each tag belongs to the rally it falls in, by time: the browser tags a rally that is
    still open, so the server makes the link. Derived: no new version, no revision."""
    starts = [r.start_ms for r in rallies]
    events = db.scalars(select(Event).where(Event.video_id == video.id, Event.deleted_at.is_(None)))
    for event in events:
        at = bisect_left(starts, event.t_ms + 1) - 1
        rally = rallies[at] if at >= 0 and rallies[at].end_ms >= event.t_ms else None
        segment_id = rally.id if rally is not None else None
        if event.segment_id != segment_id:
            event.segment_id = segment_id


def basketball_running_score(
    db: Session, video_id: uuid.UUID
) -> tuple[dict[uuid.UUID, tuple[int, int]], list[tuple[Period, int, int]]]:
    """Basketball keeps a running score across quarters: the first quarter's starting score
    seeds it (a recording may start mid-game) and every made shot inside a quarter adds its
    points. Returns the score right after each tag and each quarter's final."""
    from wieplay.core.taxonomy import load

    tax = load("basketball")
    periods = list(
        db.scalars(
            select(Period)
            .where(Period.video_id == video_id, Period.deleted_at.is_(None))
            .order_by(Period.start_ms, Period.no)
        )
    )
    shots = db.execute(
        select(Event.id, Event.t_ms, Event.side, Event.code, Event.attributes)
        .where(Event.video_id == video_id, Event.deleted_at.is_(None))
        .order_by(Event.t_ms)
    ).all()
    us = periods[0].start_us if periods else 0
    them = periods[0].start_them if periods else 0
    after: dict[uuid.UUID, tuple[int, int]] = {}
    finals: list[tuple[Period, int, int]] = []
    at = 0
    for n, period in enumerate(periods):
        end = period.end_ms
        if end is None and n + 1 < len(periods):
            end = periods[n + 1].start_ms
        while at < len(shots) and (end is None or shots[at][1] < end):
            event_id, t_ms, side, code, attributes = shots[at]
            points = tax.points(code, (attributes or {}).get("grade"))
            if t_ms >= period.start_ms and points:
                if side == "us":
                    us += points
                else:
                    them += points
            after[event_id] = (us, them)
            at += 1
        finals.append((period, us, them))
    return after, finals


def _rescore_basketball(db: Session, video: Video) -> None:
    """Each quarter's final is the running score at its end."""
    _, finals = basketball_running_score(db, video.id)
    for period, us, them in finals:
        period.final_us, period.final_them = us, them
