"""The ball's path and the court of a video (PLAN §10.4, §10.6).

GET    /api/videos/{id}/ball-track    the smoothed ball track with contacts and bounces
                                      (gzipped JSON, ETag; nomal/ml/trajectory.py has the
                                      format), for the "Trajectories" overlay and the court
                                      diagram; 404 until the trajectory job has run
GET    /api/videos/{id}/court         the court model, the video's calibration, what pre-fills
                                      the dialog (the court finder's, the team's last), the
                                      ball track's state
POST   /api/videos/{id}/court/solve   a calibration from clicked landmarks, not saved: the
                                      homography, the camera, the error, the lines to draw
PUT    /api/videos/{id}/court         save it (whoever may tag)
DELETE /api/videos/{id}/court         remove it (whoever may tag)

Whoever may watch the video may read; the court of a video is set by its taggers.
"""

from __future__ import annotations

import gzip
import uuid
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from nomal.api.deps import DbDep, StoreDep, ViewerDep, audit
from nomal.api.tagging import _readable
from nomal.core import calibration, court
from nomal.core.calibration import Calibration
from nomal.core.models import Video
from nomal.core.permissions import can_tag

router = APIRouter(prefix="/api", tags=["court"])

MAX_TRACK_BYTES = 16 * 1024 * 1024
Landmark = Literal[
    "near_left",
    "near_right",
    "far_right",
    "far_left",
    "net_left",
    "net_right",
    "attack_near_left",
    "attack_near_right",
    "attack_far_left",
    "attack_far_right",
]


class PointIn(BaseModel):
    landmark: Landmark
    # Normalised to the frame; a corner may lie a little outside the picture.
    u: float = Field(ge=-0.5, le=1.5)
    v: float = Field(ge=-0.5, le=1.5)


class PointsIn(BaseModel):
    points: list[PointIn] = Field(min_length=4, max_length=len(court.LANDMARKS))


class SaveIn(PointsIn):
    method: Literal["manual", "auto", "copied"] = "manual"
    copied_from: uuid.UUID | None = None


class PointOut(BaseModel):
    landmark: str
    u: float
    v: float


class CameraOut(BaseModel):
    """A pinhole camera (principal point at the centre, square pixels) recovered from the
    floor homography. Image coordinates for it: X = (u - 0.5) * aspect, Y = v - 0.5; the
    ray through (X, Y) is rays · (X, Y, 1) from center (metres, court coordinates)."""

    center: list[float]
    focal: float
    rays: list[list[float]]
    aspect: float


class SolveOut(BaseModel):
    image_to_court: list[list[float]]
    court_to_image: list[list[float]]
    camera: CameraOut | None
    # RMS and worst distance on the court between where a landmark was clicked and where it
    # belongs, in cm; none with only four points (four always fit exactly).
    error_cm: float | None
    max_error_cm: float | None
    residuals_cm: dict[str, float]
    lines: list[list[list[float]]]  # the court's lines in the image, as polylines (u, v)


class CalibrationOut(SolveOut):
    id: uuid.UUID
    video_id: uuid.UUID
    status: str  # confirmed | proposed
    method: str  # manual | auto | copied
    points: list[PointOut]
    confidence: float | None
    copied_from: uuid.UUID | None
    updated_at: datetime


class SuggestionOut(BaseModel):
    source: Literal["auto", "team"]
    points: list[PointOut]
    confidence: float | None
    video_id: uuid.UUID
    video_title: str | None
    recorded_on: date | None
    calibration_id: uuid.UUID


class BallTrackOut(BaseModel):
    available: bool
    fps: float | None = None
    bytes: int | None = None
    contacts: int | None = None
    bounces: int | None = None
    created_at: datetime | None = None
    # none | queued | running | done | failed: the newest trajectory job of the video
    job: str = "none"


class LandmarkOut(BaseModel):
    key: str
    x: float
    y: float
    corner: bool


class CourtOut(BaseModel):
    video_id: uuid.UUID
    can_edit: bool
    aspect: float
    calibration: CalibrationOut | None
    suggestions: list[SuggestionOut]
    ball_track: BallTrackOut
    landmarks: list[LandmarkOut]
    lines: list[list[float]]  # x1, y1, x2, y2 in metres


STATUS = {"queued": "queued", "leased": "running", "running": "running", "succeeded": "done"}


def _points(names: list[str], pts: list[list[float]]) -> list[PointOut]:
    return [PointOut(landmark=n, u=p[0], v=p[1]) for n, p in zip(names, pts, strict=True)]


def _camera(camera: court.Camera | None) -> CameraOut | None:
    return CameraOut.model_validate(camera.as_dict()) if camera is not None else None


def _solve_out(solution: court.Solution) -> SolveOut:
    rms, worst = solution.rms_m, solution.max_m
    return SolveOut(
        image_to_court=court.as_lists(solution.image_to_court),
        court_to_image=court.as_lists(solution.court_to_image),
        camera=_camera(solution.camera),
        error_cm=round(rms * 100, 1) if rms is not None else None,
        max_error_cm=round(worst * 100, 1) if worst is not None else None,
        residuals_cm={k: round(v * 100, 1) for k, v in solution.residuals.items()},
        lines=[
            [[round(u, 5), round(v, 5)] for u, v in line]
            for line in court.project_lines(solution.court_to_image)
        ],
    )


