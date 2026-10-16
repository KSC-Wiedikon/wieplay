"""import channels: the allow-list for imports from YouTube and Twitch (PLAN §7.2.2)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16 02:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_channel",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("ref", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("added_by", sa.Integer(), nullable=True),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform", "ref", name="uq_import_channel_platform_ref"),
    )


def downgrade() -> None:
    op.drop_table("import_channel")
