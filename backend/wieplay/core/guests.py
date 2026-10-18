"""Guest clubs (PLAN §4.5): other clubs' teams that use WiePlay with their own logins.

A guest team is a lineage with `guest_club` set and a team row with `guest = true`. Its
people live in wd_member and wd_team_member like KSCW members, with ids from `guest_id_seq`
(GUEST_ID_BASE and up), so every roster consumer (tagging, players, comments, the video
roster) works unchanged. The Wiedisync sync never touches these rows (core/sync.py).

Nobody signs up: an admin creates the team and invites its first coach; coaches add their
people and send each one an invite link (shown once, valid 14 days). The link sets up an
email and a password, or resets the password of an existing account; WiePlay sends no email.

Seasons: a guest team row is one season of its lineage, as a KSCW team row is. Its coaches
start the next season themselves (start_next_season): a new row in the same lineage with the
people they take along (same member ids, so their logins keep working), the adults
declaration asked again. A person on no current row (the newest season of their team) has no
login any more; earlier seasons keep their rosters for as long as Wiedisync keeps KSCW's
(this season and the previous one, retire_old_rosters).

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
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, select, text, update
from sqlalchemy.orm import Session

from wieplay.core.models import (
    GuestAccount,
    GuestInvite,
    TeamLineage,
    UserSession,
    Video,
    WdMember,
    WdTeam,
    WdTeamMember,
)
from wieplay.core.permissions import Viewer
from wieplay.core.pipeline import season_of

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


class EarlierSeason(GuestError):
    """People join a team's newest season only; earlier seasons keep their roster."""


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
    teams (wieplay/core/names.py)."""
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


# --- seasons ----------------------------------------------------------------------------------

_SEASON = re.compile(r"^(\d{4})/(\d{2})$")


def is_season(value: str) -> bool:
    """A season as WiePlay writes it: '2026/27'."""
    found = _SEASON.match(value)
    return found is not None and (int(found[1]) + 1) % 100 == int(found[2])


def next_season_of(season: str) -> str:
    """'2026/27' -> '2027/28'."""
    start = int(season[:4]) + 1
    return f"{start}/{str(start + 1)[2:]}"


def previous_season_of(season: str) -> str:
    """'2026/27' -> '2025/26'."""
    start = int(season[:4]) - 1
    return f"{start}/{str(start + 1)[2:]}"


def default_next_season(team: WdTeam, today: date) -> str:
    """The season after the team's; this season when the team skipped one."""
    return max(next_season_of(team.season), season_of(today))


def next_season_for(team: WdTeam, today: date) -> str | None:
    """The season a guest team's coaches can start now, if any: the one after the team's (this
    season, when the team skipped one), at the latest the season after this one."""
    season = default_next_season(team, today)
    return season if season <= next_season_of(season_of(today)) else None


def newest_team(db: Session, lineage_id: uuid.UUID) -> WdTeam | None:
    """The newest season's row of a team."""
    return db.scalars(
        select(WdTeam)
        .where(WdTeam.lineage_id == lineage_id)
        .order_by(WdTeam.season.desc(), WdTeam.id.desc())
        .limit(1)
    ).first()


def is_newest(db: Session, team: WdTeam) -> bool:
    newest = newest_team(db, team.lineage_id)
    return newest is not None and newest.id == team.id


def check_newest(db: Session, team: WdTeam) -> None:
    """People join, and get their links, on a team's newest season only: an earlier season
    keeps its roster as it was."""
    newest = newest_team(db, team.lineage_id)
    if newest is not None and newest.id != team.id:
        raise EarlierSeason(
            f"{team.season} is an earlier season of {team.name}: "
            f"add people and make links on {newest.season}"
        )


def on_current_team(db: Session, member_id: int) -> bool:
    """On the newest season's row of a guest team. Only they keep a login: the others have
    left (not taken along into the new season, or taken off its roster)."""
    newest = (
        select(WdTeam.lineage_id, func.max(WdTeam.season).label("season"))
        .where(WdTeam.guest.is_(True))
        .group_by(WdTeam.lineage_id)
        .subquery()
    )
    found = db.scalar(
        select(func.count())
        .select_from(WdTeamMember)
        .join(WdTeam, WdTeam.id == WdTeamMember.team_id)
        .join(
            newest,
            and_(newest.c.lineage_id == WdTeam.lineage_id, newest.c.season == WdTeam.season),
        )
        .where(WdTeamMember.member_id == member_id, WdTeam.guest.is_(True))
    )
    return bool(found)


