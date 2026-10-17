"""Team helpers (PLAN §5.1 "analyst"): club members a coach picks to help tag and review a
team's games. The team's coaches and team managers, sport admins and admins list, add and
remove them; every change is in the audit log.

What a helper may do is decided in core/permissions.py (they tag, review, upload and share;
they do not download, delete or pick other helpers).

Helpers are KSCW members for KSCW teams: guest clubs (PLAN §4.5) have none for now, a guest
gets 403 here, and the search never lists a guest club's people.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nomal.api.access import TeamAccess, visible_team
from nomal.api.deps import DbDep, ViewerDep, audit
from nomal.api.media import permission_cache
from nomal.core.grants import ANALYST, open_grants
from nomal.core.guests import GUEST_ID_BASE, is_guest_id
from nomal.core.models import TeamGrant, WdMember, WdTeam, WdTeamMember
from nomal.core.permissions import LEAD_RELS, Viewer, can_grant_analyst

router = APIRouter(prefix="/api/teams", tags=["helpers"])

SEARCH_LIMIT = 20
SPORT_ORDER = {"volleyball": 0, "basketball": 1}


class HelperOut(BaseModel):
    id: uuid.UUID
    member_id: int
    name: str
    # The teams they play in now (from the Wiedisync mirror; empty once it no longer has them).
    teams: list[str]
    note: str | None
    granted_by_name: str
    granted_at: datetime


class HelperIn(BaseModel):
    member_id: int
    note: str | None = Field(default=None, max_length=300)


class CandidateOut(BaseModel):
    """A club member who could help: the name and teams, nothing else (no number, no
    positions, no birthdate; Nomal never has the latter)."""

    id: int
    name: str
    teams: list[str]


def _granting(db: Session, viewer: Viewer, team_id: int) -> TeamAccess:
    if viewer.guest:
        raise HTTPException(403, "Guest clubs have no helpers")
    access = visible_team(db, viewer, team_id)
    if not can_grant_analyst(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches, team managers and admins pick helpers")
    return access


def _team_names(db: Session, member_ids: Iterable[int]) -> dict[int, list[str]]:
    """Each member's teams in their newest season in the mirror ("H3", "D1")."""
    ids = set(member_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(WdTeamMember.member_id, WdTeam.name, WdTeam.sport, WdTeam.season)
        .join(WdTeam, WdTeam.id == WdTeamMember.team_id)
        .where(WdTeamMember.member_id.in_(ids), WdTeam.listed.is_(True))
    ).all()
    newest: dict[int, str] = {}
    for r in rows:
        newest[r.member_id] = max(newest.get(r.member_id, ""), r.season)
    teams: dict[int, set[tuple[int, str]]] = {}
    for r in rows:
        if r.season == newest[r.member_id]:
            teams.setdefault(r.member_id, set()).add((SPORT_ORDER.get(r.sport, 9), r.name))
    return {mid: [name for _, name in sorted(names)] for mid, names in teams.items()}


def _lead_member_ids(db: Session, lineage_id: uuid.UUID) -> set[int]:
    """The lineage's coaches and team managers (any season): they need no grant."""
    return set(
        db.scalars(
            select(WdTeamMember.member_id)
            .join(WdTeam, WdTeam.id == WdTeamMember.team_id)
            .where(WdTeam.lineage_id == lineage_id, WdTeamMember.rel.in_(LEAD_RELS))
        )
    )


def _helper_out(
    grant: TeamGrant, teams: dict[int, list[str]], names: dict[int, str] | None = None
) -> HelperOut:
    return HelperOut(
        id=grant.id,
        member_id=grant.member_id,
        # The mirror's name when it has one (a renamed member), else the name when granted.
        name=(names or {}).get(grant.member_id) or grant.member_name,
        teams=teams.get(grant.member_id, []),
        note=grant.note,
        granted_by_name=grant.granted_by_name,
        granted_at=grant.granted_at,
    )


