"""Relative recall: frames where any of the three models has the ball in a plausible track
(the union) and which share of those each model finds on its own."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s0_report import load, stuck, tracked

out = Path(sys.argv[1])
for clip in sorted({p.name.split(".")[0] for p in out.glob("*.s2.csv")}):
    good, det = {}, {}
    for m in ["vballfast", "wasb", "tracknet"]:
        p = out / f"{clip}.{m}.s2.csv"
        if p.exists():
            det[m] = load(p)
            good[m] = tracked(det[m], 2, stuck(det[m], 2))
    if len(good) < 3:
        continue
    union = set().union(*good.values())
    n = 5400
    print(
        f"{clip}: ball in some track {len(union) / n:.0%} of frames;",
        ", ".join(f"{m} finds {len(set(det[m]) & union) / len(union):.0%}" for m in good),
    )
