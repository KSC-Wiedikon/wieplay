"""The court job (PLAN §10.6): where the court is in a video's picture, all match long.
Minutes of CPU; queued after each trajectory job, on the worker's background lane.

    a key frame every ~30 s ─▶ the camera's setups (bumps, drift: court_segments.py)
    the main setup's frames ─median─▶ the empty hall
        ─▶ the hall's memory: calibrated pictures of earlier videos (the team's first,
           then everyone's), matched to this one (court_register.py) ─▶ their court here
        ─▶ the court finder for a hall seen for the first time (court_find.py)
        ─▶ the best court, carried to the other setups through their pictures
    the empty hall, as a JPEG on the NAS: this video's picture for the hall's memory

Writes only below the job's analysis folder; the API decides what becomes the video's
calibration (nomal/core/calibration.py).
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from nomal.core import court
from nomal.ml import court_detect, court_find, court_segments
from nomal.ml import court_register as reg
from nomal.ml.extract import NICE, lower_thread_priority
from nomal.worker import ffmpeg
from nomal.worker.analyse import _out_dir, _sha256, _write, code_version
from nomal.worker.handlers import Context, PermanentError, inside, raw_inputs

log = logging.getLogger(__name__)
MODEL = "nomal-court"
SCHEMA = 1
SIZE = (1280, 720)  # the court is found at this size, like the trajectory job's finder
STEP_S = 30.0
MAX_SAMPLES = 360
MEDIAN_FRAMES = 15
JPEG_QUALITY = 92
GRAB_TIMEOUT_S = 120
# The hall's memory: a transfer this sure is taken over the finder.
HALL_MIN_INLIERS = 30


def _nice() -> None:
    os.nice(NICE)


def grab(parts: ffmpeg.Parts, at_s: float, *, key: bool) -> np.ndarray | None:
    """An RGB frame at SIZE: the key frame at or after `at_s` (fast), or the exact one. Seeks
    in the camera file itself (ffmpeg.Parts), never through the concat demuxer."""
    w, h = SIZE
    source, offset = parts.locate(at_s)
    args = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error",
        *(["-skip_frame", "nokey", "-noaccurate_seek"] if key else []),
        "-ss", f"{offset:.3f}", *ffmpeg.input_guard(), "-i", str(source),
        "-frames:v", "1", "-an", "-vf", f"scale={w}:{h}:flags=area",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
    ]  # fmt: skip
    try:
        out = subprocess.run(  # noqa: S603 - argument list, ffmpeg from PATH
            args, capture_output=True, timeout=GRAB_TIMEOUT_S, check=False, preexec_fn=_nice
        )
    except subprocess.TimeoutExpired:
        log.warning("no frame at %.1f s within %d s", at_s, GRAB_TIMEOUT_S)
        return None
    if out.returncode != 0 or len(out.stdout) < w * h * 3:
        return None
    return np.frombuffer(out.stdout[: w * h * 3], np.uint8).reshape(h, w, 3)


def sample_times(duration_ms: int) -> list[int]:
    """Every STEP_S (at most MAX_SAMPLES; a short clip gets a dozen), not in the last seconds:
    after the last key frame there is none to skip to."""
    duration_s = max(duration_ms / 1000, 1.0)
    step = min(max(STEP_S, duration_s / MAX_SAMPLES), max(duration_s / 12, 0.25))
    end = duration_s - min(10.0, 0.1 * duration_s)
    return [int(1000 * t) for t in np.arange(step / 2, max(end, step / 2 + 0.01), step)]


def encode_jpeg(image: np.ndarray) -> bytes:
    ok, data = cv2.imencode(
        ".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    )
    if not ok:
        raise RuntimeError("could not encode the hall's picture")
    return bytes(data.tobytes())


def decode_jpeg(data: bytes) -> np.ndarray | None:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    out: np.ndarray = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    if out.shape[:2] != (SIZE[1], SIZE[0]):
        out = cv2.resize(out, SIZE, interpolation=cv2.INTER_AREA)
    return out


def load_reference(root: Path, ref: dict[str, Any]) -> np.ndarray | None:
    """The empty hall of an earlier video (its court job's frames file on the NAS)."""
    try:
        path = inside(root, root / ref["nas_path"])
        if _sha256(path) != ref["sha256"]:
            log.warning("hall reference %s: SHA-256 does not match", path.name)
            return None
        with np.load(path, allow_pickle=False) as data:
            return decode_jpeg(bytes(data["frame"].tobytes()))
    except (OSError, KeyError, ValueError, PermanentError) as exc:
        log.warning("hall reference %s unusable: %s", ref.get("nas_path"), exc)
        return None


def load_bounces(
    root: Path, track: dict[str, Any] | None, window: tuple[int, int]
) -> np.ndarray | None:
    """The ball's floor contacts in a time window (normalised image points), from the
    trajectory job's track."""
    if not track:
        return None
    try:
        path = inside(root, root / track["nas_path"])
        payload = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, ValueError, KeyError, PermanentError) as exc:
        log.warning("ball track unusable: %s", exc)
        return None
    scale = float(payload.get("scale") or 1000)
    pts = [
        (c[1] / scale, c[2] / scale)
        for c in payload.get("contacts", [])
        if c[3] == 0 and window[0] <= c[0] < window[1]
    ]
    return np.array(pts, np.float64) if pts else None


