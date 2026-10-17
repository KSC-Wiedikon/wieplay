"""Small per-video series the API keeps (PLAN §10.3): plain Python, no numpy.

The worker turns a video's signals into two compact series and sends them with the analysis
result; the API stores them (`analysis_track`) and reads them whenever it scores the video,
also after a coach moved or added rallies, without asking the worker again:

- **serve** (every 0.5 s): P(the server stood at the near end line) for a rally starting
  there (wieplay/ml/serve.py);
- **activity** (every second): the ball in play, movement in the middle of the court and along
  its sides (benches), whistles starting.

One byte per step and channel: a share 0..1 as 0..254, 255 for "no evidence"; zlib and base64
for JSON. A 2 h match is ~15 KB of serve track and ~30 KB of activity before compression.
"""

from __future__ import annotations

import base64
import math
import zlib
from collections.abc import Iterable
from dataclasses import dataclass

FORMAT = 1
SERVE_STEP_MS = 500
ACTIVITY_STEP_MS = 1000
NONE = 255
MOTION_SCALE = 8.0  # movement relative to the video's median, divided by this before quantising
SERVE_CHANNELS = ("p_near",)
ACTIVITY_CHANNELS = ("ball", "court", "side", "whistle")
MAX_STEPS = 24 * 3600 * 2  # a day at 0.5 s


def quantise(values: Iterable[float | None]) -> bytes:
    out = bytearray()
    for v in values:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            out.append(NONE)
        else:
            out.append(min(254, max(0, round(float(v) * 254))))
    return bytes(out)


def encode(data: bytes) -> str:
    return base64.b64encode(zlib.compress(data, 9)).decode()


def decode(text: str) -> bytes:
    raw = zlib.decompress(base64.b64decode(text.encode(), validate=True), bufsize=MAX_STEPS)
    if len(raw) > MAX_STEPS:
        raise ValueError("track too long")
    return raw


@dataclass(frozen=True)
class Series:
    """One channel: a value per step from 0 ms."""

    step_ms: int
    data: bytes
    scale: float = 1.0

    def value(self, k: int) -> float | None:
        if k < 0 or k >= len(self.data) or self.data[k] == NONE:
            return None
        return self.data[k] / 254 * self.scale

    def at(self, t_ms: int) -> float | None:
        return self.value(t_ms // self.step_ms)

    def near(self, t_ms: int, reach: int = 1) -> float | None:
        """The value at t, else the closest one within `reach` steps."""
        k = t_ms // self.step_ms
        for d in range(reach + 1):
            for kk in (k,) if d == 0 else (k - d, k + d):
                v = self.value(kk)
                if v is not None:
                    return v
        return None

    def mean(self, a_ms: int, b_ms: int) -> float | None:
        vals = [
            v
            for k in range(max(0, a_ms // self.step_ms), max(0, -(-b_ms // self.step_ms)))
            if (v := self.value(k)) is not None
        ]
        return sum(vals) / len(vals) if vals else None

    def total(self, a_ms: int, b_ms: int) -> int:
        """Sum of raw counts (whistles) over [a, b)."""
        lo, hi = max(0, a_ms // self.step_ms), min(len(self.data), -(-b_ms // self.step_ms))
        return sum(b for b in self.data[lo:hi] if b != NONE)


def series(channels: dict[str, str], name: str, step_ms: int) -> Series | None:
    text = channels.get(name)
    if not text:
        return None
    scale = MOTION_SCALE if name in ("court", "side") else 1.0
    return Series(step_ms, decode(text), scale)
