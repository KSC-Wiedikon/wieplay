"""Nightly housekeeping: retention rules from PLAN §5.5.

| Data | Kept |
|---|---|
| Videos (renditions, thumbnails, NAS originals) | Recording season + 1 (video.retention_until) |
| Audit log | 12 months |
| Sessions, login nonces | 30 days |
| Inbox entries that left the share, adult-check answers | 30 days |
| A guest club's login (email, password hash) | 30 days after it was disabled (PLAN §4.5) |
| Invite links | 30 days after they expired |

Expired videos go the same way as a coach's delete: soft-deleted, NAS originals and analysis
signals purged by the worker, bucket objects swept by the API (videos.sweep_deleted), inbox
entries released.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Delete, delete, select
from sqlalchemy.orm import Session

from wieplay.core import pipeline
from wieplay.core.models import (
    AdultCheck,
    AuditLog,
    GuestAccount,
    GuestInvite,
    InboxFile,
    LoginNonce,
    UserSession,
    Video,
)

AUDIT_KEEP = timedelta(days=365)
SHORT_KEEP = timedelta(days=30)


@dataclass(frozen=True)
class Housekeeping:
    videos: int
    sessions: int
    nonces: int
    audit: int
    inbox: int
    adult_checks: int
    guest_accounts: int = 0
    guest_invites: int = 0
    video_ids: tuple[uuid.UUID, ...] = ()  # the expired videos, so the API forgets them

    def counts(self) -> dict[str, int]:
        return {k: v for k, v in vars(self).items() if k != "video_ids"}

    def any(self) -> bool:
        return any(self.counts().values())


def run_housekeeping(db: Session, now: datetime | None = None) -> Housekeeping:
    now = now or datetime.now(UTC)
    today: date = now.date()

    expired = db.scalars(
        select(Video)
        .where(
            Video.status.in_(("ready", "failed", "refused")),
            Video.retention_until.is_not(None),
            Video.retention_until < today,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for video in expired:
        ingest_started = pipeline.ingest_started(db, video)
        pipeline.mark_deleted(db, video, actor=None)
        for f in video.files:
            inbox = db.get(InboxFile, f.inbox_file_id) if f.inbox_file_id else None
            if inbox is not None and inbox.status == "assigned":
                # As in a coach's delete: ingest moved the file into the raw folder.
                inbox.status = "gone" if ingest_started else "new"
                inbox.video_id = None
        db.add(
            AuditLog(
                action="retention_deleted",
                object_type="video",
                object_id=str(video.id),
                details={"title": video.title, "retention_until": str(video.retention_until)},
            )
        )

    def purge(statement: Delete) -> int:
        return int(getattr(db.execute(statement), "rowcount", 0) or 0)

    done = Housekeeping(
        videos=len(expired),
        sessions=purge(delete(UserSession).where(UserSession.expires_at < now - SHORT_KEEP)),
        nonces=purge(delete(LoginNonce).where(LoginNonce.used_at < now - SHORT_KEEP)),
        audit=purge(delete(AuditLog).where(AuditLog.at < now - AUDIT_KEEP)),
        inbox=purge(
            delete(InboxFile).where(
                InboxFile.status == "gone", InboxFile.seen_at < now - SHORT_KEEP
            )
        ),
        adult_checks=purge(delete(AdultCheck).where(AdultCheck.on_date < today - SHORT_KEEP)),
        # Taken off every team (or removed on request): the email goes 30 days later.
        guest_accounts=purge(
            delete(GuestAccount).where(GuestAccount.disabled_at < now - SHORT_KEEP)
        ),
        guest_invites=purge(delete(GuestInvite).where(GuestInvite.expires_at < now - SHORT_KEEP)),
        video_ids=tuple(video.id for video in expired),
    )
    if done.any():
        db.add(
            AuditLog(action="housekeeping", object_type=None, object_id=None, details=done.counts())
        )
    db.commit()
    return done
