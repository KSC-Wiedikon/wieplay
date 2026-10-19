"""Cutting and reading the Matchblatt's cells (PLAN §10.3a, SS4; research note §5.3 steps 2-3).

    registered photo ─▶ each set block warped onto the form (6 px per mm), the paper's light
                        divided out, the printed rules taken out (long straight strokes) and
                        the ink scaled to the block's strongest strokes (a faint carbon copy
                        reads like a fresh pen)
                     ─▶ each cell's window cut out (wieplay/ml/sheet_form.py), 48 x 40
                     ─▶ the cell reader (a small CNN, ONNX Runtime on the CPU): one class per
                        number 0-99, "blank" (nothing, or only the printed number struck) and
                        "X" (the receiving team's mark), as probabilities
                     ─▶ the marks (the X over S or R) by ink, compared between the two circles

The reader's weights are WiePlay's own (scripts/ml/sheet_cells.py trains them on synthetic
sheets); their SHA-256 is checked before use. numpy, OpenCV, ONNX Runtime: the worker image.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from wieplay.ml import sheet_form as sf
from wieplay.ml import sheet_register as sr

PATCH_W, PATCH_H = 48, 40
CUT_RES = 6.0  # px per mm of the warped block
BLANK, X = 100, 101
CLASSES = 102
LABELS = [str(v) for v in range(100)] + ["", "X"]

WEIGHTS = Path(__file__).parent / "weights" / "sheet-cells-v1.onnx"
WEIGHTS_SHA256 = "8f054170346a9ef61377c9d1c9f65634672ec38637539269942c55738fcc6b83"
MODEL_NAME = "wieplay-sheet-cells"
MODEL_VERSION = "v1"


def value_of(cls: int) -> int | str | None:
    """A class as a cell's value: a number, "X", or None (blank)."""
    if cls == BLANK:
        return None
    if cls == X:
        return "X"
    return cls


def class_of(value: int | str | None) -> int:
    if value is None:
        return BLANK
    if value == "X":
        return X
    return int(value)


# --- cutting --------------------------------------------------------------------------------------


@dataclass
class BlockInk:
    """A set block warped onto the form: ink 0..1 (1 = the block's strongest strokes)."""

    set_no: int
    ink: np.ndarray
    x0: float  # mm of the picture's left edge
    y0: float
    res: float
    strength: float  # the raw darkness that became 1 (how faint the sheet is)


MARGIN_MM = 6.0
RULE_MM = 7.0  # a straight stroke this long along the form's axes is a printed rule


def without_rules(ink: np.ndarray, res: float) -> np.ndarray:
    """The ink without the form's printed rules. The block is warped onto the form, so its
    rules stand straight: any horizontal or vertical stroke longer than RULE_MM (no digit is that
    tall or wide) is taken out, with a pixel around it. A box's border then no longer reads as a
    "1" beside the number, and a digit crossing a rule loses only the rule's width."""
    k = max(3, round(RULE_MM * res))
    across = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1))
    )
    down = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
    rules = cv2.dilate(np.maximum(across, down), np.ones((3, 3), np.uint8))
    return np.asarray(np.clip(ink - rules, 0, 1), np.float32)


def block_ink(
    gray: np.ndarray, reg: sr.Registration, set_no: int, res: float = CUT_RES
) -> BlockInk:
    b = sf.BLOCKS[set_no]
    h_px = reg.mapping(set_no)
    # A photo sharper than the cut: blur a little first, so the warp averages (no aliasing).
    scale = sr.px_per_mm(h_px, b.rect) / res
    src = gray
    if scale > 1.3:
        src = cv2.GaussianBlur(gray, (0, 0), 0.45 * scale)
    warped, _ = sr.warp_to_form(src, h_px, b.rect, res, MARGIN_MM)
    g = warped.astype(np.float32)
    paper = sr.light(warped, sr._odd(3.0 * res))
    ink = without_rules(np.clip((paper - g) / np.maximum(paper, 8.0), 0, 1), res)
    strength = float(max(np.percentile(ink, 99.7), 0.12))
    return BlockInk(
        set_no,
        np.clip(ink / strength, 0, 1).astype(np.float32),
        b.x - MARGIN_MM,
        b.y - MARGIN_MM,
        res,
        strength,
    )


def cut(block: BlockInk, rect: sf.Rect) -> np.ndarray:
    """A window (mm) of the block as a PATCH_H x PATCH_W patch (float32 0..1)."""
    x0 = (rect.x - block.x0) * block.res
    y0 = (rect.y - block.y0) * block.res
    x1 = (rect.x1 - block.x0) * block.res
    y1 = (rect.y1 - block.y0) * block.res
    # An affine map from the patch to the window, sub-pixel exact.
    m = np.array([[(x1 - x0) / PATCH_W, 0, x0], [0, (y1 - y0) / PATCH_H, y0]], np.float32)
    return np.asarray(
        cv2.warpAffine(
            block.ink,
            m,
            (PATCH_W, PATCH_H),
            flags=cv2.WARP_INVERSE_MAP | cv2.INTER_AREA,
            borderValue=0,
        ),
        np.float32,
    )


