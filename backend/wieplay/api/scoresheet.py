"""Official scoresheets (PLAN §10.3a): the typed sheet of a match, its checks and numbers.

GET    /api/videos/{id}/scoresheet         the sheet, each set decoded and checked, its numbers
PUT    /api/videos/{id}/scoresheet         save it (whoever may tag); the match is scored again
POST   /api/videos/{id}/scoresheet/check   the checks of a sheet not saved yet (repairs too)
POST   /api/videos/{id}/scoresheet/correct apply the correction the scoring found for a set
                                           (the board log and the video against the paper): a
                                           new version, the paper's kept in the revisions
DELETE /api/videos/{id}/scoresheet         remove it (and its photos); scored again without it
GET    /api/videos/{id}/scoresheet/court   per rally lined up with it: who served (team and
                                           number) and both teams' six by zone; its timeouts,
                                           substitutions and cards at their breaks, checked
                                           (wieplay/core/lineups.py, sheet_notes.py)

Photos of the paper sheet (SS3, wieplay/core/sheet_photos.py), for the team's coaches, team
managers, sport admins and admins only (permissions.can_see_sheet_photo):
GET    /api/videos/{id}/scoresheet/images             the photos
POST   /api/videos/{id}/scoresheet/images             add one: the redacted JPEG/PNG/WebP as the
                                                      body (≤ 10 MB), `redacted=true`
GET    /api/videos/{id}/scoresheet/images/{image}     302 to a URL signed for 5 minutes, audited
DELETE /api/videos/{id}/scoresheet/images/{image}     remove it: the bucket object goes at once

Reading the photos on club hardware (SS4, wieplay/core/sheet_reading.py), for whoever may type
the sheet (the photo itself stays under the photo rule):
POST   /api/videos/{id}/scoresheet/read      read the photos (a job on the Lenovo's CPU), or
                                             decode the last reading again ("we are team B")
DELETE /api/videos/{id}/scoresheet/read      drop the drafts it made
POST   /api/videos/{id}/scoresheet/confirm   save the sheet and confirm drafts: they are typed
                                             sets from then on, scored; their cells are labels

The typed sheet holds shirt numbers and scores only; the photos carry the players' names
(dates of birth blacked out before the upload), hence their narrower rule.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from wieplay.api.access import TeamAccess, team_refs, visible_video
from wieplay.api.deps import DbDep, SettingsDep, StoreDep, ViewerDep, audit
from wieplay.api.media import PRIVATE
from wieplay.core import lineups, scoring, tagging
from wieplay.core import scoresheet as ss
from wieplay.core import sheet_photos as sp
from wieplay.core import sheet_reading as sr
from wieplay.core.models import Period, ScoresheetImage, Segment, Video, VideoRoster
from wieplay.core.permissions import (
    Viewer,
    can_delete_video,
    can_edit_sheet,
    can_edit_sheet_photo,
    can_see_sheet,
    can_see_sheet_photo,
    can_upload,
)
from wieplay.core.storage import ObjectStore
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


class CorrectionOut(BaseModel):
    """Where the joint decode departs from a typed set (PLAN §10.3a): the boxes to change so the
    set says what the board log and the video say (`old` the typed value, `new` theirs), from
    which score to which, and on whose word (log, video, coach, board)."""

    boxes: list[RepairOut]
    from_score: list[int] = Field(alias="from")
    to: list[int]
    sources: list[str]

    model_config = {"populate_by_name": True}


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
    # The boxes the newest scoring found otherwise than typed (while the set is as it was then).
    correction: CorrectionOut | None = None


class ChecksOut(BaseModel):
    checks: list[SetCheckOut]
    trusted: bool
    match: SetStatsOut | None
    match_by_setter: bool


class PhotoOut(BaseModel):
    """A photo of the paper sheet; `url` answers with a short-lived signed link (audited)."""

    id: uuid.UUID
    page: int
    set_no: int | None
    width: int
    height: int
    bytes: int
    uploaded_by: str | None
    uploaded_at: datetime
    url: str


class SheetOut(ChecksOut):
    video_id: uuid.UUID
    exists: bool  # something was typed in (a row with photos only is not a sheet yet)
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
    # The photos of the paper sheet: None for whoever may not see them (players, helpers).
    images: list[PhotoOut] | None = None
    can_add_images: bool = False
    # SS4: draft (a set read from the photo awaits confirmation) | entered; the reading's state,
    # its drafts and the cells to check (wieplay/core/sheet_reading.py state()).
    status: str = "entered"
    read: dict[str, Any] | None = None
    can_read: bool = False


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


def _writable(db: Session, viewer: Viewer, video_id: uuid.UUID) -> tuple[Video, TeamAccess]:
    video, access = _readable(db, viewer, video_id)
    if not can_edit_sheet(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only those who tag this match enter its scoresheet")
    _lock(db, video)
    tagging.set_actor(db, viewer.member_id)
    tagging.ensure_roster(db, video)  # the setter's zone comes from the roster snapshot
    return video, access


def _lock(db: Session, video: Video) -> None:
    db.execute(
        select(Video)
        .where(Video.id == video.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _photos(db: Session, viewer: Viewer, video_id: uuid.UUID, *, write: bool) -> Video:
    """A video whose sheet photos the viewer may see (or add and remove): its team's coaches,
    team managers, sport admins and admins. Players and helpers see the typed sheet only."""
    video, access = _readable(db, viewer, video_id)
    rule = can_edit_sheet_photo if write else can_see_sheet_photo
    if not rule(viewer, access.ref, access.leads):
        raise HTTPException(
            403, "Only the team's coaches, team managers and admins see the scoresheet's photos"
        )
    return video


def _photo_out(db: Session, video: Video, row: ScoresheetImage) -> PhotoOut:
    return PhotoOut(
        id=row.id,
        page=row.page,
        set_no=row.set_no,
        width=row.width,
        height=row.height,
        bytes=row.bytes,
        uploaded_by=ss.member_name(db, row.uploaded_by),
        uploaded_at=row.uploaded_at,
        url=f"/api/videos/{video.id}/scoresheet/images/{row.id}",
    )


def _refused(exc: sp.PhotoRefused) -> HTTPException:
    return HTTPException(exc.status, {"message": exc.message, "code": exc.code})


async def photo_body(request: Request) -> bytes:
    """The uploaded photo's bytes, at most 10 MB, read before anything is decoded. Resolved
    after the viewer (a dependency listed later), so nobody uploads without a session."""
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind not in sp.FORMATS:
        raise _refused(sp.PhotoRefused(415, "type", "A scoresheet photo is a JPEG, PNG or WebP."))
    too_large = sp.PhotoRefused(413, "too_large", "A scoresheet photo is at most 10 MB.")
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > sp.MAX_BYTES:
        raise _refused(too_large)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > sp.MAX_BYTES:
            raise _refused(too_large)
    return bytes(data)


PhotoBody = Annotated[bytes, Depends(photo_body)]


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
                correction=_correction_out(c, sheet_on, (scoring_sets or {}).get(str(c.no))),
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


def _correction_out(c: ss.SetCheck, on: bool, scored: Any) -> CorrectionOut | None:
    if c.use(on) != "path" or not isinstance(scored, dict):
        return None
    found = ss.correction_now(c, scored.get("correction"))
    return CorrectionOut.model_validate(found) if found is not None else None


def _state(db: Session, viewer: Viewer, video: Video, access: TeamAccess) -> SheetOut:
    row = ss.stored(db, video.id)
    typed = ss.typed(row)
    official = ss.official_sets(db, video)
    sets = row.sets if row is not None else []
    checks = ss.check(sets, official, ss.drafts_of(row))
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
    photos = None
    sees_photos = can_see_sheet_photo(viewer, access.ref, access.leads)
    live = sp.live(db, row)
    if sees_photos:
        photos = [_photo_out(db, video, r) for r in live]
    entered = row if typed else None
    edit = can_edit_sheet(viewer, access.ref, access.leads) and video.status == "ready"
    return SheetOut(
        **found.model_dump(),
        video_id=video.id,
        exists=typed,
        can_edit=edit,
        version=row.version if row is not None else 0,
        use_in_scoring=on,
        source=entered.source if entered is not None else None,
        sets=sets,
        entered_by=ss.member_name(db, entered.created_by) if entered is not None else None,
        entered_at=entered.created_at if entered is not None else None,
        updated_by=ss.member_name(db, entered.updated_by) if entered is not None else None,
        updated_at=entered.updated_at if entered is not None else None,
        conflicts=[c.as_json() for c in conflicts],
        official_sets=[list(s) for s in official] if official else None,
        roster=[
            {"number": n, "setter": position == "setter", "libero": bool(libero)}
            for n, position, libero in roster
        ],
        previous_lineup=ss.previous_lineup(db, video),
        no_scoresheet=video.no_scoresheet,
        images=photos,
        can_add_images=can_edit_sheet_photo(viewer, access.ref, access.leads)
        and video.status == "ready",
        status=row.status if row is not None else "entered",
        read=sr.state(db, video, row, photos_visible=sees_photos),
        can_read=edit and bool(live),
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


class CourtRallyOut(BaseModel):
    """A rally the sheet was lined up with: its point, who served (team and number), both
    teams' six in zones I-VI, their rotations so far, the point's winner."""

    rally: uuid.UUID
    set: int
    point: int  # 1-based, in the set's points
    serving: str
    server: int | None
    us: list[int | None] | None
    them: list[int | None] | None
    rotation_us: int
    rotation_them: int
    winner: str
    penalty: bool


class SheetNoteOut(BaseModel):
    """A timeout, substitution (or return) or sanction of the sheet in the video's break after
    the rally of its last point, and what disagrees there (wieplay/core/sheet_notes.py)."""

    set: int
    kind: str  # timeout | sub | sub_back | sanction
    index: int
    side: str
    score: list[int]  # us, them
    at_points: int | None
    sanction: str | None  # W P E D DW DP (no person)
    number_in: int | None
    position: int | None
    rally: uuid.UUID | None
    start_ms: int | None
    end_ms: int | None
    line: list[int] | None  # the score line after that rally
    log: list[int] | None  # the club scoreboard's log there
    issues: list[str]  # score_line | log | short_break | penalty_rally | off_path | no_rally


class CourtOut(BaseModel):
    """Who was on court per rally and the sheet's notes in the video (PLAN §10.3a, §10.5);
    empty without a sheet lined up with the video."""

    rallies: list[CourtRallyOut]
    notes: list[SheetNoteOut]
    liberos: dict[str, list[int]]


@router.get("/videos/{video_id}/scoresheet/court", response_model=CourtOut)
def sheet_court(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> CourtOut:
    video, _ = _readable(db, viewer, video_id)
    found = lineups.for_video(db, video)
    if found is None:
        return CourtOut(rallies=[], notes=[], liberos={})
    rallies = [
        CourtRallyOut(rally=rid, **lu.as_json())
        for rid, lu in sorted(found.by_rally.items(), key=lambda kv: (kv[1].set_no, kv[1].point))
    ]
    return CourtOut(
        rallies=rallies,
        notes=[SheetNoteOut.model_validate(p.as_json()) for p in lineups.notes(db, video, found)],
        liberos={k: sorted(v) for k, v in found.liberos.items() if v},
    )


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


class CorrectIn(BaseModel):
    """Apply the scoring's correction to this set; `base_version`: the version the page saw."""

    set: int = Field(ge=1, le=ss.MAX_SETS)
    base_version: int = Field(ge=0)


