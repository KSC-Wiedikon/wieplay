"""serve rule: which version of the serve rule made a serve track (PLAN §10.3)

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-18 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The serve rule's version (wieplay/ml/serve.py RULE) a serve track was computed with;
    # null for the tracks made before it was kept (rule 1). A training recomputes the serve
    # tracks an older rule made.
    op.add_column("analysis_track", sa.Column("rule", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("analysis_track", "rule")
