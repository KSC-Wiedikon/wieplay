"""Guest clubs (PLAN §4.5): other clubs' teams that use Nomal with their own logins.

A guest team is a lineage with `guest_club` set and a team row with `guest = true`. Its
people live in wd_member and wd_team_member like KSCW members, with ids from `guest_id_seq`
(GUEST_ID_BASE and up), so every roster consumer (tagging, players, comments, the video
roster) works unchanged. The Wiedisync sync never touches these rows (core/sync.py).

Nobody signs up: an admin creates the team and invites its first coach; coaches add their
people and send each one an invite link (shown once, valid 14 days). The link sets up an
email and a password, or resets the password of an existing account; Nomal sends no email.

This module holds the guest logic: teams and people, invites, password hashing, the login,
and what a guest session may see. The rules about who may do what stay in
core/permissions.py.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import re
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from nomal.core.models import (
    GuestAccount,
    GuestInvite,
    TeamLineage,
    UserSession,
    Video,
    WdMember,
    WdTeam,
    WdTeamMember,
)
from nomal.core.permissions import Viewer

# Guest teams and people get ids from here up (guest_id_seq); Wiedisync's ids are far below.
GUEST_ID_BASE = 1_000_000_000
DEFAULT_VIDEO_LIMIT = 30
INVITE_TTL = timedelta(days=14)
# Guests cannot renew silently through Wiedisync, so their sessions last longer.
SESSION_TTL = timedelta(days=14)
MIN_PASSWORD = 10
MAX_PASSWORD = 200  # scrypt hashes whatever it is given: keep the work bounded
LOCK_AFTER = 10  # wrong passwords in a row
LOCK_FOR = timedelta(minutes=15)
ROLES = ("player", "coach")
SPORTS = ("volleyball",)  # basketball later

# scrypt as hashlib has it (RFC 7914): 2^15 x 8 x 128 bytes = 32 MiB per hash.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024
# A burst of logins (or someone trying passwords) hashes at most this many at once: the
# VPS's memory and CPU stay bounded.
_HASHING = threading.BoundedSemaphore(4)
HASH_WAIT_SECONDS = 5.0
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class GuestError(Exception):
    """A request the guest rules refuse; the message is safe to show."""


class InviteUnknown(GuestError):
    pass


class InviteGone(GuestError):
    """Expired, used, replaced by a newer link, or the person left the team."""


class EmailTaken(GuestError):
    pass


class NotOnTeam(GuestError):
    """No such person on this team."""


class Busy(GuestError):
    """Every hashing slot is taken (a burst of logins): try again in a moment."""


def is_guest_id(value: int) -> bool:
    """A guest team or person (ids from guest_id_seq), not one from Wiedisync."""
    return value >= GUEST_ID_BASE


def next_id(db: Session) -> int:
    value = db.scalar(text("SELECT nextval('guest_id_seq')"))
    assert isinstance(value, int) and is_guest_id(value)
    return value


# --- passwords --------------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    # A flood of logins waits a little, then gets "busy" instead of taking every thread.
    if not _HASHING.acquire(timeout=HASH_WAIT_SECONDS):
        raise Busy("Too many logins at this moment. Try again in a minute.")
    try:
        return hashlib.scrypt(
            password.encode(), salt=salt, n=n, r=r, p=p, maxmem=_SCRYPT_MAXMEM, dklen=dklen
        )
    finally:
        _HASHING.release()


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """scrypt$n$r$p$salt$hash, salt and hash in base64."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = _scrypt(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, 32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        found = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p), len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(found, expected)


