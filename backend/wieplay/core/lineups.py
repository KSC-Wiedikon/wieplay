"""Who was on court at each rally, from the official scoresheet (PLAN §10.3a, §10.5), API side.

    the typed sheet ──decoded (ml/sheet.py)──▶ every point: server, both teams' six by zone
    the scoring ──lines the sheet's points up with the rallies──▶ segment.sheet_point
    here: per rally its point's lineup (ml/on_court.py) ──▶ the players job (labels at the
        serves, who may be read), the serves' players, the stats per player

Only sets the scoring lined up with the sheet (a consistent set, used): a rally without a
sheet point has no lineup. The liberos are not on the sheet: they come from the roster
snapshot (`video_roster.is_libero`; the team's roster while there is no snapshot), by number.
Shirt numbers only; nothing here names a person.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from wieplay.core import sheet_notes
from wieplay.core.models import Period, Segment, Video, VideoRoster, WdMember, WdTeamMember
from wieplay.ml import on_court
from wieplay.ml import sheet as sh

# The players job starts tracking this long before a set's first rally: the second referee
# checks the lineups there, the starting six stand on court (PLAN §10.5).
LINEUP_LEAD_MS = 45_000


@dataclass
class VideoLineups:
    """A video's sheet as lineups: per set its points' lineups, per rally its point's, the
    liberos and middle blockers per team (numbers), every number the sheet names per team."""

    sets: dict[int, list[on_court.Lineup]] = field(default_factory=dict)
    by_rally: dict[uuid.UUID, on_court.Lineup] = field(default_factory=dict)
    # Per set: its sheet points (0-based) lined up with a rally, and the points played before
    # the video joined it.
    point_rally: dict[int, dict[int, uuid.UUID]] = field(default_factory=dict)
    offsets: dict[int, int] = field(default_factory=dict)
    liberos: dict[str, set[int]] = field(default_factory=dict)
    middles: dict[str, set[int]] = field(default_factory=dict)
    numbers: dict[str, set[int]] = field(default_factory=dict)
    checks: list[Any] = field(default_factory=list)

    def of(self, rally_id: uuid.UUID) -> on_court.Lineup | None:
        return self.by_rally.get(rally_id)

    def allowed(self, rally_id: uuid.UUID, side: on_court.Side) -> set[int] | None:
        return on_court.allowed(self.of(rally_id), side, self.liberos.get(side, ()))


def roles(db: Session, video: Video) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """(liberos, middle blockers) per team, by number: the roster snapshot, else the team's
    roster now (read without taking the snapshot)."""
    liberos: dict[str, set[int]] = {}
    middles: dict[str, set[int]] = {}
    rows = list(db.scalars(select(VideoRoster).where(VideoRoster.video_id == video.id)))
    if rows:
        for r in rows:
            if r.is_libero or (r.position or "").lower() == "libero":
                liberos.setdefault(r.side, set()).add(int(r.number))
            elif (r.position or "").lower() == "middle":
                middles.setdefault(r.side, set()).add(int(r.number))
        return liberos, middles
    members = db.scalars(
        select(WdMember)
        .join(WdTeamMember, WdTeamMember.member_id == WdMember.id)
        .where(WdTeamMember.team_id == video.team_id, WdTeamMember.rel == "player")
        .distinct()
    )
    for m in members:
        if m.number is None:
            continue
        positions = [str(p).lower() for p in (m.positions or [])]
        if "libero" in positions:
            liberos.setdefault("us", set()).add(int(m.number))
        elif positions and positions[0] == "middle":
            middles.setdefault("us", set()).add(int(m.number))
    return liberos, middles


def _offset(decoded: sh.Decoded, period: Period) -> int | None:
    """Points of the sheet's set played before the video's set starts (a set the video joins
    late starts at its starting score)."""
    if not period.start_us and not period.start_them:
        return 0
    return decoded.index_of((period.start_us, period.start_them))


def for_video(db: Session, video: Video) -> VideoLineups | None:
    """The video's lineups from its sheet, or None (no sheet, switched off, not volleyball)."""
    from wieplay.core import scoresheet

    use = scoresheet.for_scoring(db, video)
    if use is None or not use.on:
        return None
    out = VideoLineups(checks=list(use.checks))
    for c in use.checks:
        if c.use(use.on) != "path":
            continue
        out.sets[c.no] = on_court.point_lineups(c.sheet, c.decoded)
        for side in on_court.SIDES:
            out.numbers.setdefault(side, set()).update(
                n for n in c.sheet.lineup.get(side) or () if n is not None
            )
        for sub in c.sheet.subs:
            if sub.number_in is not None:
                out.numbers.setdefault(sub.side, set()).add(sub.number_in)
    if not out.sets:
        return None
    out.liberos, out.middles = roles(db, video)
    periods = {
        p.id: p
        for p in db.scalars(
            select(Period).where(Period.video_id == video.id, Period.deleted_at.is_(None))
        )
    }
    rows = db.execute(
        select(Segment.id, Segment.period_id, Segment.sheet_point).where(
            Segment.video_id == video.id,
            Segment.kind == "rally",
            Segment.deleted_at.is_(None),
            Segment.sheet_point.is_not(None),
        )
    ).all()
    by_no = {c.no: c for c in use.checks}
    for rally_id, period_id, point in rows:
        period = periods.get(period_id) if period_id is not None else None
        if period is None or period.no not in out.sets or point is None:
            continue
        offset = _offset(by_no[period.no].decoded, period)
        if offset is None:
            continue
        k = offset + int(point) - 1
        table = out.sets[period.no]
        if 0 <= k < len(table):
            out.by_rally[rally_id] = table[k]
            out.point_rally.setdefault(period.no, {})[k] = rally_id
            out.offsets[period.no] = offset
    return out


