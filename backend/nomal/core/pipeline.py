"""A video's way from upload to "ready" (PLAN §7.3).

    imported link ──import──┐
    uploaded files ──ingest──▶ raw originals on the NAS ──transcode 1080p, 540p──▶ HLS
                                                         └─thumbs──▶ sprites + poster

Each arrow is a job (core/jobs.py). The API validates what a worker reports and writes the
rows here, in the worker's request transaction.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nomal.core import imports, jobs
from nomal.core.media import (
    CODECS,
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
from nomal.core.models import Job, Video, VideoExport, VideoRendition, WdTeam

INGEST_CAPS = ("ingest", "nas")
IMPORT_CAPS = ("import", "nas")
TRANSCODE_CAPS = ("transcode", "nas")
THUMBS_CAPS = ("thumbs", "nas")
PURGE_CAPS = ("nas",)
EXPORT_CAPS = ("export", "nas")
EXPORT_DAYS = 30


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


class ImportResult(BaseModel):
    file_name: str = Field(pattern=r"^(youtube|twitch)-[A-Za-z0-9_-]{5,15}\.mp4$")
    bytes: int = Field(gt=0)
    # A few fields of yt-dlp's metadata: channel, title, upload date (PLAN §7.2.2).
    info: dict[str, Any] = Field(default_factory=dict)


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


# What ffmpeg writes for a VOD fMP4 rendition; any other line is refused (PLAN §7.6: the
# playlist the API serves may only name this rendition's own segments).
class ExportResult(BaseModel):
    bytes: int = Field(gt=0)
    duration_ms: int = Field(ge=0)


PLAYLIST_TAGS = re.compile(
    r"#EXTM3U|#EXT-X-VERSION:[0-9]{1,2}|#EXT-X-TARGETDURATION:[0-9]{1,3}"
    r"|#EXT-X-MEDIA-SEQUENCE:0|#EXT-X-PLAYLIST-TYPE:VOD|#EXT-X-INDEPENDENT-SEGMENTS"
    r'|#EXT-X-MAP:URI="init\.mp4"|#EXTINF:[0-9]{1,4}(\.[0-9]{1,6})?,|#EXT-X-ENDLIST'
)
NAS_PATH = re.compile(r"[A-Za-z0-9._/-]+")


def season_of(day: date) -> str:
    start = day.year if day.month >= 6 else day.year - 1
    return f"{start}/{str(start + 1)[2:]}"


def retention_until(day: date) -> date:
    """Recording season + 1 (PLAN §5.5): a 2026/27 video is kept until 31.05.2028."""
    start = day.year if day.month >= 6 else day.year - 1
    return date(start + 2, 5, 31)


def team_folder(team: WdTeam) -> str:
    """A team's folder name on the NAS, e.g. vb-H3, bb-Herren-1: in raw/ and in the inbox."""
    sport = "vb" if team.sport == "volleyball" else "bb"
    team_part = re.sub(r"[^A-Za-z0-9-]+", "-", team.name).strip("-") or str(team.id)
    return f"{sport}-{team_part}"


