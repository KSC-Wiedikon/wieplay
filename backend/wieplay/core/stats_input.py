"""The rows the stats read (PLAN §9.7): a video's tags, rallies and sets as wieplay/core/stats.py
takes them. Shared by the stats API and the smart reels' search (wieplay/core/reels.py).

Everything counts that nobody rejected (PLAN §10, 20.10.2026): a person's tags and WiePlay's,
the sure ones ("auto") and those still to check ("check", marked in the rows). A rally's server
and winner are the coach's, else the official scoresheet's (PLAN §10.3a), else WiePlay's; a
tag's player the coach's, else WiePlay's (PLAN §10.5). `sure_only` ("Sure only"): without what
is still to check, WiePlay's server and winner only where the joint decode is sure, WiePlay's
players only from the scoresheet.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import replace
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


def tag_in(e: Event, player: tuple[int, int | None] | None = None) -> stats.TagIn:
    """A tag; `player`: WiePlay's number (and member) for it, where it has none of its own."""
    side: stats.Side = "them" if e.side == "them" else "us"
    attributes = e.attributes or {}
    grade = attributes.get("grade")
    number, member_id = e.number, e.member_id
    if number is None and player is not None:
        number, member_id = player
    return stats.TagIn(
        code=e.code,
        side=side,
        number=number,
        member_id=member_id if side == "us" else None,
        grade=grade if isinstance(grade, str) else None,
        t_ms=e.t_ms,
        segment_id=e.segment_id,
        id=e.id,
        zone_from=_zone(attributes.get("zone_from")),
        zone_to=_zone(attributes.get("zone_to")),
        check=e.source == "model" and e.review == "check",
    )


def rally_in(s: Segment, sure_only: bool = False) -> stats.RallyIn:
    """A rally's server and winner: the coach's facts, else the official scoresheet's where the
    scoring lined it up with one of the sheet's points (PLAN §10.3a), else WiePlay's (with
    `sure_only` only where the joint decode is sure of the rally)."""
    model = not sure_only or bool(s.score_sure)
    return stats.RallyIn(
        s.id,
        s.start_ms,
        s.end_ms,
        s.period_id,
        _side(s.serving) or _side(s.sheet_serving) or (_side(s.model_serving) if model else None),
        _side(s.won_by) or _side(s.sheet_won_by) or (_side(s.model_won_by) if model else None),
    )


def _lineup(value: Any) -> tuple[int | None, ...] | None:
    if not isinstance(value, list) or len(value) != 6:
        return None
    return tuple(_zone(n) for n in value)


def set_in(p: Period) -> stats.SetIn:
    return stats.SetIn(
        p.id, p.no, p.start_ms, p.end_ms, p.final_us, p.final_them, p.start_rotation,
        _lineup(p.start_lineup),
    )  # fmt: skip


def _wieplay_players(
    db: Session, video_ids: list[uuid.UUID], sure_only: bool
) -> dict[uuid.UUID, tuple[int, int | None]]:
    """WiePlay's player for each tag it attributed (PLAN §10.5, the `event_player` row beside
    the tag, still current): what a tag without a number of its own counts for. With
    `sure_only`, only the scoresheet's servers."""
    from wieplay.core.players import APPLY_CONFIDENCE, EventPlayer

    rows = db.execute(
        select(EventPlayer.event_id, EventPlayer.number, EventPlayer.member_id, EventPlayer.method)
        .join(Event, Event.id == EventPlayer.event_id)
        .where(
            EventPlayer.video_id.in_(video_ids),
            EventPlayer.number.is_not(None),
            EventPlayer.event_version == Event.version,
            EventPlayer.confidence >= APPLY_CONFIDENCE,
        )
    ).all()
    return {
        event_id: (number, member_id)
        for event_id, number, member_id, method in rows
        if number is not None and (not sure_only or method == "sheet")
    }


def _wieplay_number(e: Event) -> bool:
    """WiePlay wrote the number of its own action from a players run (not the scoresheet)."""
    return (
        e.source == "model"
        and e.review in tagging.MODEL_OWN
        and e.number is not None
        and e.track_ref is not None
        and not e.track_ref.startswith("sheet")
    )


def load_tags(
    db: Session, video_ids: list[uuid.UUID], sure_only: bool = False
) -> dict[uuid.UUID, list[stats.TagIn]]:
    """Each video's tags in time order."""
    out: dict[uuid.UUID, list[stats.TagIn]] = defaultdict(list)
    if not video_ids:
        return out
    rows = db.scalars(
        select(Event)
        .where(
            Event.video_id.in_(video_ids),
            Event.deleted_at.is_(None),
            tagging.event_stands(sure_only),
        )
        .order_by(Event.t_ms, Event.id)
    )
    players = _wieplay_players(db, video_ids, sure_only)
    for e in rows:
        if sure_only and _wieplay_number(e):
            # Sure only: a player WiePlay found by the ball is no sure answer.
            out[e.video_id].append(replace(tag_in(e), number=None, member_id=None))
            continue
        out[e.video_id].append(tag_in(e, players.get(e.id)))
    return out


def load_rallies(
    db: Session, video_ids: list[uuid.UUID], sure_only: bool = False
) -> dict[uuid.UUID, list[stats.RallyIn]]:
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
            tagging.stands(sure_only),
        )
        .order_by(Segment.start_ms)
    )
    for s in rows:
        out[s.video_id].append(rally_in(s, sure_only))
    return out


def load_sets(
    db: Session, video_ids: list[uuid.UUID], sure_only: bool = False
) -> dict[uuid.UUID, list[stats.SetIn]]:
    """Each video's sets (WiePlay's count like a coach's)."""
    out: dict[uuid.UUID, list[stats.SetIn]] = defaultdict(list)
    if not video_ids:
        return out
    rows = db.scalars(
        select(Period).where(
            Period.video_id.in_(video_ids),
            Period.deleted_at.is_(None),
            tagging.period_stands(sure_only),
        )
    )
    for p in rows:
        out[p.video_id].append(set_in(p))
    return out