def _calibration_out(row: Calibration) -> CalibrationOut:
    to_court = court.from_lists(row.homography)
    to_image = court.invert(to_court)
    return CalibrationOut(
        image_to_court=court.as_lists(to_court),
        court_to_image=court.as_lists(to_image),
        camera=CameraOut.model_validate(row.camera) if row.camera else None,
        error_cm=row.reproj_error_cm,
        max_error_cm=row.max_error_cm,
        residuals_cm={},
        lines=[
            [[round(u, 5), round(v, 5)] for u, v in line] for line in court.project_lines(to_image)
        ],
        id=row.id,
        video_id=row.video_id,
        status=row.status,
        method=row.method,
        points=_points(row.landmarks, row.image_points),
        confidence=row.confidence,
        copied_from=row.copied_from,
        updated_at=row.updated_at,
    )


def _ball_track(db: DbDep, video: Video) -> BallTrackOut:
    job = calibration.active_job(db, video.id)
    artifact = calibration.latest_track(db, video.id)
    if job is not None:
        state = STATUS.get(job.status, job.status)
    elif artifact is not None:
        state = "done"
    else:
        state = "none"
    if artifact is None:
        return BallTrackOut(available=False, job=state)
    stats = artifact.stats or {}
    return BallTrackOut(
        available=True,
        fps=stats.get("fps"),
        bytes=artifact.bytes,
        contacts=stats.get("contacts"),
        bounces=stats.get("bounces"),
        created_at=artifact.created_at,
        job=state,
    )


@router.get("/videos/{video_id}/ball-track")
def ball_track(
    video_id: uuid.UUID, request: Request, viewer: ViewerDep, db: DbDep, store: StoreDep
) -> Response:
    """The newest ball track, as stored (gzip) for browsers that take it, which is all of
    them; revalidated by ETag (a new analysis brings a new track)."""
    video, _ = _readable(db, viewer, video_id)
    artifact = calibration.latest_track(db, video.id)
    if artifact is None:
        raise HTTPException(404, "No ball track for this video yet")
    etag = f'"{artifact.sha256[:32]}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Accept-Encoding"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    key = str((artifact.stats or {}).get("object_key", ""))
    data = store.read(key, MAX_TRACK_BYTES) if key else None
    if data is None:
        raise HTTPException(404, "The ball track is not available")
    db.rollback()
    if "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
    else:
        data = gzip.decompress(data)
    return Response(data, media_type="application/json", headers=headers)


@router.get("/videos/{video_id}/court", response_model=CourtOut)
def get_court(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> CourtOut:
    video, access = _readable(db, viewer, video_id)
    row = calibration.current(db, video.id)
    found = calibration.suggestions(db, video) if row is None else []
    return CourtOut(
        video_id=video.id,
        can_edit=can_tag(viewer, access.ref, access.leads) and video.status == "ready",
        aspect=calibration.aspect_of(video),
        calibration=_calibration_out(row) if row is not None else None,
        suggestions=[
            SuggestionOut(
                source=s.source,
                points=[PointOut(landmark=n, u=p[0], v=p[1]) for n, p in s.points.items()],
                confidence=s.confidence,
                video_id=s.video_id,
                video_title=s.video_title,
                recorded_on=s.recorded_on,
                calibration_id=s.calibration_id,
            )
            for s in found
        ],
        ball_track=_ball_track(db, video),
        landmarks=[
            LandmarkOut(key=k, x=x, y=y, corner=k in court.CORNERS)
            for k, (x, y) in court.LANDMARKS.items()
        ],
        lines=[list(line) for line in court.LINES],
    )


def _solve(points: list[PointIn], aspect: float) -> court.Solution:
    names = [p.landmark for p in points]
    if len(set(names)) != len(names):
        raise HTTPException(422, "Each landmark once")
    try:
        return court.solve({p.landmark: (p.u, p.v) for p in points}, aspect)
    except court.CourtError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/videos/{video_id}/court/solve", response_model=SolveOut)
def solve(video_id: uuid.UUID, body: PointsIn, viewer: ViewerDep, db: DbDep) -> SolveOut:
    video, _ = _readable(db, viewer, video_id)
    out = _solve_out(_solve(body.points, calibration.aspect_of(video)))
    db.rollback()
    return out


def _editable(db: DbDep, viewer: ViewerDep, video_id: uuid.UUID) -> Video:
    video, access = _readable(db, viewer, video_id)
    if not can_tag(viewer, access.ref, access.leads):
        raise HTTPException(403, "Only the team's coaches and sport admins set up the court")
    if video.status != "ready":
        raise HTTPException(409, "Set up the court once the video is ready")
    return video


@router.put("/videos/{video_id}/court", response_model=CalibrationOut)
def save(video_id: uuid.UUID, body: SaveIn, viewer: ViewerDep, db: DbDep) -> CalibrationOut:
    video = _editable(db, viewer, video_id)
    solution = _solve(body.points, calibration.aspect_of(video))
    if body.copied_from is not None:
        source = db.get(Calibration, body.copied_from)
        if source is None or source.lineage_id != video.lineage_id:
            raise HTTPException(422, "Copied from an unknown calibration")
    row = calibration.save(
        db,
        video,
        {p.landmark: (p.u, p.v) for p in body.points},
        solution,
        status="confirmed",
        method=body.method,
        actor=viewer.member_id,
        copied_from=body.copied_from,
    )
    audit(
        db,
        viewer.member_id,
        "court_calibrated",
        "video",
        str(video.id),
        method=body.method,
        points=len(body.points),
        error_cm=row.reproj_error_cm,
    )
    db.commit()
    db.refresh(row)
    return _calibration_out(row)


@router.delete("/videos/{video_id}/court", status_code=204)
def remove(video_id: uuid.UUID, viewer: ViewerDep, db: DbDep) -> Response:
    video = _editable(db, viewer, video_id)
    row = calibration.current(db, video.id)
    if row is None:
        raise HTTPException(404, "The video has no court set up")
    db.delete(row)
    audit(db, viewer.member_id, "court_calibration_removed", "video", str(video.id))
    db.commit()
    return Response(status_code=204)
