"""scoresheets: the official sheet typed in per video, what it says about each rally (PLAN §10.3a)

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-18 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The segment revision trigger (0017) with the fields the scoring writes as derived: the score
# log's (0021, forgotten there: a rescore wrote a revision per rally whose log answer changed)
# and the scoresheet's. A scoring run that only changes them is not someone's edit.
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
DERIVED_NOW = (
    ",\n                              'log_won_by', 'log_score_us', 'log_score_them',"
    " 'log_at_ms',\n                              'sheet_point', 'sheet_won_by',"
    " 'sheet_serving'"
)

# Every change of a scoresheet keeps its before and after (a corrected box is a label for the
# reading later, research §6.1). Its own function: the sheet has no review state.
SHEET_REVISION_FUNCTION = """
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
    # One official scoresheet per video (a match): every set's first server, lineups (shirt
    # numbers), service boxes, timeouts, substitutions, sanctions, as typed by a coach. No
    # names, birthdates or licence numbers. A photo of the sheet (SS3) gets its own table.
    op.create_table(
        "scoresheet",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("video_id", sa.UUID(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=16), server_default="typed", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="entered", nullable=False),
        sa.Column("use_in_scoring", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "sets",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["video_id"], ["video.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id"),
    )
    op.create_table(
        "scoresheet_revision",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("scoresheet_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.Integer(), nullable=True),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["scoresheet_id"], ["scoresheet.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scoresheet_revision_scoresheet_id", "scoresheet_revision", ["scoresheet_id"]
    )
    op.execute(SHEET_REVISION_FUNCTION)
    op.execute(
        "CREATE TRIGGER scoresheet_revision AFTER INSERT OR UPDATE ON scoresheet "
        "FOR EACH ROW EXECUTE FUNCTION wieplay_scoresheet_revision()"
    )
    # Per rally, written by the scoring like the score log's: the sheet's point it was lined up
    # with (1-based in its set), that point's winner and server. A timeout's team (the sheet's).
    op.add_column("segment", sa.Column("sheet_point", sa.Integer(), nullable=True))
    op.add_column("segment", sa.Column("sheet_won_by", sa.String(length=8), nullable=True))
    op.add_column("segment", sa.Column("sheet_serving", sa.String(length=8), nullable=True))
    op.add_column("segment", sa.Column("called_by", sa.String(length=8), nullable=True))
    # The opponent's starting six by shirt number (zones I-VI at the set's first serve).
    op.add_column(
        "period",
        sa.Column("start_lineup_them", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # "No scoresheet for this video" (the setup's scoresheet step is done).
    op.add_column(
        "video",
        sa.Column("no_scoresheet", sa.Boolean(), server_default="false", nullable=False),
    )
    op.execute(REVISION_FUNCTION.replace("{extra}", DERIVED_NOW))


def downgrade() -> None:
    op.execute(REVISION_FUNCTION.replace("{extra}", ""))
    op.drop_column("video", "no_scoresheet")
    op.drop_column("period", "start_lineup_them")
    for column in ("called_by", "sheet_serving", "sheet_won_by", "sheet_point"):
        op.drop_column("segment", column)
    op.execute("DROP TRIGGER IF EXISTS scoresheet_revision ON scoresheet")
    op.execute("DROP FUNCTION IF EXISTS wieplay_scoresheet_revision()")
    op.drop_index("ix_scoresheet_revision_scoresheet_id", table_name="scoresheet_revision")
    op.drop_table("scoresheet_revision")
    op.drop_table("scoresheet")
