"""People in a frame and the numbers on their shirts (PLAN §10.5), ONNX Runtime on the CPU.

- **PersonDetector**: Ultralytics YOLO (COCO weights, AGPL-3.0 like WiePlay), exported to ONNX
  with dynamic input size (`scripts/ml/export_yolo.py`, run by Dockerfile.worker). Person
  class only. Reads both export formats: the end-to-end one of YOLO26 (n, 6: x1 y1 x2 y2
  score class, no NMS needed) and the classic one of YOLO11 (84, n: cx cy w h + 80 class
  scores, NMS here). No PyTorch in the worker image: ONNX Runtime is already there for the
  ball.
- **NumberReader**: the shirt number on a crop of a player's back (or chest), PaddleOCR's
  PP-OCR text detector and English recogniser (Apache-2.0, ONNX as distributed by
  RapidOCR), decoded to digits only. A suggestion for the coach, never an identity: WiePlay
  identifies a player only by the number a coach confirms (PLAN §5.7). No faces: the
  crops are of the shirt.

Both run on the CPU with ONNX Runtime; a worker with a GPU can pass other providers.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

Arr = npt.NDArray[Any]

# Where Dockerfile.worker puts the weights (scripts/ml/player_weights.py); the worker
# setting WIEPLAY_WORKER_PLAYERS_WEIGHTS points elsewhere (local runs).
WEIGHTS_DIR = Path("/opt/wieplay-weights")
DETECTOR_FILE = "yolo26s.onnx"
DETECTOR_NAME = "yolo26s-coco"
OCR_DET_FILE = "en_PP-OCRv3_det_mobile.onnx"
OCR_REC_FILE = "en_PP-OCRv5_rec_mobile.onnx"
PERSON = 0
PAD = 114  # letterbox grey, as Ultralytics pads
STRIDE = 32


@cache
def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _session(path: Path, threads: int, providers: Sequence[str] | None) -> Any:
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, threads)
    opts.inter_op_num_threads = 1
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")  # as for the ball
    return ort.InferenceSession(
        str(path), sess_options=opts, providers=list(providers or ["CPUExecutionProvider"])
    )


def resize(img: Arr, width: int, height: int) -> Arr:
    """Bilinear resize of an (h, w, 3) uint8 image (Pillow: C speed, no OpenCV)."""
    from PIL import Image

    if img.shape[1] == width and img.shape[0] == height:
        return img
    out: Arr = np.asarray(Image.fromarray(img).resize((width, height), Image.Resampling.BILINEAR))
    return out


def letterbox(img: Arr, size: int) -> tuple[Arr, float]:
    """Scale the longer side to `size`, pad right and bottom to multiples of 32."""
    h, w = img.shape[:2]
    scale = size / max(h, w)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    pw, ph = -(-nw // STRIDE) * STRIDE, -(-nh // STRIDE) * STRIDE
    out = np.full((ph, pw, 3), PAD, np.uint8)
    out[:nh, :nw] = resize(img, nw, nh)
    return out, scale


def nms(boxes: Arr, scores: Arr, iou: float = 0.5) -> list[int]:
    """Greedy non-maximum suppression; indices of the boxes kept, best first."""
    order = np.argsort(-scores)
    keep: list[int] = []
    while len(order):
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        order = rest[inter / np.maximum(area_i + area_r - inter, 1e-9) < iou]
    return keep


def decode(out: Arr, conf: float) -> tuple[Arr, Arr]:
    """A YOLO output for one image → person boxes (n, 4) in input pixels and scores."""
    if out.ndim == 2 and out.shape[-1] == 6:  # end-to-end: x1 y1 x2 y2 score class
        keep = (out[:, 4] >= conf) & (out[:, 5].round() == PERSON)
        return out[keep, :4].astype(np.float32), out[keep, 4].astype(np.float32)
    if out.ndim == 2 and out.shape[0] >= 5:  # classic: (4 + classes, anchors)
        scores = out[4 + PERSON]
        keep = scores >= conf
        c = out[:4, keep].T
        boxes = np.stack(
            [c[:, 0] - c[:, 2] / 2, c[:, 1] - c[:, 3] / 2, c[:, 0] + c[:, 2] / 2,
             c[:, 1] + c[:, 3] / 2], axis=1,
        )  # fmt: skip
        idx = nms(boxes, scores[keep])
        return boxes[idx].astype(np.float32), scores[keep][idx].astype(np.float32)
    raise ValueError(f"unexpected detector output {out.shape}")


@dataclass
class Detections:
    boxes: Arr  # float32 (n, 4) x1 y1 x2 y2, pixels of the image given
    scores: Arr  # float32 (n,)


class PersonDetector:
    def __init__(
        self,
        path: Path | None = None,
        *,
        imgsz: int = 1280,
        conf: float = 0.15,
        threads: int = 4,
        providers: Sequence[str] | None = None,
    ) -> None:
        self.path = path or WEIGHTS_DIR / DETECTOR_FILE
        if not self.path.is_file():
            raise FileNotFoundError(f"person detector weights missing: {self.path}")
        self.imgsz, self.conf = imgsz, conf
        self._session = _session(self.path, threads, providers)
        self._input = self._session.get_inputs()[0].name

    @property
    def sha256(self) -> str:
        return file_sha256(self.path)

    def detect(self, img: Arr) -> Detections:
        """People in an (h, w, 3) uint8 RGB image."""
        boxed, scale = letterbox(img, self.imgsz)
        x = boxed.transpose(2, 0, 1)[None].astype(np.float32) * np.float32(1 / 255)
        out = self._session.run(None, {self._input: x})[0][0]
        boxes, scores = decode(out, self.conf)
        boxes = boxes / scale
        h, w = img.shape[:2]
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)
        ok = (boxes[:, 2] - boxes[:, 0] >= 2) & (boxes[:, 3] - boxes[:, 1] >= 4)
        return Detections(boxes[ok], scores[ok])


# --- shirt numbers --------------------------------------------------------------------------

OCR_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
OCR_STD = np.array([0.229, 0.224, 0.225], np.float32)
OCR_DET_SIDE = 256  # the torso crop's longer side for the text detector
OCR_DET_THRESHOLD = 0.3
OCR_REC_HEIGHT = 48
DIGITS = "0123456789"


def ctc_digits(probs: Arr, characters: Sequence[str]) -> tuple[str, float]:
    """Greedy CTC decoding of one line (T, classes; class 0 = blank, then `characters`).
    Returns the text and the lowest confidence of its characters; "" when it contains
    anything but digits (a sponsor's name, a club's)."""
    best = probs.argmax(axis=1)
    out: list[str] = []
    confs: list[float] = []
    previous = 0
    for t, k in enumerate(best):
        k = int(k)
        if k != 0 and k != previous:
            ch = characters[k - 1] if k - 1 < len(characters) else " "
            if ch not in DIGITS:
                return "", 0.0
            out.append(ch)
            confs.append(float(probs[t, k]))
        previous = k
    return "".join(out), (min(confs) if confs else 0.0)


def text_boxes(prob: Arr, threshold: float = OCR_DET_THRESHOLD) -> list[tuple[int, int, int, int]]:
    """Axis-aligned boxes (y0, y1, x0, x1) of the text regions in a detector map, grown like
    DB's unclip (shirt numbers are upright: no rotated boxes needed)."""
    from scipy import ndimage

    labels, _ = ndimage.label(prob > threshold)
    h, w = prob.shape
    out = []
    for sl in ndimage.find_objects(labels):
        if sl is None:
            continue
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        if (y1 - y0) * (x1 - x0) < 30:
            continue
        cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
        hh, ww = (y1 - y0) * 1.6 + 4, (x1 - x0) * 1.3 + 6
        out.append(
            (
                max(0, int(cy - hh / 2)), min(h, int(cy + hh / 2) + 1),
                max(0, int(cx - ww / 2)), min(w, int(cx + ww / 2) + 1),
            )
        )  # fmt: skip
    return out