def raw_subdir(video: Video, team: WdTeam) -> str:
    """Where the originals go on the NAS, readable for people:
    2026-27/vb-H3/2026-10-11_match_ab12cd34"""
    return (
        f"{season_of(video.recorded_on).replace('/', '-')}/{team_folder(team)}/"
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


def start_import(
    db: Session,
    video: Video,
    team: WdTeam,
    source: imports.ImportSource,
    allowed: set[str],
    *,
    not_before: datetime | None = None,
) -> Job:
    """Fetch a YouTube or Twitch video into the video's raw folder; ingest follows. A batch
    spreads its imports with `not_before`; more attempts than usual, because YouTube's bot
    check makes an import wait an hour and try again."""
    video.status = "importing"
    return jobs.enqueue(
        db,
        "import",
        caps=IMPORT_CAPS,
        video_id=video.id,
        priority=10,
        max_attempts=8,
        not_before=not_before,
        params={
            "video_id": str(video.id),
            "url": source.url,
            "platform": source.platform,
            "external_id": source.external_id,
            "raw_subdir": raw_subdir(video, team),
            "file_name": source.file_name,
            "channels": sorted(allowed),
            "max_seconds": imports.MAX_SECONDS,
            "max_bytes": imports.MAX_BYTES,
        },
    )


def _short(value: Any, limit: int = 200) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _on_import(db: Session, video: Video, job: Job, result: ImportResult) -> None:
    platform = job.params["platform"]
    if result.file_name != job.params["file_name"] or len(video.files) != 1:
        raise ValueError("import result does not match the job")
    # The worker checked the channel before downloading; the list may have changed since.
    if not imports.channel_allowed(platform, result.info, imports.allowed_refs(db, platform)):
        video.status = "refused"
        video.error = "The video's channel is not on the import allow-list"
        purge_raw(db, video)
        return
    [source] = video.files
    source.bytes = result.bytes
    source.uploaded_at = datetime.now(UTC)
    refs = sorted(imports.info_refs(platform, result.info))
    video.capture = {
        **(video.capture or {}),
        "import": {
            "platform": platform,
            "id": job.params["external_id"],
            "url": job.params["url"],
            "channel": _short(result.info.get("channel") or result.info.get("uploader")),
            "channel_ref": refs[0] if refs else None,
            "title": _short(result.info.get("title")),
            "published": _short(result.info.get("upload_date"), 8),
        },
    }
    team = db.get(WdTeam, video.team_id)
    assert team is not None
    start_ingest(db, video, team)


def _on_ingest(db: Session, video: Video, job: Job, result: IngestResult) -> None:
    by_id = {f.file_id: f for f in result.files}
    if set(by_id) != {f.id for f in video.files}:
        raise ValueError("ingest result does not cover exactly the video's files")
    folder = f"/{job.params['raw_subdir']}/"
    for got in result.files:
        # These paths become ffmpeg inputs and purge targets: only this video's folder.
        if (
            not NAS_PATH.fullmatch(got.nas_path)
            or folder not in got.nas_path
            or "/../" in got.nas_path
            or "/" in got.nas_path.split(folder, 1)[1]
        ):
            raise ValueError("nas_path outside the video's raw folder")
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
    # Camera metadata from the probe; an import keeps where it came from.
    origin = (video.capture or {}).get("import")
    video.capture = {**result.capture, **({"import": origin} if origin else {})}
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
    """An allow-list of lines: known tags, and this rendition's own segment names."""
    lines = [line.rstrip("\r") for line in playlist.split("\n")]
    if not lines or lines[0] != "#EXTM3U":
        raise ValueError("playlist must start with #EXTM3U")
    for line in lines:
        if not line:
            continue
        ok = PLAYLIST_TAGS.fullmatch(line) if line.startswith("#") else SEGMENT_NAME.fullmatch(line)
        if not ok:
            raise ValueError(f"unexpected playlist line: {line[:40]}")


def _on_transcode(db: Session, video: Video, job: Job, result: TranscodeResult) -> None:
    if result.name != job.params["rendition"]["name"]:
        raise ValueError("rendition name does not match the job")
    if not CODECS.fullmatch(result.codecs):
        raise ValueError("codecs must be an RFC 6381 list")
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
    """Mark the video ready as soon as it can be watched: thumbnails and one rendition, with
    nothing else left to do but further renditions. The small one comes first, so a backlog
    of matches becomes playable one by one instead of all at the end; the master playlist
    lists each rendition as it arrives."""
    active = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.video_id == video.id, Job.status.in_(jobs.ACTIVE), Job.kind != "transcode")
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
    from nomal.core import analysis, calibration

    if job.kind in analysis.KINDS:
        analysis.complete(db, video, job, payload)
        calibration.after_analyse(db, video, job)  # the ball's path and the court follow
        return
    if job.kind in calibration.KINDS:
        calibration.complete(db, video, job, payload)
        return
    if job.kind == "purge":
        jobs.succeed(job, payload)
        return
    if job.kind == "export":
        result = ExportResult.model_validate(payload)
        export = db.get(VideoExport, uuid.UUID(job.params["export_id"]), with_for_update=True)
        if export is None:
            raise ValueError("export row is gone")
        export.status = "ready"
        export.bytes = result.bytes
        export.object_key = job.params["key"]
        export.expires_at = datetime.now(UTC) + timedelta(days=EXPORT_DAYS)
        jobs.succeed(job, payload)
        return
    if video is None:
        raise ValueError("job has no video")
    if job.kind == "import":
        _on_import(db, video, job, ImportResult.model_validate(payload))
    elif job.kind == "ingest":
        _on_ingest(db, video, job, IngestResult.model_validate(payload))
    elif job.kind == "transcode":
        _on_transcode(db, video, job, TranscodeResult.model_validate(payload))
    elif job.kind == "thumbs":
        _on_thumbs(video, ThumbsResult.model_validate(payload))
    else:
        raise ValueError(f"unknown job kind {job.kind}")
    jobs.succeed(job, {k: v for k, v in payload.items() if k != "playlist"})


