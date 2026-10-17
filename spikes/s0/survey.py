"""Spike S0: survey the H3 dataset (picture, sound) and save one frame per game.

python3 survey.py FRAMES_DIR
"""

import json
import subprocess
import sys
from pathlib import Path

D = Path("/mnt/nas/wieplay/datasets/youtube-h3/videos")
OUT = Path(sys.argv[1])


def run(*a):
    return subprocess.run(a, capture_output=True, text=True)


rows = []
for mp4 in sorted(D.glob("*.mp4")):
    info = json.loads(mp4.with_suffix(".info.json").read_text())
    pr = json.loads(
        run(
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(mp4)
        ).stdout
    )
    v = next(s for s in pr["streams"] if s["codec_type"] == "video")
    a = [s for s in pr["streams"] if s["codec_type"] == "audio"]
    dur = float(pr["format"]["duration"])
    fps = v.get("avg_frame_rate", "0/1")
    num, den = (int(x) for x in fps.split("/"))
    vols = []
    if a:
        for frac in (0.25, 0.5, 0.75):
            r = run(
                "ffmpeg",
                "-nostats",
                "-ss",
                str(dur * frac),
                "-t",
                "60",
                "-i",
                str(mp4),
                "-vn",
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            )
            mv = [ln for ln in r.stderr.splitlines() if "mean_volume" in ln]
            vols.append(mv[0].split(":")[1].strip() if mv else "?")
    run(
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-ss",
        str(dur * 0.4),
        "-i",
        str(mp4),
        "-frames:v",
        "1",
        "-vf",
        "scale=960:-2",
        str(OUT / f"{mp4.stem}.jpg"),
    )
    rows.append(
        {
            "file": mp4.name,
            "title": info.get("title", "")[:70],
            "res": f"{v['width']}x{v['height']}",
            "fps": round(num / den, 2) if den else 0,
            "min": round(dur / 60),
            "mbps": round(int(pr["format"]["bit_rate"]) / 1e6, 1),
            "audio": f"{a[0]['codec_name']} {a[0].get('channels')}ch" if a else "NONE",
            "mean_dB": vols,
        }
    )
for r in rows:
    print(json.dumps(r, ensure_ascii=False))