class NumberReader:
    def __init__(
        self,
        det_path: Path | None = None,
        rec_path: Path | None = None,
        *,
        threads: int = 2,
        providers: Sequence[str] | None = None,
    ) -> None:
        self.det_path = det_path or WEIGHTS_DIR / OCR_DET_FILE
        self.rec_path = rec_path or WEIGHTS_DIR / OCR_REC_FILE
        self._det = _session(self.det_path, threads, providers)
        self._rec = _session(self.rec_path, threads, providers)
        meta = self._rec.get_modelmeta().custom_metadata_map
        self.characters = meta.get("character", "").splitlines()
        if not self.characters:
            raise ValueError("the recogniser carries no character list")

    @property
    def sha256(self) -> str:
        return file_sha256(self.det_path)[:32] + file_sha256(self.rec_path)[:32]

    def _line(self, img: Arr) -> tuple[str, float]:
        h, w = img.shape[:2]
        nw = max(16, min(320, round(OCR_REC_HEIGHT * w / max(h, 1))))
        x = resize(img, nw, OCR_REC_HEIGHT).astype(np.float32) / 127.5 - 1
        probs = self._rec.run(None, {self._rec.get_inputs()[0].name: x.transpose(2, 0, 1)[None]})
        return ctc_digits(probs[0][0], self.characters)

    def read(self, torso: Arr) -> tuple[int | None, float]:
        """The number on a torso crop (uint8 RGB) and how sure the recogniser is."""
        h, w = torso.shape[:2]
        if h < 16 or w < 8:
            return None, 0.0
        s = OCR_DET_SIDE / max(h, w)
        nh = max(STRIDE, round(h * s / STRIDE) * STRIDE)
        nw = max(STRIDE, round(w * s / STRIDE) * STRIDE)
        x = (resize(torso, nw, nh).astype(np.float32) / 255 - OCR_MEAN) / OCR_STD
        prob = self._det.run(None, {self._det.get_inputs()[0].name: x.transpose(2, 0, 1)[None]})
        best: tuple[int | None, float] = (None, 0.0)
        for y0, y1, x0, x1 in text_boxes(prob[0][0, 0]):
            Y0, Y1 = int(y0 * h / nh), min(h, int(y1 * h / nh) + 1)
            X0, X1 = int(x0 * w / nw), min(w, int(x1 * w / nw) + 1)
            if Y1 - Y0 < 6 or X1 - X0 < 4:
                continue
            text, conf = self._line(torso[Y0:Y1, X0:X1])
            # 1-99: a lone "0" or "07" is a number cut off at the shirt's edge (a coach can
            # still give 0 by hand).
            if 1 <= len(text) <= 2 and not text.startswith("0") and conf > best[1]:
                best = (int(text), conf)
        return best
