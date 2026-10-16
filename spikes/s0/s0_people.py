"""Spike S0: off-the-shelf person detection (Ultralytics YOLO, COCO weights) on a clip.

    python s0_people.py CLIP OUT [--model yolo11m.pt] [--every 1.0] [--imgsz 1280]

One frame per `every` seconds: number of people found, the smallest box heights (far court),
speed; plus one annotated frame for a look.
"""

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("out")
    ap.add_argument("--model", default="yolo11m.pt")
    ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--imgsz", type=int, default=1280)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    model = YOLO(a.model)
    cap = cv2.VideoCapture(a.clip)
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    counts, heights, t_inf, shot = [], [], 0.0, None
    for idx in range(0, total, int(fps * a.every)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            break
        t = time.perf_counter()
        r = model.predict(frame, imgsz=a.imgsz, classes=[0], conf=0.25, half=True, verbose=False)[0]
        t_inf += time.perf_counter() - t
        boxes = r.boxes.xyxy.cpu().numpy()
        counts.append(len(boxes))
        heights.extend((boxes[:, 3] - boxes[:, 1]).tolist())
        if shot is None and idx >= total // 2:
            shot = r.plot(labels=False, line_width=1)
    stem = Path(a.clip).stem
    if shot is not None:
        cv2.imwrite(str(out / f"people-{stem}.jpg"), cv2.resize(shot, (1280, 720)))
    h = np.array(heights) if heights else np.zeros(1)
    info = {
        "clip": stem,
        "model": a.model,
        "imgsz": a.imgsz,
        "frames": len(counts),
        "people_median": float(np.median(counts)),
        "people_p10": float(np.percentile(counts, 10)),
        "box_h_p5": round(float(np.percentile(h, 5))),
        "box_h_median": round(float(np.median(h))),
        "infer_fps": round(len(counts) / t_inf, 1),
    }
    with open(out / "people.jsonl", "a") as f:
        f.write(json.dumps(info) + "\n")
    print(json.dumps(info))


if __name__ == "__main__":
    main()
