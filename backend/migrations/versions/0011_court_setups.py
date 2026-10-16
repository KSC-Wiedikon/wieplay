"""the court set automatically and the camera's setups (PLAN §10.6)

Adds to `calibration`: `segments` (the camera's setups when it moved during the match, each
with its own landmarks and homography; NULL: one setup for the whole video, the row's own
fields), `checked` (whether a person set or checked it; false: set by the court job) and
`at_ms` (where in the video the coach set it up). Rows saved before are checked.

Revision ID: 0011
Revises: 0008 (after 0009/0010 of the actions and players branches: relink when they merge)
Create Date: 2026-10-17 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "calibration",
        sa.Column("segments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "calibration",
        sa.Column("checked", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column("calibration", sa.Column("at_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("calibration", "at_ms")
    op.drop_column("calibration", "checked")
    op.drop_column("calibration", "segments")