def _like(word: str) -> str:
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/{team_id}/helpers", response_model=list[HelperOut])
def list_helpers(team_id: int, viewer: ViewerDep, db: DbDep) -> list[HelperOut]:
    access = _granting(db, viewer, team_id)
    grants = open_grants(db, access.team.lineage_id)
    ids = {g.member_id for g in grants}
    names = {
        r.id: r.display_name
        for r in db.execute(select(WdMember.id, WdMember.display_name).where(WdMember.id.in_(ids)))
    }
    teams = _team_names(db, ids)
    return [_helper_out(g, teams, names) for g in grants]


@router.get("/{team_id}/helpers/candidates", response_model=list[CandidateOut])
def helper_candidates(
    team_id: int,
    viewer: ViewerDep,
    db: DbDep,
    q: str = Query("", max_length=100),
) -> list[CandidateOut]:
    """Active club members whose name matches every word of `q` (at least 2 letters), minus
    the team's helpers, coaches and team managers. The mirror holds the members of the teams
    that use Nomal; guest clubs' people are never listed."""
    access = _granting(db, viewer, team_id)
    words = q.split()[:4]
    if len("".join(words)) < 2:
        return []
    lineage = access.team.lineage_id
    taken = {g.member_id for g in open_grants(db, lineage)} | _lead_member_ids(db, lineage)
    query = select(WdMember.id, WdMember.display_name).where(
        WdMember.active.is_(True), WdMember.id < GUEST_ID_BASE
    )
    for word in words:
        query = query.where(WdMember.display_name.ilike(_like(word), escape="\\"))
    if taken:
        query = query.where(WdMember.id.not_in(taken))
    rows = db.execute(query.order_by(WdMember.display_name).limit(SEARCH_LIMIT)).all()
    teams = _team_names(db, (r.id for r in rows))
    return [CandidateOut(id=r.id, name=r.display_name, teams=teams.get(r.id, [])) for r in rows]


@router.post("/{team_id}/helpers", response_model=HelperOut, status_code=201)
def add_helper(team_id: int, body: HelperIn, viewer: ViewerDep, db: DbDep) -> HelperOut:
    access = _granting(db, viewer, team_id)
    lineage = access.team.lineage_id
    member = None if is_guest_id(body.member_id) else db.get(WdMember, body.member_id)
    if member is None or not member.active:
        raise HTTPException(404, "No active club member with this id")
    if member.id in _lead_member_ids(db, lineage):
        raise HTTPException(409, "Already a coach or team manager of this team")
    if any(g.member_id == member.id for g in open_grants(db, lineage)):
        raise HTTPException(409, "Already a helper of this team")
    grant = TeamGrant(
        id=uuid.uuid4(),
        lineage_id=lineage,
        member_id=member.id,
        member_name=member.display_name,
        role=ANALYST,
        note=(body.note or "").strip() or None,
        granted_by=viewer.member_id,
        granted_by_name=viewer.name,
    )
    db.add(grant)
    try:
        db.flush()
    except IntegrityError:  # added twice at the same moment
        db.rollback()
        raise HTTPException(409, "Already a helper of this team") from None
    audit(
        db,
        viewer.member_id,
        "helper_added",
        "team_lineage",
        str(lineage),
        member_id=member.id,
        team_id=team_id,
        grant_id=str(grant.id),
    )
    db.commit()
    db.refresh(grant)
    return _helper_out(grant, _team_names(db, [member.id]))


@router.delete("/{team_id}/helpers/{grant_id}", status_code=204)
def remove_helper(team_id: int, grant_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> None:
    access = _granting(db, viewer, team_id)
    grant = db.get(TeamGrant, grant_id)
    if grant is None or grant.lineage_id != access.team.lineage_id or grant.revoked_at:
        raise HTTPException(404, "Helper not found")
    grant.revoked_at = datetime.now(UTC)
    grant.revoked_by = viewer.member_id
    audit(
        db,
        viewer.member_id,
        "helper_removed",
        "team_lineage",
        str(grant.lineage_id),
        member_id=grant.member_id,
        team_id=team_id,
        grant_id=str(grant.id),
    )
    db.commit()
    # Playback permissions are cached for a minute per session and video: end them now.
    permission_cache.clear()
