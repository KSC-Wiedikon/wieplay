# Spike S0 scripts

Throwaway code for [`docs/research/spike-s0-2026-10.md`](../../docs/research/spike-s0-2026-10.md):
off-the-shelf ball trackers, person detection and whistle detection on our footage. Not part
of the app; kept so the numbers can be reproduced.

| Script | Runs on | Does |
|---|---|---|
| `survey.py` | Lenovo | resolution, fps, sound level per game, one frame each |
| `fw16-setup.sh` | Framework 16 | clones VballNet, WASB, TrackNetV3; venv with PyTorch (CUDA 12.8); then fetch the weights (see below) |
| `s0_ball.py` | Framework 16 (GPU) or any CPU (VballNet only) | one ball tracker over a clip: CSV, timing, optional preview video |
| `s0_report.py` | anywhere | detections, stuck spots, tracks, agreement; crop sheets for checking by eye |
| `s0_recall.py` | anywhere | relative recall against the union of all models' tracks |
| `s0_timeline.py` | anywhere | ball tracks and whistles of one clip on a time axis |
| `s0_people.py` | Framework 16 | YOLO person detection, one frame per second |
| `s0_whistle.py` | Lenovo (CPU) | whistle events of a clip or a whole match, optional spectrogram |

Weights (into `~/projects/wieplay-s0/weights/` next to the scripts):

- VballNet: the `vb-models` archive linked in its README (Google Drive), unpacked to
  `weights/vballnet/vb-models/`.
- WASB volleyball: the "WASB (Ours)" volleyball entry of its MODEL_ZOO, saved as
  `weights/wasb_volleyball.pth.tar`.
- TrackNetV3 beach: `tracknet_best.pt` from `deadfast/beach-volley-vision-models` on Hugging
  Face, saved as `weights/beach_tracknet_best.pt`.

Example, as run on 16.10.2026:

```bash
python s0_ball.py clips/good-legends.mp4 out --model vballfast --step 2 --overlay 30
python s0_report.py out clips --crops 20
python3 s0_whistle.py clips/good-legends.mp4 whistle/good-legends --plot 0:90
```

ONNX Runtime on the laptop: `onnxruntime-gpu==1.22.0` (CUDA 12, cuDNN from the PyTorch wheels
via `ort.preload_dlls()`); 1.31 wants CUDA 13 and silently falls back to the CPU.