def on_failed(db: Session, video: Video | None, job: Job) -> None:
    """A job failed for good: the video failed, its other jobs stop. A failed export only
    fails that export. Failed analysis leaves the video as it is: it plays without it."""
    from nomal.core import analysis, calibration

    if job.kind in analysis.KINDS or job.kind in calibration.KINDS:
        return
    if job.kind == "export":
        export = db.get(VideoExport, uuid.UUID(job.params["export_id"]))
        if export is not None:
            export.status, export.error = "failed", (job.error or "failed")[:2000]
        return
    if video is None or video.status in {"deleted", "refused"}:
        return
    if video.status == "ready" and job.kind == "transcode":
        return  # a further rendition failed: the video still plays in the ones it has
    video.status = "failed"
    video.error = f"{job.kind}: {job.error or 'failed'}"[:2000]
    jobs.cancel_active(db, video.id, f"{job.kind} failed")


def may_sign(job: Job, op: str, key: str) -> bool:
    """Object keys a worker may touch for this job (PLAN §8.1: workers hold no credentials)."""
    from nomal.core import calibration

    if job.kind in calibration.KINDS:
        return calibration.may_sign(job, op, key)
    video = job.params.get("video_id")
    if job.kind == "ingest":
        staged = {f["object_key"] for f in job.params.get("files", []) if f.get("object_key")}
        return op in {"get", "delete"} and key in staged
    if job.kind == "transcode" and op == "put":
        prefix = f"media/{video}/{job.params['rendition']['name']}/"
        return key.startswith(prefix) and bool(SEGMENT_NAME.fullmatch(key[len(prefix) :]))
    if job.kind == "export" and op == "put":
        return key == job.params.get("key")
    if job.kind == "thumbs" and op == "put":
        prefix = f"media/{video}/thumbs/"
        return key.startswith(prefix) and bool(THUMB_NAME.fullmatch(key[len(prefix) :]))
    return False


def _ingest_job(db: Session, video: Video) -> Job | None:
    """The first job that writes into the video's raw folder: its import, else its ingest."""
    return db.scalars(
        select(Job)
        .where(Job.video_id == video.id, Job.kind.in_(("import", "ingest")))
        .order_by(Job.created_at)
    ).first()


def raw_folder(db: Session, video: Video) -> str | None:
    """The video's folder under the raw root, as its ingest wrote it."""
    job = _ingest_job(db, video)
    return str(job.params["raw_subdir"]) if job is not None else None


def ingest_started(db: Session, video: Video) -> bool:
    """A worker picked up the ingest: files may sit in the video's raw folder by now."""
    job = _ingest_job(db, video)
    return job is not None and job.attempts > 0


def purge_raw(db: Session, video: Video) -> Job | None:
    """Queue removal of the video's raw folder on the NAS, including half-ingested files."""
    job = _ingest_job(db, video)
    if job is None or job.attempts == 0:
        return None  # nothing was ever written there
    return jobs.enqueue(
        db,
        "purge",
        caps=PURGE_CAPS,
        video_id=video.id,
        priority=1,
        params={
            "video_id": str(video.id),
            "raw_subdir": job.params["raw_subdir"],
            "nas_paths": [f.nas_path for f in video.files if f.nas_path],
            # Signals of the automatic analysis (PLAN §5.5: they go with the video).
            "analysis_subdir": f"videos/{job.params['raw_subdir']}",
        },
    )


def mark_deleted(db: Session, video: Video, actor: int | None) -> list[str]:
    """Soft-delete a video and queue removal of its NAS originals. Returns the bucket
    prefixes the caller deletes now; sweep_deleted deletes them again later."""
    video.status = "deleted"
    video.deleted_at = datetime.now(UTC)
    video.deleted_by = actor
    jobs.cancel_active(db, video.id, "video deleted")
    purge_raw(db, video)
    return [f"media/{video.id}/", f"incoming/{video.id}/"]


def refuse_after_ingest(db: Session, video: Video, reason: str) -> None:
    """The filming rules refused a video whose originals are already on the NAS."""
    video.status = "refused"
    video.error = reason[:2000]
    jobs.cancel_active(db, video.id, "refused")
    purge_raw(db, video)


def enqueue_export(
    db: Session, export: VideoExport, pieces: list[dict[str, Any]], watermark: str
) -> Job:
    """Queue the rendering of an export (PLAN §7.7). Each piece names its video's NAS
    originals; the worker cuts frame-accurately, burns in the watermark and joins."""
    key = f"exports/{export.id}.mp4"
    job = jobs.enqueue(
        db,
        "export",
        caps=EXPORT_CAPS,
        priority=3,
        params={
            "export_id": str(export.id),
            "key": key,
            "pieces": pieces,
            "watermark": watermark,
            "duration_ms": export.duration_ms,
        },
    )
    export.job_id = job.id
    return job
