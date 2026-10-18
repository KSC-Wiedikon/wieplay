"""guest clubs' seasons: the adults declaration belongs to a season's team row (PLAN §4.5)

A guest team starts its next season as a new team row in the same lineage, and its coach
confirms again for that season that everyone filmed is 18 or older. The declaration moves
from team_lineage to wd_team; an existing one stays with the lineage's newest row (each guest
lineage has a single row so far).

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-18 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wd_team",
        sa.Column("guest_adults_declared_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("wd_team", sa.Column("guest_adults_declared_by", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE wd_team t
        SET guest_adults_declared_at = l.guest_adults_declared_at,
            guest_adults_declared_by = l.guest_adults_declared_by
        FROM team_lineage l,
             (SELECT DISTINCT ON (lineage_id) id FROM wd_team
              WHERE guest ORDER BY lineage_id, season DESC, id DESC) newest
        WHERE t.id = newest.id AND t.lineage_id = l.id
          AND l.guest_adults_declared_at IS NOT NULL
        """
    )
    op.drop_column("team_lineage", "guest_adults_declared_by")
    op.drop_column("team_lineage", "guest_adults_declared_at")


def downgrade() -> None:
    op.add_column(
        "team_lineage",
        sa.Column("guest_adults_declared_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "team_lineage", sa.Column("guest_adults_declared_by", sa.Integer(), nullable=True)
    )
    # The newest declared season's declaration goes back to the lineage.
    op.execute(
        """
        UPDATE team_lineage l
        SET guest_adults_declared_at = d.guest_adults_declared_at,
            guest_adults_declared_by = d.guest_adults_declared_by
        FROM (SELECT DISTINCT ON (lineage_id) lineage_id, guest_adults_declared_at,
                     guest_adults_declared_by
              FROM wd_team WHERE guest AND guest_adults_declared_at IS NOT NULL
              ORDER BY lineage_id, season DESC, id DESC) d
        WHERE d.lineage_id = l.id
        """
    )
    op.drop_column("wd_team", "guest_adults_declared_by")
    op.drop_column("wd_team", "guest_adults_declared_at")
