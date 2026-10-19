"""score sure: per rally whether the joint decode is sure, and why it asks the coach (PLAN §10.3)

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-19 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The segment revision trigger (0023) with the two new fields as derived: a scoring run that only
# changes whether a rally is sure, and why not, is not someone's edit.
REVISION_FUNCTION = """
CREATE OR REPLACE FUNCTION wieplay_write_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  actor int := NULLIF(current_setting('wieplay.actor', true), '')::int;
  act text;
  derived text[] := CASE TG_TABLE_NAME
    WHEN 'segment' THEN ARRAY['score_us_after', 'score_them_after', 'score_source',
                              'score_confidence', 'score_conflict', 'score_note', 'serving',
                              'serving_court', 'serve_confidence', 'model_serving',
                              'model_won_by', 'model_missed_us', 'model_missed_them',
                              'period_id', 'updated_at'{extra}]
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
DERIVED_0023 = (
    ",\n                              'log_won_by', 'log_score_us', 'log_score_them',"
    " 'log_at_ms',\n                              'sheet_point', 'sheet_won_by',"
    " 'sheet_serving'"
)
DERIVED_NOW = DERIVED_0023 + ",\n                              'score_sure', 'score_reasons'"


def upgrade() -> None:
    # Written by the scoring: sure (null: not scored, e.g. a rally outside the sets) and the
    # reasons it needs the coach ([{"code": ..., ...}], empty when sure).
    op.add_column("segment", sa.Column("score_sure", sa.Boolean(), nullable=True))
    op.add_column(
        "segment",
        sa.Column("score_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(REVISION_FUNCTION.replace("{extra}", DERIVED_NOW))


def downgrade() -> None:
    op.execute(REVISION_FUNCTION.replace("{extra}", DERIVED_0023))
    op.drop_column("segment", "score_reasons")
    op.drop_column("segment", "score_sure")
