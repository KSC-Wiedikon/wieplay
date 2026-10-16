"""Spike S0: run off-the-shelf ball trackers on a clip, unchanged (no fine-tuning).

    python s0_ball.py CLIP --model vball|vballfast|wasb|tracknet [--step 2] [--overlay 60] OUT

Writes OUT/<clip>.<model>.csv (frame, vis, x, y, score in source pixels) and a timing line
to OUT/timing.jsonl; with --overlay N also a 960x540 H.264 preview of the first N seconds.
"""

import argparse
import json
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

try:
    import torch
except ImportError:  # the ONNX models run without it (CPU test on the Lenovo)
    torch = None

W, H = 512, 288
HERE = Path(__file__).resolve().parent
REPOS = HERE / "repos"
WEIGHTS = HERE / "weights"


class Vball:
    group = 3

    def __init__(self, fast: bool) -> None:
        import onnxruntime as ort

        if torch is not None:
            ort.preload_dlls()  # CUDA/cuDNN from the torch wheels

        name = "VballNetFastV1_155_h288_w512.onnx" if fast else "VballNetV1_150_h288_w512.onnx"
        self.sess = ort.InferenceSession(
            str(WEIGHTS / "vballnet/vb-models" / name),
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        self.inp = self.sess.get_inputs()[0].name

    def __call__(self, groups: np.ndarray) -> np.ndarray:
        # groups: (B, 3, H, W, 3) uint8 RGB -> (B, 3, H, W) heatmaps in 0..1
        x = groups.astype(np.float32) / 255.0
        x = x.transpose(0, 1, 4, 2, 3).reshape(len(x), 9, H, W)
        return self.sess.run(None, {self.inp: x})[0]


class Wasb:
    group = 3

    def __init__(self) -> None:
        sys.path.insert(0, str(REPOS / "WASB-SBDT/src"))
        from models import build_model
        from omegaconf import OmegaConf

        cfg = OmegaConf.load(REPOS / "WASB-SBDT/src/configs/model/wasb.yaml")
        self.model = build_model({"model": cfg})
        ck = torch.load(WEIGHTS / "wasb_volleyball.pth.tar", map_location="cpu", weights_only=False)
        self.model.load_state_dict(ck["model_state_dict"])
        self.model.cuda().eval().half()
        self.mean = torch.tensor([0.485, 0.456, 0.406], device="cuda").view(1, 1, 3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225], device="cuda").view(1, 1, 3, 1, 1)

    def __call__(self, groups: np.ndarray) -> np.ndarray:
        with torch.inference_mode():
            return self._run(groups)

    def _run(self, groups: np.ndarray) -> np.ndarray:
        x = torch.from_numpy(groups).cuda().permute(0, 1, 4, 2, 3).float() / 255.0
        x = ((x - self.mean) / self.std).reshape(len(groups), 9, H, W).half()
        return self.model(x)[0].float().sigmoid().cpu().numpy()


class TrackNet:
    group = 8

    def __init__(self, median: np.ndarray) -> None:
        sys.path.insert(0, str(REPOS / "TrackNetV3"))
        from model import TrackNet as Net

        ck = torch.load(WEIGHTS / "beach_tracknet_best.pt", map_location="cpu", weights_only=False)
        self.model = Net(in_dim=27, out_dim=8)
        self.model.load_state_dict(ck["model"])
        self.model.cuda().eval().half()
        self.median = torch.from_numpy(median).cuda().permute(2, 0, 1).float() / 255.0

    def __call__(self, groups: np.ndarray) -> np.ndarray:
        with torch.inference_mode():
            return self._run(groups)

    def _run(self, groups: np.ndarray) -> np.ndarray:
        x = torch.from_numpy(groups).cuda().permute(0, 1, 4, 2, 3).float() / 255.0
        x = x.reshape(len(groups), 24, H, W)
        bg = self.median.unsqueeze(0).expand(len(groups), -1, -1, -1)
        return self.model(torch.cat([bg, x], 1).half()).float().cpu().numpy()


def peak(hm: np.ndarray, threshold: float = 0.5) -> tuple[int, float, float, float]:
    """Largest blob above the threshold, as in the reference code: (vis, x, y, score)."""
    score = float(hm.max())
    if score < threshold:
        return 0, -1.0, -1.0, score
    n, labels, stats, cents = cv2.connectedComponentsWithStats((hm >= threshold).astype(np.uint8))
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return 1, float(cents[i][0]), float(cents[i][1]), score


