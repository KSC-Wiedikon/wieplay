"""the video's setup: the team names a coach set, "no scoreboard in this video"

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-17 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The names the app writes instead of us and them, when a coach set them for this video
    # (else they come from the team, the game or the title: nomal/core/names.py).
    op.add_column("video", sa.Column("name_us", sa.String(length=40), nullable=True))
    op.add_column("video", sa.Column("name_them", sa.String(length=40), nullable=True))
    # A coach said there is no scoreboard in the picture: the setup step is done.
    op.add_column(
        "video",
        sa.Column("no_scoreboard", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("video", "no_scoreboard")
    op.drop_column("video", "name_them")
    op.drop_column("video", "name_us")
