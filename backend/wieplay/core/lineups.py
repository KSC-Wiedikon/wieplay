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

import bisect
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from wieplay.core import sheet_notes
from wieplay.core.models import (
    Event,
    Period,
    Segment,
    Video,
    VideoRoster,
    WdMember,
    WdTeamMember,
)
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


# --- the team's memory where the sheet is missing (PLAN §10.5b) ----------------------------------

# A serve's player from the memory is written into a model's serve from this probability on
# (a suggestion the coach reviews with the serve), and given to the players job as a label
# from MEMORY_LABEL on. On the two typed H3 sheets, the memory from the other game and
# simulated readings at a quarter of our serves (docs/research/team-memory-2026-10.md): 61 %
# of our serves at 0.9 with 97 % of them right, 59 % at 0.95 (97 %); at 0.8 a coach's setter
# zone alone made 56 % sure but only 83 % right.
MEMORY_APPLY = 0.9
MEMORY_LABEL = 0.95
# What a serve's observed player is worth: a coach's tag, a card the coach named, a number the
# reader read on the card (its own confidence, with the team's number prior).
COACH_SEEN = 0.97
NAMED_SEEN = 0.95
READ_SEEN_MAX = 0.9


@dataclass
class PriorLineups:
    """Our likely server per rally where we serve, and our likely six per rally, from the
    team's usual rotations (wieplay/ml/team_prior.py) and what the video shows; per set how it
    was anchored."""

    servers: dict[uuid.UUID, tuple[int, float]] = field(default_factory=dict)
    six: dict[uuid.UUID, tuple[tuple[int | None, ...], float]] = field(default_factory=dict)
    rotation: dict[uuid.UUID, tuple[int, int]] = field(default_factory=dict)  # (set, rotation)
    sets: dict[int, dict[str, Any]] = field(default_factory=dict)

    def server(self, rally_id: uuid.UUID, at_least: float = 0.0) -> int | None:
        found = self.servers.get(rally_id)
        return found[0] if found is not None and found[1] >= at_least else None


def serving_of(seg: Segment) -> str | None:
    found = seg.serving_manual or seg.serving or seg.model_serving
    return found if found in ("us", "them") else None


def rally_rotations(rallies: Sequence[Segment]) -> list[int]:
    """Our rotation at each rally of a set (side-outs we won before it): one more each time
    the serve passes from them to us."""
    out = []
    r = 0
    before: str | None = None
    for seg in rallies:
        now = serving_of(seg)
        if now == "us" and before == "them":
            r += 1
        if now is not None:
            before = now
        out.append(r)
    return out


def serve_observations(
    db: Session, video: Video, rally_ids: set[uuid.UUID], number_prior: dict[int, float]
) -> dict[uuid.UUID, list[tuple[int, float]]]:
    """Per rally of ours the servers seen: a coach's serve tag with a number, else WiePlay's
    player at a serve (`event_player`, the image attribution: a card the coach named, or the
    number read on it with the team's number prior). Never the memory's own writing."""
    from wieplay.core.players import EventPlayer, PlayerCluster
    from wieplay.ml import team_prior as tp

    out: dict[uuid.UUID, list[tuple[int, float]]] = {}
    rows = db.execute(
        select(Event, EventPlayer, PlayerCluster)
        .outerjoin(EventPlayer, EventPlayer.event_id == Event.id)
        .outerjoin(PlayerCluster, PlayerCluster.id == EventPlayer.cluster_id)
        .where(
            Event.video_id == video.id,
            Event.code == "S",
            Event.side == "us",
            Event.deleted_at.is_(None),
            Event.segment_id.in_(list(rally_ids)),
        )
    ).all()
    for ev, ep, card in rows:
        assert ev.segment_id is not None
        coach = ev.source != "model" or ev.review in ("confirmed", "corrected")
        mine = ev.number is not None and (ep is None or ev.number != ep.applied_number)
        if coach and mine and ev.number is not None:
            out.setdefault(ev.segment_id, []).append((int(ev.number), COACH_SEEN))
            continue
        if ep is None or ep.method in ("sheet", "memory") or card is None or card.team != "us":
            continue
        if card.not_player:
            continue
        if card.number is not None and card.assigned_by is not None:
            out.setdefault(ev.segment_id, []).append((int(card.number), NAMED_SEEN))
        elif card.ocr_number is not None and card.kind == "person":
            p = number_prior.get(int(card.ocr_number), 0.0) if number_prior else 0.5
            right = min(READ_SEEN_MAX, tp.reading_confidence(card.ocr_confidence or 0.0, p))
            if right > 0.3:
                out.setdefault(ev.segment_id, []).append((int(card.ocr_number), right))
    return out


