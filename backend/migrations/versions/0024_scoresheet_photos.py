"""scoresheet photos: a redacted photo of the paper sheet beside the typed form (PLAN §10.3a, SS3)

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-19 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Photos of the paper Matchblatt (research note §5.1, §6.1; owner's privacy OK 18.10.2026,
    # PLAN O14): the dates of birth are blacked out on the coach's device before the upload, the
    # API stores only its own re-encoded JPEG (no EXIF/GPS) under sheets/<video>/<id>.jpg in the
    # private bucket. One sheet may have several (front and back, or one per set).
    op.create_table(
        "scoresheet_image",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scoresheet_id", sa.UUID(), nullable=False),
        sa.Column("video_id", sa.UUID(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("set_no", sa.Integer(), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column(
            "content_type", sa.String(length=32), server_default="image/jpeg", nullable=False
        ),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("exif_stripped", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("redacted", sa.Boolean(), nullable=False),
        sa.Column("redaction", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("registration", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column(
            "uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Integer(), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("page >= 1", name="ck_scoresheet_image_page"),
        sa.CheckConstraint("set_no BETWEEN 1 AND 5", name="ck_scoresheet_image_set_no"),
        sa.CheckConstraint("redacted", name="ck_scoresheet_image_redacted"),
        sa.ForeignKeyConstraint(["scoresheet_id"], ["scoresheet.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["video.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index("ix_scoresheet_image_scoresheet_id", "scoresheet_image", ["scoresheet_id"])
    op.create_index("ix_scoresheet_image_video_id", "scoresheet_image", ["video_id"])
    # Removed photos whose bucket object is still to be deleted (the API's sweep).
    op.create_index(
        "ix_scoresheet_image_unpurged",
        "scoresheet_image",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL AND purged_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_scoresheet_image_unpurged", table_name="scoresheet_image")
    op.drop_index("ix_scoresheet_image_video_id", table_name="scoresheet_image")
    op.drop_index("ix_scoresheet_image_scoresheet_id", table_name="scoresheet_image")
    op.drop_table("scoresheet_image")