@router.post("/videos/{video_id}/scoresheet/correct", response_model=SheetOut)
def correct_scoresheet(
    video_id: uuid.UUID, body: CorrectIn, viewer: ViewerDep, db: DbDep
) -> SheetOut:
    """Change a typed set's boxes as the newest scoring found them (the board log and the video
    against what the scorer wrote): saved as a new version, the paper's stays in the revisions,
    audited as a correction from those records; the match is scored again."""
    video, _ = _writable(db, viewer, video_id)
    if video.status != "ready":
        raise HTTPException(409, "Correct the scoresheet once the video is ready")
    row = ss.stored(db, video.id)
    if row is None or not ss.typed(row):
        raise HTTPException(404, "The video has no scoresheet")
    if row.version != body.base_version:
        raise HTTPException(409, "The scoresheet was changed meanwhile: reload it")
    run = scoring.latest_run(db, video.id)
    sheet_metrics = (run.metrics or {}).get("sheet") if run is not None else None
    scored = ((sheet_metrics or {}).get("sets") or {}).get(str(body.set)) if isinstance(
        sheet_metrics, dict) else None  # fmt: skip
    found = scored.get("correction") if isinstance(scored, dict) else None
    if not isinstance(found, dict):
        raise HTTPException(409, "WiePlay has no correction for this set")
    try:
        sets = ss.apply_correction(row.sets, body.set, found)
    except ss.CorrectionStale:
        raise HTTPException(409, "The set was changed since: score the match again") from None
    now = tagging.now_utc()
    sheet_in = ss.SheetIn(base_version=row.version, sets=sets, use_in_scoring=row.use_in_scoring)
    try:
        row = ss.save(db, video, sheet_in, viewer.member_id, now)
    except ss.Stale:
        db.rollback()
        raise HTTPException(409, "The scoresheet was changed meanwhile: reload it") from None
    checks = ss.check(row.sets, ss.official_sets(db, video), ss.drafts_of(row))
    ss.fill_periods(db, video, checks, row.use_in_scoring)
    scoring.score_video(db, video, reason="scoresheet")
    boxes = [
        {"side": e["box"]["side"], "position": e["box"]["position"], "box": e["box"]["box"],
         "paper": e.get("old"), "value": e.get("new")}
        for e in found["boxes"]
    ]  # fmt: skip
    audit(db, viewer.member_id, "scoresheet_corrected", "video", str(video.id),
          version=row.version, set=body.set, boxes=boxes, sources=found.get("sources") or [],
          span=[found.get("from"), found.get("to")])  # fmt: skip
    db.commit()
    _, access = visible_video(db, viewer, video.id)
    return _state(db, viewer, video, access)