def prior_for_video(
    db: Session, video: Video, sheet: VideoLineups | None = None
) -> PriorLineups | None:
    """Our server and six per rally of the sets the sheet does not cover, from the team's
    memory, the set's starting six or setter zone (a coach's) and the servers seen. None:
    nothing to go on."""
    from wieplay.core import team_memory
    from wieplay.ml import team_prior as tp

    if video.sport not in team_memory.SPORTS:
        return None
    mem = team_memory.get(db, video.team_id)
    prior = team_memory.order_prior(mem)
    numbers = team_memory.number_prior(db, video.team_id, mem) if mem is not None else {}
    periods = list(
        db.scalars(
            select(Period)
            .where(Period.video_id == video.id, Period.deleted_at.is_(None))
            .order_by(Period.start_ms)
        )
    )
    covered = set(sheet.sets) if sheet is not None else set()
    todo = [p for p in periods if p.no not in covered]
    if not todo or (prior.empty and not any(p.start_lineup for p in todo)):
        return None
    starts = [p.start_ms for p in periods]
    rallies = list(
        db.scalars(
            select(Segment)
            .where(
                Segment.video_id == video.id,
                Segment.kind == "rally",
                Segment.deleted_at.is_(None),
                Segment.review.not_in(("rejected", "merged")),
            )
            .order_by(Segment.start_ms, Segment.id)
        )
    )
    by_period: dict[uuid.UUID, list[Segment]] = {}
    for seg in rallies:
        k = bisect.bisect_right(starts, seg.start_ms) - 1
        if k < 0:
            continue
        period = periods[k]
        if period.end_ms is not None and seg.start_ms > period.end_ms:
            continue
        by_period.setdefault(period.id, []).append(seg)
    ours = {s.id for s in rallies if serving_of(s) == "us"}
    seen_at = serve_observations(db, video, ours, numbers) if ours else {}
    out = PriorLineups()
    for period in todo:
        segs = by_period.get(period.id, [])
        if not segs:
            continue
        rots = rally_rotations(segs)
        seen = [
            tp.Seen(r, n, right)
            for seg, r in zip(segs, rots, strict=True)
            if serving_of(seg) == "us"
            for n, right in seen_at.get(seg.id, [])
        ]
        lineup = (
            period.start_lineup if period.start_lineup and len(period.start_lineup) == 6 else None
        )
        post = tp.posterior(
            prior,
            seen,
            setter_zone=period.start_rotation
            if period.start_rotation in (1, 2, 3, 4, 5, 6)
            else None,
            lineup=lineup,
        )
        if not post.offsets:
            continue
        start = post.lineup()
        anchors = "lineup" if lineup else ("setter zone" if period.start_rotation else None)
        out.sets[period.no] = {
            "anchor": anchors,
            "seen": len(seen),
            "start": list(start[0]) if start else None,
            "p": round(start[1], 3) if start else None,
        }
        for seg, r in zip(segs, rots, strict=True):
            out.rotation[seg.id] = (period.no, r)
            if start is not None:
                six = tuple(start[0][(r + z) % 6] for z in range(6))
                out.six[seg.id] = (six, start[1])
            if serving_of(seg) == "us":
                best = post.best(r)
                if best is not None:
                    out.servers[seg.id] = (best[0], round(best[1], 3))
    return out if out.sets else None
