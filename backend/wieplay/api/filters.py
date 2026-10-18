"""Saved filters (PLAN §9.7): a search kept by name, for oneself or shared with a team.

GET    /api/filters?team_id=   mine (private: any team) and the team's shared ones; without
                               team_id the shared ones of every team I see
POST   /api/filters            save one: {title, query, visibility, team_id}
PUT    /api/filters/{id}       rename it, change its words, share or unshare it (the owner)
DELETE /api/filters/{id}       the owner; a team's shared one also its coaches

The query is the words as typed ("set 3, rotation 2, reception -"); the web reads them
(web/src/lib/search.ts), so a filter keeps working as the search learns words. Who may do
what is in wieplay/core/permissions.py (can_share_filter, can_see_filter, can_edit_filter,
can_remove_filter).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from wieplay.api.access import team_refs, visible_team
from wieplay.api.deps import DbDep, ViewerDep
from wieplay.core.models import SavedFilter, WdMember
from wieplay.core.permissions import (
    TeamRef,
    Viewer,
    can_edit_filter,
    can_remove_filter,
    can_see_filter,
    can_share_filter,
    lead_lineages,
    visible_team_ids,
)

router = APIRouter(prefix="/api", tags=["filters"])

MAX_PER_PERSON = 100


class FilterIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=300)
    visibility: Literal["private", "team"] = "private"
    # The team it is saved for (any season of it); a shared filter needs one.
    team_id: int | None = None


class FilterOut(BaseModel):
    id: uuid.UUID
    title: str
    query: str
    visibility: str
    lineage_id: uuid.UUID | None
    owner: int
    owner_name: str | None
    mine: bool
    can_edit: bool
    can_delete: bool
    created_at: datetime
    updated_at: datetime


class _Teams:
    """The viewer's view of the teams, read once per request."""

    def __init__(self, db: Session, viewer: Viewer) -> None:
        self.refs = team_refs(db)
        self.visible = visible_team_ids(viewer, self.refs)
        self.leads = lead_lineages(viewer, self.refs)
        self.seen_lineages = {r.lineage_id for r in self.refs if r.id in self.visible}

    def rows_of(self, lineage_id: uuid.UUID | None) -> list[TeamRef]:
        return [r for r in self.refs if r.lineage_id == lineage_id and r.id in self.visible]


def _out(row: SavedFilter, viewer: Viewer, teams: _Teams, names: dict[int, str]) -> FilterOut:
    shared = row.visibility == "team"
    rows = teams.rows_of(row.lineage_id) if shared else []
    return FilterOut(
        id=row.id,
        title=row.title,
        query=row.query,
        visibility=row.visibility,
        lineage_id=row.lineage_id,
        owner=row.owner,
        owner_name=names.get(row.owner),
        mine=row.owner == viewer.member_id,
        can_edit=can_edit_filter(viewer, row.owner),
        can_delete=can_remove_filter(viewer, row.owner, rows[0] if rows else None, teams.leads),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = db.execute(select(WdMember.id, WdMember.display_name).where(WdMember.id.in_(ids)))
    return {mid: name for mid, name in rows}


def _readable(db: Session, viewer: Viewer, teams: _Teams, filter_id: uuid.UUID) -> SavedFilter:
    row = db.get(SavedFilter, filter_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "Filter not found")
    sees = row.lineage_id in teams.seen_lineages
    if not can_see_filter(viewer, row.owner, row.visibility == "team", sees):
        raise HTTPException(404, "Filter not found")
    return row


def _lineage(db: Session, viewer: Viewer, body: FilterIn, teams: _Teams) -> uuid.UUID | None:
    """The lineage the filter is saved for; sharing needs the right to share with that team."""
    if body.team_id is None:
        if body.visibility == "team":
            raise HTTPException(422, "A shared filter names its team")
        return None
    access = visible_team(db, viewer, body.team_id)
    if body.visibility == "team" and not any(
        can_share_filter(viewer, r, teams.leads) for r in teams.rows_of(access.team.lineage_id)
    ):
        raise HTTPException(403, "Only the team's coaches and helpers share filters with it")
    return access.team.lineage_id


@router.get("/filters", response_model=list[FilterOut])
def list_filters(viewer: ViewerDep, db: DbDep, team_id: int | None = None) -> list[FilterOut]:
    teams = _Teams(db, viewer)
    if team_id is not None:
        shared = {visible_team(db, viewer, team_id).team.lineage_id}
    else:
        shared = teams.seen_lineages
    mine = SavedFilter.owner == viewer.member_id
    if team_id is not None:
        mine = mine & (SavedFilter.visibility == "private")
    rows = list(
        db.scalars(
            select(SavedFilter)
            .where(
                SavedFilter.deleted_at.is_(None),
                or_(
                    mine,
                    (SavedFilter.visibility == "team") & SavedFilter.lineage_id.in_(shared),
                ),
            )
            .order_by(func.lower(SavedFilter.title), SavedFilter.created_at)
        )
    )
    names = _names(db, {r.owner for r in rows})
    return [_out(r, viewer, teams, names) for r in rows]


@router.post("/filters", response_model=FilterOut, status_code=201)
def create_filter(body: FilterIn, viewer: ViewerDep, db: DbDep) -> FilterOut:
    teams = _Teams(db, viewer)
    lineage = _lineage(db, viewer, body, teams)
    count = db.scalar(
        select(func.count())
        .select_from(SavedFilter)
        .where(SavedFilter.owner == viewer.member_id, SavedFilter.deleted_at.is_(None))
    )
    if (count or 0) >= MAX_PER_PERSON:
        raise HTTPException(409, f"At most {MAX_PER_PERSON} saved filters each")
    row = SavedFilter(
        id=uuid.uuid4(),
        owner=viewer.member_id,
        lineage_id=lineage,
        title=body.title.strip(),
        query=body.query.strip(),
        visibility=body.visibility,
    )
    db.add(row)
    db.commit()
    return _out(row, viewer, teams, _names(db, {row.owner}))


@router.put("/filters/{filter_id}", response_model=FilterOut)
def update_filter(filter_id: uuid.UUID, body: FilterIn, viewer: ViewerDep, db: DbDep) -> FilterOut:
    teams = _Teams(db, viewer)
    row = _readable(db, viewer, teams, filter_id)
    if not can_edit_filter(viewer, row.owner):
        raise HTTPException(403, "Only its owner changes a filter")
    lineage = _lineage(db, viewer, body, teams) if body.team_id is not None else row.lineage_id
    # Without team_id a shared filter stays with the team it was saved for.
    if (
        body.visibility == "team"
        and body.team_id is None
        and not any(can_share_filter(viewer, r, teams.leads) for r in teams.rows_of(lineage))
    ):
        raise HTTPException(403, "Only the team's coaches and helpers share filters with it")
    row.title, row.query = body.title.strip(), body.query.strip()
    row.visibility, row.lineage_id = body.visibility, lineage
    db.commit()
    return _out(row, viewer, teams, _names(db, {row.owner}))


@router.delete("/filters/{filter_id}", status_code=204)
def delete_filter(filter_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> None:
    teams = _Teams(db, viewer)
    row = _readable(db, viewer, teams, filter_id)
    rows = teams.rows_of(row.lineage_id) if row.visibility == "team" else []
    if not can_remove_filter(viewer, row.owner, rows[0] if rows else None, teams.leads):
        raise HTTPException(403, "Only its owner or the team's coaches delete a filter")
    row.deleted_at = datetime.now(UTC)
    db.commit()