def cut_cells(
    gray: np.ndarray, reg: sr.Registration, cells: list[sf.Cell]
) -> tuple[np.ndarray, dict[int, BlockInk]]:
    """Every cell's patch (N, PATCH_H, PATCH_W), and the warped blocks."""
    blocks: dict[int, BlockInk] = {}
    out = np.zeros((len(cells), PATCH_H, PATCH_W), np.float32)
    for i, c in enumerate(cells):
        if c.set_no not in blocks:
            blocks[c.set_no] = block_ink(gray, reg, c.set_no)
        out[i] = cut(blocks[c.set_no], c.window)
    return out, blocks


def mark_ink(block: BlockInk, rect: sf.Rect) -> float:
    """The mean ink inside a mark's rect (its middle, where a cross goes)."""
    inner = rect.grow(-0.15, -0.15, -0.15, -0.15)
    x0 = int((inner.x - block.x0) * block.res)
    y0 = int((inner.y - block.y0) * block.res)
    x1 = int((inner.x1 - block.x0) * block.res)
    y1 = int((inner.y1 - block.y0) * block.res)
    sub = block.ink[max(0, y0) : y1, max(0, x0) : x1]
    return float(sub.mean()) if sub.size else 0.0


def quad(reg: sr.Registration, cell: sf.Cell) -> list[float]:
    """A cell's window in the photo, as fractions of its width and height (x, y x 4 corners,
    clockwise from top left): what the page zooms to."""
    p = reg.to_photo(cell.set_no, np.array(cell.window.corners()))
    w, h = max(reg.width, 1), max(reg.height, 1)
    return [round(float(v), 5) for xy in p for v in (xy[0] / w, xy[1] / h)]


# --- reading --------------------------------------------------------------------------------------


class WeightsMismatch(Exception):
    pass


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


class CellReader:
    """The cell classifier (ONNX): patches → class probabilities (N, CLASSES)."""

    def __init__(
        self, path: Path = WEIGHTS, sha256: str | None = WEIGHTS_SHA256, threads: int = 2
    ) -> None:
        import onnxruntime as ort

        self.path = path
        self.sha256 = sha256_of(path)
        if sha256 and self.sha256 != sha256:
            raise WeightsMismatch(f"{path.name}: SHA-256 {self.sha256}, expected {sha256}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def probabilities(self, patches: np.ndarray, batch: int = 256) -> np.ndarray:
        out = []
        for k in range(0, len(patches), batch):
            x = patches[k : k + batch, None, :, :].astype(np.float32)
            out.append(self.session.run(None, {self.input: x})[0])
        if not out:
            return np.zeros((0, CLASSES), np.float32)
        return np.concatenate(out).astype(np.float32)


def top(probs: np.ndarray, k: int = 5) -> list[list[tuple[int | str | None, float]]]:
    """Per cell its k likeliest readings: (value, probability)."""
    order = np.argsort(-probs, axis=1)[:, :k]
    return [
        [(value_of(int(c)), float(round(float(probs[i, c]), 5))) for c in row]
        for i, row in enumerate(order)
    ]


# --- one photo ------------------------------------------------------------------------------------


@dataclass
class PhotoReading:
    """What a photo shows: its registration and per cell the likeliest readings."""

    registration: sr.Registration
    cells: dict[str, dict[str, Any]]  # key → {"alts": [[value, p], ...], "quad": [...]}
    marks: dict[str, float]  # "s1.L.s" → ink
    strength: dict[int, float]  # per set block: how strong its ink was

    def as_json(self) -> dict[str, Any]:
        return {
            "registration": self.registration.as_json(),
            "cells": self.cells,
            "marks": {k: round(v, 4) for k, v in self.marks.items()},
            "strength": {str(k): round(v, 4) for k, v in self.strength.items()},
        }


def read_photo(
    gray: np.ndarray,
    reader: CellReader,
    set_no: int | None = None,
    reg: sr.Registration | None = None,
) -> PhotoReading:
    """Register a photo, cut every cell of the set blocks it shows, read them."""
    reg = reg or sr.register(gray, set_no)
    sets = [set_no] if set_no else sorted(reg.blocks) or sorted(sf.BLOCKS)
    cells = [c for c in sf.CELLS if c.set_no in sets and c.kind != "mark"]
    patches, blocks = cut_cells(gray, reg, cells)
    probs = reader.probabilities(patches)
    alts = top(probs)
    out: dict[str, dict[str, Any]] = {}
    for c, a in zip(cells, alts, strict=True):
        out[c.key] = {"alts": [[v, p] for v, p in a], "quad": quad(reg, c)}
    marks: dict[str, float] = {}
    for c in sf.CELLS:
        if c.kind == "mark" and c.set_no in blocks:
            marks[c.key] = mark_ink(blocks[c.set_no], c.rect)
    return PhotoReading(reg, out, marks, {k: b.strength for k, b in blocks.items()})
