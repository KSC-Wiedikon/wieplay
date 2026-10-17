"""team helpers: analyst grants per team lineage (PLAN §5.1, §6.10 `team_grant`)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-17 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A member a coach picked to help tag a team, for every season of its lineage. member_id
    # has no foreign key: the mirror purges members whose teams are not enabled.
    op.create_table(
        "team_grant",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("lineage_id", sa.UUID(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("member_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("granted_by", sa.Integer(), nullable=False),
        sa.Column("granted_by_name", sa.String(length=255), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["lineage_id"], ["team_lineage.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_team_grant_lineage_id"), "team_grant", ["lineage_id"], unique=False)
    op.create_index(op.f("ix_team_grant_member_id"), "team_grant", ["member_id"], unique=False)
    op.create_index(
        "uq_team_grant_open",
        "team_grant",
        ["lineage_id", "member_id", "role"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_team_grant_open", table_name="team_grant")
    op.drop_index(op.f("ix_team_grant_member_id"), table_name="team_grant")
    op.drop_index(op.f("ix_team_grant_lineage_id"), table_name="team_grant")
    op.drop_table("team_grant")
