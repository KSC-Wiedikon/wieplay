"""scoresheet reading: the photo read on club hardware, a draft to confirm (PLAN §10.3a, SS4)

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The sheet's revisions keep what people typed; the reading's proposals (scoresheet.read) are
# the reader's, kept apart, and do not make a revision of their own.
SHEET_REVISION_FUNCTION = """
CREATE OR REPLACE FUNCTION wieplay_scoresheet_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  actor int := NULLIF(current_setting('wieplay.actor', true), '')::int;
  act text;
BEGIN
  IF TG_OP = 'UPDATE'
     AND (to_jsonb(OLD) - 'updated_at' - 'read') = (to_jsonb(NEW) - 'updated_at' - 'read') THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'INSERT' THEN act := 'create';
  ELSIF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL THEN act := 'delete';
  ELSIF NEW.deleted_at IS NULL AND OLD.deleted_at IS NOT NULL THEN act := 'restore';
  ELSE act := 'update';
  END IF;
  INSERT INTO scoresheet_revision (scoresheet_id, version, changed_by, changed_at, action,
                                   before, after)
  VALUES (NEW.id, NEW.version, actor, now(), act,
          CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) - 'read' END,
          to_jsonb(NEW) - 'read');
  RETURN NEW;
END $$;
"""

OLD_FUNCTION = """
CREATE OR REPLACE FUNCTION wieplay_scoresheet_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  actor int := NULLIF(current_setting('wieplay.actor', true), '')::int;
  act text;
BEGIN
  IF TG_OP = 'UPDATE' AND (to_jsonb(OLD) - 'updated_at') = (to_jsonb(NEW) - 'updated_at') THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'INSERT' THEN act := 'create';
  ELSIF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL THEN act := 'delete';
  ELSIF NEW.deleted_at IS NULL AND OLD.deleted_at IS NOT NULL THEN act := 'restore';
  ELSE act := 'update';
  END IF;
  INSERT INTO scoresheet_revision (scoresheet_id, version, changed_by, changed_at, action,
                                   before, after)
  VALUES (NEW.id, NEW.version, actor, now(), act,
          CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END, to_jsonb(NEW));
  RETURN NEW;
END $$;
"""


def upgrade() -> None:
    # Per set: draft or confirmed, the proposals with the reader's alternatives, which cells to
    # check, where each cell is on the photo; the set as it was before (research §6.1 `read`).
    op.add_column(
        "scoresheet", sa.Column("read", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    # What the reader saw on the photo (per cell its likeliest values and where), so the sheet
    # can be decoded again (another orientation) without a new job.
    op.add_column(
        "scoresheet_image",
        sa.Column("reading", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(SHEET_REVISION_FUNCTION)
    # A confirmed cell is a labelled example for the reader's next training: where the cell is
    # on the stored photo and what a person confirmed it holds. No copy of the picture: the crop
    # is cut again from the photo, and goes when the photo goes.
    op.create_table(
        "scoresheet_label",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("image_id", sa.UUID(), nullable=False),
        sa.Column("video_id", sa.UUID(), nullable=False),
        sa.Column("set_no", sa.Integer(), nullable=False),
        sa.Column("cell", sa.String(length=40), nullable=False),
        sa.Column("quad", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("value", sa.String(length=8), nullable=False),  # a number, "X" or "" (blank)
        sa.Column("read_value", sa.String(length=8), nullable=True),
        sa.Column("read_p", sa.Float(), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("confirmed_by", sa.Integer(), nullable=True),
        sa.Column(
            "confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["image_id"], ["scoresheet_image.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["video.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("image_id", "cell", name="uq_scoresheet_label_cell"),
    )
    op.create_index("ix_scoresheet_label_video_id", "scoresheet_label", ["video_id"])


def downgrade() -> None:
    op.drop_index("ix_scoresheet_label_video_id", table_name="scoresheet_label")
    op.drop_table("scoresheet_label")
    op.execute(OLD_FUNCTION)
    op.drop_column("scoresheet_image", "reading")
    op.drop_column("scoresheet", "read")
