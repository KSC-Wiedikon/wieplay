"""score logs: what the club scoreboard's log says about each rally (PLAN §10.10)

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-18 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Written by the scoring next to the model's guesses, never in place of a coach's: who won
    # the rally by the point pressed on the scoreboard after it (us | them | none: no point),
    # the set's score after it as the board had it, and when the point was pressed (video ms).
    # The log itself is an analysis_track of kind "score_log" (no new table).
    op.add_column("segment", sa.Column("log_won_by", sa.String(length=8), nullable=True))
    op.add_column("segment", sa.Column("log_score_us", sa.Integer(), nullable=True))
    op.add_column("segment", sa.Column("log_score_them", sa.Integer(), nullable=True))
    op.add_column("segment", sa.Column("log_at_ms", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("segment", "log_at_ms")
    op.drop_column("segment", "log_score_them")
    op.drop_column("segment", "log_score_us")
    op.drop_column("segment", "log_won_by")