@functools.cache
def _dummy_hash() -> str:
    """An unknown email still costs one hash, so the answer's timing tells nothing."""
    return hash_password("not a password of anyone", salt=b"\0" * 16)


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise GuestError(f"The password needs at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        raise GuestError(f"The password can have at most {MAX_PASSWORD} characters")


def normalise_email(email: str) -> str:
    return email.strip().lower()


def check_email(email: str) -> str:
    value = normalise_email(email)
    if len(value) > 320 or not _EMAIL.match(value):
        raise GuestError("This is not an email address")
    return value


# --- teams and people -------------------------------------------------------------------------


def club_of(team: WdTeam) -> str | None:
    """The guest club of a team row; None for a KSCW team."""
    return team.lineage.guest_club if team.guest else None


def club_names(team: WdTeam, club_short: str, club_name: str) -> tuple[str, str]:
    """The names the app puts before our team ("KSCW H3"): the guest club's own for its
    teams (nomal/core/names.py)."""
    club = club_of(team)
    return (club, club) if club else (club_short, club_name)


def create_team(
    db: Session,
    *,
    club: str,
    name: str,
    sport: str,
    league: str | None,
    season: str,
    video_limit: int | None,
    training_ok: bool,
    actor: int,
    now: datetime | None = None,
) -> WdTeam:
    """A guest club's team for one season: its lineage (switched on) and its team row."""
    now = now or datetime.now(UTC)
    lineage = TeamLineage(
        id=uuid.uuid4(),
        sport=sport,
        label=name,
        team_key=None,
        enabled=True,
        enabled_by=actor,
        enabled_at=now,
        guest_club=club,
        guest_training_ok=training_ok,
        guest_video_limit=video_limit,
    )
    db.add(lineage)
    team = WdTeam(
        id=next_id(db),
        lineage=lineage,
        name=name,
        sport=sport,
        season=season,
        league=league,
        team_key=None,
        listed=True,
        guest=True,
        synced_at=now,
    )
    db.add(team)
    db.flush()
    return team


def guest_teams(db: Session) -> list[WdTeam]:
    """Every guest team row, club and name order, newest season first."""
    rows = db.scalars(
        select(WdTeam)
        .join(TeamLineage, TeamLineage.id == WdTeam.lineage_id)
        .where(WdTeam.guest.is_(True))
        .order_by(TeamLineage.guest_club, WdTeam.name, WdTeam.season.desc())
    )
    return list(rows)


@dataclass(frozen=True)
class Person:
    member_id: int
    name: str
    number: int | None
    roles: tuple[str, ...]
    account: bool
    disabled: bool
    last_login_at: datetime | None
    # The newest invite link that still works, if any.
    invite_expires_at: datetime | None


def people(db: Session, team: WdTeam, now: datetime | None = None) -> list[Person]:
    """A guest team row's roster: coaches first, then by number and name."""
    now = now or datetime.now(UTC)
    rows = db.execute(
        select(WdMember, WdTeamMember.rel)
        .join(WdTeamMember, WdTeamMember.member_id == WdMember.id)
        .where(WdTeamMember.team_id == team.id)
    ).all()
    roles: dict[int, set[str]] = {}
    members: dict[int, WdMember] = {}
    for member, rel in rows:
        members[member.id] = member
        roles.setdefault(member.id, set()).add(rel)
    accounts = {
        a.member_id: a
        for a in db.scalars(select(GuestAccount).where(GuestAccount.member_id.in_(members)))
    }
    invites: dict[int, datetime] = {}
    for member_id, expires_at in db.execute(
        select(GuestInvite.member_id, GuestInvite.expires_at).where(
            GuestInvite.team_id == team.id,
            GuestInvite.member_id.in_(members),
            GuestInvite.used_at.is_(None),
            GuestInvite.expires_at > now,
        )
    ):
        invites[member_id] = max(invites.get(member_id, expires_at), expires_at)
    out = []
    for member_id, member in members.items():
        account = accounts.get(member_id)
        out.append(
            Person(
                member_id=member_id,
                name=member.display_name,
                number=member.number,
                roles=tuple(sorted(roles[member_id])),
                account=account is not None,
                disabled=account is not None and account.disabled_at is not None,
                last_login_at=account.last_login_at if account else None,
                invite_expires_at=invites.get(member_id),
            )
        )
    return sorted(
        out, key=lambda p: ("coach" not in p.roles, p.number is None, p.number or 0, p.name)
    )


def _number_taken(db: Session, team: WdTeam, number: int, but: int | None = None) -> str | None:
    query = (
        select(WdMember.display_name)
        .join(WdTeamMember, WdTeamMember.member_id == WdMember.id)
        .where(WdTeamMember.team_id == team.id, WdMember.number == number)
    )
    if but is not None:
        query = query.where(WdMember.id != but)
    return db.scalars(query.limit(1)).first()


def add_person(db: Session, team: WdTeam, *, name: str, number: int | None, role: str) -> WdMember:
    if role not in ROLES:
        raise GuestError("A person is a player or a coach")
    if number is not None and (taken := _number_taken(db, team, number)):
        raise GuestError(f"Number {number} is taken by {taken}")
    member = WdMember(
        id=next_id(db),
        uuid=uuid.uuid4(),
        display_name=name,
        number=number,
        positions=[],
        active=True,
    )
    db.add(member)
    db.flush()
    db.add(WdTeamMember(team_id=team.id, member_id=member.id, rel=role, is_guest=False))
    db.flush()
    return member


def on_team(db: Session, team: WdTeam, member_id: int) -> bool:
    return (
        db.scalar(
            select(func.count())
            .select_from(WdTeamMember)
            .where(WdTeamMember.team_id == team.id, WdTeamMember.member_id == member_id)
        )
        or 0
    ) > 0


def update_person(
    db: Session,
    team: WdTeam,
    member_id: int,
    *,
    name: str | None = None,
    number: int | None = None,
    clear_number: bool = False,
) -> WdMember:
    member = db.get(WdMember, member_id)
    if member is None or not is_guest_id(member_id) or not on_team(db, team, member_id):
        raise NotOnTeam("No such person on this team")
    if name is not None:
        member.display_name = name
    if clear_number:
        member.number = None
    elif number is not None:
        if taken := _number_taken(db, team, number, but=member_id):
            raise GuestError(f"Number {number} is taken by {taken}")
        member.number = number
    db.flush()
    return member


def remove_person(db: Session, team: WdTeam, member_id: int, now: datetime | None = None) -> bool:
    """Take a person off this team row. On no other team, their account is disabled, their
    sessions end and their name leaves Nomal, as the Wiedisync sync does for a KSCW member who
    leaves (their tags keep the shirt number). True when an account was disabled."""
    now = now or datetime.now(UTC)
    if not is_guest_id(member_id) or not on_team(db, team, member_id):
        raise NotOnTeam("No such person on this team")
    db.execute(
        delete(WdTeamMember).where(
            WdTeamMember.team_id == team.id, WdTeamMember.member_id == member_id
        )
    )
    db.execute(
        update(GuestInvite)
        .where(
            GuestInvite.member_id == member_id,
            GuestInvite.team_id == team.id,
            GuestInvite.used_at.is_(None),
        )
        .values(expires_at=now)
    )
    left = db.scalar(
        select(func.count()).select_from(WdTeamMember).where(WdTeamMember.member_id == member_id)
    )
    if left:
        return False
    account = db.get(GuestAccount, member_id)
    if account is not None and account.disabled_at is None:
        account.disabled_at = now  # deleted by the housekeeping 30 days later
    db.execute(delete(UserSession).where(UserSession.member_id == member_id))
    db.execute(delete(WdMember).where(WdMember.id == member_id))
    db.flush()
    return account is not None


# --- invites ----------------------------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_invite(
    db: Session, team: WdTeam, member_id: int, actor: int, now: datetime | None = None
) -> tuple[str, datetime]:
    """A fresh link for a person of the team (the raw token, shown once, and when it
    expires). Older links of that person stop working."""
    now = now or datetime.now(UTC)
    if not team.guest or not on_team(db, team, member_id):
        raise NotOnTeam("No such person on this team")
    db.execute(
        update(GuestInvite)
        .where(
            GuestInvite.member_id == member_id,
            GuestInvite.used_at.is_(None),
            GuestInvite.expires_at > now,
        )
        .values(expires_at=now)
    )
    token = secrets.token_urlsafe(32)
    expires_at = now + INVITE_TTL
    db.add(
        GuestInvite(
            token_hash=_token_hash(token),
            member_id=member_id,
            team_id=team.id,
            created_by=actor,
            created_at=now,
            expires_at=expires_at,
        )
    )
    db.flush()
    return token, expires_at


