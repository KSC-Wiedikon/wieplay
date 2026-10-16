"""court calibration per video (PLAN §6.9 `calibration`, §10.6)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-16 22:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calibration",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("video_id", sa.UUID(), nullable=False),
        sa.Column("lineage_id", sa.UUID(), nullable=False),
        sa.Column("hall_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("landmarks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("image_points", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("court_points", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("image_aspect", sa.Float(), nullable=False),
        sa.Column("homography", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("camera", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reproj_error_cm", sa.Float(), nullable=True),
        sa.Column("max_error_cm", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model_run_id", sa.UUID(), nullable=True),
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
        sa.ForeignKeyConstraint(["model_run_id"], ["model_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id", "status", name="uq_calibration_video_status"),
    )
    op.create_index(op.f("ix_calibration_video_id"), "calibration", ["video_id"], unique=False)
    op.create_index(op.f("ix_calibration_lineage_id"), "calibration", ["lineage_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_calibration_lineage_id"), table_name="calibration")
    op.drop_index(op.f("ix_calibration_video_id"), table_name="calibration")
    op.drop_table("calibration")