# --- the court in the empty hall ---------------------------------------------------------------


def _support(h_px: np.ndarray, lines: np.ndarray) -> float:
    samples, which = court_detect._model_samples()
    _, share = court_detect._score(h_px, lines, samples, which)
    return share


def _refined(h_px: np.ndarray, raw: np.ndarray) -> np.ndarray:
    """The court's lines fitted to the line pixels near them (a transfer is a few pixels off)."""
    size = (int(raw.shape[0]), int(raw.shape[1]))
    try:
        out = court_detect._refine(court_detect._refine(h_px, raw), raw)
        to_image = court.normalise(court_detect._normalised(out, size))
        if court.mirrored(to_image) or reg.corner_distance_px(out, h_px) > 0.04 * size[1]:
            return h_px
        return out
    except (court.CourtError, np.linalg.LinAlgError):
        return h_px


def from_hall(
    image: np.ndarray, references: list[tuple[dict[str, Any], np.ndarray]]
) -> list[dict[str, Any]]:
    """The court through each usable reference picture, best first."""
    size = (int(image.shape[0]), int(image.shape[1]))
    raw = court_find.lines_only(court_find.ridges(image) >= court_detect.RIDGE_MIN)
    tight = court_detect.dilate(raw, 1)
    feats = reg.features(image, "sift", 6000)
    out: list[dict[str, Any]] = []
    for ref, ref_image in references:
        ref_h = reg.to_pixels(np.array(ref["court_to_image"], float), size)
        best: dict[str, Any] | None = None
        for found in reg.transfers(ref_image, ref_h, image, image_features=feats):
            if found.registration.inliers < HALL_MIN_INLIERS:
                continue
            before = _support(found.court_to_image, tight)
            h = _refined(found.court_to_image, raw)
            support = _support(h, tight)
            if support <= before:  # the lines found nothing better: the transfer as it came
                h, support = found.court_to_image, before
            to_image = court.normalise(court_detect._normalised(h, size))
            if court.mirrored(to_image) or not court_detect._plausible(to_image, size[1] / size[0]):
                continue
            r = found.registration
            confidence = hall_confidence(found.how, r.inliers, r.spread, support)
            if best is not None and confidence <= float(best["confidence"]):
                continue
            best = {
                "h": h,
                "confidence": confidence,
                "reference": {
                    "calibration_id": ref["calibration_id"],
                    "video_id": ref["video_id"],
                },
                "stats": {
                    "how": found.how,
                    "inliers": r.inliers,
                    "matches": r.matches,
                    "spread": round(r.spread, 3),
                    "error_px": round(r.error_px, 2),
                    "support": round(support, 4),
                    "support_before": round(before, 4),
                    "same_team": bool(ref.get("same_team")),
                    "same_hall": bool(ref.get("same_hall")),
                },
            }
        if best is not None:
            out.append(best)
    out.sort(key=lambda c: -float(c["confidence"]))
    return out


def hall_confidence(how: str, inliers: int, spread: float, support: float) -> float:
    """How sure a court carried over from the hall's memory is: many matches spread over
    the floor (the pictures agree), and the court's lines on line pixels here."""
    agree = min(1.0, inliers / (120.0 if how == "direct" else 80.0)) * min(1.0, spread / 0.12)
    return round(0.5 * agree + 0.5 * min(1.0, support / 0.8), 4)


def points_of(h_px: np.ndarray, size: tuple[int, int]) -> dict[str, list[float]] | None:
    """The landmarks inside the picture (normalised), or None when they define no court."""
    to_image = court.normalise(court_detect._normalised(h_px, size))
    found: dict[str, list[float]] = {}
    for name, xy in court.LANDMARKS.items():
        try:
            u, v = court.apply(to_image, xy)
        except court.CourtError:
            continue
        if -0.02 <= u <= 1.02 and -0.02 <= v <= 1.02:
            found[name] = [round(min(1.0, max(0.0, u)), 5), round(min(1.0, max(0.0, v)), 5)]
    try:
        court.check_spread([court.LANDMARKS[n] for n in found])
    except court.CourtError:
        return None
    return found


