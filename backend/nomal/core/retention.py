"""Nightly housekeeping: retention rules from PLAN §5.5.

| Data | Kept |
|---|---|
| Videos (renditions, thumbnails, NAS originals) | Recording season + 1 (video.retention_until) |
| Audit log | 12 months |
| Sessions, login nonces | 30 days |
| Inbox entries that left the share, adult-check answers | 30 days |

Expired videos go the same way as a coach's delete: soft-deleted, NAS originals purged by
the worker, bucket objects swept by the API (videos.sweep_deleted).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Delete, delete, select
from sqlalchemy.orm import Session

from nomal.core import pipeline
from nomal.core.models import (
    AdultCheck,
    AuditLog,
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

    def any(self) -> bool:
        return any(vars(self).values())


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
        pipeline.mark_deleted(db, video, actor=None)
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
    )
    if done.any():
        db.add(
            AuditLog(action="housekeeping", object_type=None, object_id=None, details=vars(done))
        )
    db.commit()
    return done
