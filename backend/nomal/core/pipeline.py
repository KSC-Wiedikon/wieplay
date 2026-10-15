"""A video's way from upload to "ready" (PLAN §7.3).

    uploaded files ──ingest──▶ raw originals on the NAS ──transcode 1080p, 540p──▶ HLS
                                                         └─thumbs──▶ sprites + poster

Each arrow is a job (core/jobs.py). The API validates what a worker reports and writes the
rows here, in the worker's request transaction.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nomal.core import jobs
from nomal.core.media import (
    SEGMENT_NAME,
    SEGMENT_SECONDS,
    THUMB_COLUMNS,
    THUMB_INTERVAL_SECONDS,
    THUMB_NAME,
    THUMB_ROWS,
    THUMB_WIDTH,
    renditions_for,
    standard_issues,
)
from nomal.core.models import Job, Video, VideoRendition, WdTeam

INGEST_CAPS = ("ingest", "nas")
TRANSCODE_CAPS = ("transcode", "nas")
THUMBS_CAPS = ("thumbs", "nas")
PURGE_CAPS = ("nas",)


class IngestedFile(BaseModel):
    file_id: uuid.UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    nas_path: str = Field(min_length=1, max_length=1024)
    duration_ms: int = Field(ge=0)
    probe: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    files: list[IngestedFile] = Field(min_length=1)
    duration_ms: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps_num: int = Field(gt=0)
    fps_den: int = Field(gt=0)
    has_audio: bool
    capture: dict[str, Any] = Field(default_factory=dict)


class TranscodeResult(BaseModel):
    name: str
    codecs: str = Field(max_length=64)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    bandwidth: int = Field(gt=0)
    segment_seconds: int = Field(gt=0)
    segment_count: int = Field(gt=0)
    bytes: int = Field(ge=0)
    playlist: str = Field(min_length=1, max_length=2_000_000)


class ThumbsResult(BaseModel):
    interval_s: int = Field(gt=0)
    cols: int = Field(gt=0)
    rows: int = Field(gt=0)
    tile_w: int = Field(gt=0)
    tile_h: int = Field(gt=0)
    count: int = Field(ge=0)
    sheets: int = Field(ge=0)
    poster: bool


PLAYLIST_URI = re.compile(r'^(?!#)(.+)$|URI="([^"]+)"', re.MULTILINE)


def season_of(day: date) -> str:
    start = day.year if day.month >= 6 else day.year - 1
    return f"{start}/{str(start + 1)[2:]}"


def retention_until(day: date) -> date:
    """Recording season + 1 (PLAN §5.5): a 2026/27 video is kept until 31.05.2028."""
    start = day.year if day.month >= 6 else day.year - 1
    return date(start + 2, 5, 31)


def raw_subdir(video: Video, team: WdTeam) -> str:
    """Where the originals go on the NAS, readable for people:
    2026-27/vb-H3/2026-10-11_match_ab12cd34"""
    sport = "vb" if video.sport == "volleyball" else "bb"
    team_part = re.sub(r"[^A-Za-z0-9-]+", "-", team.name).strip("-") or str(team.id)
    return (
        f"{season_of(video.recorded_on).replace('/', '-')}/{sport}-{team_part}/"
        f"{video.recorded_on.isoformat()}_{video.kind}_{video.id.hex[:8]}"
    )


def start_ingest(db: Session, video: Video, team: WdTeam) -> Job:
    video.status = "queued"
    return jobs.enqueue(
        db,
        "ingest",
        caps=INGEST_CAPS,
        video_id=video.id,
        priority=10,
        params={
            "video_id": str(video.id),
            "raw_subdir": raw_subdir(video, team),
            "files": [
                {
                    "id": str(f.id),
                    "part_no": f.part_no,
                    "name": f.file_name,
                    "bytes": f.bytes,
                    "object_key": f.object_key,
                    "inbox_path": f.probe.get("inbox_path") if f.probe else None,
                }
                for f in video.files
            ],
        },
    )


def _on_ingest(db: Session, video: Video, result: IngestResult) -> None:
    by_id = {f.file_id: f for f in result.files}
    if set(by_id) != {f.id for f in video.files}:
        raise ValueError("ingest result does not cover exactly the video's files")
    offset = 0
    for f in video.files:
        got = by_id[f.id]
        f.sha256, f.nas_path, f.duration_ms = got.sha256, got.nas_path, got.duration_ms
        f.probe = {**(f.probe or {}), **got.probe}
        f.offset_ms = offset
        offset += got.duration_ms

    fps = result.fps_num / result.fps_den
    video.duration_ms = result.duration_ms
    video.width, video.height = result.width, result.height
    video.fps_num, video.fps_den = result.fps_num, result.fps_den
    video.has_audio = result.has_audio
    video.capture = result.capture
    video.standard_issues = standard_issues(result.width, result.height, fps)
    video.meets_standard = not video.standard_issues
    video.status = "processing"

    inputs = [f.nas_path for f in video.files]
    common = {"video_id": str(video.id), "inputs": inputs, "duration_ms": result.duration_ms}
    for priority, spec in enumerate(reversed(renditions_for(result.height))):
        # The small rendition first: it is ready sooner.
        jobs.enqueue(
            db,
            "transcode",
            caps=TRANSCODE_CAPS,
            video_id=video.id,
            priority=5 - priority,
            params={
                **common,
                "rendition": spec.as_params(),
                "fps": spec.fps_for(fps),
                "has_audio": result.has_audio,
                "source": {"width": result.width, "height": result.height},
                "segment_seconds": SEGMENT_SECONDS,
                "prefix": f"media/{video.id}/{spec.name}/",
            },
        )
    jobs.enqueue(
        db,
        "thumbs",
        caps=THUMBS_CAPS,
        video_id=video.id,
        priority=6,
        params={
            **common,
            "interval_s": THUMB_INTERVAL_SECONDS,
            "cols": THUMB_COLUMNS,
            "rows": THUMB_ROWS,
            "tile_w": THUMB_WIDTH,
            "source": {"width": result.width, "height": result.height},
            "prefix": f"media/{video.id}/thumbs/",
        },
    )


def _check_playlist(playlist: str) -> None:
    """The stored playlist may only name this rendition's own segment files."""
    if not playlist.startswith("#EXTM3U"):
        raise ValueError("playlist must start with #EXTM3U")
    for match in PLAYLIST_URI.finditer(playlist):
        uri = (match.group(1) or match.group(2) or "").strip()
        if uri and not SEGMENT_NAME.match(uri):
            raise ValueError(f"unexpected URI in playlist: {uri[:40]}")