@router.put("/videos/{video_id}/scoresheet", response_model=SheetOut)
def put_scoresheet(video_id: uuid.UUID, body: ss.SheetIn, viewer: ViewerDep, db: DbDep) -> SheetOut:
    """Save the typed sheet; the match is scored again with it. A change based on an older
    version than the stored one (someone else saved meanwhile) is refused with 409."""
    video, _ = _writable(db, viewer, video_id)
    if video.status != "ready":
        raise HTTPException(409, "Enter the scoresheet once the video is ready")
    now = tagging.now_utc()
    try:
        row = ss.save(db, video, body, viewer.member_id, now)
    except ss.Stale:
        db.rollback()
        raise HTTPException(409, "The scoresheet was changed meanwhile: reload it") from None
    video.no_scoresheet = False
    checks = ss.check(row.sets, ss.official_sets(db, video), ss.drafts_of(row))
    ss.fill_periods(db, video, checks, row.use_in_scoring)
    scoring.score_video(db, video, reason="scoresheet")
    consistent = sum(1 for c in checks if c.status == "consistent")
    audit(db, viewer.member_id, "scoresheet_saved", "video", str(video.id), version=row.version,
          sets=len(row.sets), consistent=consistent)  # fmt: skip
    db.commit()
    _, access = visible_video(db, viewer, video.id)
    return _state(db, viewer, video, access)


