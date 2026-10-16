"""Timeline of one clip: ball in a plausible track (per model) and detected whistles."""

import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from s0_report import load, stuck, tracked

res, wh, clip = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
fig, ax = plt.subplots(figsize=(16, 3.2))
for row, m in enumerate(["vballfast", "wasb", "tracknet"]):
    det = load(res / f"{clip}.{m}.s2.csv")
    good = tracked(det, 2, stuck(det, 2))
    t = np.array(sorted(good)) / 60.0
    ax.scatter(t, np.full(len(t), row), s=2, marker="|")
w = [(float(r["t"]), float(r["dur"])) for r in csv.DictReader(open(wh / f"{clip}.whistles.csv"))]
for s, d in w:
    ax.axvspan(s, s + d, color="red", alpha=0.5)
ax.set_yticks([0, 1, 2], ["VballNet-Fast", "WASB", "TrackNetV3 beach"])
ax.set_ylim(-0.7, 2.7)
ax.set_xlim(0, 180)
ax.set_xlabel("seconds (red = whistle)")
fig.tight_layout()
fig.savefig(res / f"timeline-{clip}.png", dpi=80)
