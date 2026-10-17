"""WiePlay: the app was called Nomal until 17.10.2026; its database objects follow the name

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-17 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The revision trigger function as 0007 left it, reading who acts from `<prefix>.actor`
# (set per transaction by wieplay/core/tagging.py).
REVISION_FUNCTION = """
CREATE OR REPLACE FUNCTION {name}_write_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  actor int := NULLIF(current_setting('{name}.actor', true), '')::int;
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


def _rename(old: str, new: str) -> None:
    # The triggers point at the function itself, not its name: they follow the rename.
    op.execute(f"ALTER FUNCTION {old}_write_revision() RENAME TO {new}_write_revision")
    op.execute(REVISION_FUNCTION.format(name=new))
    # Model names are written with every run and never looked up: the old runs read the same.
    op.execute(
        sa.text(
            "UPDATE model_run SET model_name = :new || substr(model_name, :cut) "
            "WHERE model_name LIKE :old"
        ).bindparams(new=f"{new}-", cut=len(old) + 2, old=f"{old}-%")
    )


def upgrade() -> None:
    _rename("nomal", "wieplay")


def downgrade() -> None:
    _rename("wieplay", "nomal")
