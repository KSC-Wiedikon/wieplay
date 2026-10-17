"""the scoreboard in the picture (PLAN §6.9 `scoreboard_region`, §10.3 step 5)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-17 04:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Where the score is in a video's picture; the hall's memory offers it to the next videos.
    op.create_table(
        "scoreboard_region",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("video_id", sa.UUID(), nullable=False),
        sa.Column("lineage_id", sa.UUID(), nullable=False),
        sa.Column("hall_name", sa.String(length=255), nullable=True),
        sa.Column("rect", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("layout", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("at_ms", sa.Integer(), nullable=True),
        sa.Column("copied_from", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["video_id"], ["video.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lineage_id"], ["team_lineage.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id"),
    )
    op.create_index(
        op.f("ix_scoreboard_region_lineage_id"), "scoreboard_region", ["lineage_id"], unique=False
    )
    op.create_index(
        op.f("ix_scoreboard_region_hall_name"), "scoreboard_region", ["hall_name"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_scoreboard_region_hall_name"), table_name="scoreboard_region")
    op.drop_index(op.f("ix_scoreboard_region_lineage_id"), table_name="scoreboard_region")
    op.drop_table("scoreboard_region")
