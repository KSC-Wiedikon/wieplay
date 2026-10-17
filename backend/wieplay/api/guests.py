"""Guest clubs (PLAN §4.5): their login, invite links, a guest team's people, and the admins'
"Guest clubs" section.

/api/auth/guest/login            email and password → a session like the Wiedisync callback's
/api/auth/guest/invite/{token}   what a link is for (GET), set up the account from it (POST)
/api/teams/{id}/people           a guest team's roster: its coaches add, edit and remove
                                 people and make invite links; its players read it
/api/teams/{id}/adults           the coach's "everyone we film is 18 or older"
/api/admin/guest-teams           admins: create guest teams, limits, training OK, on/off,
                                 the first coach's invite

The rules (who sees and does what) are in core/permissions.py, the logic in core/guests.py.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import AfterValidator, BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from wieplay.api.access import TeamAccess, visible_team
from wieplay.api.deps import (
    SESSION_COOKIE,
    AdminDep,
    DbDep,
    SettingsDep,
    ViewerDep,
    audit,
    hash_session_id,
)
from wieplay.api.media import permission_cache
from wieplay.core import guests
from wieplay.core.config import Settings
from wieplay.core.filming import club_today
from wieplay.core.models import (
    GuestAccount,
    TeamLineage,
    UserSession,
    WdMember,
    WdTeam,
    WdTeamMember,
)
from wieplay.core.permissions import Viewer, can_manage_people
from wieplay.core.pipeline import season_of

log = logging.getLogger(__name__)
auth_router = APIRouter(prefix="/api/auth/guest", tags=["guests"])
router = APIRouter(prefix="/api", tags=["guests"])

NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
WRONG_LOGIN = "Wrong email or password"
SEASON = re.compile(r"^\d{4}/\d{2}$")


def _plain_name(value: str) -> str:
    """Names go into the stats CSV and the export's watermark: none starts like a spreadsheet
    formula."""
    if value[:1] in ("=", "+", "-", "@"):
        raise ValueError("A name starts with a letter or a digit")
    return value


Name = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
    AfterValidator(_plain_name),
]
Number = Annotated[int, Field(ge=0, le=99)]


def _set_session(
    response: Response, db: DbDep, request: Request, settings: Settings, member: WdMember
) -> None:
    """Start a guest session (PLAN §4.5) the way the Wiedisync callback does, for 14 days."""
    raw = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.add(
        UserSession(
            id_hash=hash_session_id(raw),
            member_id=member.id,
            claims=guests.session_claims(db, member),
            created_at=now,
            last_seen_at=now,
            expires_at=now + guests.SESSION_TTL,
            user_agent=(request.headers.get("user-agent") or "")[:255] or None,
        )
    )
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=int(guests.SESSION_TTL.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


# --- login ------------------------------------------------------------------------------------


class LoginIn(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1000)


@auth_router.post("/login", status_code=204)
def guest_login(body: LoginIn, request: Request, db: DbDep, settings: SettingsDep) -> Response:
    """One answer for every failure (wrong password, unknown email, locked, disabled), so the
    form tells nobody which emails have an account. A burst of logins gets 429."""
    try:
        account = guests.authenticate(db, body.email, body.password)
    except guests.Busy as exc:
        db.rollback()
        raise HTTPException(429, str(exc), headers={**NO_STORE, "Retry-After": "60"}) from None
    if account is None:
        known = db.scalar(
            select(GuestAccount.member_id).where(
                GuestAccount.email == guests.normalise_email(body.email)
            )
        )
        if known is not None:  # unknown emails are not logged: a flood would fill the log
            audit(db, None, "guest_login_failed", "guest_account", str(known))
        else:
            log.info("guest login with an unknown email")
        db.commit()
        raise HTTPException(401, WRONG_LOGIN, headers=NO_STORE)
    member = db.get(WdMember, account.member_id)
    if member is None:
        db.commit()
        raise HTTPException(401, WRONG_LOGIN, headers=NO_STORE)
    response = Response(status_code=204, headers=NO_STORE)
    _set_session(response, db, request, settings, member)
    audit(db, member.id, "guest_login")
    db.commit()
    return response


# --- invites ----------------------------------------------------------------------------------


class InviteOut(BaseModel):
    club: str
    team: str
    team_id: int
    season: str
    sport: str
    name: str
    role: Literal["player", "coach"]
    # An account exists: the link sets a new password (no email asked).
    has_account: bool


class AcceptIn(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    password: str = Field(min_length=1, max_length=1000)
    accept_privacy: Literal[True]


class NextOut(BaseModel):
    next: str


def _invite_error(exc: guests.GuestError) -> HTTPException:
    if isinstance(exc, guests.InviteUnknown):
        return HTTPException(404, str(exc), headers=NO_STORE)
    if isinstance(exc, guests.InviteGone):
        return HTTPException(410, str(exc), headers=NO_STORE)
    if isinstance(exc, guests.EmailTaken):
        return HTTPException(409, str(exc), headers=NO_STORE)
    return HTTPException(422, str(exc), headers=NO_STORE)


def _role(roles: tuple[str, ...]) -> Literal["player", "coach"]:
    return "coach" if "coach" in roles else "player"


@auth_router.get("/invite/{token}", response_model=InviteOut)
def invite_info(token: str, db: DbDep, response: Response) -> InviteOut:
    try:
        found = guests.open_invite(db, token)
    except guests.GuestError as exc:
        raise _invite_error(exc) from None
    response.headers.update(NO_STORE)
    return InviteOut(
        club=found.club,
        team=found.team.name,
        team_id=found.team.id,
        season=found.team.season,
        sport=found.team.sport,
        name=found.member.display_name,
        role=_role(found.roles),
        has_account=found.account is not None,
    )


@auth_router.post("/invite/{token}", response_model=NextOut)
def accept_invite(
    token: str, body: AcceptIn, request: Request, db: DbDep, settings: SettingsDep
) -> Response:
    """Set up the account (or a new password) from the link, and log in."""
    try:
        found = guests.accept_invite(db, token, email=body.email, password=body.password)
    except guests.Busy as exc:
        db.rollback()
        raise HTTPException(429, str(exc), headers={**NO_STORE, "Retry-After": "60"}) from None
    except guests.GuestError as exc:
        db.rollback()
        raise _invite_error(exc) from None
    except IntegrityError:  # the same email set up from another link at the same moment
        db.rollback()
        taken = guests.EmailTaken("This email address belongs to another account")
        raise _invite_error(taken) from None
    next_path = f"/teams/{found.team.id}"
    response = Response(
        NextOut(next=next_path).model_dump_json(),
        media_type="application/json",
        headers=NO_STORE,
    )
    _set_session(response, db, request, settings, found.member)
    audit(db, found.member.id, "guest_invite_accepted", "wd_team", str(found.team.id),
          new_account=found.created)  # fmt: skip
    audit(db, found.member.id, "guest_login")
    db.commit()
    return response


# --- a guest team's people --------------------------------------------------------------------


class PersonOut(BaseModel):
    member_id: int
    name: str
    number: int | None
    role: Literal["player", "coach"]
    # For the team's coaches and admins only; None for players.
    account: bool | None = None
    disabled: bool | None = None
    last_login_at: datetime | None = None
    invite_expires_at: datetime | None = None
    me: bool = False


class PersonIn(BaseModel):
    name: Name
    number: Number | None = None
    role: Literal["player", "coach"] = "player"


class PersonEdit(BaseModel):
    """Fields left out stay; number null takes it away."""

    name: Name | None = None
    number: Number | None = None


class InviteLinkOut(BaseModel):
    link: str  # shown once: only its hash is kept
    expires_at: datetime


class AdultsOut(BaseModel):
    adults_declared_at: datetime


def _guest_team(db: DbDep, viewer: Viewer, team_id: int) -> TeamAccess:
    access = visible_team(db, viewer, team_id)
    if not access.team.guest:
        raise HTTPException(404, "Only a guest club's team has its people here")
    return access


def _managing(db: DbDep, viewer: Viewer, team_id: int) -> TeamAccess:
    access = _guest_team(db, viewer, team_id)
    if not can_manage_people(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches and admins change its people")
    return access


def _person_out(person: guests.Person, viewer: Viewer, full: bool) -> PersonOut:
    out = PersonOut(
        member_id=person.member_id,
        name=person.name,
        number=person.number,
        role=_role(person.roles),
        me=person.member_id == viewer.member_id,
    )
    if full:
        out.account = person.account
        out.disabled = person.disabled
        out.last_login_at = person.last_login_at
        out.invite_expires_at = person.invite_expires_at
    return out


def _person(db: DbDep, team: WdTeam, member_id: int) -> guests.Person:
    found = next((p for p in guests.people(db, team) if p.member_id == member_id), None)
    if found is None:
        raise HTTPException(404, "No such person on this team")
    return found


def invite_link(settings: Settings, token: str) -> str:
    return f"{settings.public_url.rstrip('/')}/join/{token}"


@router.get("/teams/{team_id}/people", response_model=list[PersonOut])
def list_people(team_id: int, viewer: ViewerDep, db: DbDep) -> list[PersonOut]:
    """The roster. Its players see names, numbers and roles; coaches and admins also who has
    an account, the last login and open links."""
    access = _guest_team(db, viewer, team_id)
    full = can_manage_people(viewer, access.ref, access.leads)
    return [_person_out(p, viewer, full) for p in guests.people(db, access.team)]


@router.post("/teams/{team_id}/people", response_model=PersonOut, status_code=201)
def add_person(team_id: int, body: PersonIn, viewer: ViewerDep, db: DbDep) -> PersonOut:
    access = _managing(db, viewer, team_id)
    try:
        member = guests.add_person(
            db, access.team, name=body.name, number=body.number, role=body.role
        )
    except guests.GuestError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    audit(db, viewer.member_id, "guest_person_added", "wd_team", str(team_id),
          member_id=member.id, role=body.role)  # fmt: skip
    db.commit()
    return _person_out(_person(db, access.team, member.id), viewer, True)


@router.patch("/teams/{team_id}/people/{member_id}", response_model=PersonOut)
def edit_person(
    team_id: int, member_id: int, body: PersonEdit, viewer: ViewerDep, db: DbDep
) -> PersonOut:
    access = _managing(db, viewer, team_id)
    fields = body.model_fields_set
    try:
        guests.update_person(
            db,
            access.team,
            member_id,
            name=body.name if "name" in fields else None,
            number=body.number if "number" in fields else None,
            clear_number="number" in fields and body.number is None,
        )
    except guests.NotOnTeam:
        raise HTTPException(404, "No such person on this team") from None
    except guests.GuestError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    audit(db, viewer.member_id, "guest_person_updated", "wd_team", str(team_id),
          member_id=member_id, fields=sorted(fields))  # fmt: skip
    db.commit()
    return _person_out(_person(db, access.team, member_id), viewer, True)


@router.delete("/teams/{team_id}/people/{member_id}", status_code=204)
def remove_person(team_id: int, member_id: int, viewer: ViewerDep, db: DbDep) -> None:
    """Off the roster at once: their running session loses the team on its next request. On
    no other team, their account is disabled."""
    access = _managing(db, viewer, team_id)
    if member_id == viewer.member_id:
        raise HTTPException(409, "You cannot remove yourself; ask another coach or an admin")
    try:
        disabled = guests.remove_person(db, access.team, member_id)
    except guests.NotOnTeam:
        raise HTTPException(404, "No such person on this team") from None
    audit(db, viewer.member_id, "guest_person_removed", "wd_team", str(team_id),
          member_id=member_id, account_disabled=disabled)  # fmt: skip
    db.commit()
    permission_cache.clear()  # cached playback permissions end now too


@router.post("/teams/{team_id}/people/{member_id}/invite", response_model=InviteLinkOut)
def invite_person(
    team_id: int, member_id: int, viewer: ViewerDep, db: DbDep, settings: SettingsDep
) -> InviteLinkOut:
    """A new link for this person: sets up their account, or a new password. Older links of
    theirs stop working."""
    access = _managing(db, viewer, team_id)
    try:
        token, expires_at = guests.new_invite(db, access.team, member_id, viewer.member_id)
    except guests.NotOnTeam:
        raise HTTPException(404, "No such person on this team") from None
    audit(db, viewer.member_id, "guest_invite_created", "wd_team", str(team_id),
          member_id=member_id)  # fmt: skip
    db.commit()
    return InviteLinkOut(link=invite_link(settings, token), expires_at=expires_at)


@router.post("/teams/{team_id}/adults", response_model=AdultsOut)
def declare_adults(team_id: int, viewer: ViewerDep, db: DbDep) -> AdultsOut:
    """The coach confirms that everyone the team films (its players, coaches and opponents)
    is 18 or older: an adult league. Uploads need it (PLAN §5.3). The guest club's own coaches
    only: it is their statement."""
    access = _guest_team(db, viewer, team_id)
    if not (viewer.guest and access.team.lineage_id in access.leads):
        raise HTTPException(403, "Only the team's coaches confirm this")
    lineage = access.team.lineage
    if lineage.guest_adults_declared_at is None:
        guests.declare_adults(db, lineage, viewer.member_id)
        audit(db, viewer.member_id, "guest_adults_declared", "team_lineage", str(lineage.id),
              team_id=team_id)  # fmt: skip
        db.commit()
    assert lineage.guest_adults_declared_at is not None
    return AdultsOut(adults_declared_at=lineage.guest_adults_declared_at)


# --- admins: the guest clubs ------------------------------------------------------------------


class GuestTeamOut(BaseModel):
    id: int
    lineage_id: uuid.UUID
    club: str
    name: str
    sport: Literal["volleyball", "basketball"]
    season: str
    league: str | None
    enabled: bool
    training_ok: bool
    video_limit: int | None
    videos: int
    people: int
    accounts: int
    adults_declared_at: datetime | None


class GuestTeamIn(BaseModel):
    club: Name
    name: Name
    sport: Literal["volleyball"] = "volleyball"
    league: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None = None
    season: str | None = Field(default=None, max_length=7)
    video_limit: int | None = Field(default=guests.DEFAULT_VIDEO_LIMIT, ge=0, le=1000)
    training_ok: bool = False


class GuestTeamEdit(BaseModel):
    video_limit: int | None = Field(default=None, ge=0, le=1000)
    training_ok: bool | None = None
    enabled: bool | None = None


class CoachIn(BaseModel):
    name: Name
    number: Number | None = None


class CoachInvitedOut(BaseModel):
    person: PersonOut
    link: str  # shown once
    expires_at: datetime


def _guest_team_out(db: DbDep, team: WdTeam) -> GuestTeamOut:
    lineage = team.lineage
    member_ids = set(
        db.scalars(select(WdTeamMember.member_id).where(WdTeamMember.team_id == team.id))
    )
    accounts = (
        db.scalar(
            select(func.count())
            .select_from(GuestAccount)
            .where(GuestAccount.member_id.in_(member_ids), GuestAccount.disabled_at.is_(None))
        )
        if member_ids
        else 0
    )
    return GuestTeamOut(
        id=team.id,
        lineage_id=lineage.id,
        club=lineage.guest_club or "",
        name=team.name,
        sport=team.sport,
        season=team.season,
        league=team.league,
        enabled=lineage.enabled,
        training_ok=lineage.guest_training_ok,
        video_limit=lineage.guest_video_limit,
        videos=guests.video_count(db, team.id),
        people=len(member_ids),
        accounts=int(accounts or 0),
        adults_declared_at=lineage.guest_adults_declared_at,
    )


def _admin_guest_team(db: DbDep, team_id: int) -> WdTeam:
    team = db.get(WdTeam, team_id)
    if team is None or not team.guest:
        raise HTTPException(404, "Guest team not found")
    return team


@router.get("/admin/guest-teams", response_model=list[GuestTeamOut])
def list_guest_teams(_: AdminDep, db: DbDep) -> list[GuestTeamOut]:
    return [_guest_team_out(db, t) for t in guests.guest_teams(db)]


@router.post("/admin/guest-teams", response_model=GuestTeamOut, status_code=201)
def create_guest_team(body: GuestTeamIn, viewer: AdminDep, db: DbDep) -> GuestTeamOut:
    season = body.season or season_of(club_today())
    if not SEASON.match(season):
        raise HTTPException(422, "A season looks like 2026/27")
    duplicate = db.scalar(
        select(WdTeam.id)
        .where(WdTeam.guest.is_(True), WdTeam.season == season)
        .where(func.lower(WdTeam.name) == body.name.lower())
        .where(WdTeam.lineage.has(func.lower(TeamLineage.guest_club) == body.club.lower()))
    )
    if duplicate is not None:
        raise HTTPException(409, f"{body.club} {body.name} {season} exists already")
    team = guests.create_team(
        db,
        club=body.club,
        name=body.name,
        sport=body.sport,
        league=body.league or None,
        season=season,
        video_limit=body.video_limit,
        training_ok=body.training_ok,
        actor=viewer.member_id,
    )
    audit(db, viewer.member_id, "guest_team_created", "team_lineage", str(team.lineage_id),
          team_id=team.id, club=body.club, team=body.name, season=season,
          video_limit=body.video_limit, training_ok=body.training_ok)  # fmt: skip
    db.commit()
    return _guest_team_out(db, team)


@router.put("/admin/guest-teams/{team_id}", response_model=GuestTeamOut)
def update_guest_team(
    team_id: int, body: GuestTeamEdit, viewer: AdminDep, db: DbDep
) -> GuestTeamOut:
    team = _admin_guest_team(db, team_id)
    lineage = team.lineage
    fields = body.model_fields_set
    changed: dict[str, object] = {}
    if "video_limit" in fields and body.video_limit != lineage.guest_video_limit:
        lineage.guest_video_limit = changed["video_limit"] = body.video_limit
    if body.training_ok is not None and body.training_ok != lineage.guest_training_ok:
        lineage.guest_training_ok = changed["training_ok"] = body.training_ok
    if changed:
        audit(db, viewer.member_id, "guest_team_updated", "team_lineage", str(lineage.id),
              team_id=team_id, **changed)  # fmt: skip
    if body.enabled is not None and body.enabled != lineage.enabled:
        lineage.enabled = body.enabled
        lineage.enabled_by = viewer.member_id
        lineage.enabled_at = datetime.now(UTC)
        audit(db, viewer.member_id,
              "guest_team_enabled" if body.enabled else "guest_team_disabled",
              "team_lineage", str(lineage.id), team_id=team_id)  # fmt: skip
    db.commit()
    if body.enabled is False:
        permission_cache.clear()  # its people's playback ends now
    return _guest_team_out(db, team)


@router.post(
    "/admin/guest-teams/{team_id}/coaches", response_model=CoachInvitedOut, status_code=201
)
def invite_coach(
    team_id: int, body: CoachIn, viewer: AdminDep, db: DbDep, settings: SettingsDep
) -> CoachInvitedOut:
    """Put a coach on the team and make their invite link (shown once). They add their
    players from the team page."""
    team = _admin_guest_team(db, team_id)
    try:
        member = guests.add_person(db, team, name=body.name, number=body.number, role="coach")
    except guests.GuestError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    token, expires_at = guests.new_invite(db, team, member.id, viewer.member_id)
    audit(db, viewer.member_id, "guest_person_added", "wd_team", str(team_id),
          member_id=member.id, role="coach")  # fmt: skip
    audit(db, viewer.member_id, "guest_invite_created", "wd_team", str(team_id),
          member_id=member.id)  # fmt: skip
    db.commit()
    return CoachInvitedOut(
        person=_person_out(_person(db, team, member.id), viewer, True),
        link=invite_link(settings, token),
        expires_at=expires_at,
    )


__all__ = ["auth_router", "router"]