@router.delete("/videos/{video_id}/scoresheet", status_code=204)
def delete_scoresheet(
    video_id: uuid.UUID, viewer: ViewerDep, db: DbDep, request: Request
) -> Response:
    """Remove the sheet (kept as a revision); the match is scored again without it. What it
    filled into the sets stays: a coach's to change. Its photos go with it, so a sheet with
    photos is removed only by those who may remove photos."""
    video, access = _writable(db, viewer, video_id)
    row = ss.stored(db, video.id)
    photos = sp.live(db, row)
    if photos and not can_edit_sheet_photo(viewer, access.ref, access.leads):
        raise HTTPException(403, "The scoresheet has photos: the team's coaches remove it")
    now = tagging.now_utc()
    if not (ss.typed(row) or photos) or not ss.remove(db, video, viewer.member_id, now):
        raise HTTPException(404, "The video has no scoresheet")
    sp.mark_removed(photos, viewer.member_id, now)
    if video.status == "ready":
        scoring.score_video(db, video, reason="scoresheet")
    audit(db, viewer.member_id, "scoresheet_removed", "video", str(video.id),
          photos=len(photos))  # fmt: skip
    db.commit()
    store: ObjectStore | None = request.app.state.store
    if photos and store is not None and sp.purge(db, store, photos, now):
        db.commit()
    return Response(status_code=204)


# --- photos of the paper sheet (SS3) -----------------------------------------------------------


