"""The viewer's profile and Nomal-enabled teams with their games and trainings."""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from nomal.api.deps import DbDep, ViewerDep
from nomal.core.models import TeamLineage, WdGame, WdTeam, WdTraining
from nomal.core.permissions import TeamRef, Viewer, can_manage_nomal, visible_team_ids

router = APIRouter(prefix="/api", tags=["teams"])


class TeamOut(BaseModel):
    id: int
    name: str
    sport: Literal["volleyball", "basketball"]
    season: str
    league: str | None
    rel: list[str]


class TeamDetailOut(TeamOut):
    lineage_id: uuid.UUID


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


class TrainingOut(BaseModel):
    id: int
    date: date | None
    start_time: time | None
    end_time: time | None
    hall_name: str | None


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
    return TeamDetailOut(**_team_out(team, viewer).model_dump(), lineage_id=team.lineage_id)


@router.get("/teams/{team_id}/games", response_model=list[GameOut])
def team_games(team_id: int, viewer: ViewerDep, db: DbDep) -> list[GameOut]:
    _get_visible_team(db, viewer, team_id)
    rows = db.scalars(
        select(WdGame).where(WdGame.team_id == team_id).order_by(WdGame.date, WdGame.time)
    )
    return [GameOut.model_validate(g, from_attributes=True) for g in rows]


@router.get("/teams/{team_id}/trainings", response_model=list[TrainingOut])
def team_trainings(team_id: int, viewer: ViewerDep, db: DbDep) -> list[TrainingOut]:
    _get_visible_team(db, viewer, team_id)
    rows = db.scalars(
        select(WdTraining)
        .where(WdTraining.team_id == team_id)
        .order_by(WdTraining.date, WdTraining.start_time)
    )
    return [TrainingOut.model_validate(t, from_attributes=True) for t in rows]
