"""players in a video and who made each action (PLAN §10.5, §11.5)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-17 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A person seen in a video (tracks the players job grouped), and the coach's answer:
    # a number of the video's roster snapshot, or not a player. Nothing across videos.
    op.create_table(
        "player_cluster",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("video_id", sa.UUID(), nullable=False),
        sa.Column("model_run_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Integer(), nullable=False),
        sa.Column("team", sa.String(length=8), nullable=False),
        sa.Column("tracks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("crops", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("detections", sa.Integer(), nullable=False),
        sa.Column("ocr_number", sa.Integer(), nullable=True),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("member_id", sa.Integer(), nullable=True),
        sa.Column("not_player", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("referee_guess", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("merged_into", sa.UUID(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("assigned_by", sa.Integer(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["video_id"], ["video.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_run_id"], ["model_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )  # fmt: skip
    op.create_index(
        op.f("ix_player_cluster_video_id"), "player_cluster", ["video_id"], unique=False
    )
    op.create_index(
        op.f("ix_player_cluster_model_run_id"), "player_cluster", ["model_run_id"], unique=False
    )
    # Who made an action as Nomal sees it, beside the event (a coach's tag is never changed).
    op.create_table(
        "event_player",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("video_id", sa.UUID(), nullable=False),
        sa.Column("model_run_id", sa.UUID(), nullable=True),
        sa.Column("cluster_id", sa.UUID(), nullable=True),
        sa.Column("track_id", sa.BigInteger(), nullable=True),
        sa.Column("number", sa.Integer(), nullable=True),
        sa.Column("member_id", sa.Integer(), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=True),
        sa.Column("distance", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("contact_ms", sa.BigInteger(), nullable=True),
        sa.Column("applied_number", sa.Integer(), nullable=True),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["video.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_run_id"], ["model_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("event_id"),
    )  # fmt: skip
    op.create_index(op.f("ix_event_player_video_id"), "event_player", ["video_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_event_player_video_id"), table_name="event_player")
    op.drop_table("event_player")
    op.drop_index(op.f("ix_player_cluster_model_run_id"), table_name="player_cluster")
    op.drop_index(op.f("ix_player_cluster_video_id"), table_name="player_cluster")
    op.drop_table("player_cluster")