def notes(db: Session, video: Video, found: VideoLineups) -> list[sheet_notes.Placed]:
    """The sheet's timeouts, substitutions and sanctions in the video's breaks, checked
    (wieplay/core/sheet_notes.py)."""
    rows = db.execute(
        select(
            Segment.id,
            Segment.start_ms,
            Segment.end_ms,
            Segment.score_us_after,
            Segment.score_them_after,
            Segment.log_score_us,
            Segment.log_score_them,
        )
        .where(
            Segment.video_id == video.id,
            Segment.kind == "rally",
            Segment.deleted_at.is_(None),
        )
        .order_by(Segment.start_ms, Segment.id)
    ).all()
    rallies = [
        sheet_notes.RallyFacts(
            rid,
            int(start),
            int(end),
            (int(su), int(st)) if su is not None and st is not None else None,
            (int(lu), int(lt)) if lu is not None and lt is not None else None,
        )
        for rid, start, end, su, st, lu, lt in rows
    ]
    out: list[sheet_notes.Placed] = []
    for c in found.checks:
        if c.no not in found.sets:
            continue
        out.extend(
            sheet_notes.place(
                sheet_notes.notes_of(c.sheet, c.decoded),
                found.point_rally.get(c.no, {}),
                rallies,
                found.offsets.get(c.no, 0),
            )
        )
    return out


def job_entry(lineup: on_court.Lineup) -> dict[str, Any]:
    """A rally's lineup for the players job (wieplay/ml/serve_labels.py `from_inputs`)."""
    return {
        "set": lineup.set_no,
        "point": lineup.point + 1,
        "serving": lineup.serving,
        "server": lineup.server,
        "us": list(lineup.zones.get("us") or []) or None,
        "them": list(lineup.zones.get("them") or []) or None,
    }


def job_extra(found: VideoLineups) -> dict[str, Any]:
    """The rest the players job needs: liberos, middle blockers, the sheet's numbers."""

    def lists(values: dict[str, set[int]]) -> dict[str, list[int]]:
        return {k: sorted(v) for k, v in values.items() if v}

    return {
        "liberos": lists(found.liberos),
        "middles": lists(found.middles),
        "numbers": lists(found.numbers),
    }


def rallies_json(found: VideoLineups, rally_ids: Sequence[uuid.UUID]) -> list[dict[str, Any]]:
    """Per rally (in the order given) its lineup, for the web (None without one)."""
    out = []
    for rid in rally_ids:
        lu = found.of(rid)
        out.append({"rally": str(rid), **lu.as_json()} if lu is not None else {"rally": str(rid)})
    return out


def serve_grades(db: Session, video: Video, found: VideoLineups) -> dict[tuple[int, int], str]:
    """The grade of the serve tag (a fact: a coach's, or a suggested one accepted) of each
    sheet point lined up with a rally, where the tag's team served it."""
    from wieplay.core import tagging
    from wieplay.core.models import Event

    rows = db.execute(
        select(Event.segment_id, Event.side, Event.attributes).where(
            Event.video_id == video.id,
            Event.code == "S",
            Event.deleted_at.is_(None),
            Event.segment_id.is_not(None),
            tagging.event_stands(),
        )
    ).all()
    out: dict[tuple[int, int], str] = {}
    for segment_id, side, attributes in rows:
        lu = found.of(segment_id)
        grade = (attributes or {}).get("grade")
        if lu is None or lu.serving != side or grade not in ("#", "="):
            continue
        out[(lu.set_no, lu.point)] = str(grade)
    return out


def player_stats(
    db: Session, video: Video, found: VideoLineups
) -> list[tuple[str, int, on_court.PlayerLine]]:
    """Per player (team, number) the sheet's numbers (wieplay/ml/on_court.py `player_lines`);
    our rotations by the setter's zone (P1-P6) where the set's starting rotation is known."""
    starts = {
        p.no: p.start_rotation
        for p in db.scalars(
            select(Period).where(Period.video_id == video.id, Period.deleted_at.is_(None))
        )
    }

    def rotation(lu: on_court.Lineup, side: on_court.Side) -> int:
        start = starts.get(lu.set_no)
        if side == "us" and start in (1, 2, 3, 4, 5, 6):
            assert start is not None
            return sh.zone_of(start, lu.rotation["us"])
        return lu.rotation[side] % 6 + 1

    lines = on_court.player_lines(found.sets.values(), serve_grades(db, video, found), rotation)
    order = {"us": 0, "them": 1}
    return sorted(
        ((side, number, line) for (side, number), line in lines.items()),
        key=lambda x: (order[x[0]], x[1]),
    )


def by_setter(db: Session, video: Video, found: VideoLineups) -> bool:
    """Our rotations count by the setter's zone: every set the sheet was used for has one."""
    starts = {
        p.no: p.start_rotation
        for p in db.scalars(
            select(Period).where(Period.video_id == video.id, Period.deleted_at.is_(None))
        )
    }
    return bool(found.sets) and all(starts.get(no) in (1, 2, 3, 4, 5, 6) for no in found.sets)
