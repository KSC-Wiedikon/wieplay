"""The team page's "What WiePlay knows about this team" (PLAN §10.5b): our players with their
numbers and how many games named each, the usual lineups and rotations, the models' scores
for the team; coaches fix a wrong number ↔ player link there."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from wieplay.api.access import visible_team
from wieplay.api.deps import DbDep, ViewerDep, audit
from wieplay.core import team_memory
from wieplay.core.models import WdMember, WdTeam
from wieplay.core.permissions import Viewer, can_run_analysis

router = APIRouter(prefix="/api", tags=["teams"])


class MemoryPlayerOut(BaseModel):
    number: int
    member_id: int | None
    name: str | None
    libero: bool
    games: int  # the team's games that named the number this season
    answers: int  # a coach named a card, a tag or a serve with it
    sets: float  # sets in the starting six (a substitute counts half)
    last_on: date | None
    source: str  # roster | learned | coach
    ambiguous: bool  # several roster members wear it, nobody said who plays
    fixed: bool
    hidden: bool


class MemberChoiceOut(BaseModel):
    id: int
    name: str
    number: int | None


class OrderOut(BaseModel):
    order: list[int]  # the service order, the setter first
    share: float  # of the sets the memory has (0..1)


class RotationsOut(BaseModel):
    sets: float  # sets learnt from (an earlier season's counts half)
    orders: list[OrderOut]
    # The setter's zone at the first serve (the starting rotation): share per zone 1-6.
    start_zones: dict[str, float]
    setters: list[int]
    liberos: list[int]
    # How well the memory named our server at each of our serves on the team's own typed
    # sheets (each game left out in turn): right / serves, and the sure ones.
    check: dict[str, int]


class MemoryOut(BaseModel):
    team_id: int
    available: bool  # volleyball
    games: int
    refreshed_at: datetime | None
    players: list[MemoryPlayerOut]
    rotations: RotationsOut | None
    models: dict[str, Any]
    can_fix: bool
    members: list[MemberChoiceOut]  # who a coach can pick for a number


class FixIn(BaseModel):
    member_id: int | None = None
    hidden: bool = False


def _team(db: Session, viewer: Viewer, team_id: int) -> tuple[WdTeam, bool]:
    access = visible_team(db, viewer, team_id)
    team = db.get(WdTeam, team_id)
    assert team is not None
    return team, can_run_analysis(viewer, access.ref, access.leads)


def _rotations(mem: team_memory.TeamMemory) -> RotationsOut | None:
    rot = mem.rotations or {}
    if not rot:
        return None
    zones = {str(z): float(w) for z, w in (rot.get("start_zones") or {}).items()}
    total = sum(zones.values())
    setters = rot.get("setters") or {}
    return RotationsOut(
        sets=float(rot.get("sets") or 0.0),
        orders=[
            OrderOut(order=[int(n) for n in o["order"]], share=round(float(o["weight"]), 3))
            for o in rot.get("orders") or []
        ],
        start_zones={z: round(w / total, 3) for z, w in zones.items()} if total else {},
        setters=[int(n) for n, _ in sorted(setters.items(), key=lambda kv: -float(kv[1]))],
        liberos=[int(n) for n in rot.get("liberos") or []],
        check={k: int(v) for k, v in (rot.get("check") or {}).items()},
    )


def memory_out(db: Session, team: WdTeam, can_fix: bool) -> MemoryOut:
    mem = team_memory.get(db, team.id)
    if mem is None:
        return MemoryOut(
            team_id=team.id, available=False, games=0, refreshed_at=None, players=[],
            rotations=None, models={}, can_fix=False, members=[],
        )  # fmt: skip
    rows = team_memory.players(db, team.id)
    ids = {r.member_id for r in rows if r.member_id is not None}
    roster = team_memory._roster_members(db, team.id)
    names = {m.id: m.display_name for m in roster}
    missing = ids - set(names)
    if missing:
        names.update(
            {
                m.id: m.display_name
                for m in db.scalars(select(WdMember).where(WdMember.id.in_(missing)))
            }
        )
    # Numbers that are only a roster placeholder (nobody played them, no fix) are not shown.
    shown = [
        r for r in rows if r.games or r.fixed or r.hidden or r.source != "roster" or r.member_id
    ]
    return MemoryOut(
        team_id=team.id,
        available=True,
        games=mem.games,
        refreshed_at=mem.refreshed_at,
        players=[
            MemoryPlayerOut(
                number=r.number,
                member_id=r.member_id,
                name=names.get(r.member_id) if r.member_id is not None else None,
                libero=r.libero,
                games=r.games,
                answers=r.answers,
                sets=r.sets,
                last_on=r.last_on,
                source=r.source,
                ambiguous=r.ambiguous,
                fixed=r.fixed,
                hidden=r.hidden,
            )
            for r in shown
        ],
        rotations=_rotations(mem),
        models=dict(mem.models or {}),
        can_fix=can_fix,
        members=[
            MemberChoiceOut(id=m.id, name=m.display_name, number=m.number)
            for m in sorted(roster, key=lambda m: (m.number is None, m.number or 0, m.display_name))
        ]
        if can_fix
        else [],
    )


@router.get("/teams/{team_id}/memory", response_model=MemoryOut)
def get_memory(team_id: int, viewer: ViewerDep, db: DbDep) -> MemoryOut:
    team, can_fix = _team(db, viewer, team_id)
    out = memory_out(db, team, can_fix)
    db.commit()  # a refresh it needed
    return out


@router.put("/teams/{team_id}/memory/players/{number}", response_model=MemoryOut)
def fix_number(team_id: int, number: int, body: FixIn, viewer: ViewerDep, db: DbDep) -> MemoryOut:
    """A coach says who wears the number this season (or that it is none of ours); the
    season's games follow (their roster snapshots, our tags and cards with the number)."""
    team, can_fix = _team(db, viewer, team_id)
    if not can_fix:
        raise HTTPException(403, "Only the team's coaches and helpers fix the players' numbers")
    if team.sport not in team_memory.SPORTS:
        raise HTTPException(409, "Only for volleyball teams")
    try:
        team_memory.fix(
            db, team, number, member_id=body.member_id, hidden=body.hidden, actor=viewer.member_id
        )
    except team_memory.FixError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(
        db, viewer.member_id, "team_number_fixed", "team", str(team.id),
        number=number, member_id=body.member_id, hidden=body.hidden,
    )  # fmt: skip
    db.commit()
    return memory_out(db, team, can_fix)


@router.delete("/teams/{team_id}/memory/players/{number}", response_model=MemoryOut)
def unfix_number(team_id: int, number: int, viewer: ViewerDep, db: DbDep) -> MemoryOut:
    """Back to what the roster and the games say about the number."""
    team, can_fix = _team(db, viewer, team_id)
    if not can_fix:
        raise HTTPException(403, "Only the team's coaches and helpers fix the players' numbers")
    team_memory.unfix(db, team, number, viewer.member_id)
    audit(db, viewer.member_id, "team_number_unfixed", "team", str(team.id), number=number)
    db.commit()
    return memory_out(db, team, can_fix)
