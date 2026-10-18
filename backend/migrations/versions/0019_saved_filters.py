"""saved filters: searches kept by name, private or shared with a team (PLAN §9.7)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-18 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The words as typed; the web reads them (web/src/lib/search.ts). owner has no foreign key,
    # like a reel's: the mirror purges members whose teams are not enabled.
    op.create_table(
        "saved_filter",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner", sa.Integer(), nullable=False),
        sa.Column("lineage_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("query", sa.String(length=300), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["lineage_id"], ["team_lineage.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_saved_filter_owner"), "saved_filter", ["owner"], unique=False)
    op.create_index(
        op.f("ix_saved_filter_lineage_id"), "saved_filter", ["lineage_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_saved_filter_lineage_id"), table_name="saved_filter")
    op.drop_index(op.f("ix_saved_filter_owner"), table_name="saved_filter")
    op.drop_table("saved_filter")