# --- the job ---------------------------------------------------------------------------------


def _median(
    ctx: Context,
    parts: ffmpeg.Parts,
    times: list[int],
    members: list[int],
    progress: tuple[float, float],
) -> np.ndarray | None:
    """The empty hall: the median of up to MEDIAN_FRAMES frames of a setup."""
    pick = [
        members[round(k)]
        for k in np.linspace(0, len(members) - 1, min(MEDIAN_FRAMES, len(members)))
    ]
    pick = list(dict.fromkeys(pick))
    frames = []
    for k, i in enumerate(pick):
        if ctx.cancel.is_set():
            raise ffmpeg.Cancelled()
        frame = grab(parts, times[i] / 1000, key=True)
        if frame is not None:
            frames.append(frame)
        ctx.report(
            progress[0] + (progress[1] - progress[0]) * (k + 1) / len(pick), "the empty hall"
        )
    if not frames:
        return None
    out: np.ndarray = np.median(np.stack(frames), axis=0).astype(np.uint8)
    return out


def best_court(
    hall_courts: list[dict[str, Any]], guess: court_detect.CourtGuess | None, size: tuple[int, int]
) -> dict[str, Any] | None:
    """The hall's memory unless the finder is clearly surer."""
    best: dict[str, Any] | None = None
    if hall_courts:
        c = hall_courts[0]
        best = {
            "method": "auto-hall",
            "h": c["h"],
            "confidence": c["confidence"],
            "reference": c["reference"],
            "stats": c["stats"],
        }
    if guess is not None and (best is None or guess.confidence > float(best["confidence"]) + 0.15):
        best = {
            "method": "auto",
            "h": reg.to_pixels(np.array(guess.court_to_image, float), size),
            "confidence": guess.confidence,
            "reference": None,
            "stats": guess.stats,
        }
    return best


