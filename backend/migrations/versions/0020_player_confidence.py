"""players v2: how sure the players job is that a card is one whole person (PLAN §10.5)

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-18 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0..1 from the players job (wieplay/ml/identities.py `confidence`); NULL for the cards of
    # runs before players v2. The gallery marks cards below 0.5 for the coach to check.
    op.add_column("player_cluster", sa.Column("confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("player_cluster", "confidence")
