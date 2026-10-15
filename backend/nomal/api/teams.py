"""The viewer's profile and Nomal-enabled teams with their games (matches only, 16.10.2026)."""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from nomal.api.access import team_refs
from nomal.api.deps import DbDep, ViewerDep
from nomal.core.models import TeamLineage, WdGame, WdTeam
from nomal.core.permissions import (
    TeamRef,
    Viewer,
    can_export,
    can_manage_nomal,
    can_share_with_team,
    can_tag,
    can_upload,
    lead_lineages,
    visible_team_ids,
)

router = APIRouter(prefix="/api", tags=["teams"])


class TeamOut(BaseModel):
    id: int
    name: str
    sport: Literal["volleyball", "basketball"]
    season: str
    league: str | None
    rel: list[str]
    # The same team across seasons (D1 2026/27 was D2 in 2025/26): the start page groups by it.
    lineage_id: uuid.UUID


class TeamDetailOut(TeamOut):
    # Coach/team manager of this season's team or sport admin: upload videos.
    can_upload: bool
    # Coaches and team managers (any season) and sport admins: tag, share, download.
    can_tag: bool
    can_share: bool
    can_export: bool


class MemberOut(BaseModel):
    id: int
    uuid: str
    name: str


class MeOut(BaseModel):
    member: MemberOut
    roles: list[str]
    is_admin: bool
    sport_admin: list[str]
    teams: list[TeamOut]


class GameOut(BaseModel):
    id: int
    date: date | None
    time: time | None
    home_name: str | None
    away_name: str | None
    our_side: Literal["home", "away"] | None
    league: str | None
    hall_name: str | None
    home_score: int | None
    away_score: int | None
    sets: list[list[int]] | None


SPORT_ORDER = {"volleyball": 0, "basketball": 1}


def _visible_teams(db: Session, viewer: Viewer) -> list[WdTeam]:
    rows = db.execute(select(WdTeam, TeamLineage.enabled).join(WdTeam.lineage)).all()
    refs = [TeamRef(t.id, t.sport, t.lineage_id, enabled) for t, enabled in rows]
    ids = visible_team_ids(viewer, refs)
    teams = [t for t, _ in rows if t.id in ids]
    return sorted(teams, key=lambda t: (SPORT_ORDER.get(t.sport, 9), t.name, t.season))


def _team_out(team: WdTeam, viewer: Viewer) -> TeamOut:
    return TeamOut(
        id=team.id,
        name=team.name,
        sport=team.sport,
        season=team.season,
        league=team.league,
        rel=sorted(viewer.team_rels.get(team.id, frozenset())),
        lineage_id=team.lineage_id,
    )


def _get_visible_team(db: Session, viewer: Viewer, team_id: int) -> WdTeam:
    for team in _visible_teams(db, viewer):
        if team.id == team_id:
            return team
    raise HTTPException(404, "Team not found")


@router.get("/me", response_model=MeOut)
def me(viewer: ViewerDep, db: DbDep) -> MeOut:
    return MeOut(
        member=MemberOut(id=viewer.member_id, uuid=viewer.member_uuid, name=viewer.name),
        roles=sorted(viewer.roles),
        is_admin=can_manage_nomal(viewer),
        sport_admin=sorted(viewer.sport_admin),
        teams=[_team_out(t, viewer) for t in _visible_teams(db, viewer)],
    )


@router.get("/teams", response_model=list[TeamOut])
def list_teams(viewer: ViewerDep, db: DbDep) -> list[TeamOut]:
    return [_team_out(t, viewer) for t in _visible_teams(db, viewer)]


@router.get("/teams/{team_id}", response_model=TeamDetailOut)
def get_team(team_id: int, viewer: ViewerDep, db: DbDep) -> TeamDetailOut:
    team = _get_visible_team(db, viewer, team_id)
    ref = TeamRef(team.id, team.sport, team.lineage_id, team.lineage.enabled)
    leads = lead_lineages(viewer, team_refs(db))
    return TeamDetailOut(
        **_team_out(team, viewer).model_dump(),
        can_upload=can_upload(viewer, ref),
        can_tag=can_tag(viewer, ref, leads),
        can_share=can_share_with_team(viewer, ref, leads),
        can_export=can_export(viewer, ref, leads),
    )


class SeasonOut(BaseModel):
    team_id: int
    name: str
    season: str


@router.get("/teams/{team_id}/seasons", response_model=list[SeasonOut])
def team_seasons(team_id: int, viewer: ViewerDep, db: DbDep) -> list[SeasonOut]:
    """The seasons of this team the viewer can see (newest first)."""
    team = _get_visible_team(db, viewer, team_id)
    visible = {t.id for t in _visible_teams(db, viewer)}
    rows = db.scalars(select(WdTeam).where(WdTeam.lineage_id == team.lineage_id))
    out = [SeasonOut(team_id=t.id, name=t.name, season=t.season) for t in rows if t.id in visible]
    return sorted(out, key=lambda s: s.season, reverse=True)


@router.get("/teams/{team_id}/games", response_model=list[GameOut])
def team_games(team_id: int, viewer: ViewerDep, db: DbDep) -> list[GameOut]:
    _get_visible_team(db, viewer, team_id)
    rows = db.scalars(
        select(WdGame).where(WdGame.team_id == team_id).order_by(WdGame.date, WdGame.time)
    )
    return [GameOut.model_validate(g, from_attributes=True) for g in rows]