def _on_transcode(db: Session, video: Video, job: Job, result: TranscodeResult) -> None:
    if result.name != job.params["rendition"]["name"]:
        raise ValueError("rendition name does not match the job")
    _check_playlist(result.playlist)
    row = db.get(VideoRendition, (video.id, result.name)) or VideoRendition(
        video_id=video.id, name=result.name
    )
    for key, value in result.model_dump().items():
        setattr(row, key, value)
    row.job_id = job.id
    db.add(row)


def _on_thumbs(video: Video, result: ThumbsResult) -> None:
    video.thumbs = result.model_dump()


def finish_if_done(db: Session, video: Video, now: datetime | None = None) -> bool:
    """Mark the video ready once nothing is left to do and it has something to play."""
    active = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.video_id == video.id, Job.status.in_(jobs.ACTIVE))
    )
    if video.status != "processing" or active:
        return False
    db.flush()
    db.refresh(video, ["renditions"])
    if not video.renditions or not video.thumbs:
        return False
    video.status = "ready"
    video.retention_until = retention_until(video.recorded_on)
    video.updated_at = now or datetime.now(UTC)
    return True


def complete(db: Session, video: Video | None, job: Job, payload: dict[str, Any]) -> None:
    """Validate a worker's result and write it. Raises ValueError on a bad result."""
    if job.kind == "purge":
        jobs.succeed(job, payload)
        return
    if video is None:
        raise ValueError("job has no video")
    if job.kind == "ingest":
        _on_ingest(db, video, IngestResult.model_validate(payload))
    elif job.kind == "transcode":
        _on_transcode(db, video, job, TranscodeResult.model_validate(payload))
    elif job.kind == "thumbs":
        _on_thumbs(video, ThumbsResult.model_validate(payload))
    else:
        raise ValueError(f"unknown job kind {job.kind}")
    jobs.succeed(job, {k: v for k, v in payload.items() if k != "playlist"})


def on_failed(db: Session, video: Video | None, job: Job) -> None:
    """A job failed for good: the video failed, its other jobs stop."""
    if video is None or video.status == "deleted":
        return
    video.status = "failed"
    video.error = f"{job.kind}: {job.error or 'failed'}"[:2000]
    jobs.cancel_active(db, video.id, f"{job.kind} failed")


def may_sign(job: Job, op: str, key: str) -> bool:
    """Object keys a worker may touch for this job (PLAN §8.1: workers hold no credentials)."""
    video = job.params.get("video_id")
    if job.kind == "ingest":
        staged = {f["object_key"] for f in job.params.get("files", []) if f.get("object_key")}
        return op in {"get", "delete"} and key in staged
    if job.kind == "transcode" and op == "put":
        prefix = f"media/{video}/{job.params['rendition']['name']}/"
        return key.startswith(prefix) and bool(SEGMENT_NAME.match(key[len(prefix) :]))
    if job.kind == "thumbs" and op == "put":
        prefix = f"media/{video}/thumbs/"
        return key.startswith(prefix) and bool(THUMB_NAME.match(key[len(prefix) :]))
    return False


def mark_deleted(db: Session, video: Video, actor: int) -> list[str]:
    """Soft-delete a video and queue removal of its NAS originals. Returns the bucket
    prefixes the caller deletes."""
    video.status = "deleted"
    video.deleted_at = datetime.now(UTC)
    video.deleted_by = actor
    jobs.cancel_active(db, video.id, "video deleted")
    nas_paths = [f.nas_path for f in video.files if f.nas_path]
    if nas_paths:
        jobs.enqueue(
            db,
            "purge",
            caps=PURGE_CAPS,
            video_id=video.id,
            priority=1,
            params={"video_id": str(video.id), "nas_paths": nas_paths},
        )
    return [f"media/{video.id}/", f"incoming/{video.id}/"]
