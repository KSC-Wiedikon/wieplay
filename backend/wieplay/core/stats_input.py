"""The rows the stats read (PLAN §9.7): a video's tags, rallies and sets as wieplay/core/stats.py
takes them. Shared by the stats API and the smart reels' search (wieplay/core/reels.py).

Facts only: a suggested action counts once a coach accepted it, a set the scoring proposed
once a coach accepted it, and the rallies are those that stand (PLAN §10.3, §10.4a).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from wieplay.core import stats, tagging
from wieplay.core.models import Event, Period, Segment


def _zone(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _side(value: str | None) -> stats.Side | None:
    if value == "us":
        return "us"
    return "them" if value == "them" else None


def tag_in(e: Event) -> stats.TagIn:
    side: stats.Side = "them" if e.side == "them" else "us"
    attributes = e.attributes or {}
    grade = attributes.get("grade")
    return stats.TagIn(
        code=e.code,
        side=side,
        number=e.number,
        member_id=e.member_id if side == "us" else None,
        grade=grade if isinstance(grade, str) else None,
        t_ms=e.t_ms,
        segment_id=e.segment_id,
        id=e.id,
        zone_from=_zone(attributes.get("zone_from")),
        zone_to=_zone(attributes.get("zone_to")),
    )


def rally_in(s: Segment) -> stats.RallyIn:
    return stats.RallyIn(s.id, s.start_ms, s.end_ms, s.period_id, _side(s.serving), _side(s.won_by))


def _lineup(value: Any) -> tuple[int | None, ...] | None:
    if not isinstance(value, list) or len(value) != 6:
        return None
    return tuple(_zone(n) for n in value)


def set_in(p: Period) -> stats.SetIn:
    return stats.SetIn(
        p.id, p.no, p.start_ms, p.end_ms, p.final_us, p.final_them, p.start_rotation,
        _lineup(p.start_lineup),
    )  # fmt: skip


def load_tags(db: Session, video_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[stats.TagIn]]:
    """Each video's tags in time order."""
    out: dict[uuid.UUID, list[stats.TagIn]] = defaultdict(list)
    if not video_ids:
        return out
    rows = db.scalars(
        select(Event)
        .where(
            Event.video_id.in_(video_ids),
            Event.deleted_at.is_(None),
            tagging.event_stands(),  # facts only: a suggested action counts once accepted
        )
        .order_by(Event.t_ms, Event.id)
    )
    for e in rows:
        out[e.video_id].append(tag_in(e))
    return out


def load_rallies(db: Session, video_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[stats.RallyIn]]:
    """Each video's rallies in time order."""
    out: dict[uuid.UUID, list[stats.RallyIn]] = defaultdict(list)
    if not video_ids:
        return out
    rows = db.scalars(
        select(Segment)
        .where(
            Segment.video_id.in_(video_ids),
            Segment.kind == "rally",
            Segment.deleted_at.is_(None),
            tagging.stands(),
        )
        .order_by(Segment.start_ms)
    )
    for s in rows:
        out[s.video_id].append(rally_in(s))
    return out


def load_sets(db: Session, video_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[stats.SetIn]]:
    """Each video's sets."""
    out: dict[uuid.UUID, list[stats.SetIn]] = defaultdict(list)
    if not video_ids:
        return out
    rows = db.scalars(
        select(Period).where(
            Period.video_id.in_(video_ids),
            Period.deleted_at.is_(None),
            # A set the scoring proposed counts once a coach accepted it.
            ~((Period.source == "model") & (Period.review == "unreviewed")),
        )
    )
    for p in rows:
        out[p.video_id].append(set_in(p))
    return out
