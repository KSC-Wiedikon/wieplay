"""The `sheet_read` job (PLAN §10.3a, SS4): read the scoresheet's photos on this machine's CPU.

Each photo comes from WiePlay's own bucket through a URL the API signed for this job alone, is
decoded in memory (never written to disk, never sent anywhere else), matched to the form and
read cell by cell (wieplay/ml/sheet_cells.py). The result is what the reader saw: per photo its
registration, per cell its likeliest values and where the cell is; the API decodes them with the
rules. numpy, OpenCV and ONNX Runtime: the worker image.
"""

from __future__ import annotations

import os
import socket
import time
from typing import Any

import cv2
import httpx
import numpy as np

from wieplay.core.sheet_photos import MAX_BYTES
from wieplay.ml import sheet_cells as sc
from wieplay.ml import sheet_register as sr
from wieplay.worker import ffmpeg
from wieplay.worker.handlers import Context, PermanentError

THREADS = 2


def _fetch(url: str) -> bytes:
    with httpx.stream("GET", url, timeout=httpx.Timeout(60.0, connect=15.0)) as response:
        if response.status_code != 200:
            raise RuntimeError(f"photo download failed: HTTP {response.status_code}")
        data = bytearray()
        for chunk in response.iter_bytes(1 << 20):
            data += chunk
            if len(data) > MAX_BYTES * 2:
                raise PermanentError("the photo is larger than any WiePlay stores")
    return bytes(data)


def run(ctx: Context) -> dict[str, Any]:
    images = ctx.job.params.get("images") or []
    if not images:
        raise PermanentError("no photo to read")
    started = time.perf_counter()
    urls = ctx.api.sign(ctx.job.id, "get", [img["key"] for img in images])
    reader = sc.CellReader(threads=THREADS)
    out: list[dict[str, Any]] = []
    timing: dict[str, float] = {}
    for k, img in enumerate(images):
        if ctx.cancel.is_set():
            raise ffmpeg.Cancelled()
        ctx.report(k / len(images), f"reading photo {k + 1} of {len(images)}")
        t0 = time.perf_counter()
        data = _fetch(urls[img["key"]])
        gray = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        del data
        if gray is None:
            out.append({"id": img["id"], "error": "unreadable"})
            continue
        t1 = time.perf_counter()
        try:
            reading = sc.read_photo(gray, reader, img.get("set_no"))
        except sr.NotFound:
            out.append({"id": img["id"], "error": "not_found"})
            continue
        t2 = time.perf_counter()
        out.append({"id": img["id"], **reading.as_json()})
        timing[f"photo{k + 1}_fetch_s"] = round(t1 - t0, 2)
        timing[f"photo{k + 1}_read_s"] = round(t2 - t1, 2)
    timing["total_s"] = round(time.perf_counter() - started, 2)
    return {
        "model": {
            "name": sc.MODEL_NAME,
            "version": os.environ.get("WIEPLAY_GIT_SHA", sc.MODEL_VERSION)[:64],
            "weights_sha256": reader.sha256,
        },
        "images": out,
        "timing": timing,
        "host": socket.gethostname()[:64],
    }
