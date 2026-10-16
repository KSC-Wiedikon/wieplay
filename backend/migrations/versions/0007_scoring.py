"""automatic scoring: the model's server and winner per rally, sets, rotations, tracks (PLAN §10.3)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-16 22:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The revision trigger (0004) with the model's scoring fields as derived: a scoring run that
# only changes them is not someone's edit and leaves no revision.
REVISION_FUNCTION = """
CREATE OR REPLACE FUNCTION nomal_write_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  actor int := NULLIF(current_setting('nomal.actor', true), '')::int;
  act text;
  derived text[] := CASE TG_TABLE_NAME
    WHEN 'segment' THEN ARRAY['score_us_after', 'score_them_after', 'score_source',
                              'score_confidence', 'score_conflict', 'score_note', 'serving',
                              'serving_court', 'serve_confidence', 'model_serving',
                              'model_won_by', 'model_missed_us', 'model_missed_them',
                              'period_id', 'updated_at']
    WHEN 'event' THEN ARRAY['updated_at', 'member_id', 'segment_id']
    ELSE ARRAY['updated_at'] END;
BEGIN
  IF TG_OP = 'UPDATE' AND (to_jsonb(OLD) - derived) = (to_jsonb(NEW) - derived) THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'INSERT' THEN act := 'create';
  ELSIF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL THEN act := 'delete';
  ELSIF NEW.deleted_at IS NULL AND OLD.deleted_at IS NOT NULL THEN act := 'restore';
  ELSIF NEW.review IS DISTINCT FROM OLD.review AND NEW.review = 'confirmed' THEN act := 'confirm';
  ELSIF NEW.review IS DISTINCT FROM OLD.review AND NEW.review = 'rejected' THEN act := 'reject';
  ELSE act := 'update';
  END IF;
  EXECUTE format(
    'INSERT INTO %I (%I, version, changed_by, changed_at, action, before, after) '
    'VALUES ($1, $2, $3, now(), $4, $5, $6)',
    TG_TABLE_NAME || '_revision', TG_TABLE_NAME || '_id')
  USING NEW.id, NEW.version, actor, act,
        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END, to_jsonb(NEW);
  RETURN NEW;
END $$;
"""

PREVIOUS_DERIVED = (
    "ARRAY['score_us_after', 'score_them_after', 'score_source',\n"
    "                              'score_confidence', 'score_conflict', 'serving', 'period_id',\n"
    "                              'updated_at']"
)


def upgrade() -> None:
    # A rally: the serving team a coach called in review; the model's server and winner, next
    # to the coach's (never in their place); why a rally is flagged; how sure the serve side is.
    op.add_column("segment", sa.Column("serving_manual", sa.String(length=8), nullable=True))
    op.add_column("segment", sa.Column("model_serving", sa.String(length=8), nullable=True))
    op.add_column("segment", sa.Column("model_won_by", sa.String(length=8), nullable=True))
    op.add_column("segment", sa.Column("score_note", sa.String(length=16), nullable=True))
    op.add_column("segment", sa.Column("serve_confidence", sa.Float(), nullable=True))
    # Points the model thinks were played without a rally of their own, right before this one
    # (a rally the finder missed); on a set, after its last rally.
    for table in ("segment", "period"):
        op.add_column(table, sa.Column("model_missed_us", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("model_missed_them", sa.Integer(), nullable=True))
    # A set: the starting rotation (our setter's zone 1-6), the model's guesses and confidence.
    op.add_column("period", sa.Column("start_rotation", sa.Integer(), nullable=True))
    op.add_column("period", sa.Column("model_first_serve", sa.String(length=8), nullable=True))
    op.add_column("period", sa.Column("model_court_side", sa.String(length=8), nullable=True))
    op.add_column("period", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("period", sa.Column("model_run_id", sa.UUID(), nullable=True))
    op.create_table(
        "analysis_track",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("video_id", sa.UUID(), nullable=False),
        sa.Column("model_run_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("step_ms", sa.Integer(), nullable=False),
        sa.Column("finder", sa.String(length=16), nullable=True),
        sa.Column("source_run_id", sa.UUID(), nullable=True),
        sa.Column("channels", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["model_run_id"], ["model_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["video_id"], ["video.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_track_video_kind", "analysis_track", ["video_id", "kind", "created_at"]
    )
    op.create_index(
        op.f("ix_analysis_track_model_run_id"), "analysis_track", ["model_run_id"], unique=False
    )
    op.execute(REVISION_FUNCTION)


def downgrade() -> None:
    op.execute(
        REVISION_FUNCTION.replace(
            "ARRAY['score_us_after', 'score_them_after', 'score_source',\n"
            "                              'score_confidence', 'score_conflict', 'score_note', 'serving',\n"
            "                              'serving_court', 'serve_confidence', 'model_serving',\n"
            "                              'model_won_by', 'model_missed_us', 'model_missed_them',\n"
            "                              'period_id', 'updated_at']",
            PREVIOUS_DERIVED,
        )
    )
    op.drop_index(op.f("ix_analysis_track_model_run_id"), table_name="analysis_track")
    op.drop_index("ix_analysis_track_video_kind", table_name="analysis_track")
    op.drop_table("analysis_track")
    for column in ("model_run_id", "confidence", "model_court_side", "model_first_serve",
                   "start_rotation", "model_missed_us", "model_missed_them"):  # fmt: skip
        op.drop_column("period", column)
    for column in ("serve_confidence", "score_note", "model_won_by", "model_serving",
                   "serving_manual", "model_missed_us", "model_missed_them"):  # fmt: skip
        op.drop_column("segment", column)
