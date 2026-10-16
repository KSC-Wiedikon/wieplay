"""Spike S0: referee whistles from the audio (PLAN §10.1 v0), CPU only.

    python3 s0_whistle.py MEDIA OUT_PREFIX [--plot START:END]

A whistle is a loud, narrow tone between 2 and 4.5 kHz (its harmonics sit above), lasting
0.2-2.5 s. Per 23 ms frame: the band's share of the energy, how far the band's strongest bin
stands out of the band (tonality), and the band level against a rolling noise floor. Writes
OUT_PREFIX.whistles.csv and, with --plot, a spectrogram PNG with the detections marked.
"""

import argparse
import json
import subprocess

import numpy as np
from scipy.ndimage import median_filter

SR = 22050
N_FFT = 2048
HOP = 512
BAND = (2000.0, 4500.0)


def load_audio(path: str) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SR),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def spectrogram(x: np.ndarray) -> np.ndarray:
    win = np.hanning(N_FFT).astype(np.float32)
    n = 1 + (len(x) - N_FFT) // HOP
    out = np.empty((n, N_FFT // 2 + 1), dtype=np.float32)
    for i0 in range(0, n, 4096):  # chunks keep memory small for a 3 h match
        idx = np.arange(i0, min(n, i0 + 4096))[:, None] * HOP + np.arange(N_FFT)[None, :]
        out[i0 : i0 + len(idx)] = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2
    return out


def detect(spec: np.ndarray) -> tuple[list[dict], dict[str, np.ndarray]]:
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    band = (freqs >= BAND[0]) & (freqs <= BAND[1])
    eps = 1e-12
    e_band = spec[:, band].sum(1) + eps
    e_all = spec[:, freqs > 100].sum(1) + eps
    share = e_band / e_all
    peak = spec[:, band].max(1)
    tonal = 10 * np.log10(peak / (np.median(spec[:, band], 1) + eps))  # dB above the band median
    level = 10 * np.log10(e_band)
    floor = median_filter(level, size=int(10 * SR / HOP), mode="nearest")  # 10 s rolling floor
    rise = level - floor
    hit = (share > 0.25) & (tonal > 18) & (rise > 10)
    # close 2-frame gaps; runs under 0.2 s are mostly shoe squeaks and go first; a whistle that
    # broke up (a pea whistle warbles) is joined again when its parts are < 0.6 s apart
    hit = np.convolve(hit.astype(int), np.ones(3, int), "same") > 0
    runs = []
    i, n, dt = 0, len(hit), HOP / SR
    while i < n:
        if not hit[i]:
            i += 1
            continue
        j = i
        while j < n and hit[j]:
            j += 1
        if (j - i) * dt >= 0.2:
            if runs and (i - runs[-1][1]) * dt < 0.6:
                runs[-1][1] = j
            else:
                runs.append([i, j])
        i = j
    events = []
    for i, j in runs:
        dur = (j - i) * dt
        if dur <= 2.5:
            k = i + int(np.argmax(peak[i:j]))
            f0 = freqs[band][int(np.argmax(spec[k, band]))]
            events.append(
                {
                    "t": round(i * dt, 2),
                    "dur": round(dur, 2),
                    "hz": int(f0),
                    "rise_db": round(float(rise[i:j].max()), 1),
                    "tonal_db": round(float(tonal[i:j].max()), 1),
                }
            )
    return events, {"share": share, "tonal": tonal, "rise": rise}


def plot(
    spec: np.ndarray, feats: dict, events: list[dict], start: float, end: float, path: str
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    a, b = int(start * SR / HOP), int(end * SR / HOP)
    freqs = np.fft.rfftfreq(N_FFT, 1 / SR)
    keep = freqs <= 8000
    fig, ax = plt.subplots(
        2, 1, figsize=(16, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    t = np.arange(a, b) * HOP / SR
    ax[0].pcolormesh(
        t, freqs[keep], 10 * np.log10(spec[a:b, keep].T + 1e-12), shading="auto", cmap="magma"
    )
    ax[0].set_ylabel("Hz")
    for e in events:
        if start <= e["t"] <= end:
            ax[0].axvspan(e["t"], e["t"] + e["dur"], color="cyan", alpha=0.35)
    ax[1].plot(t, feats["rise"][a:b], lw=0.7, label="band level above floor (dB)")
    ax[1].plot(t, feats["tonal"][a:b], lw=0.7, label="tonality (dB)")
    ax[1].axhline(10, color="k", lw=0.5)
    ax[1].legend(loc="upper right", fontsize=8)
    ax[1].set_xlabel("s")
    fig.tight_layout()
    fig.savefig(path, dpi=80)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("media")
    ap.add_argument("out")
    ap.add_argument("--plot", default="")
    a = ap.parse_args()
    x = load_audio(a.media)
    spec = spectrogram(x)
    events, feats = detect(spec)
    with open(a.out + ".whistles.csv", "w") as f:
        f.write("t,dur,hz,rise_db,tonal_db\n")
        for e in events:
            f.write(f"{e['t']},{e['dur']},{e['hz']},{e['rise_db']},{e['tonal_db']}\n")
    if a.plot:
        s, e = (float(v) for v in a.plot.split(":"))
        plot(spec, feats, events, s, e, a.out + ".spectrogram.png")
    mins = len(x) / SR / 60
    print(
        json.dumps(
            {
                "media": a.media.split("/")[-1],
                "minutes": round(mins, 1),
                "whistles": len(events),
                "per_min": round(len(events) / mins, 2),
                "median_hz": int(np.median([e["hz"] for e in events])) if events else None,
            }
        )
    )


if __name__ == "__main__":
    main()
