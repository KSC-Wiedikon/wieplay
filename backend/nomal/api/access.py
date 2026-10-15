"""Load teams and videos through the permission rules (core/permissions.py)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from nomal.core.models import TeamLineage, Video, WdTeam
from nomal.core.permissions import TeamRef, Viewer, lead_lineages, visible_team_ids


@dataclass(frozen=True)
class TeamAccess:
    team: WdTeam
    ref: TeamRef
    leads: set[uuid.UUID]


def team_refs(db: Session) -> list[TeamRef]:
    rows = db.execute(
        select(WdTeam.id, WdTeam.sport, WdTeam.lineage_id, TeamLineage.enabled).join(
            TeamLineage, TeamLineage.id == WdTeam.lineage_id
        )
    ).all()
    return [TeamRef(r.id, r.sport, r.lineage_id, r.enabled) for r in rows]


def visible_team(db: Session, viewer: Viewer, team_id: int) -> TeamAccess:
    refs = team_refs(db)
    if team_id not in visible_team_ids(viewer, refs):
        raise HTTPException(404, "Team not found")
    team = db.get(WdTeam, team_id)
    assert team is not None
    ref = next(r for r in refs if r.id == team_id)
    return TeamAccess(team, ref, lead_lineages(viewer, refs))


def visible_video(db: Session, viewer: Viewer, video_id: uuid.UUID) -> tuple[Video, TeamAccess]:
    """A video the viewer may watch; 404 for anything else, deleted videos included."""
    video = db.get(Video, video_id)
    if video is None or video.status == "deleted":
        raise HTTPException(404, "Video not found")
    try:
        access = visible_team(db, viewer, video.team_id)
    except HTTPException:
        raise HTTPException(404, "Video not found") from None
    return video, access