def median_frame(path: str, samples: int = 300) -> np.ndarray:
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    for idx in np.linspace(0, total - 1, samples).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, f = cap.read()
        if ok:
            frames.append(cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2RGB), (W, H)))
    return np.median(np.stack(frames), 0).astype(np.uint8)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("out")
    ap.add_argument("--model", required=True, choices=["vball", "vballfast", "wasb", "tracknet"])
    ap.add_argument("--step", type=int, default=1, help="use every n-th frame (2: 60 -> 30 fps)")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--overlay", type=float, default=0, help="seconds of preview video")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{Path(a.clip).stem}.{a.model}.s{a.step}"
    cap = cv2.VideoCapture(a.clip)
    fps = cap.get(cv2.CAP_PROP_FPS)
    sw, sh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    sx, sy = sw / W, sh / H

    t0 = time.perf_counter()
    if a.model == "tracknet":
        net = TrackNet(median_frame(a.clip))
    elif a.model == "wasb":
        net = Wasb()
    else:
        net = Vball(fast=a.model == "vballfast")
    t_load = time.perf_counter() - t0

    preview = None
    if a.overlay:
        preview = subprocess.Popen(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-s",
                "960x540",
                "-r",
                str(fps / a.step),
                "-i",
                "-",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "26",
                "-pix_fmt",
                "yuv420p",
                str(out / f"{tag}.preview.mp4"),
            ],
            stdin=subprocess.PIPE,
        )
    keep = int(a.overlay * fps)
    rows: list[tuple[int, int, float, float, float]] = []
    pending: list[tuple[int, np.ndarray]] = []  # (source frame index, small RGB)
    shown: dict[int, np.ndarray] = {}
    trail: deque = deque(maxlen=12)
    t_decode = t_infer = 0.0
    group, batch = net.group, a.batch

    def flush(items: list[tuple[int, np.ndarray]]) -> None:
        nonlocal t_infer
        n = len(items) // group
        if n == 0:
            return
        arr = np.stack([f for _, f in items[: n * group]]).reshape(n, group, H, W, 3)
        t = time.perf_counter()
        hms = net(arr)
        t_infer += time.perf_counter() - t
        for g in range(n):
            for k in range(group):
                idx = items[g * group + k][0]
                vis, x, y, score = peak(hms[g, k])
                rows.append((idx, vis, x * sx if vis else -1, y * sy if vis else -1, score))
                if preview and idx in shown:
                    img = shown.pop(idx)
                    trail.append((int(x * 960 / W), int(y * 540 / H)) if vis else None)
                    for p in list(trail)[:-1]:
                        if p:
                            cv2.circle(img, p, 3, (255, 160, 0), -1)
                    if trail[-1]:
                        cv2.circle(img, trail[-1], 7, (0, 0, 255), 2)
                    cv2.putText(
                        img,
                        f"{a.model} {score:.2f}",
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (255, 255, 255),
                        2,
                    )
                    preview.stdin.write(img.tobytes())

    idx = -1
    while True:
        t = time.perf_counter()
        ok, frame = cap.read()
        t_decode += time.perf_counter() - t
        if not ok:
            break
        idx += 1
        if idx % a.step:
            continue
        pending.append(
            (
                idx,
                cv2.resize(
                    cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), (W, H), interpolation=cv2.INTER_AREA
                ),
            )
        )
        if preview and idx < keep:
            shown[idx] = cv2.resize(frame, (960, 540), interpolation=cv2.INTER_AREA)
        if len(pending) >= group * batch:
            flush(pending)
            pending = []
    flush(pending)  # a last incomplete group is dropped
    if preview:
        preview.stdin.close()
        preview.wait()

    with open(out / f"{tag}.csv", "w") as f:
        f.write("frame,vis,x,y,score\n")
        for r in rows:
            f.write(f"{r[0]},{r[1]},{r[2]:.1f},{r[3]:.1f},{r[4]:.3f}\n")
    n = len(rows)
    info = {
        "clip": Path(a.clip).stem,
        "model": a.model,
        "step": a.step,
        "frames": n,
        "source_fps": fps,
        "load_s": round(t_load, 1),
        "decode_s": round(t_decode, 1),
        "infer_s": round(t_infer, 1),
        "infer_fps": round(n / t_infer, 1) if t_infer else None,
        "detected": round(sum(r[1] for r in rows) / max(n, 1), 3),
        "gpu_mem_mb": round(torch.cuda.max_memory_allocated() / 2**20)
        if torch and torch.cuda.is_available()
        else None,
    }
    with open(out / "timing.jsonl", "a") as f:
        f.write(json.dumps(info) + "\n")
    print(json.dumps(info))


if __name__ == "__main__":
    main()
