"""Train the scoresheet's cell reader (PLAN §10.3a, SS4) on synthetic sheets; export ONNX.

    cd backend && uv sync --group train
    uv run python ../scripts/ml/sheet_cells.py DATA_DIR wieplay/ml/weights/sheet-cells-v1.onnx \
        [--sheets 700] [--epochs 14] [--seed 11] [--workers 8]

DATA_DIR holds handwritten digits in the MNIST file format, downloaded by hand (never in git):

- mnist/{train,t10k}-{images-idx3,labels-idx1}-ubyte.gz: MNIST (Y. LeCun, C. Cortes, C. Burges;
  CC BY-SA 3.0), e.g. from https://ossci-datasets.s3.amazonaws.com/mnist/
- emnist/emnist-digits-{train,test}-{images-idx3,labels-idx1}-ubyte.gz: EMNIST Digits (G. Cohen et
  al. 2017, arXiv:1702.05373; from NIST Special Database 19, a US government work), from
  https://biometrics.nist.gov/cs_links/EMNIST/gzip.zip

Each training sheet is a whole synthetic Matchblatt (wieplay/ml/sheet_synth.py: random numbers in
every kind of cell, a pen or a faint carbon copy, a phone photo of it on a table), cut exactly as
the worker cuts a real photo (wieplay/ml/sheet_cells.py), with a registration that is off by a
fraction of a millimetre as real ones are. The digits come from the datasets' training splits,
plus drawn "Λ"-ones and barred sevens (continental handwriting); the held-out sheets use the test
splits. A small CNN learns 102 classes (0-99, blank, X); its softmax temperature is fitted on the
held-out sheets so its probabilities mean what they say (on synthetic data); the ONNX file takes
(N, 1, 40, 48) float ink and gives (N, 102) probabilities. Prints the held-out accuracy and the
file's SHA-256 (for wieplay/ml/sheet_cells.py and the weights README). Deterministic for a seed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from wieplay.ml import sheet_cells as sc  # noqa: E402
from wieplay.ml import sheet_form as sf  # noqa: E402
from wieplay.ml import sheet_register as sr  # noqa: E402
from wieplay.ml import sheet_synth as ss  # noqa: E402


def idx(path: Path) -> np.ndarray:
    """An MNIST-format file (gzip): images (N, h, w) or labels (N,)."""
    with gzip.open(path, "rb") as handle:
        data = handle.read()
    dims = data[3]
    shape = [int.from_bytes(data[4 + 4 * k : 8 + 4 * k], "big") for k in range(dims)]
    return np.frombuffer(data, np.uint8, offset=4 + 4 * dims).reshape(shape)


def glyph_source(data: Path, split: str) -> ss.Glyphs:
    m = "train" if split == "train" else "t10k"
    e = "train" if split == "train" else "test"
    mnist = ss.ArrayGlyphs(
        idx(data / f"mnist/{m}-images-idx3-ubyte.gz"), idx(data / f"mnist/{m}-labels-idx1-ubyte.gz")
    )
    # EMNIST stores its images transposed.
    em_img = idx(data / f"emnist/emnist-digits-{e}-images-idx3-ubyte.gz").transpose(0, 2, 1)
    emnist = ss.ArrayGlyphs(em_img, idx(data / f"emnist/emnist-digits-{e}-labels-idx1-ubyte.gz"))
    return ss.MixedGlyphs(
        [(emnist, 0.55), (mnist, 0.2), (ss.StrokeGlyphs(), 0.2), (ss.HersheyGlyphs(), 0.05)]
    )


_GLYPHS: dict[str, ss.Glyphs] = {}


def _init(data: str) -> None:
    for split in ("train", "test"):
        _GLYPHS[split] = glyph_source(Path(data), split)


def perturbed(h: np.ndarray, rng: np.random.Generator, box: sf.Rect) -> np.ndarray:
    """A mapping off by a little: shifted, turned and scaled about the block's middle (mm)."""
    cx, cy = box.x + box.w / 2, box.y + box.h / 2
    a = np.radians(rng.normal(0, 0.25))
    k = 1 + rng.normal(0, 0.003)
    dx, dy = rng.normal(0, 0.3, 2)
    c, s = np.cos(a) * k, np.sin(a) * k
    t = np.array([[c, -s, cx - c * cx + s * cy + dx], [s, c, cy - s * cx - c * cy + dy], [0, 0, 1]])
    return h @ t


