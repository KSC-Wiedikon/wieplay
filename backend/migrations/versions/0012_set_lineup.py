"""a set's starting lineup (PLAN §9.7): our six shirt numbers in zones I-VI

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-17 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Next to start_rotation (our setter's zone): who stood in zones I-VI at the set's first
    # serve, as six shirt numbers (null: a zone left empty). The rotation charts show it.
    op.add_column(
        "period", sa.Column("start_lineup", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("period", "start_lineup")
