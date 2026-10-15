"""Who may see what (PLAN §5.1). M0 covers team visibility and Nomal administration.

Every rule lives here and is tested in tests/test_permissions.py; routes only call these.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

ADMIN_ROLES = frozenset({"admin", "superuser"})
LEAD_RELS = frozenset({"coach", "team_responsible"})


@dataclass(frozen=True)
class Viewer:
    member_id: int
    member_uuid: str
    name: str
    roles: frozenset[str]
    sport_admin: frozenset[str]
    # Wiedisync team row id -> relations of this member on that row.
    team_rels: Mapping[int, frozenset[str]]

    @property
    def is_admin(self) -> bool:
        return bool(self.roles & ADMIN_ROLES)

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any]) -> Viewer:
        rels: dict[int, frozenset[str]] = {}
        for entry in claims.get("teams", []):
            rels[int(entry["team"])] = frozenset(entry.get("rel", [])) | rels.get(
                int(entry["team"]), frozenset()
            )
        return cls(
            member_id=int(claims["mid"]),
            member_uuid=str(claims["sub"]),
            name=str(claims["name"]),
            roles=frozenset(claims.get("roles", [])),
            sport_admin=frozenset(claims.get("sport_admin", [])),
            team_rels=rels,
        )


@dataclass(frozen=True)
class TeamRef:
    id: int
    sport: str
    lineage_id: uuid.UUID
    enabled: bool


def can_manage_nomal(viewer: Viewer) -> bool:
    """Enable teams, run the sync: full Wiedisync admins only."""
    return viewer.is_admin


def visible_team_ids(viewer: Viewer, teams: Iterable[TeamRef]) -> set[int]:
    """Team rows the viewer may see.

    - Only teams whose lineage is enabled for Nomal.
    - Admins: all. Sport admins: all teams of their sport.
    - Members of a team row (player, guest, captain, coach, team manager) see that row:
      players see the seasons they were rostered in.
    - Coaches and team managers see every season of their team's lineage.
    """
    teams = [t for t in teams if t.enabled]
    if viewer.is_admin:
        return {t.id for t in teams}
    lead_lineages = {
        t.lineage_id for t in teams if viewer.team_rels.get(t.id, frozenset()) & LEAD_RELS
    }
    return {
        t.id
        for t in teams
        if t.sport in viewer.sport_admin
        or t.id in viewer.team_rels
        or t.lineage_id in lead_lineages
    }
