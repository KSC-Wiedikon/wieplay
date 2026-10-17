"""guest clubs: other clubs' teams with their own logins (PLAN §4.5)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-17 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Guest teams and guest people get ids from here up; Wiedisync's ids are far below.
GUEST_ID_BASE = 1_000_000_000


def upgrade() -> None:
    # A guest lineage: another club's team. NULL club = a KSCW team from Wiedisync.
    op.add_column("team_lineage", sa.Column("guest_club", sa.String(length=255), nullable=True))
    # The club agreed that its reviewed games train Nomal's shared models.
    op.add_column(
        "team_lineage",
        sa.Column("guest_training_ok", sa.Boolean(), server_default="false", nullable=False),
    )
    # At most this many videos (not deleted) per team row and season.
    op.add_column("team_lineage", sa.Column("guest_video_limit", sa.Integer(), nullable=True))
    # The guest coach's "everyone we film is 18 or older" (replaces Wiedisync's adult check).
    op.add_column(
        "team_lineage",
        sa.Column("guest_adults_declared_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "team_lineage", sa.Column("guest_adults_declared_by", sa.Integer(), nullable=True)
    )
    # Lets the Wiedisync sync leave guest rows alone without a join.
    op.add_column(
        "wd_team", sa.Column("guest", sa.Boolean(), server_default="false", nullable=False)
    )
    op.execute(f"CREATE SEQUENCE guest_id_seq START {GUEST_ID_BASE} MINVALUE {GUEST_ID_BASE}")

    # A guest's login: email and password, one per roster person (wd_member id).
    op.create_table(
        "guest_account",
        sa.Column("member_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_logins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("member_id"),
        sa.UniqueConstraint("email", name="uq_guest_account_email"),
    )
    # An invite link (shown once): sets up an account, or resets its password.
    op.create_table(
        "guest_invite",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["wd_team.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(op.f("ix_guest_invite_member_id"), "guest_invite", ["member_id"])
    op.create_index(op.f("ix_guest_invite_team_id"), "guest_invite", ["team_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_guest_invite_team_id"), table_name="guest_invite")
    op.drop_index(op.f("ix_guest_invite_member_id"), table_name="guest_invite")
    op.drop_table("guest_invite")
    op.drop_table("guest_account")
    op.execute("DROP SEQUENCE guest_id_seq")
    op.drop_column("wd_team", "guest")
    op.drop_column("team_lineage", "guest_adults_declared_by")
    op.drop_column("team_lineage", "guest_adults_declared_at")
    op.drop_column("team_lineage", "guest_video_limit")
    op.drop_column("team_lineage", "guest_training_ok")
    op.drop_column("team_lineage", "guest_club")
