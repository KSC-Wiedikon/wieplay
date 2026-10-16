#!/usr/bin/env bash
# Nomal spike S0 on the Framework 16: repos, Python env, weights. Idempotent.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
W=$HOME/projects/nomal-s0
mkdir -p "$W"/{repos,weights,clips,out}
cd "$W/repos"
[ -d vball-net ] || git clone -q --depth 1 https://github.com/asigatchov/vball-net
[ -d WASB-SBDT ] || git clone -q --depth 1 https://github.com/nttcom/WASB-SBDT
[ -d TrackNetV3 ] || git clone -q --depth 1 https://github.com/qaz812345/TrackNetV3
for r in vball-net WASB-SBDT TrackNetV3; do echo "$r $(git -C $r rev-parse --short HEAD)"; done
cd "$W"
[ -d .venv ] || uv venv -q --python 3.12 .venv
uv pip install -q --python .venv/bin/python --index-url https://download.pytorch.org/whl/cu128 torch torchvision
uv pip install -q --python .venv/bin/python onnxruntime-gpu opencv-python-headless numpy scipy pandas gdown ultralytics hydra-core omegaconf tqdm soundfile
.venv/bin/python - <<'PY'
import torch, onnxruntime as ort, cv2, ultralytics
print("torch", torch.__version__, "cuda", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else "-")
print("ort", ort.__version__, ort.get_available_providers())
print("cv2", cv2.__version__, "ultralytics", ultralytics.__version__)
PY