@dataclass(frozen=True)
class InviteInfo:
    invite: GuestInvite
    team: WdTeam
    member: WdMember
    club: str
    roles: tuple[str, ...]
    account: GuestAccount | None
    created: bool = False  # accept_invite set up a new account


def open_invite(
    db: Session, token: str, now: datetime | None = None, *, lock: bool = False
) -> InviteInfo:
    """The invite behind a link, if it still works."""
    now = now or datetime.now(UTC)
    if not token or len(token) > 200:
        raise InviteUnknown("Unknown invite link")
    invite = (
        db.get(GuestInvite, _token_hash(token), with_for_update=True)
        if lock
        else db.get(GuestInvite, _token_hash(token))
    )
    if invite is None:
        raise InviteUnknown("Unknown invite link")
    if invite.used_at is not None or invite.expires_at <= now:
        raise InviteGone("This invite link was used or has expired")
    team = db.get(WdTeam, invite.team_id)
    member = db.get(WdMember, invite.member_id)
    if team is None or member is None or not team.guest or not team.lineage.enabled:
        raise InviteGone("This invite link no longer works")
    roles = tuple(
        sorted(
            db.scalars(
                select(WdTeamMember.rel).where(
                    WdTeamMember.team_id == team.id, WdTeamMember.member_id == member.id
                )
            )
        )
    )
    if not roles:
        raise InviteGone("This invite link no longer works")
    club = team.lineage.guest_club or ""
    return InviteInfo(invite, team, member, club, roles, db.get(GuestAccount, member.id))


