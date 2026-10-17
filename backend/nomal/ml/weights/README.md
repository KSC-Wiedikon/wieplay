# Vendored model weights

| File | Model | Source | Licence | SHA-256 |
|---|---|---|---|---|
| `VballNetFastV1_155_h288_w512.onnx` (49,982 bytes) | VballNet-Fast V1, epoch 155: TrackNetV4-based volleyball ball tracker, input 9×288×512 (three RGB frames), output 3×288×512 heatmaps | [asigatchov/vball-net](https://github.com/asigatchov/vball-net), the `vb-models` archive linked from its README (Google Drive), as downloaded for spike S0 on 16.10.2026 (repository HEAD then: `2de50e46`) | MIT, © 2025 Alexander Sigatchov ([`LICENSE-vballnet`](LICENSE-vballnet)) | `6985d90318dd309b295b0bfa7ba88169069cd81721c0c16a214466871025a0da` |
| `scoreboard-digits-v1.npz` (273,131 bytes) | The scoreboard's digit network (PLAN §10.3, step 5): one hidden layer of 128, 560 inputs (a 28×20 glyph), 11 classes (0-9, no digit), numpy arrays `w1 b1 w2 b2` (no pickle) | Trained here by `scripts/ml/scoreboard_digits.py` (120 000 glyphs cut from synthetic boards: seven segments, OpenCV's Hershey fonts, DejaVu, Liberation and FreeFont faces; seed 7) on 17.10.2026; held out: 98.9 % of digits right, 91.5 % of non-digits rejected | AGPL-3.0 (WiePlay's own; the fonts only drew the training pictures) | `2823e371d6cc6b30371fff3adf376dbbfeade8f4173090b1ed5316829c82c134` |

VballNet is used unchanged by `nomal/ml/ball.py`, which refuses a file with another SHA-256. Why this
model: [spike S0](../../../../docs/research/spike-s0-2026-10.md). A fine-tuned version
(PLAN §10.4) will get its own file and row here.
