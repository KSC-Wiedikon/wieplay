"""Nomal administration: which teams use Nomal, and the Wiedisync sync."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from nomal.api.deps import AdminDep, DbDep, audit
from nomal.core.models import SyncState, WdTeam
from nomal.core.sync import BridgeError, run_sync

router = APIRouter(prefix="/api/admin", tags=["admin"])

SPORT_ORDER = {"volleyball": 0, "basketball": 1}


class AdminTeamOut(BaseModel):
    id: int
    name: str
    sport: Literal["volleyball", "basketball"]
    season: str
    league: str | None
    enabled: bool


class EnableIn(BaseModel):
    enabled: bool


class SyncStateOut(BaseModel):
    last_run: datetime | None
    last_ok: datetime | None
    error: str | None
    counts: dict[str, int]


def _admin_team(team: WdTeam) -> AdminTeamOut:
    return AdminTeamOut(
        id=team.id,
        name=team.name,
        sport=team.sport,
        season=team.season,
        league=team.league,
        enabled=team.lineage.enabled,
    )


def _state(db: DbDep) -> SyncStateOut:
    state = db.get(SyncState, 1)
    if state is None:
        return SyncStateOut(last_run=None, last_ok=None, error=None, counts={})
    return SyncStateOut(
        last_run=state.last_run, last_ok=state.last_ok, error=state.error, counts=state.counts
    )


@router.get("/teams", response_model=list[AdminTeamOut])
def admin_teams(_: AdminDep, db: DbDep) -> list[AdminTeamOut]:
    teams = db.scalars(select(WdTeam).where(WdTeam.listed.is_(True))).all()
    # Sport, then newest season first ("2026/27" sorts after "2025/26"), then name.
    ordered = sorted(teams, key=lambda t: (SPORT_ORDER.get(t.sport, 9), t.name))
    ordered = sorted(ordered, key=lambda t: t.season, reverse=True)
    ordered = sorted(ordered, key=lambda t: SPORT_ORDER.get(t.sport, 9))
    return [_admin_team(t) for t in ordered]


@router.put("/teams/{team_id}", response_model=AdminTeamOut)
def set_team_enabled(team_id: int, body: EnableIn, viewer: AdminDep, db: DbDep) -> AdminTeamOut:
    team = db.get(WdTeam, team_id)
    if team is None or not team.listed:
        raise HTTPException(404, "Team not found")
    lineage = team.lineage
    if lineage.enabled != body.enabled:
        lineage.enabled = body.enabled
        lineage.enabled_by = viewer.member_id
        lineage.enabled_at = datetime.now(UTC)
        audit(
            db,
            viewer.member_id,
            "team_enabled" if body.enabled else "team_disabled",
            "team_lineage",
            str(lineage.id),
            team_id=team_id,
            team=team.name,
        )
        db.commit()
    return _admin_team(team)


@router.get("/sync", response_model=SyncStateOut)
def sync_status(_: AdminDep, db: DbDep) -> SyncStateOut:
    return _state(db)


@router.post("/sync", response_model=SyncStateOut)
def sync_now(request: Request, viewer: AdminDep, db: DbDep) -> SyncStateOut:
    client = request.app.state.bridge_client_factory()
    try:
        run_sync(db, client)
    except BridgeError as exc:
        raise HTTPException(502, f"Sync failed: {exc}") from exc
    finally:
        audit(db, viewer.member_id, "sync_run")
        db.commit()
    return _state(db)