def _left(db: Session, member_id: int, now: datetime) -> bool:
    """After a person left a team row: on no current row, their login is disabled (deleted by
    the housekeeping 30 days later), their sessions end and their links stop working; on no
    row at all, their name leaves WiePlay too, as the Wiedisync sync does for a KSCW member
    who leaves (their tags keep the shirt number). True when they had a login."""
    if on_current_team(db, member_id):
        return False
    account = db.get(GuestAccount, member_id)
    if account is not None and account.disabled_at is None:
        account.disabled_at = now
    db.execute(delete(UserSession).where(UserSession.member_id == member_id))
    db.execute(
        update(GuestInvite)
        .where(
            GuestInvite.member_id == member_id,
            GuestInvite.used_at.is_(None),
            GuestInvite.expires_at > now,
        )
        .values(expires_at=now)
    )
    rows = db.scalar(
        select(func.count()).select_from(WdTeamMember).where(WdTeamMember.member_id == member_id)
    )
    if not rows:
        db.execute(delete(WdMember).where(WdMember.id == member_id))
    db.flush()
    return account is not None


@dataclass(frozen=True)
class SeasonStarted:
    team: WdTeam
    carried: tuple[int, ...]
    left: tuple[int, ...]
    # Of those left behind: the ones whose login was disabled (on no current team).
    disabled: tuple[int, ...]


def start_next_season(
    db: Session,
    team: WdTeam,
    *,
    name: str,
    league: str | None,
    season: str,
    member_ids: Collection[int] | None,
    actor: int,
    today: date,
    now: datetime | None = None,
) -> SeasonStarted:
    """The team's next season (PLAN §4.5): a new row in the same lineage with the people the
    coach takes along (everyone when member_ids is None), in the roles they had. Same member
    ids, so their logins keep working; they see both seasons. The adults declaration is asked
    again, the video limit counts again from zero. This season stays as it is; those left
    behind keep their place on its roster, but on no current team their login is disabled and
    their links stop working. A guest coach who starts the season always comes along."""
    now = now or datetime.now(UTC)
    if not team.guest:
        raise GuestError("Only a guest club's team starts its seasons in WiePlay")
    # One new season at a time: two coaches clicking at once must not make two rows.
    db.execute(select(TeamLineage.id).where(TeamLineage.id == team.lineage_id).with_for_update())
    newest = newest_team(db, team.lineage_id)
    if newest is None or newest.id != team.id:
        later = newest.season if newest is not None else "?"
        raise GuestError(f"{team.name} has a later season already: {later}")
    if not is_season(season):
        raise GuestError("A season looks like 2027/28")
    latest = next_season_of(season_of(today))
    if season <= team.season or season > latest:
        raise GuestError(f"The next season comes after {team.season}, at the latest {latest}")
    club = team.lineage.guest_club or ""
    duplicate = db.scalar(
        select(WdTeam.id)
        .join(TeamLineage, TeamLineage.id == WdTeam.lineage_id)
        .where(WdTeam.guest.is_(True), WdTeam.season == season)
        .where(func.lower(WdTeam.name) == name.lower())
        .where(func.lower(TeamLineage.guest_club) == club.lower())
    )
    if duplicate is not None:
        raise GuestError(f"{club} {name} {season} exists already")

    roster: dict[int, set[str]] = {}
    for member_id, rel in db.execute(
        select(WdTeamMember.member_id, WdTeamMember.rel).where(WdTeamMember.team_id == team.id)
    ):
        roster.setdefault(member_id, set()).add(rel)
    chosen = set(roster) if member_ids is None else set(member_ids)
    if chosen - roster.keys():
        raise NotOnTeam("No such person on this team")
    if actor in roster:
        chosen.add(actor)  # the coach who starts the season comes along

    new = WdTeam(
        id=next_id(db),
        lineage=team.lineage,
        name=name,
        sport=team.sport,
        season=season,
        league=league,
        team_key=None,
        listed=True,
        guest=True,
        synced_at=now,
    )
    db.add(new)
    db.flush()
    for member_id in sorted(chosen):
        for rel in sorted(roster[member_id]):
            db.add(WdTeamMember(team_id=new.id, member_id=member_id, rel=rel, is_guest=False))
    if chosen:
        # Their open links lead to the new season now.
        db.execute(
            update(GuestInvite)
            .where(
                GuestInvite.team_id == team.id,
                GuestInvite.member_id.in_(chosen),
                GuestInvite.used_at.is_(None),
                GuestInvite.expires_at > now,
            )
            .values(team_id=new.id)
        )
    db.flush()
    left = sorted(roster.keys() - chosen)
    disabled = tuple(member_id for member_id in left if _left(db, member_id, now))
    return SeasonStarted(new, tuple(sorted(chosen)), tuple(left), disabled)


@dataclass(frozen=True)
class EarlierPerson:
    member_id: int
    name: str
    number: int | None
    roles: tuple[str, ...]
    season: str  # the latest earlier season they were on


