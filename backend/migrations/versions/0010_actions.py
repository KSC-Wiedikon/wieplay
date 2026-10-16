"""suggested actions (PLAN §10.4a): the ball at contact on events, "actions reviewed"

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-17 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Where the ball was at the contact (normalised image coordinates, 0..1): a model's
    # suggested action carries it, so the player attribution (PLAN §10.5) can look there.
    op.add_column("event", sa.Column("ball_x", sa.Float(), nullable=True))
    op.add_column("event", sa.Column("ball_y", sa.Float(), nullable=True))
    # A coach says the video's actions are complete and right: they train the proposer.
    op.add_column(
        "video", sa.Column("actions_reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("video", sa.Column("actions_reviewed_by", sa.Integer(), nullable=True))
    # The open suggestions of a video are counted on every tagging read and stats read.
    op.create_index(
        "ix_event_video_open_model",
        "event",
        ["video_id"],
        postgresql_where=sa.text(
            "source = 'model' AND review = 'unreviewed' AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_event_video_open_model", table_name="event")
    op.drop_column("video", "actions_reviewed_by")
    op.drop_column("video", "actions_reviewed_at")
    op.drop_column("event", "ball_y")
    op.drop_column("event", "ball_x")
