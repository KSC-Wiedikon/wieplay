"""Playlists made of time ranges (PLAN §6.8, §7.7).

A smart reel is a saved query over tags ("member 101, attacks graded #, 2026/27"),
evaluated when it is opened, so it grows as tagging continues. Its query may carry a search
(wieplay/core/search.py: set, rotation, zones, outcome, who won the rally…), read per match
like the stats read it. A condensed match is the
same idea over rally segments. Nothing is rendered to watch them; exports render.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import Row, or_, select
from sqlalchemy.orm import Session

from wieplay.core import stats_input, tagging
from wieplay.core.models import Event, Segment, Video, WdTeam
from wieplay.core.search import TagSearch, select_match
from wieplay.core.taxonomy import SPORTS_WITH_TAXONOMY, load

PRE_ROLL_MS = 2000  # the serve toss
POST_ROLL_MS = 1500  # the reaction
MERGE_GAP_MS = 500
DEFAULT_PRE_MS, DEFAULT_POST_MS = 3000, 2000


class ReelQuery(BaseModel):
    lineage_id: uuid.UUID | None = None
    season: str | None = Field(default=None, pattern=r"^\d{4}/\d{2}$")
    video_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    member_id: int | None = None
    side: Literal["us", "them"] | None = None
    codes: list[str] = Field(default_factory=list, max_length=10)
    # None stands for a tag without a grade (the stats' "in play" and "touches" count those).
    grades: list[str | None] = Field(default_factory=list, max_length=7)
    # A search on top (PLAN §9.7, wieplay/core/search.py): set, rotation, zones, outcome…
    search: TagSearch | None = None
    # The words it was typed as ("set 3, rotation 2, reception -"), shown again in the builder.
    text: str | None = Field(default=None, max_length=300)
    limit: int = Field(default=200, ge=1, le=500)


@dataclass(frozen=True)
class Piece:
    video_id: uuid.UUID
    start_ms: int
    end_ms: int
    event_id: uuid.UUID | None = None
    clip_id: uuid.UUID | None = None
    caption: str | None = None
    # For display
    video_title: str | None = None
    recorded_on: date | None = None
    code: str | None = None
    grade: str | None = None
    side: str | None = None
    number: int | None = None
    member_id: int | None = None


def clamp(piece: Piece, duration_ms: int | None) -> Piece:
    end = piece.end_ms if duration_ms is None else min(piece.end_ms, duration_ms)
    return replace(piece, start_ms=max(0, piece.start_ms), end_ms=end)


def window(event: Event) -> tuple[int, int]:
    """A tag's clip window: its own, else its skill's default (e.g. attack -3 s / +2 s)."""
    pre, post = DEFAULT_PRE_MS, DEFAULT_POST_MS
    if event.sport in SPORTS_WITH_TAXONOMY:
        skill = load(event.sport).skills.get(event.code)
        if skill is not None:
            pre, post = skill.pre_ms, skill.post_ms
    pre = event.clip_pre_ms if event.clip_pre_ms is not None else pre
    post = event.clip_post_ms if event.clip_post_ms is not None else post
    return event.t_ms - pre, event.t_ms + post


def evaluate(db: Session, visible_team_ids: set[int], query: ReelQuery) -> list[Piece]:
    """The tags matching a smart reel's query, as clip windows, oldest match first. Only
    videos the viewer may watch take part."""
    if not visible_team_ids:
        return []
    stmt = (
        select(Event, Video)
        .join(Video, Video.id == Event.video_id)
        .join(WdTeam, WdTeam.id == Video.team_id)
        .where(
            Event.deleted_at.is_(None),
            tagging.event_stands(),  # suggested actions only once a coach accepted them
            Video.status == "ready",
            Video.team_id.in_(visible_team_ids),
        )
    )
    if query.lineage_id:
        stmt = stmt.where(Video.lineage_id == query.lineage_id)
    if query.season:
        stmt = stmt.where(WdTeam.season == query.season)
    if query.video_ids:
        stmt = stmt.where(Video.id.in_(query.video_ids))
    if query.member_id is not None:
        stmt = stmt.where(Event.member_id == query.member_id)
    if query.side:
        stmt = stmt.where(Event.side == query.side)
    if query.codes:
        stmt = stmt.where(Event.code.in_(query.codes))
    if query.grades:
        grade = Event.attributes["grade"].astext
        named = [g for g in query.grades if g is not None]
        stmt = stmt.where(
            or_(grade.in_(named), grade.is_(None)) if None in query.grades else grade.in_(named)
        )
    search = query.search if query.search is not None and not query.search.is_empty() else None
    stmt = stmt.order_by(Video.recorded_on, Video.created_at, Event.t_ms)
    if search is not None:
        # The simple terms narrow the query; the rest is checked per match below.
        if search.sides:
            stmt = stmt.where(Event.side.in_(search.sides))
        if search.codes:
            stmt = stmt.where(Event.code.in_(search.codes))
    else:
        stmt = stmt.limit(query.limit)
    rows: Sequence[Row[Event, Video]] = db.execute(stmt).all()
    if search is not None:
        rows = _searched(db, rows, search)[: query.limit]
    pieces = []
    for event, video in rows:
        start, end = window(event)
        pieces.append(
            clamp(
                Piece(
                    video_id=video.id,
                    start_ms=start,
                    end_ms=end,
                    event_id=event.id,
                    video_title=video.title,
                    recorded_on=video.recorded_on,
                    code=event.code,
                    grade=(event.attributes or {}).get("grade"),
                    side=event.side,
                    number=event.number,
                    member_id=event.member_id,
                ),
                video.duration_ms,
            )
        )
    return pieces


def _searched(
    db: Session, rows: Sequence[Row[Event, Video]], search: TagSearch
) -> list[Row[Event, Video]]:
    """The rows whose tags the search keeps, in their order: each match's rallies and sets
    give the tags their set, rotation, winner and server (the stats' reading)."""
    video_ids = list(dict.fromkeys(video.id for _, video in rows))
    tags = stats_input.load_tags(db, video_ids)
    rallies = stats_input.load_rallies(db, video_ids)
    sets = stats_input.load_sets(db, video_ids)
    kept: set[uuid.UUID] = set()
    for video_id in video_ids:
        sport = next(video.sport for _, video in rows if video.id == video_id)
        if sport not in SPORTS_WITH_TAXONOMY:
            continue
        match = tags.get(video_id, [])
        selection = select_match(search, load(sport), match, rallies.get(video_id, []),
                                 sets.get(video_id, []))  # fmt: skip
        kept.update(t.id for i, t in enumerate(match) if i in selection.tags and t.id)
    return [row for row in rows if row[0].id in kept]


def merge(pieces: list[Piece]) -> list[Piece]:
    """Join overlapping or nearly touching windows of the same video (in order)."""
    out: list[Piece] = []
    for piece in pieces:
        last = out[-1] if out else None
        if (
            last is not None
            and last.video_id == piece.video_id
            and piece.start_ms <= last.end_ms + MERGE_GAP_MS
        ):
            out[-1] = replace(last, end_ms=max(last.end_ms, piece.end_ms))
        else:
            out.append(piece)
    return out


def condensed(db: Session, video: Video) -> list[Piece]:
    """The rallies of a match with pre- and post-roll: skip dead time (PLAN §9.5)."""
    rallies = db.scalars(
        select(Segment)
        .where(
            Segment.video_id == video.id,
            Segment.kind.in_(("rally", "possession")),
            Segment.deleted_at.is_(None),
            tagging.stands(),
        )
        .order_by(Segment.start_ms)
    )
    pieces = [
        clamp(
            Piece(
                video_id=video.id,
                start_ms=r.start_ms - PRE_ROLL_MS,
                end_ms=r.end_ms + POST_ROLL_MS,
                video_title=video.title,
                recorded_on=video.recorded_on,
            ),
            video.duration_ms,
        )
        for r in rallies
    ]
    return merge(pieces)


def total_ms(pieces: list[Piece]) -> int:
    return sum(max(0, p.end_ms - p.start_ms) for p in pieces)