def sheet_samples(job: tuple[int, str]) -> tuple[np.ndarray, np.ndarray]:
    """One synthetic sheet's cells: patches (uint8) and classes; blanks thinned out."""
    seed, split = job
    rng = np.random.default_rng(seed)
    content = ss.random_content(rng, fill=float(rng.uniform(0.5, 0.9)))
    page = ss.page(content, _GLYPHS[split], rng, s=8.0)
    photo = ss.photograph(page, rng, width=int(rng.integers(1300, 3800)), crop=0.0)
    gray = ss.cv2.cvtColor(photo.image, ss.cv2.COLOR_BGR2GRAY)
    reg = sr.Registration(photo.h_page, width=gray.shape[1], height=gray.shape[0])
    for no, b in sf.BLOCKS.items():
        reg.blocks[no] = perturbed(photo.h_page, rng, b.rect)
    cells = [c for c in sf.CELLS if c.kind != "mark"]
    truth = ss.truth_cells(content)
    patches, _ = sc.cut_cells(gray, reg, cells)
    labels = np.array([sc.class_of(truth[c.key]) for c in cells], np.int64)
    keep = (labels != sc.BLANK) | (rng.random(len(labels)) < 0.18)
    return (np.clip(patches[keep] * 255, 0, 255).astype(np.uint8), labels[keep])


def build(seeds: list[int], split: str, data: Path, workers: int) -> tuple[np.ndarray, np.ndarray]:
    """The cells of these sheets; kept in DATA_DIR/cache (the same seeds give the same cells)."""
    cache = data / "cache" / f"{split}-{seeds[0]}-{len(seeds)}.npz"
    if cache.exists():
        with np.load(cache) as f:
            print(f"{split}: {len(f['y'])} cells from {cache.name}", flush=True)
            return f["x"], f["y"]
    started = time.perf_counter()
    with mp.get_context("fork").Pool(workers, initializer=_init, initargs=(str(data),)) as pool:
        parts = pool.map(sheet_samples, [(s, split) for s in seeds], chunksize=2)
    x = np.concatenate([p[0] for p in parts])
    y = np.concatenate([p[1] for p in parts])
    print(
        f"{split}: {len(seeds)} sheets, {len(y)} cells in {time.perf_counter() - started:.0f} s; "
        f"blank {np.mean(y == sc.BLANK):.0%}, X {np.mean(y == sc.X):.1%}",
        flush=True,
    )
    cache.parent.mkdir(exist_ok=True)
    np.savez(cache, x=x, y=y)
    return x, y


def model():  # type: ignore[no-untyped-def]
    """Three stages of 3 x 3 convolutions (24, 48, 96 channels), then two dense layers: 0.9 M
    weights, a few milliseconds per cell on a CPU. The position of each digit survives (no
    global pooling): "12" is not "21"."""
    import torch.nn as nn

    def block(a: int, b: int) -> list[nn.Module]:
        return [nn.Conv2d(a, b, 3, padding=1), nn.BatchNorm2d(b), nn.ReLU(inplace=True)]

    return nn.Sequential(
        *block(1, 24),
        nn.MaxPool2d(2),  # 20 x 24
        *block(24, 48),
        *block(48, 48),
        nn.MaxPool2d(2),  # 10 x 12
        *block(48, 96),
        *block(96, 96),
        nn.MaxPool2d(2),  # 5 x 6
        nn.Flatten(),
        nn.Dropout(0.3),
        nn.Linear(96 * 5 * 6, 256),
        nn.ReLU(inplace=True),
        nn.Dropout(0.3),
        nn.Linear(256, sc.CLASSES),
    )