@router.get("/videos/{video_id}/scoresheet/images", response_model=list[PhotoOut])
def list_photos(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> list[PhotoOut]:
    video = _photos(db, viewer, video_id, write=False)
    out = [_photo_out(db, video, r) for r in sp.live(db, ss.stored(db, video.id))]
    db.rollback()
    return out


@router.post("/videos/{video_id}/scoresheet/images", response_model=PhotoOut, status_code=201)
def add_photo(
    video_id: uuid.UUID,
    viewer: ViewerDep,
    db: DbDep,
    store: StoreDep,
    request: Request,
    response: Response,
    body: PhotoBody,
    redacted: Annotated[bool, Query()] = False,
    set_no: Annotated[int | None, Query(ge=1, le=5)] = None,
    boxes: Annotated[int, Query(ge=0, le=50)] = 0,
    rotation: Annotated[int, Query(ge=0, le=270, multiple_of=90)] = 0,
    cropped: Annotated[bool, Query()] = False,
) -> PhotoOut:
    """Keep a photo of the paper sheet. Only what WiePlay's redaction step made is taken: the
    coach confirmed the dates of birth are blacked out (`redacted`), and the image carries no
    camera metadata. The API stores its own JPEG of the pixels; the same photo again (a
    resend) answers 200 with the one kept already."""
    video = _photos(db, viewer, video_id, write=True)
    _, access = visible_video(db, viewer, video.id)
    if video.status != "ready":
        raise HTTPException(409, "Add the scoresheet's photo once the video is ready")
    if not redacted:
        raise _refused(
            sp.PhotoRefused(
                422,
                "not_redacted",
                "Black out the dates of birth and licence numbers first, then upload.",
            )
        )
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    try:
        photo = sp.clean(body, content_type)
    except sp.PhotoRefused as exc:
        raise _refused(exc) from None
    _lock(db, video)
    tagging.set_actor(db, viewer.member_id)
    sheet = ss.ensure_row(db, video, viewer.member_id)
    redaction = {"boxes": boxes, "rotation": rotation, "cropped": cropped}
    try:
        row, created = sp.add(
            db, store, video, sheet, photo, set_no=set_no, redaction=redaction,
            actor=viewer.member_id,
        )  # fmt: skip
    except sp.PhotoRefused as exc:
        db.rollback()
        raise _refused(exc) from None
    except (BotoCoreError, ClientError):
        db.rollback()
        raise HTTPException(503, "The photo could not be stored: try again") from None
    if created:
        video.no_scoresheet = False
        audit(db, viewer.member_id, "scoresheet_photo_uploaded", "video", str(video.id),
              image=str(row.id), bytes=row.bytes, width=row.width, height=row.height,
              boxes=boxes)  # fmt: skip
        # Nothing typed yet: read the photos right away (SS4), on club hardware.
        if sr.wants_auto(sheet) and can_edit_sheet(viewer, access.ref, access.leads):
            sr.queue(db, video, sheet, sp.live(db, sheet), viewer.member_id, auto=True)
    db.commit()
    response.status_code = 201 if created else 200
    return _photo_out(db, video, row)


@router.get("/videos/{video_id}/scoresheet/images/{image_id}")
def view_photo(
    video_id: uuid.UUID,
    image_id: uuid.UUID,
    viewer: ViewerDep,
    db: DbDep,
    store: StoreDep,
    settings: SettingsDep,
) -> RedirectResponse:
    """A 302 to the photo, signed for 5 minutes (like video media, PLAN §7.6); every view is
    in the audit log."""
    video = _photos(db, viewer, video_id, write=False)
    row = sp.get(db, video.id, image_id)
    if row is None:
        raise HTTPException(404, "Photo not found")
    url = store.presign_get(row.object_key, settings.media_url_ttl_seconds)
    audit(db, viewer.member_id, "scoresheet_photo_viewed", "video", str(video.id),
          image=str(row.id))  # fmt: skip
    db.commit()
    return RedirectResponse(url, status_code=302, headers=PRIVATE)


@router.delete("/videos/{video_id}/scoresheet/images/{image_id}", status_code=204)
def remove_photo(
    video_id: uuid.UUID, image_id: uuid.UUID, viewer: ViewerDep, db: DbDep, store: StoreDep
) -> Response:
    """Remove a photo: its object leaves the bucket now (the sweep retries if the bucket is
    unreachable); the typed sheet stays."""
    video = _photos(db, viewer, video_id, write=True)
    _lock(db, video)
    row = sp.get(db, video.id, image_id)
    if row is None:
        raise HTTPException(404, "Photo not found")
    now = tagging.now_utc()
    sp.mark_removed([row], viewer.member_id, now)
    audit(db, viewer.member_id, "scoresheet_photo_removed", "video", str(video.id),
          image=str(row.id))  # fmt: skip
    db.commit()
    if sp.purge(db, store, [row], now):
        db.commit()
    return Response(status_code=204)


# --- reading the photos (SS4) ---------------------------------------------------------------------


class ReadIn(BaseModel):
    """`us`: decode the last reading again with us as team A or B on the sheet (no new job);
    without it the photos are read anew."""

    us: Literal["A", "B"] | None = None


class ConfirmIn(ss.SheetIn):
    """The sheet as the coach checked it, and the drafts to confirm."""

    confirm: list[int] = Field(default_factory=list, max_length=ss.MAX_SETS)


@router.post("/videos/{video_id}/scoresheet/read", response_model=SheetOut)
def read_photos(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep,
                body: ReadIn | None = None) -> SheetOut:  # fmt: skip
    """Read the sheet's photos on club hardware (a job), or decode the last reading again as
    team A or B. The drafts never replace what someone typed."""
    video, _ = _writable(db, viewer, video_id)
    if video.status != "ready":
        raise HTTPException(409, "Read the scoresheet once the video is ready")
    row = ss.stored(db, video.id)
    photos = sp.live(db, row)
    if row is None or not photos:
        raise HTTPException(409, "The scoresheet has no photo to read")
    if body is not None and body.us is not None:
        if not any(p.reading and p.reading.get("cells") for p in photos):
            raise HTTPException(409, "The photos have not been read yet")
        summary = sr.apply(db, video, row, photos, actor=viewer.member_id, us_letter=body.us)
        audit(db, viewer.member_id, "scoresheet_read_again", "video", str(video.id),
              us=body.us, **summary)  # fmt: skip
    else:
        job = sr.queue(db, video, row, photos, viewer.member_id)
        audit(db, viewer.member_id, "scoresheet_read_requested", "video", str(video.id),
              job=str(job.id) if job else None, photos=len(photos))  # fmt: skip
    db.commit()
    _, access = visible_video(db, viewer, video.id)
    return _state(db, viewer, video, access)


@router.delete("/videos/{video_id}/scoresheet/read", response_model=SheetOut)
def discard_reading(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> SheetOut:
    """Drop the drafts the reading made: an empty set goes again, a typed one is as it was."""
    video, _ = _writable(db, viewer, video_id)
    row = ss.stored(db, video.id)
    if row is None or not ss.drafts_of(row):
        raise HTTPException(404, "The scoresheet has no draft from the photo")
    n = sr.discard(db, row, viewer.member_id)
    if video.status == "ready":
        scoring.score_video(db, video, reason="scoresheet")  # the drafts were evidence
    audit(db, viewer.member_id, "scoresheet_read_discarded", "video", str(video.id), sets=n)
    db.commit()
    _, access = visible_video(db, viewer, video.id)
    return _state(db, viewer, video, access)


@router.post("/videos/{video_id}/scoresheet/confirm", response_model=SheetOut)
def confirm_sheet(video_id: uuid.UUID, body: ConfirmIn, viewer: ViewerDep, db: DbDep) -> SheetOut:
    """Save the sheet as checked and confirm the given drafts: typed sets from then on, scored;
    their cells are kept as labelled examples (where the cell is, what it holds)."""
    video, _ = _writable(db, viewer, video_id)
    if video.status != "ready":
        raise HTTPException(409, "Enter the scoresheet once the video is ready")
    row = ss.stored(db, video.id)
    drafts = ss.drafts_of(row)
    missing = sorted(set(body.confirm) - drafts)
    if not body.confirm or missing:
        raise HTTPException(409, f"No draft to confirm for set {missing[0] if missing else '-'}")
    now = tagging.now_utc()
    sheet_in = ss.SheetIn.model_validate(body.model_dump(exclude={"confirm"}))
    try:
        row = ss.save(db, video, sheet_in, viewer.member_id, now)
    except ss.Stale:
        db.rollback()
        raise HTTPException(409, "The scoresheet was changed meanwhile: reload it") from None
    done = sr.confirm(db, video, row, body.confirm, viewer.member_id, now)
    row.version += 1
    video.no_scoresheet = False
    checks = ss.check(row.sets, ss.official_sets(db, video), ss.drafts_of(row))
    ss.fill_periods(db, video, checks, row.use_in_scoring)
    scoring.score_video(db, video, reason="scoresheet")
    audit(db, viewer.member_id, "scoresheet_confirmed", "video", str(video.id),
          version=row.version, **done)  # fmt: skip
    db.commit()
    _, access = visible_video(db, viewer, video.id)
    return _state(db, viewer, video, access)