def earlier_people(db: Session, team: WdTeam) -> list[EarlierPerson]:
    """People of the team's earlier seasons who are not on this row: a coach can take them
    along later (a forgotten tick, someone back after a break). Coaches first, then by number
    and name."""
    on_row = select(WdTeamMember.member_id).where(WdTeamMember.team_id == team.id)
    found: dict[int, tuple[WdMember, str, set[str]]] = {}
    for member, rel, season in db.execute(
        select(WdMember, WdTeamMember.rel, WdTeam.season)
        .join(WdTeamMember, WdTeamMember.member_id == WdMember.id)
        .join(WdTeam, WdTeam.id == WdTeamMember.team_id)
        .where(
            WdTeam.lineage_id == team.lineage_id,
            WdTeam.guest.is_(True),
            WdTeam.season < team.season,
            WdMember.id.not_in(on_row),
        )
    ):
        known = found.get(member.id)
        if known is None or season > known[1]:
            found[member.id] = (member, season, {rel})
        elif season == known[1]:
            known[2].add(rel)
    people = [
        EarlierPerson(m.id, m.display_name, m.number, tuple(sorted(rels)), season)
        for m, season, rels in found.values()
    ]
    return sorted(
        people, key=lambda p: ("coach" not in p.roles, p.number is None, p.number or 0, p.name)
    )


def take_along(
    db: Session, team: WdTeam, member_ids: Collection[int], now: datetime | None = None
) -> list[int]:
    """Put people of the team's earlier seasons on its newest row, in the roles they had last.
    A login disabled when they left works again (the housekeeping deletes it 30 days after it
    was disabled)."""
    now = now or datetime.now(UTC)
    check_newest(db, team)
    earlier = {p.member_id: p for p in earlier_people(db, team)}
    wanted = sorted(set(member_ids))
    if not wanted or any(member_id not in earlier for member_id in wanted):
        raise NotOnTeam("No such person in an earlier season of this team")
    for member_id in wanted:
        person = earlier[member_id]
        if person.number is not None and (taken := _number_taken(db, team, person.number)):
            raise GuestError(f"Number {person.number} is taken by {taken}")
        for rel in person.roles:
            db.add(WdTeamMember(team_id=team.id, member_id=member_id, rel=rel, is_guest=False))
        db.flush()
        account = db.get(GuestAccount, member_id)
        if account is not None and account.disabled_at is not None:
            account.disabled_at = None
            account.failed_logins = 0
            account.locked_until = None
    db.flush()
    return wanted


def retire_old_rosters(db: Session, today: date, now: datetime | None = None) -> int:
    """Rosters of guest team rows older than the previous season go, as Wiedisync's sync keeps
    KSCW rosters of this season and the previous one only (PLAN §4.3); those seasons' videos
    expired with them (PLAN §5.5). A team's newest row is never touched, so a team that paused
    keeps its people. Whoever is on no row any more loses their name and their login. The
    number of roster rows removed."""
    now = now or datetime.now(UTC)
    newest = (
        select(WdTeam.lineage_id, func.max(WdTeam.season).label("season"))
        .where(WdTeam.guest.is_(True))
        .group_by(WdTeam.lineage_id)
        .subquery()
    )
    old = (
        select(WdTeam.id)
        .join(newest, newest.c.lineage_id == WdTeam.lineage_id)
        .where(
            WdTeam.guest.is_(True),
            WdTeam.season < newest.c.season,
            WdTeam.season < previous_season_of(season_of(today)),
        )
    )
    members = set(db.scalars(select(WdTeamMember.member_id).where(WdTeamMember.team_id.in_(old))))
    if not members:
        return 0
    removed = db.execute(delete(WdTeamMember).where(WdTeamMember.team_id.in_(old)))
    for member_id in sorted(members):
        _left(db, member_id, now)
    return int(getattr(removed, "rowcount", 0) or 0)


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
    check_newest(db, team)
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
    """Take a person off this team row (any season: a request to be removed from an earlier
    season too). On no current team, their account is disabled and their sessions end; on no
    team row at all, their name leaves WiePlay, as the Wiedisync sync does for a KSCW member
    who leaves (their tags keep the shirt number). True when an account was disabled."""
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
    db.flush()
    return _left(db, member_id, now)


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
    check_newest(db, team)
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
    if not is_newest(db, team):  # links of people taken along moved to the new season
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


def declare_adults(db: Session, team: WdTeam, actor: int, now: datetime | None = None) -> None:
    """The coach's "everyone we film is 18 or older" for this season's team: asked again every
    season (PLAN §5.3)."""
    team.guest_adults_declared_at = now or datetime.now(UTC)
    team.guest_adults_declared_by = actor
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
    """Guest teams whose club has not agreed that its games train WiePlay's shared models:
    their reviewed games never go into a training run."""
    return set(
        db.scalars(
            select(TeamLineage.id).where(
                TeamLineage.guest_club.is_not(None), TeamLineage.guest_training_ok.is_(False)
            )
        )
    )
