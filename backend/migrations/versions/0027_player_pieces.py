"""players v3: a card is one person, or a pieces card (PLAN §10.5)

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-19 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # person: one person (the pieces of a number, or one stretch the tracker followed);
    # pieces: pieces without a number that look alike, likely several teammates, for the
    # coach to name a few at a time. The cards of older runs are persons.
    op.add_column(
        "player_cluster",
        sa.Column("kind", sa.String(length=8), nullable=False, server_default="person"),
    )


def downgrade() -> None:
    op.drop_column("player_cluster", "kind")