def augment(x, rng):  # type: ignore[no-untyped-def]
    """On the fly: a little contrast and noise, and a pixel or two of shift."""
    import torch

    n = x.shape[0]
    gain = torch.from_numpy(rng.uniform(0.7, 1.25, (n, 1, 1, 1)).astype(np.float32))
    noise = torch.from_numpy(rng.normal(0, 0.04, x.shape).astype(np.float32))
    x = (x * gain + noise).clamp(0, 1)
    dx, dy = int(rng.integers(-2, 3)), int(rng.integers(-2, 3))
    return torch.roll(x, shifts=(dy, dx), dims=(2, 3))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--sheets", type=int, default=700)
    parser.add_argument("--val-sheets", type=int, default=60)
    parser.add_argument("--epochs", type=int, default=14)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    import torch
    import torch.nn.functional as F

    torch.manual_seed(args.seed)
    torch.set_num_threads(args.workers)
    rng = np.random.default_rng(args.seed)
    base = args.seed * 100_000
    xt, yt = build(list(range(base, base + args.sheets)), "train", args.data, args.workers)
    xv, yv = build(
        list(range(base + 50_000, base + 50_000 + args.val_sheets)), "test", args.data, args.workers
    )
    xt_t = torch.from_numpy(xt).unsqueeze(1)
    yt_t = torch.from_numpy(yt)
    xv_t = torch.from_numpy(xv).unsqueeze(1).float() / 255
    yv_t = torch.from_numpy(yv)

    net = model()
    opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-4)
    steps = args.epochs * (len(yt) // 256 + 1)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=steps)
    started = time.perf_counter()
    for epoch in range(args.epochs):
        net.train()
        order = torch.from_numpy(rng.permutation(len(yt)))
        total = 0.0
        for k in range(0, len(order), 256):
            idx_ = order[k : k + 256]
            x = augment(xt_t[idx_].float() / 255, rng)
            loss = F.cross_entropy(net(x), yt_t[idx_], label_smoothing=0.02)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            total += loss.item() * len(idx_)
        net.eval()
        with torch.no_grad():
            logits = torch.cat([net(xv_t[k : k + 1024]) for k in range(0, len(yv), 1024)])
        acc = float((logits.argmax(1) == yv_t).float().mean())
        print(
            f"epoch {epoch + 1}: loss {total / len(yt):.4f}, held-out {acc:.4f} "
            f"({time.perf_counter() - started:.0f} s)",
            flush=True,
        )
        torch.save(net.state_dict(), args.data / "cache" / "sheet-cells.pt")  # survives a crash

    # The temperature that makes the held-out probabilities honest (least log loss).
    with torch.no_grad():
        logits = torch.cat([net(xv_t[k : k + 1024]) for k in range(0, len(yv), 1024)])
    temps = np.exp(np.linspace(np.log(0.5), np.log(4.0), 60))
    nll = [float(F.cross_entropy(logits / t, yv_t)) for t in temps]
    temp = float(temps[int(np.argmin(nll))])
    probs = torch.softmax(logits / temp, 1)
    conf, pred = probs.max(1)
    right = pred == yv_t
    print(f"temperature {temp:.3f}; held-out accuracy {float(right.float().mean()):.4f}")
    for lo in (0.5, 0.8, 0.9, 0.95, 0.99):
        sel = conf >= lo
        if int(sel.sum()):
            print(
                f"  p >= {lo}: {float(sel.float().mean()):.1%} of cells, "
                f"{float(right[sel].float().mean()):.4f} right"
            )
    top3 = (torch.topk(probs, 3, 1).indices == yv_t[:, None]).any(1)
    print(f"  right answer in the top 3: {float(top3.float().mean()):.4f}")

    class WithSoftmax(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.net = net

        def forward(self, x):  # type: ignore[no-untyped-def]
            return torch.softmax(self.net(x) / temp, dim=1)

    export = WithSoftmax().eval()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        export,
        (torch.zeros(1, 1, sc.PATCH_H, sc.PATCH_W),),
        str(args.out),
        input_names=["ink"],
        output_names=["probs"],
        dynamic_axes={"ink": {0: "n"}, "probs": {0: "n"}},
        opset_version=17,
        dynamo=False,
    )
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes), SHA-256 {digest}")


if __name__ == "__main__":
    main()