def accept_invite(
    db: Session,
    token: str,
    *,
    email: str | None,
    password: str,
    now: datetime | None = None,
) -> InviteInfo:
    """Set up the person's account from the link, or reset its password. Marks the link
    used; the caller starts the session and commits."""
    now = now or datetime.now(UTC)
    found = open_invite(db, token, now, lock=True)
    check_password(password)
    account = found.account
    created = account is None
    if account is None:
        if not email:
            raise GuestError("An email address is needed for a new account")
        address = check_email(email)
        if db.scalar(select(GuestAccount.member_id).where(GuestAccount.email == address)):
            raise EmailTaken("This email address belongs to another account")
        account = GuestAccount(member_id=found.member.id, email=address, created_at=now)
        db.add(account)
    account.password_hash = hash_password(password)
    account.failed_logins = 0
    account.locked_until = None
    account.disabled_at = None  # a coach sent a link to someone on the roster
    account.last_login_at = now  # the link logs them in
    if not created:
        # A new password: whoever is logged in with the old one is logged out.
        db.execute(delete(UserSession).where(UserSession.member_id == found.member.id))
    found.invite.used_at = now
    db.execute(
        update(GuestInvite)
        .where(
            GuestInvite.member_id == found.member.id,
            GuestInvite.used_at.is_(None),
            GuestInvite.expires_at > now,
        )
        .values(expires_at=now)
    )
    db.flush()
    return InviteInfo(
        found.invite, found.team, found.member, found.club, found.roles, account, created
    )


# --- login and sessions -----------------------------------------------------------------------


def authenticate(
    db: Session, email: str, password: str, now: datetime | None = None
) -> GuestAccount | None:
    """The account for these credentials, or None (wrong, unknown, locked or disabled: the
    caller says the same for all). Counts failures; 10 in a row lock it for 15 minutes.
    Flushes; the caller commits either way."""
    now = now or datetime.now(UTC)
    # No row lock while hashing: a flood on one email must not hold up its owner's login.
    account = db.scalars(
        select(GuestAccount).where(GuestAccount.email == normalise_email(email))
    ).first()
    if account is None or len(password) > MAX_PASSWORD:
        verify_password(password[:MAX_PASSWORD], _dummy_hash())
        return None
    if account.locked_until is not None and account.locked_until > now:
        verify_password(password, _dummy_hash())
        return None
    if not verify_password(password, account.password_hash):
        # Counted in the database itself, so attempts at the same moment all count.
        failed = db.scalar(
            update(GuestAccount)
            .where(GuestAccount.member_id == account.member_id)
            .values(failed_logins=GuestAccount.failed_logins + 1)
            .returning(GuestAccount.failed_logins)
        )
        if failed is not None and failed >= LOCK_AFTER:
            db.execute(
                update(GuestAccount)
                .where(GuestAccount.member_id == account.member_id)
                .values(failed_logins=0, locked_until=now + LOCK_FOR)
            )
        db.expire(account)
        return None
    if account.disabled_at is not None:
        return None
    account.failed_logins = 0
    account.locked_until = None
    account.last_login_at = now
    db.flush()
    return account


