"""Tagging (PLAN §9): the workspace's data in one request, and idempotent writes.

GET  /api/videos/{id}/tagging   roster snapshot, sets, rallies, tags, official set scores
POST /api/videos/{id}/changes   a batch of upserts from the browser's outbox (PLAN §9.6)
PUT  /api/events/{id}           one tag
PUT  /api/videos/{id}/roster    the roster snapshot (numbers, liberos, opponents)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from nomal.api.access import TeamAccess, visible_video
from nomal.api.deps import DbDep, ViewerDep, audit
from nomal.core import scoring, tagging
from nomal.core.models import (
    Event,
    Period,
    Segment,
    Video,
    VideoRoster,
    WdGame,
    WdMember,
    WdTeam,
    WdTeamMember,
)
from nomal.core.permissions import Viewer, can_delete_video, can_tag, can_upload
from nomal.core.taxonomy import SPORTS_WITH_TAXONOMY, Taxonomy, load

router = APIRouter(prefix="/api", tags=["tagging"])

MAX_BATCH = 500
REVISION_FIELDS = ("code", "t_ms", "side", "number", "attributes", "deleted_at")


class RosterEntry(BaseModel):
    side: Literal["us", "them"]
    number: int = Field(ge=0, le=99)
    member_id: int | None = Field(default=None, ge=1, le=2**31 - 1)
    position: str | None = Field(default=None, max_length=32)
    is_libero: bool = False


class RosterOut(RosterEntry):
    name: str | None


class RosterIn(BaseModel):
    entries: list[RosterEntry] = Field(min_length=1, max_length=60)


class EventOut(BaseModel):
    id: uuid.UUID
    code: str
    t_ms: int
    frame: int | None
    time_precision: str
    side: str
    number: int | None
    member_id: int | None
    attributes: dict[str, Any]
    segment_id: uuid.UUID | None
    clip_pre_ms: int | None
    clip_post_ms: int | None
    note: str | None
    source: str
    # Model proposals (PLAN §10): how sure the model is, and which run made it.
    confidence: float | None
    model_run_id: uuid.UUID | None
    # A suggested action (PLAN §10.4a): the ball at the contact (normalised image
    # coordinates) and the floor below it in metres (with a calibrated court only).
    ball_x: float | None = None
    ball_y: float | None = None
    court_x: float | None = None
    court_y: float | None = None
    review: str
    version: int
    updated_at: datetime
    deleted_at: datetime | None


class SegmentOut(BaseModel):
    id: uuid.UUID
    kind: str
    start_ms: int
    end_ms: int
    period_id: uuid.UUID | None
    # The coach's: who served (derived from facts), the serve called in review, the winner.
    serving: str | None
    serving_manual: str | None = None
    won_by: str | None
    # The automatic scoring's (PLAN §10.3), never in place of the coach's: where the server
    # stood (near: the camera's end) and how sure; its server and winner; how sure the
    # winner is; "check" and why (missing_before, missing_after, extra, serve, coach, unsure).
    serving_court: str | None = None
    serve_confidence: float | None = None
    model_serving: str | None = None
    model_won_by: str | None = None
    score_confidence: float | None = None
    score_conflict: bool = False
    score_note: str | None = None
    # Points the model thinks were played right before this rally without one (counted).
    model_missed_us: int | None = None
    model_missed_them: int | None = None
    # The score line: the coach's winners, else the model's (score_source "model").
    score_us_after: int | None
    score_them_after: int | None
    score_source: str | None
    # manual | model | import; a model's rally proposal carries its confidence and run.
    source: str
    confidence: float | None
    model_run_id: uuid.UUID | None
    # unreviewed | confirmed | corrected | rejected | merged
    review: str
    merged_into: uuid.UUID | None = None
    version: int
    deleted_at: datetime | None


class PeriodOut(BaseModel):
    id: uuid.UUID
    no: int
    start_ms: int
    end_ms: int | None
    start_us: int
    start_them: int
    first_serve: str | None
    our_court_side_at_start: str | None
    side_switch_at_score: int | None
    # Our setter's zone (1-6) at the set's first serve: rotation stats.
    start_rotation: int | None = None
    # Our six shirt numbers in zones I-VI at that serve (null in a zone: not entered).
    start_lineup: list[int | None] | None = None
    final_us: int | None
    final_them: int | None
    # manual | model (a set the scoring proposed, answered like a rally proposal).
    source: str = "manual"
    review: str = "unreviewed"
    confidence: float | None = None
    # The scoring's guesses next to the coach's first_serve and our_court_side_at_start.
    model_first_serve: str | None = None
    model_court_side: str | None = None
    # Points the model thinks were played after the set's last rally without one (counted).
    model_missed_us: int | None = None
    model_missed_them: int | None = None
    version: int
    deleted_at: datetime | None


class TaggingOut(BaseModel):
    can_tag: bool
    sport: str
    taxonomy: dict[str, Any] | None
    roster: list[RosterOut]
    periods: list[PeriodOut]
    segments: list[SegmentOut]
    events: list[EventOut]
    # Official set scores of the game from the federation, as [us, them] per set.
    official_sets: list[list[int]] | None
    # The game's official final result as [us, them] (both sports), when played.
    official_final: list[int] | None
    # A coach marked the rally list complete and right (POST .../rallies-reviewed).
    rallies_reviewed_at: datetime | None = None
    # ... and its actions (POST .../actions-reviewed).
    actions_reviewed_at: datetime | None = None
    # The match's first serve (start of set 1, else the end of the warm-up): "start at the
    # first serve" on the video page.
    first_serve_ms: int | None = None


class ChangesIn(BaseModel):
    events: list[tagging.EventIn] = Field(default_factory=list, max_length=MAX_BATCH)
    segments: list[tagging.SegmentIn] = Field(default_factory=list, max_length=MAX_BATCH)
    periods: list[tagging.PeriodIn] = Field(default_factory=list, max_length=20)


class ChangesOut(BaseModel):
    events: list[EventOut]
    segments: list[SegmentOut]
    periods: list[PeriodOut]
    # Changes skipped because the server had a newer version; the lists above carry the
    # current state of those items, which the browser takes over.
    stale: list[uuid.UUID]


class EventPutIn(tagging.EventIn):
    video_id: uuid.UUID


def _taxonomy(sport: str) -> Taxonomy | None:
    return load(sport) if sport in SPORTS_WITH_TAXONOMY else None


def _readable(db: Session, viewer: Viewer, video_id: uuid.UUID) -> tuple[Video, TeamAccess]:
    video, access = visible_video(db, viewer, video_id)
    manager = can_upload(viewer, access.ref) or can_delete_video(viewer, access.ref, access.leads)
    if video.status != "ready" and not manager:
        raise HTTPException(404, "Video not found")
    return video, access


def _lock(db: Session, video: Video) -> None:
    """One writer per video at a time: the roster snapshot and the rescore read and write
    the whole video, so concurrent taggers are serialised here."""
    db.execute(
        select(Video)
        .where(Video.id == video.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _writable(db: Session, viewer: Viewer, video_id: uuid.UUID) -> tuple[Video, Taxonomy]:
    video, access = _readable(db, viewer, video_id)
    if not can_tag(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches and sport admins tag videos")
    if video.status != "ready":
        raise HTTPException(409, "Tag a video once it is ready")
    tax = _taxonomy(video.sport)
    if tax is None:
        raise HTTPException(409, f"No tagging vocabulary for {video.sport} yet")
    _lock(db, video)
    tagging.set_actor(db, viewer.member_id)
    tagging.ensure_roster(db, video)  # numbers resolve through the snapshot from the first tag
    return video, tax


def _roster_out(db: Session, rows: list[VideoRoster]) -> list[RosterOut]:
    ids = [r.member_id for r in rows if r.member_id]
    names = {m.id: m.display_name for m in db.scalars(select(WdMember).where(WdMember.id.in_(ids)))}
    out = [
        RosterOut(
            side=r.side,
            number=r.number,
            member_id=r.member_id,
            position=r.position,
            is_libero=r.is_libero,
            name=names.get(r.member_id) if r.member_id else None,
        )
        for r in rows
    ]
    return sorted(out, key=lambda r: (r.side != "us", r.number))


def _events(db: Session, video: Video) -> list[EventOut]:
    rows = db.scalars(
        select(Event)
        .where(Event.video_id == video.id, Event.deleted_at.is_(None))
        .order_by(Event.t_ms)
    )
    return [EventOut.model_validate(r, from_attributes=True) for r in rows]


def _segments(
    db: Session, video: Video, touched: set[uuid.UUID] | frozenset[uuid.UUID] = frozenset()
) -> list[SegmentOut]:
    """Live segments, plus deleted ones this request touched (the browser needs their
    version to undo the delete)."""
    rows = db.scalars(
        select(Segment)
        .where(
            Segment.video_id == video.id,
            Segment.deleted_at.is_(None) | Segment.id.in_(touched),
        )
        .order_by(Segment.start_ms)
    )
    return [SegmentOut.model_validate(r, from_attributes=True) for r in rows]


def _periods(
    db: Session, video: Video, touched: set[uuid.UUID] | frozenset[uuid.UUID] = frozenset()
) -> list[PeriodOut]:
    rows = db.scalars(
        select(Period)
        .where(Period.video_id == video.id, Period.deleted_at.is_(None) | Period.id.in_(touched))
        .order_by(Period.no)
    )
    return [PeriodOut.model_validate(r, from_attributes=True) for r in rows]


def _official_sets(db: Session, video: Video) -> list[list[int]] | None:
    if video.game_id is None:
        return None
    game = db.get(WdGame, video.game_id)
    if game is None or not game.sets:
        return None
    # Wiedisync stores [home, away]; the coach thinks in [us, them].
    if game.our_side == "away":
        return [[s[1], s[0]] for s in game.sets]
    return [list(s) for s in game.sets]


def _official_final(db: Session, video: Video) -> list[int] | None:
    if video.game_id is None:
        return None
    game = db.get(WdGame, video.game_id)
    if game is None or game.home_score is None or game.away_score is None:
        return None
    if game.our_side == "away":
        return [game.away_score, game.home_score]
    return [game.home_score, game.away_score]


@router.get("/taxonomies/{sport}")
def get_taxonomy(sport: str, _: ViewerDep) -> dict[str, Any]:
    tax = _taxonomy(sport)
    if tax is None:
        raise HTTPException(404, "No tagging vocabulary for this sport yet")
    return tax.as_json()


@router.get("/videos/{video_id}/tagging", response_model=TaggingOut)
def get_tagging(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> TaggingOut:
    video, access = _readable(db, viewer, video_id)
    writer = can_tag(viewer, access.ref, access.leads)
    rows = list(db.scalars(select(VideoRoster).where(VideoRoster.video_id == video.id)))
    if writer and video.status == "ready" and not rows:
        _lock(db, video)  # two first opens at once must not both take the snapshot
        rows = tagging.ensure_roster(db, video)
        db.commit()
    tax = _taxonomy(video.sport)
    return TaggingOut(
        can_tag=writer and video.status == "ready" and tax is not None,
        sport=video.sport,
        taxonomy=tax.as_json() if tax else None,
        roster=_roster_out(db, rows),
        periods=_periods(db, video),
        segments=_segments(db, video),
        events=_events(db, video),
        official_sets=_official_sets(db, video),
        official_final=_official_final(db, video),
        rallies_reviewed_at=video.rallies_reviewed_at,
        actions_reviewed_at=video.actions_reviewed_at,
        first_serve_ms=scoring.first_serve_ms(db, video),
    )


@router.put("/videos/{video_id}/roster", response_model=list[RosterOut])
def put_roster(
    video_id: uuid.UUID, body: RosterIn, viewer: ViewerDep, db: DbDep
) -> list[RosterOut]:
    video, _ = _writable(db, viewer, video_id)
    keys = [(e.side, e.number) for e in body.entries]
    if len(keys) != len(set(keys)):
        raise HTTPException(422, "A number appears twice on one side")
    # Only people of this team (any season) can be named: a roster edit must not reach
    # other teams' members, let alone teams switched off for minors.
    team_members = set(
        db.scalars(
            select(WdTeamMember.member_id)
            .join(WdTeam, WdTeam.id == WdTeamMember.team_id)
            .where(WdTeam.lineage_id == video.lineage_id)
        )
    )
    for entry in body.entries:
        if entry.side == "them" and entry.member_id is not None:
            raise HTTPException(422, "Opponents are numbers only")
        if entry.member_id is not None and entry.member_id not in team_members:
            raise HTTPException(422, "Only members of this team can be on its roster")
    db.query(VideoRoster).filter(VideoRoster.video_id == video.id).delete()
    db.add_all(VideoRoster(video_id=video.id, **e.model_dump()) for e in body.entries)
    db.flush()
    # Tags follow the new numbers; a changed link is a new version of the tag.
    mapping = {e.number: e.member_id for e in body.entries if e.side == "us"}
    for event in db.scalars(select(Event).where(Event.video_id == video.id, Event.side == "us")):
        member = mapping.get(event.number) if event.number is not None else None
        if event.member_id != member:
            event.member_id = member  # derived from the roster: no new version
    audit(db, viewer.member_id, "roster_changed", "video", str(video.id),
          entries=len(body.entries))  # fmt: skip
    db.commit()
    rows = list(db.scalars(select(VideoRoster).where(VideoRoster.video_id == video.id)))
    return _roster_out(db, rows)


def _last_by_id[C: (tagging.EventIn, tagging.SegmentIn, tagging.PeriodIn)](
    items: list[C],
) -> list[C]:
    """An outbox can hold several changes to one item: the last one counts."""
    latest: dict[uuid.UUID, C] = {}
    for item in items:
        latest.pop(item.id, None)
        latest[item.id] = item
    return list(latest.values())


@router.post("/videos/{video_id}/changes", response_model=ChangesOut)
def post_changes(video_id: uuid.UUID, body: ChangesIn, viewer: ViewerDep, db: DbDep) -> ChangesOut:
    """Apply a batch from the outbox. Each item is an upsert by its id, based on the version
    the browser last saw; stale changes are skipped and reported. Every applied change
    lands in the revision log."""
    video, tax = _writable(db, viewer, video_id)
    now = tagging.now_utc()
    periods, segments = _last_by_id(body.periods), _last_by_id(body.segments)
    items = _last_by_id(body.events)
    stale: list[uuid.UUID] = []
    events: list[Event] = []
    rescore_model = _changes_scoring(db, periods, segments)
    try:
        tagging.check_limits(db, video, [i.id for i in items], [s.id for s in segments])
        for period in periods:
            try:
                tagging.upsert_period(db, video, period, viewer.member_id, now)
            except tagging.Stale:
                stale.append(period.id)
        for segment in segments:
            try:
                tagging.upsert_segment(db, video, segment, viewer.member_id, now)
            except tagging.Stale:
                stale.append(segment.id)
        tagging.check_periods(db, video)
        for item in items:  # the rally of a tag is set by the server, by time
            try:
                events.append(tagging.upsert_event(db, video, tax, item, viewer.member_id, now))
            except tagging.Stale as exc:
                stale.append(item.id)
                assert isinstance(exc.row, Event)
                events.append(exc.row)
        tagging.rescore(db, video)
        if rescore_model:
            scoring.score_video(db, video, reason="changes")
    except tagging.TaggingError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return ChangesOut(
        events=[EventOut.model_validate(e, from_attributes=True) for e in events],
        segments=_segments(db, video, {s.id for s in segments}),
        periods=_periods(db, video, {p.id for p in periods}),
        stale=stale,
    )


SCORING_PERIOD_FIELDS = (
    "no", "start_ms", "end_ms", "start_us", "start_them", "first_serve",
    "our_court_side_at_start", "side_switch_at_score",
)  # fmt: skip


def _changes_scoring(
    db: Session, periods: list[tagging.PeriodIn], segments: list[tagging.SegmentIn]
) -> bool:
    """Whether a batch changes what the automatic scoring stands on: a set (its bounds, its
    number, who served first, our side) or a serve a coach called in review."""
    for item in periods:
        row = db.get(Period, item.id)
        if row is None or (row.deleted_at is not None) != item.deleted:
            return True
        if any(getattr(row, f) != getattr(item, f) for f in SCORING_PERIOD_FIELDS):
            return True
        if item.review is not None and item.review != row.review:
            return True
    for seg in segments:
        if "serving_manual" in seg.model_fields_set:
            found = db.get(Segment, seg.id)
            if found is None or found.serving_manual != seg.serving_manual:
                return True
    return False


@router.put("/events/{event_id}", response_model=EventOut)
def put_event(event_id: uuid.UUID, body: EventPutIn, viewer: ViewerDep, db: DbDep) -> EventOut:
    if body.id != event_id:
        raise HTTPException(422, "Path and body ids differ")
    video, tax = _writable(db, viewer, body.video_id)
    try:
        tagging.check_limits(db, video, [body.id], [])
        try:
            event = tagging.upsert_event(db, video, tax, body, viewer.member_id, tagging.now_utc())
        except tagging.Stale as exc:
            db.rollback()
            return EventOut.model_validate(exc.row, from_attributes=True)
        tagging.rescore(db, video)
    except tagging.TaggingError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return EventOut.model_validate(event, from_attributes=True)


@router.get("/events/{event_id}/revisions")
def event_revisions(event_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> list[dict[str, Any]]:
    """Who changed a tag and how (coaches and sport admins)."""
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Tag not found")
    _, access = _readable(db, viewer, event.video_id)
    if not can_tag(viewer, access.ref, access.leads):
        raise HTTPException(403, "Coaches and sport admins only")
    from nomal.core.models import EventRevision

    rows = db.scalars(
        select(EventRevision).where(EventRevision.event_id == event_id).order_by(EventRevision.id)
    )
    return [
        {
            "version": r.version,
            "action": r.action,
            "changed_by": r.changed_by,
            "changed_at": r.changed_at,
            "after": {
                k: (r.after or {}).get(k) for k in ("code", "t_ms", "side", "number", "attributes")
            },
        }
        for r in rows
    ]


class CandidateOut(BaseModel):
    member_id: int
    name: str
    number: int | None


@router.get("/videos/{video_id}/roster/candidates", response_model=list[CandidateOut])
def roster_candidates(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> list[CandidateOut]:
    """People who can go on this video's roster: the team's players in any season."""
    video, access = _readable(db, viewer, video_id)
    if not can_tag(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches and sport admins edit the roster")
    members = list(
        db.scalars(
            select(WdMember)
            .join(WdTeamMember, WdTeamMember.member_id == WdMember.id)
            .join(WdTeam, WdTeam.id == WdTeamMember.team_id)
            .where(WdTeam.lineage_id == video.lineage_id, WdTeamMember.rel == "player")
            .distinct()
        )
    )
    out = [CandidateOut(member_id=m.id, name=m.display_name, number=m.number) for m in members]
    return sorted(out, key=lambda c: (c.number is None, c.number or 0, c.name))
