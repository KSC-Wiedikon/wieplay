"""Tables for M0: the Wiedisync mirror, team lineage, sessions and the audit log.

Video, tag and job tables arrive with M1/M2 (PLAN §6).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nomal.core.db import Base


class TeamLineage(Base):
    """A team across seasons. Wiedisync clones every team row on 1 June (PLAN §6.3)."""

    __tablename__ = "team_lineage"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    sport: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(255))
    team_key: Mapped[str | None] = mapped_column(String(255))  # teams.team_id in Wiedisync
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    enabled_by: Mapped[int | None] = mapped_column(Integer)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    teams: Mapped[list[WdTeam]] = relationship(back_populates="lineage")


class WdTeam(Base):
    """Team metadata for every listed Wiedisync team (no personal data)."""

    __tablename__ = "wd_team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    lineage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_lineage.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    sport: Mapped[str] = mapped_column(String(16))
    season: Mapped[str] = mapped_column(String(16))
    league: Mapped[str | None] = mapped_column(String(64))
    team_key: Mapped[str | None] = mapped_column(String(255))
    listed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lineage: Mapped[TeamLineage] = relationship(back_populates="teams")


class WdMember(Base):
    """Members of enabled teams only; purged when no enabled team references them."""

    __tablename__ = "wd_member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    uuid: Mapped[uuid.UUID] = mapped_column(UUID, unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    number: Mapped[int | None] = mapped_column(Integer)
    positions: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WdTeamMember(Base):
    __tablename__ = "wd_team_member"

    team_id: Mapped[int] = mapped_column(
        ForeignKey("wd_team.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("wd_member.id", ondelete="CASCADE"), primary_key=True
    )
    # player | coach | team_responsible | captain
    rel: Mapped[str] = mapped_column(String(32), primary_key=True)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class WdGame(Base):
    __tablename__ = "wd_game"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("wd_team.id"), index=True)
    date: Mapped[date | None] = mapped_column(Date)
    time: Mapped[time | None] = mapped_column(Time)
    home_name: Mapped[str | None] = mapped_column(String(255))
    away_name: Mapped[str | None] = mapped_column(String(255))
    our_side: Mapped[str | None] = mapped_column(String(8))  # home | away
    league: Mapped[str | None] = mapped_column(String(64))
    hall_name: Mapped[str | None] = mapped_column(String(255))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    sets: Mapped[list[list[int]] | None] = mapped_column(JSONB)  # official set scores
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WdTraining(Base):
    __tablename__ = "wd_training"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("wd_team.id"), index=True)
    date: Mapped[date | None] = mapped_column(Date)
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    hall_name: Mapped[str | None] = mapped_column(String(255))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncState(Base):
    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    counts: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict, server_default="{}")


class UserSession(Base):
    __tablename__ = "user_session"

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie
    member_id: Mapped[int] = mapped_column(Integer, index=True)
    claims: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    user_agent: Mapped[str | None] = mapped_column(String(255))


class LoginNonce(Base):
    """Bridge token IDs already used: every login token works once."""

    __tablename__ = "login_nonce"

    jti: Mapped[str] = mapped_column(String(128), primary_key=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_member_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str | None] = mapped_column(String(64))
    object_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