def team_rels(db: Session, member_id: int) -> dict[int, frozenset[str]]:
    """The guest team rows this person is on now, with their relations: only teams of a
    guest club that is switched on. Read on every request, so a removal or a disabled team
    takes effect at once."""
    rels: dict[int, set[str]] = {}
    for team_id, rel in db.execute(
        select(WdTeamMember.team_id, WdTeamMember.rel)
        .join(WdTeam, WdTeam.id == WdTeamMember.team_id)
        .join(TeamLineage, TeamLineage.id == WdTeam.lineage_id)
        .where(
            WdTeamMember.member_id == member_id,
            WdTeam.guest.is_(True),
            TeamLineage.guest_club.is_not(None),
            TeamLineage.enabled.is_(True),
        )
    ):
        rels.setdefault(team_id, set()).add(rel)
    return {team_id: frozenset(r) for team_id, r in rels.items()}


def club_for_member(db: Session, member_id: int) -> str | None:
    """The club of the guest teams this person is on (the newest season's)."""
    return db.scalars(
        select(TeamLineage.guest_club)
        .join(WdTeam, WdTeam.lineage_id == TeamLineage.id)
        .join(WdTeamMember, WdTeamMember.team_id == WdTeam.id)
        .where(WdTeamMember.member_id == member_id, TeamLineage.guest_club.is_not(None))
        .order_by(WdTeam.season.desc())
        .limit(1)
    ).first()


def session_claims(db: Session, member: WdMember) -> dict[str, Any]:
    """What a guest session keeps, shaped like a Wiedisync login's claims. The teams are
    informative only: current_viewer rebuilds them from the database on every request."""
    rels = team_rels(db, member.id)
    seasons = {
        r.id: r.season
        for r in db.execute(select(WdTeam.id, WdTeam.season).where(WdTeam.id.in_(rels)))
    }
    return {
        "mid": member.id,
        "sub": str(member.uuid),
        "name": member.display_name,
        "roles": [],
        "sport_admin": [],
        "teams": [
            {"team": team_id, "season": seasons.get(team_id, ""), "rel": sorted(rel)}
            for team_id, rel in sorted(rels.items())
        ],
        "guest": True,
    }


def guest_viewer(db: Session, member_id: int) -> Viewer | None:
    """The viewer of a guest session, from the database; None when the account is gone or
    disabled (the session ends)."""
    if not is_guest_id(member_id):
        return None
    account = db.get(GuestAccount, member_id)
    member = db.get(WdMember, member_id)
    if account is None or account.disabled_at is not None or member is None:
        return None
    return Viewer(
        member_id=member.id,
        member_uuid=str(member.uuid),
        name=member.display_name,
        roles=frozenset(),
        sport_admin=frozenset(),
        team_rels=team_rels(db, member_id),
        guest=True,
        club=club_for_member(db, member_id),
    )


PROCESSING_FAILED = "Processing failed. A KSCW admin can see why."


def error_for(viewer: Viewer, error: str | None) -> str | None:
    """What a job's error says to this viewer: a guest club's people get a plain sentence,
    not the worker's own text (file paths, tool output)."""
    if error is None or not viewer.guest:
        return error
    return PROCESSING_FAILED


# --- filming, quota, training -----------------------------------------------------------------


def declare_adults(
    db: Session, lineage: TeamLineage, actor: int, now: datetime | None = None
) -> None:
    lineage.guest_adults_declared_at = now or datetime.now(UTC)
    lineage.guest_adults_declared_by = actor
    db.flush()


def video_count(db: Session, team_id: int) -> int:
    """The team row's videos that count against its limit: every one not deleted."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(Video)
            .where(Video.team_id == team_id, Video.status != "deleted")
        )
        or 0
    )


def quota_problem(db: Session, team: WdTeam) -> str | None:
    """Why the team may not add another video now, or None. Locks the team row, so two
    uploads at the same moment cannot both take the last place."""
    if not team.guest:
        return None
    limit = team.lineage.guest_video_limit
    if limit is None:
        return None
    db.execute(select(WdTeam.id).where(WdTeam.id == team.id).with_for_update())
    used = video_count(db, team.id)
    if used >= limit:
        return (
            f"{team.name} has {used} of {limit} videos for {team.season}. "
            "Delete one, or ask a KSCW admin for a higher limit."
        )
    return None


def no_training_lineages(db: Session) -> set[uuid.UUID]:
    """Guest teams whose club has not agreed that its games train Nomal's shared models:
    their reviewed games never go into a training run."""
    return set(
        db.scalars(
            select(TeamLineage.id).where(
                TeamLineage.guest_club.is_not(None), TeamLineage.guest_training_ok.is_(False)
            )
        )
    )