def run(ctx: Context) -> dict[str, Any]:
    lower_thread_priority()
    started = time.perf_counter()
    params = ctx.job.params
    root, out = _out_dir(ctx)
    parts = ffmpeg.Parts(raw_inputs(ctx))
    duration_ms = int(params.get("duration_ms") or 0)
    if duration_ms <= 0:
        raise PermanentError("the video has no duration")
    size = (SIZE[1], SIZE[0])

    # 1. A key frame every ~30 s: the camera's setups and views.
    times = sample_times(duration_ms)
    feats: list[reg.Features | None] = []
    for i, t in enumerate(times):
        if ctx.cancel.is_set():
            raise ffmpeg.Cancelled()
        frame = grab(parts, t / 1000, key=True)
        feats.append(reg.features(frame, "orb") if frame is not None else None)
        ctx.report(0.5 * (i + 1) / len(times), "following the camera")
    if sum(f is not None for f in feats) < 3:
        raise PermanentError("could not read frames of the video")

    def at(t_ms: int) -> reg.Features | None:
        # Key frames again: a second or a few apart, as close as a bump needs to be placed,
        # and fast (an exact frame decodes everything since the last key frame).
        frame = grab(parts, t_ms / 1000, key=True)
        return reg.features(frame, "orb") if frame is not None else None

    setups = court_segments.find(times, feats, duration_ms, grab=at)
    main = court_segments.main_setup(setups)
    views = sorted({s.view for s in setups}, key=lambda v: v != setups[main].view)
    sampled_s = time.perf_counter() - started

    # 2. Per view, the empty hall of its longest setup (the video's main one first).
    references = []
    for ref in params.get("references") or []:
        image = load_reference(root, ref["frames"])
        if image is not None:
            references.append((ref, image))
    track = params.get("track")
    view_main: dict[int, int] = {}
    view_court: dict[int, dict[str, Any] | None] = {}
    candidates: dict[str, Any] = {}
    target = out / f"court-{ctx.job.id[:8]}.npz"
    main_hall: np.ndarray | None = None
    frames_used = 0
    hall_s = find_s = 0.0
    for n, view in enumerate(views):
        vm = court_segments.main_setup(setups, view)
        view_main[view] = vm
        span = (0.5 + 0.3 * n / len(views), 0.5 + 0.3 * (n + 1) / len(views))
        hall = _median(ctx, parts, times, setups[vm].samples, (span[0], (span[0] + span[1]) / 2))
        if hall is None:
            if view == setups[main].view:
                raise PermanentError("could not read frames of the video")
            view_court[view] = None
            continue
        if view == setups[main].view:
            frames_used = len(setups[vm].samples)
            buffer = io.BytesIO()
            np.savez(buffer, frame=np.frombuffer(encode_jpeg(hall), np.uint8))
            _write(target, data=buffer.getvalue())
        # 3. The court: the hall's memory first (for another view, the main view's court
        # too), the finder for a new hall.
        clock = time.perf_counter()
        ctx.report(span[0] + 0.5 * (span[1] - span[0]), "the hall's memory")
        refs = list(references)
        main_court = view_court.get(setups[main].view)
        if view != setups[main].view and main_court is not None and main_hall is not None:
            refs.append(
                (
                    {
                        "calibration_id": None,
                        "video_id": params.get("video_id"),
                        "court_to_image": reg.to_normalised(main_court["h"], size).tolist(),
                    },
                    main_hall,
                )
            )
        hall_courts = from_hall(hall, refs) if refs else []
        hall_s += time.perf_counter() - clock
        clock = time.perf_counter()
        bounces = load_bounces(root, track, (setups[vm].from_ms, setups[vm].to_ms))
        guess = court_find.detect(hall, bounces=bounces)
        find_s += time.perf_counter() - clock
        view_court[view] = best_court(hall_courts, guess, size)
        candidates[str(view)] = {
            "hall": [
                {"confidence": c["confidence"], "reference": c["reference"], **c["stats"]}
                for c in hall_courts[:5]
            ],
            "finder": (
                {"confidence": guess.confidence, **guess.stats} if guess is not None else None
            ),
            "bounces": len(bounces) if bounces is not None else 0,
        }
        if view == setups[main].view:
            main_hall = hall
        if ctx.cancel.is_set():
            raise ffmpeg.Cancelled()

    # 4. Every setup's picture of the court, through the pictures' homographies onto its
    # view's main setup.
    to_main: list[np.ndarray] = []
    for s in setups:
        anchor_main = setups[view_main[s.view]].to_anchor
        if s.to_anchor is None or anchor_main is None:
            to_main.append(np.eye(3))
        else:
            m = np.linalg.inv(anchor_main) @ s.to_anchor
            to_main.append(m / m[2, 2])
    court_out: dict[str, Any] | None = None
    best = view_court.get(setups[main].view)
    if best is not None:
        per_setup: list[dict[str, Any]] = []
        for s, m in zip(setups, to_main, strict=True):
            found = view_court.get(s.view)
            if found is None:
                per_setup.append({"points": None})
                continue
            h_k = np.linalg.inv(m) @ found["h"]
            per_setup.append({"points": points_of(h_k / h_k[2, 2], size)})
        if per_setup[main]["points"] is not None:
            court_out = {
                "method": best["method"],
                "confidence": round(float(best["confidence"]), 4),
                "reference": best["reference"],
                "setups": per_setup,
                "stats": {
                    **best["stats"],
                    "views": {
                        str(v): (
                            {"method": c["method"], "confidence": round(float(c["confidence"]), 4)}
                            if c is not None
                            else None
                        )
                        for v, c in view_court.items()
                    },
                },
            }

    setups_out = [
        {
            "from_ms": s.from_ms,
            "to_ms": s.to_ms,
            "start": s.start,
            "shift_px": s.shift_px,
            "samples": len(s.samples),
            "view": s.view,
            "to_main": [
                [round(float(v), 9) for v in row]
                for row in reg.image_to_image_normalised(to_main[k], size, size)
            ],
        }
        for k, s in enumerate(setups)
    ]
    return {
        "model": {
            "name": MODEL,
            "version": code_version(),
            "params": {
                "size": list(SIZE),
                "step_s": STEP_S,
                "samples": len(times),
                "shift_px": court_segments.SHIFT_PX,
                "bump_px": court_segments.BUMP_PX,
                "references": len(params.get("references") or []),
            },
        },
        "frames": {
            "nas_path": str(target.relative_to(root.resolve())),
            "sha256": _sha256(target),
            "bytes": target.stat().st_size,
            "schema_version": SCHEMA,
            "stats": {"size": list(SIZE), "frames": frames_used},
        },
        "setups": setups_out,
        "main": main,
        "court": court_out,
        "candidates": candidates,
        "timing": {
            "wall_s": round(time.perf_counter() - started, 1),
            "sampling_s": round(sampled_s, 1),
            "hall_s": round(hall_s, 1),
            "finder_s": round(find_s, 1),
        },
    }
