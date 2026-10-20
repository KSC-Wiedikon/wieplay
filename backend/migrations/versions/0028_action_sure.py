"""action sure: a suggested action's "sure" flag and the likeliest other answer (PLAN §10.4a)

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-20 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Written with a model's suggestion only (null on a person's tag): sure (accepted in bulk
    # with "Accept all sure actions"), else the likeliest other answer ("D:us", "none").
    op.add_column("event", sa.Column("model_sure", sa.Boolean(), nullable=True))
    op.add_column("event", sa.Column("model_alternative", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("event", "model_alternative")
    op.drop_column("event", "model_sure")
