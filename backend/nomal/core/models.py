"""Tables: the Wiedisync mirror, team lineage, sessions and the audit log (M0); videos, jobs,
workers, filming policy, consent and the adult-check cache (M1). Tags arrive with M2 (PLAN §6).
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
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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
    """Still mirrored by the sync, read by nothing else: Nomal handles matches only since
    16.10.2026. Kept until a cleanup migration after the M2-M4 ones."""

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


class TeamPolicy(Base):
    """What a team may film (PLAN §5.3, §6.10). No row = everything allowed."""

    __tablename__ = "team_policy"

    lineage_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("team_lineage.id", ondelete="CASCADE"), primary_key=True
    )
    matches_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    # Unused since 16.10.2026 (matches only); dropped with wd_training later.
    trainings_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str | None] = mapped_column(Text)
    set_by: Mapped[int | None] = mapped_column(Integer)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Consent(Base):
    """Unused since 16.10.2026 (the club films without per-player consent, PLAN §5.3). Kept
    until a migration after the M2-M4 ones drops the table."""

    __tablename__ = "consent"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    member_id: Mapped[int] = mapped_column(Integer, index=True)
    notice_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # accepted | withdrawn
    via: Mapped[str] = mapped_column(String(16))  # nomal | paper
    recorded_by: Mapped[int | None] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdultCheck(Base):
    """Cached answers of Wiedisync's adult check (PLAN §5.3). No birthdates, only counts."""

    __tablename__ = "adult_check"

    team_id: Mapped[int] = mapped_column(
        ForeignKey("wd_team.id", ondelete="CASCADE"), primary_key=True
    )
    on_date: Mapped[date] = mapped_column(Date, primary_key=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    failing_count: Mapped[int] = mapped_column(Integer)
    reasons: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict, server_default="{}")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Video(Base):
    __tablename__ = "video"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    team_id: Mapped[int] = mapped_column(ForeignKey("wd_team.id"), index=True)
    lineage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team_lineage.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # match (training: older rows only)
    # Plain ids, no foreign keys: the sync replaces the game mirror. No game = a match that
    # is not in Wiedisync (friendly, tournament, cup). training_id: unused since 16.10.2026.
    game_id: Mapped[int | None] = mapped_column(Integer, index=True)
    training_id: Mapped[int | None] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(255))
    sport: Mapped[str] = mapped_column(String(16))
    recorded_on: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(16), default="file")  # file | inbox
    # uploading | queued | processing | ready | failed | refused (filming rules) | deleted
    status: Mapped[str] = mapped_column(String(16), index=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    fps_num: Mapped[int | None] = mapped_column(Integer)
    fps_den: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    has_audio: Mapped[bool | None] = mapped_column(Boolean)
    audio_muted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    capture: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    meets_standard: Mapped[bool | None] = mapped_column(Boolean)
    standard_issues: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    thumbs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    retention_until: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(Integer)
    # Bucket objects of a deleted video are removed again after the job lease window
    # (late segment uploads); set once that sweep found nothing left.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    files: Mapped[list[VideoSourceFile]] = relationship(
        back_populates="video", order_by="VideoSourceFile.part_no", cascade="all, delete-orphan"
    )
    renditions: Mapped[list[VideoRendition]] = relationship(
        back_populates="video",
        order_by="VideoRendition.height.desc()",
        cascade="all, delete-orphan",
    )


class VideoSourceFile(Base):
    """One camera file of a video; long recordings arrive split (PLAN §6.4)."""

    __tablename__ = "video_source_file"
    __table_args__ = (UniqueConstraint("video_id", "part_no"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video.id", ondelete="CASCADE"), index=True
    )
    part_no: Mapped[int] = mapped_column(Integer)
    file_name: Mapped[str] = mapped_column(String(255))
    bytes: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str | None] = mapped_column(String(512))  # browser upload staging
    upload_id: Mapped[str | None] = mapped_column(String(1024))  # S3 multipart upload
    inbox_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID)  # NAS inbox source
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sha256: Mapped[str | None] = mapped_column(String(64))
    nas_path: Mapped[str | None] = mapped_column(String(1024))  # raw original after ingest
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    offset_ms: Mapped[int | None] = mapped_column(BigInteger)
    probe: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    video: Mapped[Video] = relationship(back_populates="files")


class VideoRendition(Base):
    """One HLS rendition; its media playlist is stored here and served by the API."""

    __tablename__ = "video_rendition"

    video_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("video.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(16), primary_key=True)  # 1080p | 540p
    codecs: Mapped[str] = mapped_column(String(64))  # RFC 6381, e.g. avc1.640028,mp4a.40.2
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    fps: Mapped[float] = mapped_column(Float)
    bandwidth: Mapped[int] = mapped_column(Integer)  # peak bits/s for the master playlist
    segment_seconds: Mapped[int] = mapped_column(Integer)
    segment_count: Mapped[int] = mapped_column(Integer)
    playlist: Mapped[str] = mapped_column(Text)
    bytes: Mapped[int] = mapped_column(BigInteger)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    video: Mapped[Video] = relationship(back_populates="renditions")


class InboxFile(Base):
    """A stable file in the NAS inbox share, reported by the media worker (PLAN §7.2)."""

    __tablename__ = "inbox_file"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    path: Mapped[str] = mapped_column(String(1024), unique=True)  # relative to the inbox root
    size: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    probe: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    recorded_at_guess: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="new")  # new | assigned | gone
    video_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("video.id", ondelete="SET NULL"))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Worker(Base):
    """A machine that runs jobs; authenticates with its own token (PLAN §8.1)."""

    __tablename__ = "worker"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    caps: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    version: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(Base):
    """The queue: workers claim with SELECT … FOR UPDATE SKIP LOCKED (PLAN §8.1)."""

    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    video_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("video.id", ondelete="CASCADE"), index=True
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    required_caps: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    depends_on: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID), default=list)
    # queued | running | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("worker.id"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Claims scan only queued jobs, best first (PLAN §8.1).
Index(
    "ix_job_claim",
    Job.priority.desc(),
    Job.created_at,
    postgresql_where=Job.status == "queued",
)
