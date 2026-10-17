"""Team helpers (PLAN §5.1 "analyst", §6.10 `team_grant`): Nomal-local grants per lineage.

The rules about what a helper may do live in core/permissions.py; this module only reads and
writes the grants.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from nomal.core.models import TeamGrant

ANALYST = "analyst"


def analyst_lineages(db: Session, member_id: int) -> frozenset[uuid.UUID]:
    """The lineages this member helps now (open analyst grants)."""
    rows = db.scalars(
        select(TeamGrant.lineage_id).where(
            TeamGrant.member_id == member_id,
            TeamGrant.role == ANALYST,
            TeamGrant.revoked_at.is_(None),
        )
    )
    return frozenset(rows)


def open_grants(db: Session, lineage_id: uuid.UUID) -> list[TeamGrant]:
    """A team's helpers now, longest-standing first."""
    return list(
        db.scalars(
            select(TeamGrant)
            .where(
                TeamGrant.lineage_id == lineage_id,
                TeamGrant.role == ANALYST,
                TeamGrant.revoked_at.is_(None),
            )
            .order_by(TeamGrant.granted_at, TeamGrant.member_name)
        )
    )
