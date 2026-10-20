"""Fetch and build the weights of the players job (PLAN §10.5) into a folder.

    python scripts/ml/player_weights.py OUT_DIR

Run by Dockerfile.worker in a throw-away build stage (PyTorch and Ultralytics stay there; the
worker image gets the ONNX files only), or by hand in a venv with the same packages
(versions pinned in Dockerfile.worker). Every download is checked against its SHA-256.

- yolo26s.onnx: Ultralytics YOLO26s, COCO (AGPL-3.0, like WiePlay), from the official release
  asset, exported here to ONNX with a dynamic input size (the worker feeds the court's crop
  at 1280 px on its longer side). The export is not byte-identical from one build to the
  next (ONNX metadata), so the job records the file's SHA-256 with every run.
- yolo26s-pose.onnx: Ultralytics YOLO26s-pose, COCO keypoints (AGPL-3.0), exported the same
  way, for the action finder's player-at-the-ball cue (PLAN §10.4a, wieplay/ml/contact_pose.py).
- en_PP-OCRv3_det_mobile.onnx, en_PP-OCRv5_rec_mobile.onnx: PaddleOCR's text detector and
  English recogniser (Apache-2.0) as converted to ONNX by RapidOCR (Apache-2.0), for the
  shirt-number suggestion.

MANIFEST.json in OUT_DIR lists sources, licences and the SHA-256 of every file.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

YOLO = {
    "name": "yolo26s.pt",
    "url": "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26s.pt",
    "sha256": "646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b",
    "licence": "AGPL-3.0 (Ultralytics)",
}
POSE = {
    "name": "yolo26s-pose.pt",
    "url": "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26s-pose.pt",
    "sha256": "a083adb42303728ae14c4bd6bd56d80da46f82fb2564dbd6f31dcc92ea321646",
    "licence": "AGPL-3.0 (Ultralytics)",
}
RAPIDOCR = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx"
OCR = [
    {
        "name": "en_PP-OCRv3_det_mobile.onnx",
        "url": f"{RAPIDOCR}/PP-OCRv4/det/en_PP-OCRv3_det_mobile.onnx",
        "sha256": "ea07c15d38ac40cd69da3c493444ec75b44ff23840553ff8ba102c1219ed39c2",
        "licence": "Apache-2.0 (PaddleOCR; ONNX by RapidOCR)",
    },
    {
        "name": "en_PP-OCRv5_rec_mobile.onnx",
        "url": f"{RAPIDOCR}/PP-OCRv5/rec/en_PP-OCRv5_rec_mobile.onnx",
        "sha256": "c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8",
        "licence": "Apache-2.0 (PaddleOCR; ONNX by RapidOCR)",
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(item: dict[str, str], target: Path) -> None:
    if not item["url"].startswith("https://"):
        raise SystemExit(f"refusing a non-https source: {item['url']}")
    partial = target.with_name(target.name + ".part")
    with urllib.request.urlopen(item["url"], timeout=120) as response:  # noqa: S310 - https only
        with partial.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    found = sha256(partial)
    if found != item["sha256"]:
        partial.unlink()
        raise SystemExit(f"{item['name']}: SHA-256 {found}, expected {item['sha256']}")
    partial.replace(target)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, str]] = {}
    for item in OCR:
        fetch(item, out / item["name"])
        manifest[item["name"]] = {
            "source": item["url"], "licence": item["licence"], "sha256": item["sha256"]
        }  # fmt: skip

    work = out / "build"
    work.mkdir(exist_ok=True)
    pt = work / YOLO["name"]
    fetch(YOLO, pt)
    from ultralytics import YOLO as Yolo  # the build stage only

    for item, name in ((YOLO, "yolo26s.onnx"), (POSE, "yolo26s-pose.onnx")):
        pt = work / item["name"]
        if not pt.exists():
            fetch(item, pt)
        exported = Path(Yolo(str(pt)).export(format="onnx", dynamic=True, simplify=True, opset=17))
        target = out / name
        exported.replace(target)
        manifest[target.name] = {
            "source": f"{item['url']} (sha256 {item['sha256']}), exported to ONNX (dynamic, opset 17)",
            "licence": item["licence"],
            "sha256": sha256(target),
        }
    shutil.rmtree(work)
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
