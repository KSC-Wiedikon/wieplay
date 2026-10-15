"""Tags, rallies, sets and the score (PLAN §6.6, §6.7, §9.4).

Writes are idempotent upserts by client-generated id: the browser's outbox can resend any
change after a network loss (PLAN §9.6). Deletes are soft. Every change is recorded by the
database trigger `nomal_write_revision`, attributed through the transaction setting
`nomal.actor` (set_actor).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from nomal.core.models import (
    Event,
    Period,
    Segment,
    Video,
    VideoRoster,
    WdMember,
    WdTeam,
    WdTeamMember,
)
from nomal.core.taxonomy import Taxonomy

Side = Literal["us", "them"]
Review = Literal["unreviewed", "confirmed", "corrected", "rejected"]
# A serve tag may sit slightly before the rally marker (the toss).
SERVE_SLACK_MS = 2000
SegmentKind = Literal[
    "rally", "possession", "timeout", "set_break", "warmup", "stoppage", "other_dead"
]


class EventIn(BaseModel):
    id: uuid.UUID
    code: str = Field(min_length=1, max_length=8)
    t_ms: int = Field(ge=0)
    frame: int | None = Field(default=None, ge=0)
    time_precision: Literal["live", "adjusted", "frame"] = "live"
    side: Side
    number: int | None = Field(default=None, ge=0, le=99)
    attributes: dict[str, Any] = Field(default_factory=dict)
    segment_id: uuid.UUID | None = None
    clip_pre_ms: int | None = Field(default=None, ge=0, le=60_000)
    clip_post_ms: int | None = Field(default=None, ge=0, le=60_000)
    note: str | None = Field(default=None, max_length=500)
    review: Review = "unreviewed"
    deleted: bool = False


class SegmentIn(BaseModel):
    id: uuid.UUID
    kind: SegmentKind
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    serving: Side | None = None
    won_by: Side | None = None
    review: Review = "unreviewed"
    deleted: bool = False

    @model_validator(mode="after")
    def _order(self) -> SegmentIn:
        if self.end_ms <= self.start_ms:
            raise ValueError("a segment ends after it starts")
        return self


class PeriodIn(BaseModel):
    id: uuid.UUID
    no: int = Field(ge=1, le=9)
    start_ms: int = Field(ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    start_us: int = Field(default=0, ge=0, le=99)
    start_them: int = Field(default=0, ge=0, le=99)
    first_serve: Side | None = None
    our_court_side_at_start: Literal["near", "far"] | None = None
    side_switch_at_score: int | None = Field(default=None, ge=1, le=99)
    deleted: bool = False


class TaggingError(ValueError):
    pass


def set_actor(db: Session, member_id: int) -> None:
    """Attribute this transaction's revisions to the member (read by the trigger)."""
    db.execute(text("SELECT set_config('nomal.actor', :actor, true)"), {"actor": str(member_id)})


