"""Train the scoreboard's digit network (PLAN §10.3, step 5) on synthetic boards.

    cd backend && uv run --with pillow python ../scripts/ml/scoreboard_digits.py \
        nomal/ml/weights/scoreboard-digits-v1.npz [--samples 120000] [--seed 7]

Boards are drawn by nomal/ml/scoreboard_synth.py (seven segments, OpenCV's stroke fonts) and,
here, with the TrueType fonts found on the machine (bold sans faces: printed flip cards), then
degraded like a hall camera degrades them; the glyphs are cut out exactly as the reader cuts
them. A one-hidden-layer network (scikit-learn, then stored as plain numpy arrays: no pickle)
learns digits 0-9 and "no digit". Deterministic for a seed and a set of fonts. Prints the
held-out accuracy; the weights' SHA-256 goes into nomal/ml/scoreboard.py and the weights
README.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from functools import lru_cache
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from nomal.ml import scoreboard_synth as synth  # noqa: E402
from nomal.ml.scoreboard import DigitModel  # noqa: E402

FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
]


def ttf_fonts() -> list[synth.MaskFont]:
    from PIL import Image, ImageDraw, ImageFont  # the script only

    found = [f for f in FONTS if Path(f).exists()]

    def make(path: str) -> synth.MaskFont:
        font = ImageFont.truetype(path, 220)

        @lru_cache(maxsize=10)
        def big(digit: int) -> np.ndarray:
            img = Image.new("L", (320, 320), 0)
            ImageDraw.Draw(img).text((40, 20), str(digit), fill=255, font=font)
            return np.asarray(img)

        return lambda digit, height: synth._fit(big(digit), height)

    print(f"fonts: {len(found)} TrueType + seven segments + Hershey", flush=True)
    return [make(p) for p in found]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--samples", type=int, default=120_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--hidden", type=int, default=128)
    args = parser.parse_args()

    from sklearn.neural_network import MLPClassifier

    fonts = ttf_fonts()
    rng = np.random.default_rng(args.seed)
    started = time.perf_counter()
    x, y = synth.training_samples(rng, args.samples, fonts)
    print(f"{len(y)} glyphs in {time.perf_counter() - started:.0f} s; classes {np.bincount(y)}", flush=True)
    held = np.random.default_rng(args.seed + 1)
    xt, yt = synth.training_samples(held, max(5000, args.samples // 10), fonts)
    net = MLPClassifier(
        hidden_layer_sizes=(args.hidden,),
        alpha=2e-4,
        batch_size=256,
        learning_rate_init=1e-3,
        max_iter=60,
        early_stopping=True,
        validation_fraction=0.08,
        n_iter_no_change=6,
        random_state=args.seed,
    )
    net.fit(x, y)
    w1, w2 = net.coefs_
    b1, b2 = net.intercepts_
    model = DigitModel(w1, b1, w2, b2)
    pred = model.probs(xt).argmax(axis=1)
    digits = yt < 10
    print(
        f"held out: {np.mean(pred == yt):.4f} all, {np.mean(pred[digits] == yt[digits]):.4f} digits, "
        f"{np.mean(pred[~digits] == 10):.4f} rejected non-digits; {net.n_iter_} epochs, "
        f"{time.perf_counter() - started:.0f} s",
        flush=True,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    model.save(args.out)
    print(f"{args.out}: {args.out.stat().st_size} bytes, sha256 {hashlib.sha256(args.out.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
