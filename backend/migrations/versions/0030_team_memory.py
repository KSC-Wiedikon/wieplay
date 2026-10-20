"""team memory: what WiePlay knows about a team's players and rotations (PLAN §10.5b)

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-20 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Per season's team row: our shirt numbers, the member wearing each (the roster's, else a
    # coach's fix) and how often the team's games named it. Numbers and roster links only: no
    # pictures, no appearance, nothing about the opponents (PLAN §5.7).
    op.create_table(
        "team_player",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "team_id",
            sa.Integer(),
            sa.ForeignKey("wd_team.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "lineage_id",
            UUID(),
            sa.ForeignKey("team_lineage.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=True),
        sa.Column("libero", sa.Boolean(), nullable=False, server_default="false"),
        # Games (videos) that named the number: a sheet's six or substitution, a coach's
        # starting six, a card or a tag a coach named with it.
        sa.Column("games", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("answers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sets", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_on", sa.Date(), nullable=True),
        # Those games' video ids: a video's own facts never count as "earlier games" for it.
        sa.Column("videos", JSONB(), nullable=False, server_default="[]"),
        # roster (Wiedisync / the guest roster) | learned (only the games name it) | coach
        sa.Column("source", sa.String(length=16), nullable=False, server_default="roster"),
        # Several roster members wear the number and nobody said which one plays.
        sa.Column("ambiguous", sa.Boolean(), nullable=False, server_default="false"),
        # A coach's fix on the team page: the member is theirs (kept by every refresh), or the
        # number is none of ours ("hidden").
        sa.Column("fixed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("fixed_by", sa.Integer(), nullable=True),
        sa.Column("fixed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("team_id", "number"),
    )
    # Per season's team row: the usual rotations, the number prior, the models' scores for the
    # team, and the fingerprint of what they were learnt from (refreshed when it changes).
    op.create_table(
        "team_memory",
        sa.Column(
            "team_id",
            sa.Integer(),
            sa.ForeignKey("wd_team.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "lineage_id",
            UUID(),
            sa.ForeignKey("team_lineage.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("games", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("named_videos", JSONB(), nullable=False, server_default="[]"),
        sa.Column("rotations", JSONB(), nullable=False, server_default="{}"),
        sa.Column("models", JSONB(), nullable=False, server_default="{}"),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "refreshed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("team_memory")
    op.drop_table("team_player")
