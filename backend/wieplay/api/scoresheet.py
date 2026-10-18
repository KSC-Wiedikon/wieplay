"""Official scoresheets (PLAN §10.3a): the typed sheet of a match, its checks and numbers.

GET    /api/videos/{id}/scoresheet         the sheet, each set decoded and checked, its numbers
PUT    /api/videos/{id}/scoresheet         save it (whoever may tag); the match is scored again
POST   /api/videos/{id}/scoresheet/check   the checks of a sheet not saved yet (repairs too)
DELETE /api/videos/{id}/scoresheet         remove it; the match is scored again without it

Shirt numbers and scores only. A photo of the paper sheet comes later (SS3) with its own rules.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from wieplay.api.access import TeamAccess, team_refs, visible_video
from wieplay.api.deps import DbDep, ViewerDep, audit
from wieplay.core import scoresheet as ss
from wieplay.core import scoring, tagging
from wieplay.core.models import Period, Segment, Video, VideoRoster
from wieplay.core.permissions import (
    Viewer,
    can_delete_video,
    can_edit_sheet,
    can_see_sheet,
    can_upload,
)
from wieplay.ml import sheet as sh

router = APIRouter(prefix="/api", tags=["scoresheet"])


class IssueOut(BaseModel):
    code: str
    severity: str  # error | incomplete | warning
    box: dict[str, Any] | None  # {side, position 1-6, box 1-8}: the box it doubts
    params: dict[str, Any]


class RepairOut(BaseModel):
    kind: str  # change | insert | delete
    box: dict[str, Any]
    old: int | None
    new: int | None
    cost: float
    final: list[int]


class PointOut(BaseModel):
    winner: str
    server: str
    us: int
    them: int
    position: int  # the server's starting position (1-6)
    rot_us: int  # rotations so far: the player in zone I started in position (r mod 6) + 1
    rot_them: int
    penalty: bool


class NoteOut(BaseModel):
    """Where a timeout or substitution falls: after so many points (None: not on the path), and
    when in the video once the scoring lined the sheet up (the end of that point's rally)."""

    index: int
    at_points: int | None
    at_ms: int | None = None
    back_points: int | None = None  # substitutions: the starting player's return
    back_ms: int | None = None


class LineOut(BaseModel):
    points: int
    lost: int
    served: int
    break_won: int
    received: int
    side_out_won: int
    side_out_pct: float | None
    break_point_pct: float | None


class RotationOut(BaseModel):
    rotation: int  # 1-6: 1 is the starting six, 2 after the first rotation, ...
    server: int | None  # the shirt number in zone I (serving)
    setter_zone: int | None  # ours: the setter's zone (P1-P6), when the setter is known
    line: LineOut


class SideStatsOut(BaseModel):
    team: LineOut
    rotations: list[RotationOut]
    longest_run: int


class SetStatsOut(BaseModel):
    us: SideStatsOut
    them: SideStatsOut


class SetCheckOut(BaseModel):
    no: int
    deciding: bool
    status: str  # empty | incomplete | inconsistent | consistent
    use: str | None  # path (lined up with the rallies) | hint | None
    wanted: bool  # the coach's switch for this set
    final: list[int]
    official: list[int] | None
    last: dict[str, Any] | None  # the last box read
    issues: list[IssueOut]
    repair: RepairOut | None  # the clear cheapest repair, to suggest
    repairs: list[RepairOut]
    points: list[PointOut]
    timeouts: list[NoteOut]
    subs: list[NoteOut]
    stats: SetStatsOut | None
    # The newest scoring with the sheet: its points lined up with a rally, penalties, missed
    # rallies (points without one), rallies without a point, flagged rallies.
    scoring: dict[str, Any] | None = None


class ChecksOut(BaseModel):
    checks: list[SetCheckOut]
    trusted: bool
    match: SetStatsOut | None
    match_by_setter: bool


class SheetOut(ChecksOut):
    video_id: uuid.UUID
    exists: bool
    can_edit: bool
    version: int
    use_in_scoring: bool
    source: str | None
    sets: list[dict[str, Any]]
    entered_by: str | None
    entered_at: datetime | None
    updated_by: str | None
    updated_at: datetime | None
    conflicts: list[dict[str, Any]]
    official_sets: list[list[int]] | None
    # Our roster's numbers (setters marked) and the last match's first six, for the form.
    roster: list[dict[str, Any]]
    previous_lineup: list[int | None] | None
    no_scoresheet: bool


# --- helpers ------------------------------------------------------------------------------------


def _readable(db: Session, viewer: Viewer, video_id: uuid.UUID) -> tuple[Video, TeamAccess]:
    video, access = visible_video(db, viewer, video_id)
    manager = can_upload(viewer, access.ref) or can_delete_video(viewer, access.ref, access.leads)
    if (video.status != "ready" and not manager) or not can_see_sheet(
        viewer, access.ref, team_refs(db)
    ):
        raise HTTPException(404, "Video not found")
    if video.sport not in ss.SPORTS:
        raise HTTPException(404, "Scoresheets are for volleyball matches")
    return video, access


def _writable(db: Session, viewer: Viewer, video_id: uuid.UUID) -> Video:
    video, access = _readable(db, viewer, video_id)
    if not can_edit_sheet(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only those who tag this match enter its scoresheet")
    db.execute(
        select(Video)
        .where(Video.id == video.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    tagging.set_actor(db, viewer.member_id)
    tagging.ensure_roster(db, video)  # the setter's zone comes from the roster snapshot
    return video


def _line(line: sh.Line) -> LineOut:
    return LineOut.model_validate(line.as_json())


def _side_stats(
    stats: sh.SideStats, lineup: list[int | None] | None, setter: int | None
) -> SideStatsOut:
    rotations = []
    for r, line in enumerate(stats.rotations):
        server = lineup[r % 6] if lineup and len(lineup) == 6 else None
        rotations.append(
            RotationOut(
                rotation=r + 1,
                server=server,
                setter_zone=sh.zone_of(setter, r) if setter else None,
                line=_line(line),
            )
        )
    return SideStatsOut(team=_line(stats.team), rotations=rotations, longest_run=stats.longest_run)


def _setter_zones(db: Session, video: Video, checks: list[ss.SetCheck]) -> dict[int, int | None]:
    """Our setter's zone at each set's first serve: the set's own (a coach's or the sheet's),
    else the roster's one setter among the sheet's six."""
    periods = {
        p.no: p.start_rotation
        for p in db.scalars(
            select(Period).where(Period.video_id == video.id, Period.deleted_at.is_(None))
        )
    }
    setters = set(
        db.scalars(
            select(VideoRoster.number).where(
                VideoRoster.video_id == video.id,
                VideoRoster.side == "us",
                VideoRoster.position == "setter",
            )
        )
    )
    return {
        c.no: periods.get(c.no) or ss.setter_zone(c.sheet.lineup.get("us"), setters) for c in checks
    }


def _rally_ends(db: Session, video: Video) -> dict[tuple[int, int], int]:
    """(set, sheet point) -> the end of the rally the scoring lined up with it."""
    rows = db.execute(
        select(Period.no, Segment.sheet_point, Segment.end_ms)
        .join(Period, Period.id == Segment.period_id)
        .where(
            Segment.video_id == video.id,
            Segment.kind == "rally",
            Segment.deleted_at.is_(None),
            Segment.sheet_point.is_not(None),
        )
    ).all()
    return {(int(no), int(point)): int(end) for no, point, end in rows if point is not None}


def _at(ends: dict[tuple[int, int], int], no: int, points: int | None) -> int | None:
    """The end of the latest rally lined up with one of the first `points` points."""
    if not points:
        return None
    for k in range(points, 0, -1):
        if (no, k) in ends:
            return ends[(no, k)]
    return None


def _checks_out(
    checks: list[ss.SetCheck],
    sheet_on: bool,
    setter_zones: dict[int, int | None] | None = None,
    ends: dict[tuple[int, int], int] | None = None,
    scoring_sets: dict[str, Any] | None = None,
    official: list[tuple[int, int]] | None = None,
) -> ChecksOut:
    setter_zones = setter_zones or {}
    ends = ends or {}
    out: list[SetCheckOut] = []
    match = {"us": sh.SideStats(), "them": sh.SideStats()}
    by_setter: dict[int, sh.Line] = {}
    all_setters = True
    any_points = False
    for c in checks:
        d = c.decoded
        stats = sh.set_stats(d.points) if d.points else None
        set_stats = None
        if stats is not None:
            any_points = True
            setter = setter_zones.get(c.no)
            lineup_us = c.sheet.lineup.get("us")
            lineup_them = c.sheet.lineup.get("them")
            set_stats = SetStatsOut(
                us=_side_stats(stats["us"], list(lineup_us) if lineup_us else None, setter),
                them=_side_stats(stats["them"], list(lineup_them) if lineup_them else None, None),
            )
            if c.status == "consistent":
                for side in sh.SIDES:
                    match[side].add(stats[side])
                if setter:
                    for r, line in enumerate(stats["us"].rotations):
                        by_setter.setdefault(sh.zone_of(setter, r), sh.Line()).add(line)
                else:
                    all_setters = False
        out.append(
            SetCheckOut(
                no=c.no,
                deciding=c.sheet.deciding,
                status=c.status,
                use=c.use(sheet_on),
                wanted=c.wanted,
                final=list(d.final),
                official=list(c.official) if c.official else None,
                last=d.last.as_json() if d.last else None,
                issues=[IssueOut.model_validate(i.as_json()) for i in d.issues],
                repair=RepairOut.model_validate(c.repair.as_json()) if c.repair else None,
                repairs=[RepairOut.model_validate(r.as_json()) for r in c.repairs],
                points=[
                    PointOut(
                        winner=p.winner,
                        server=p.server,
                        us=p.us,
                        them=p.them,
                        position=p.position,
                        rot_us=p.rot_us,
                        rot_them=p.rot_them,
                        penalty=p.penalty,
                    )
                    for p in d.points
                ],
                timeouts=[
                    NoteOut(index=k, at_points=at, at_ms=_at(ends, c.no, at))
                    for k, at in enumerate(d.timeout_at)
                ],
                subs=[
                    NoteOut(
                        index=k,
                        at_points=came,
                        at_ms=_at(ends, c.no, came),
                        back_points=went,
                        back_ms=_at(ends, c.no, went),
                    )
                    for k, (came, went) in enumerate(d.sub_at)
                ],
                stats=set_stats,
                scoring=(scoring_sets or {}).get(str(c.no)),
            )
        )
    match_out = None
    consistent = [c for c in checks if c.status == "consistent"]
    if consistent and any_points:
        us = match["us"]
        if all_setters and by_setter:
            # Ours by the setter's zone (P1-P6) across the sets, theirs by the starting six.
            rotations = [
                RotationOut(rotation=z, server=None, setter_zone=z, line=_line(by_setter[z]))
                for z in sorted(by_setter)
            ]
            us_out = SideStatsOut(
                team=_line(us.team), rotations=rotations, longest_run=us.longest_run
            )
        else:
            us_out = _side_stats(us, None, None)
        match_out = SetStatsOut(us=us_out, them=_side_stats(match["them"], None, None))
    return ChecksOut(
        checks=out,
        trusted=sheet_on and ss.trusted(checks, official),
        match=match_out,
        match_by_setter=bool(all_setters and by_setter),
    )


def _state(db: Session, viewer: Viewer, video: Video, access: TeamAccess) -> SheetOut:
    row = ss.stored(db, video.id)
    official = ss.official_sets(db, video)
    sets = row.sets if row is not None else []
    checks = ss.check(sets, official)
    on = row.use_in_scoring if row is not None else True
    run = scoring.latest_run(db, video.id)
    sheet_metrics = (run.metrics or {}).get("sheet") if run is not None else None
    conflicts = []
    if row is not None:
        periods = {
            p.no: p
            for p in db.scalars(
                select(Period).where(Period.video_id == video.id, Period.deleted_at.is_(None))
            )
        }
        conflicts = _conflicts(periods, checks, on)
    found = _checks_out(
        checks,
        on,
        _setter_zones(db, video, checks),
        _rally_ends(db, video),
        (sheet_metrics or {}).get("sets") if isinstance(sheet_metrics, dict) else None,
        official,
    )
    roster = db.execute(
        select(VideoRoster.number, VideoRoster.position, VideoRoster.is_libero)
        .where(VideoRoster.video_id == video.id, VideoRoster.side == "us")
        .order_by(VideoRoster.number)
    ).all()
    return SheetOut(
        **found.model_dump(),
        video_id=video.id,
        exists=row is not None,
        can_edit=can_edit_sheet(viewer, access.ref, access.leads) and video.status == "ready",
        version=row.version if row is not None else 0,
        use_in_scoring=on,
        source=row.source if row is not None else None,
        sets=sets,
        entered_by=ss.member_name(db, row.created_by) if row is not None else None,
        entered_at=row.created_at if row is not None else None,
        updated_by=ss.member_name(db, row.updated_by) if row is not None else None,
        updated_at=row.updated_at if row is not None else None,
        conflicts=[c.as_json() for c in conflicts],
        official_sets=[list(s) for s in official] if official else None,
        roster=[
            {"number": n, "setter": position == "setter", "libero": bool(libero)}
            for n, position, libero in roster
        ],
        previous_lineup=ss.previous_lineup(db, video),
        no_scoresheet=video.no_scoresheet,
    )


def _conflicts(
    periods: dict[int, Period], checks: list[ss.SetCheck], on: bool
) -> list[ss.Conflict]:
    """A set's field a coach entered that the sheet says otherwise (read only: the fill is the
    scoring's)."""
    out = []
    for c in checks:
        period = periods.get(c.no)
        if period is None or c.use(on) != "path":
            continue
        pairs: list[tuple[str, Any]] = [
            ("first_serve", c.sheet.first_serve if not (period.start_us or period.start_them)
             else None),
            ("start_lineup", ss.lineup_of(c.sheet.lineup.get("us"))),
            ("start_lineup_them", ss.lineup_of(c.sheet.lineup.get("them"))),
        ]  # fmt: skip
        for name, value in pairs:
            have = getattr(period, name)
            if value is not None and have is not None and have != value:
                out.append(ss.Conflict(c.no, name, have, value))
    return out


# --- routes -------------------------------------------------------------------------------------


@router.get("/videos/{video_id}/scoresheet", response_model=SheetOut)
def get_scoresheet(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> SheetOut:
    video, access = _readable(db, viewer, video_id)
    if (
        can_edit_sheet(viewer, access.ref, access.leads)
        and video.status == "ready"
        and db.scalars(select(VideoRoster.id).where(VideoRoster.video_id == video.id)).first()
        is None
    ):
        # The roster snapshot, taken on first use as for tagging: the form lists our numbers.
        db.execute(select(Video).where(Video.id == video.id).with_for_update())
        tagging.ensure_roster(db, video)
        db.commit()
    out = _state(db, viewer, video, access)
    db.rollback()
    return out


@router.post("/videos/{video_id}/scoresheet/check", response_model=ChecksOut)
def check_scoresheet(
    video_id: uuid.UUID, body: ss.SheetIn, viewer: ViewerDep, db: DbDep
) -> ChecksOut:
    """The checks of a sheet as typed so far, without saving it: the form's repairs."""
    video, access = _readable(db, viewer, video_id)
    if not can_edit_sheet(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only those who tag this match enter its scoresheet")
    official = ss.official_sets(db, video)
    checks = ss.check([s.model_dump(mode="json") for s in body.sets], official)
    out = _checks_out(checks, body.use_in_scoring, official=official)
    db.rollback()
    return out


@router.put("/videos/{video_id}/scoresheet", response_model=SheetOut)
def put_scoresheet(video_id: uuid.UUID, body: ss.SheetIn, viewer: ViewerDep, db: DbDep) -> SheetOut:
    """Save the typed sheet; the match is scored again with it. A change based on an older
    version than the stored one (someone else saved meanwhile) is refused with 409."""
    video = _writable(db, viewer, video_id)
    if video.status != "ready":
        raise HTTPException(409, "Enter the scoresheet once the video is ready")
    now = tagging.now_utc()
    try:
        row = ss.save(db, video, body, viewer.member_id, now)
    except ss.Stale:
        db.rollback()
        raise HTTPException(409, "The scoresheet was changed meanwhile: reload it") from None
    video.no_scoresheet = False
    checks = ss.check(row.sets, ss.official_sets(db, video))
    ss.fill_periods(db, video, checks, row.use_in_scoring)
    scoring.score_video(db, video, reason="scoresheet")
    consistent = sum(1 for c in checks if c.status == "consistent")
    audit(db, viewer.member_id, "scoresheet_saved", "video", str(video.id), version=row.version,
          sets=len(row.sets), consistent=consistent)  # fmt: skip
    db.commit()
    _, access = visible_video(db, viewer, video.id)
    return _state(db, viewer, video, access)


@router.delete("/videos/{video_id}/scoresheet", status_code=204)
def delete_scoresheet(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> Response:
    """Remove the sheet (kept as a revision); the match is scored again without it. What it
    filled into the sets stays: a coach's to change."""
    video = _writable(db, viewer, video_id)
    if not ss.remove(db, video, viewer.member_id, tagging.now_utc()):
        raise HTTPException(404, "The video has no scoresheet")
    if video.status == "ready":
        scoring.score_video(db, video, reason="scoresheet")
    audit(db, viewer.member_id, "scoresheet_removed", "video", str(video.id))
    db.commit()
    return Response(status_code=204)