def ensure_roster(db: Session, video: Video) -> list[VideoRoster]:
    """The video's roster snapshot; on first use, taken from the team's roster now."""
    rows = list(db.scalars(select(VideoRoster).where(VideoRoster.video_id == video.id)))
    if rows:
        return rows
    team = db.get(WdTeam, video.team_id)
    assert team is not None
    players = db.execute(
        select(WdMember)
        .join(WdTeamMember, WdTeamMember.member_id == WdMember.id)
        .where(WdTeamMember.team_id == team.id, WdTeamMember.rel == "player")
        .distinct()
    ).scalars()
    seen: set[int] = set()
    for member in players:
        if member.number is None or member.number in seen:
            continue  # without a unique number a tag cannot name them; the coach adds it
        seen.add(member.number)
        positions = [str(p).lower() for p in (member.positions or [])]
        rows.append(
            VideoRoster(
                video_id=video.id,
                side="us",
                number=member.number,
                member_id=member.id,
                position=positions[0] if positions else None,
                is_libero="libero" in positions,
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


def _check_attributes(tax: Taxonomy, attributes: dict[str, Any]) -> dict[str, Any]:
    allowed = {"grade", "zone_from", "zone_to"}
    if set(attributes) - allowed:
        raise TaggingError(f"unknown attributes: {sorted(set(attributes) - allowed)}")
    grade = attributes.get("grade")
    if grade is not None and grade not in tax.grades:
        raise TaggingError(f"unknown grade {grade!r}")
    for key in ("zone_from", "zone_to"):
        zone = attributes.get(key)
        if zone is not None and zone not in tax.zones:
            raise TaggingError(f"{key} must be one of {list(tax.zones)}")
    return {k: v for k, v in attributes.items() if v is not None}


def _snapshot(row: Event | Segment | Period, fields: tuple[str, ...]) -> tuple[Any, ...]:
    return (*(getattr(row, f, None) for f in fields), row.deleted_at is not None)


EVENT_FIELDS = (
    "code", "t_ms", "frame", "time_precision", "side", "number", "member_id", "attributes",
    "segment_id", "clip_pre_ms", "clip_post_ms", "note", "review",
)  # fmt: skip
SEGMENT_FIELDS = ("kind", "start_ms", "end_ms", "serving", "won_by", "review")
PERIOD_FIELDS = (
    "no", "start_ms", "end_ms", "start_us", "start_them", "first_serve",
    "our_court_side_at_start", "side_switch_at_score",
)  # fmt: skip


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
    attributes = _check_attributes(tax, item.attributes)
    member_id = _member_for(db, video, item.side, item.number)
    with db.no_autoflush:  # one INSERT with the finished row, not a half-built one
        return _apply_event(db, video, tax, item, attributes, member_id, actor, now)


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
    if row is not None and row.video_id != video.id:
        raise TaggingError("event id belongs to another video")
    before = None if row is None else _snapshot(row, EVENT_FIELDS)
    if row is None:
        row = Event(id=item.id, video_id=video.id, version=0, created_by=actor, source="manual")
        db.add(row)
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
    row.segment_id = item.segment_id
    row.clip_pre_ms, row.clip_post_ms, row.note = item.clip_pre_ms, item.clip_post_ms, item.note
    row.review = item.review
    _stamp(row, item.deleted, now)
    after = _snapshot(row, EVENT_FIELDS)
    if before != after:
        row.updated_by = actor
    _bump(row, before, after)
    return row


def upsert_segment(
    db: Session, video: Video, item: SegmentIn, actor: int, now: datetime
) -> Segment:
    row = db.get(Segment, item.id)
    if row is not None and row.video_id != video.id:
        raise TaggingError("segment id belongs to another video")
    before = None if row is None else _snapshot(row, SEGMENT_FIELDS)
    if row is None:
        row = Segment(id=item.id, video_id=video.id, version=0, created_by=actor, source="manual")
        db.add(row)
    row.kind, row.start_ms, row.end_ms = item.kind, item.start_ms, item.end_ms
    if item.serving is not None:
        row.serving = item.serving
    row.won_by, row.review = item.won_by, item.review
    _stamp(row, item.deleted, now)
    after = _snapshot(row, SEGMENT_FIELDS)
    if before != after:
        row.updated_by = actor
    _bump(row, before, after)
    return row


def upsert_period(db: Session, video: Video, item: PeriodIn, actor: int, now: datetime) -> Period:
    with db.no_autoflush:
        return _apply_period(db, video, item, actor, now)


def _apply_period(db: Session, video: Video, item: PeriodIn, actor: int, now: datetime) -> Period:
    row = db.get(Period, item.id)
    if row is not None and row.video_id != video.id:
        raise TaggingError("period id belongs to another video")
    before = None if row is None else _snapshot(row, PERIOD_FIELDS)
    if row is None:
        row = Period(id=item.id, video_id=video.id, version=0, created_by=actor)
        db.add(row)
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
    _stamp(row, item.deleted, now)
    _bump(row, before, _snapshot(row, PERIOD_FIELDS))
    return row


def rescore(db: Session, video: Video) -> None:
    """Score line from the rallies: per set, from its starting score; the rally winner
    serves next (PLAN §6.6: scores live on rallies)."""
    db.flush()
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

    by_period: dict[uuid.UUID | None, list[Segment]] = {}
    for rally in rallies:
        period = period_of(rally)
        rally.period_id = period.id if period else None
        by_period.setdefault(rally.period_id, []).append(rally)

    for rally in by_period.get(None, []):
        rally.score_us_after = rally.score_them_after = None
        rally.score_source = None
    # Who served: a serve tag in the rally wins; else the previous rally's winner; else the
    # set's first server. Derived on every rescore, so a corrected rally fixes the rest.
    serves = list(
        db.execute(
            select(Event.t_ms, Event.side)
            .where(
                Event.video_id == video.id,
                Event.code == "S",
                Event.deleted_at.is_(None),
            )
            .order_by(Event.t_ms)
        ).all()
    )

    def served_by(rally: Segment) -> str | None:
        for t_ms, side in serves:
            if rally.start_ms - SERVE_SLACK_MS <= t_ms <= rally.end_ms:
                return str(side)
        return None

    for period in periods:
        us, them = period.start_us, period.start_them
        serving = period.first_serve
        played = by_period.get(period.id, [])
        for rally in played:
            derived = served_by(rally) or serving
            if derived is not None:
                rally.serving = derived
            if rally.won_by == "us":
                us += 1
            elif rally.won_by == "them":
                them += 1
            if rally.won_by is None:
                rally.score_us_after = rally.score_them_after = None
                rally.score_source = None
            else:
                rally.score_us_after, rally.score_them_after = us, them
                rally.score_source = "tags"
                serving = rally.won_by
        period.final_us, period.final_them = (us, them) if played else (None, None)


def now_utc() -> datetime:
    return datetime.now(UTC)
