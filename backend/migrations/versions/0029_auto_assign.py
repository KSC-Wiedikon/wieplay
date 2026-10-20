"""auto assign: WiePlay's results are tags, sure ones "auto", unsure ones "check" (PLAN §10)

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-20 22:00:00.000000

Before 20.10.2026 everything WiePlay made was a suggestion (source "model", review
"unreviewed") that counted nowhere until a coach accepted it. Since then it is a tag that counts
from the start, sure ones as "auto", unsure ones as "check" (to check). This migration:

- turns WiePlay's open suggestions into tags: actions by their sure flag (`model_sure`), rallies
  by the joint decode (`score_sure`), breaks and sets by their confidence (0.9); a person's
  answers (confirmed, corrected, rejected, merged) and a person's own tags stay as they are;
- reopens who won where a coach had confirmed only a rally's bounds ("Accept the rallies ≥ 90 %
  (who won still to check)", PR #74): those rallies become "check" again, as before they were
  "Needs you" (the revision log keeps the confirmation);
- adds `segment.model_alternative`: WiePlay's likeliest other answer for a rally (us, them,
  none) or a break (another kind), like `event.model_alternative`;
- makes the revision trigger take WiePlay's own switch between "auto" and "check" (and the
  older "unreviewed") as derived: a scoring that grows surer of a rally is nobody's edit;
- indexes the actions still to check per video instead of the open suggestions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0026's function with model_alternative as derived on segments, and WiePlay's own review switch.
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
                              'period_id', 'updated_at',
                              'log_won_by', 'log_score_us', 'log_score_them', 'log_at_ms',
                              'sheet_point', 'sheet_won_by', 'sheet_serving',
                              'score_sure', 'score_reasons'{extra}]
    WHEN 'event' THEN ARRAY['updated_at', 'member_id', 'segment_id']
    ELSE ARRAY['updated_at'] END;
BEGIN
  {own}
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
OWN_SWITCH = """IF TG_OP = 'UPDATE' THEN
    IF OLD.review IN ('unreviewed', 'auto', 'check')
       AND NEW.review IN ('unreviewed', 'auto', 'check') THEN
      derived := derived || ARRAY['review'];
    END IF;
  END IF;"""
NOW = REVISION_FUNCTION.replace("{extra}", ", 'model_alternative'").replace("{own}", OWN_SWITCH)
BEFORE = REVISION_FUNCTION.replace("{extra}", "").replace("{own}", "")

# Breaks and sets are sure from confidence 0.9 (tagging.GAP_SURE, SET_SURE).


def upgrade() -> None:
    op.add_column("segment", sa.Column("model_alternative", sa.String(length=8), nullable=True))
    op.execute(NOW)
    # The conversion runs with the new trigger: WiePlay's own switch writes no revision, the
    # reopened winners do (changed_by empty: WiePlay's).
    op.execute(
        "UPDATE event SET review = CASE WHEN model_sure THEN 'auto' ELSE 'check' END "
        "WHERE source = 'model' AND review = 'unreviewed'"
    )
    op.execute(
        "UPDATE segment SET review = CASE WHEN score_sure THEN 'auto' ELSE 'check' END "
        "WHERE source = 'model' AND kind = 'rally' AND review = 'unreviewed'"
    )
    op.execute(
        "UPDATE segment SET review = 'check' "
        "WHERE source = 'model' AND kind = 'rally' AND review = 'confirmed' "
        "AND won_by IS NULL AND score_sure IS FALSE AND deleted_at IS NULL"
    )
    op.execute(
        "UPDATE segment SET review = CASE WHEN confidence >= 0.9 THEN 'auto' ELSE 'check' END "
        "WHERE source = 'model' AND review = 'unreviewed' AND kind IN ('warmup', 'set_break', "
        "'timeout', 'stoppage', 'substitution', 'other_dead')"
    )
    op.execute(
        "UPDATE period SET review = CASE WHEN confidence >= 0.9 THEN 'auto' ELSE 'check' END "
        "WHERE source = 'model' AND review = 'unreviewed'"
    )
    op.drop_index("ix_event_video_open_model", table_name="event")
    # "N still to check" is counted on every tagging, analysis and stats read.
    op.create_index(
        "ix_event_video_to_check",
        "event",
        ["video_id"],
        postgresql_where=sa.text("source = 'model' AND review = 'check' AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_event_video_to_check", table_name="event")
    op.create_index(
        "ix_event_video_open_model",
        "event",
        ["video_id"],
        postgresql_where=sa.text(
            "source = 'model' AND review = 'unreviewed' AND deleted_at IS NULL"
        ),
    )
    for table in ("event", "segment", "period"):
        op.execute(
            f"UPDATE {table} SET review = 'unreviewed' "  # noqa: S608 (fixed table names)
            "WHERE source = 'model' AND review IN ('auto', 'check')"
        )
    op.execute(BEFORE)
    op.drop_column("segment", "model_alternative")
