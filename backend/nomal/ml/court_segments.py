"""A camera that moves during a match (PLAN §10.6): the video's time split into setups,
each with its own view of the court.

    a frame every ~30 s ─ORB features─▶ each matched to one anchor frame (RANSAC
    homography) ─▶ where probe points on the floor land, smoothed over five frames ─▶ a
    new setup where they wander more than SHIFT_PX from the setup's own position

Two ways a camera moves, both seen on the H3 games: a *bump* (someone touches the tripod:
the picture jumps between two frames 30 s apart by more than BUMP_PX; the moment is then
narrowed down by halving, a frame grabbed at the middle again and again, to about a
second), and a *drift* (a tripod head that sags: tens of pixels over a match; a new setup
each time it adds up to SHIFT_PX). Single frames also wobble by a pixel or two (the
matching, the lens' stabiliser), hence the smoothing.

A camera carried to another spot (a tournament's next court, a new tripod place) shows
another *view*: its frames match none of the first view's, so they get an anchor of
their own, and the court is found in each view (the court job).

Players move, the hall does not: RANSAC keeps the hall's matches. A frame that matches
nothing (someone in front of the lens, a dark frame) is skipped. Setups shorter than
MIN_SETUP_MS (the camera being put up or taken down) are merged into their neighbours.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from nomal.ml import court_register as reg
from nomal.ml.court_register import Arr, Features

SHIFT_PX = 4.0  # at 1280x720: the court's lines move by more than a line's width
BUMP_PX = 8.0  # between two frames ~30 s apart: a jump, not a drift
SMOOTH = 2  # frames on each side in the running median
MIN_SETUP_MS = 90_000
MAX_VIEWS = 4
MIN_VIEW_FRAMES = 4  # frames matching an anchor to make it a view of its own
HALVINGS = 5  # 30 s → ~1 s


@dataclass
class Setup:
    from_ms: int
    to_ms: int
    samples: list[int] = field(default_factory=list)  # indices of the frames in it
    shift_px: float = 0.0  # how far the picture moved at its start (from the setup before)
    start: str = "start"  # start | bump | drift | moved (to another view)
    to_anchor: Arr | None = None  # 3x3: its picture (pixels) → its view's anchor frame's
    view: int = 0  # which view of the hall (0: the one most frames match)


def _offsets(h: Arr, probe: Arr) -> Arr:
    """Where the probe points land through h, minus where they are: (P, 2)."""
    out: Arr = reg.apply(h, probe) - probe
    return out


def _views(usable: list[int], feats: list[Features | None]) -> list[tuple[int, dict[int, Arr]]]:
    """The views of the hall in a video: each an anchor frame and the frames that match it
    (their homographies onto it). Usually one; a camera taken to another spot (a tournament,
    a new tripod place) gives another, matched to its own anchor."""
    out: list[tuple[int, dict[int, Arr]]] = []
    remaining = list(usable)
    while remaining and len(out) < MAX_VIEWS:
        a = remaining[len(remaining) // 2]
        fa = feats[a]
        assert fa is not None
        members: dict[int, Arr] = {a: np.eye(3)}
        for i in remaining:
            f = feats[i]
            if i == a or f is None:
                continue
            r = reg.register(f, fa)
            if r is not None:
                members[i] = r.h
        if len(members) < MIN_VIEW_FRAMES and out:
            break
        out.append((a, members))
        remaining = [i for i in remaining if i not in members]
    return out


def find(
    times_ms: list[int],
    feats: list[Features | None],
    duration_ms: int,
    *,
    grab: Callable[[int], Features | None] | None = None,
    shift_px: float = SHIFT_PX,
    min_setup_ms: int = MIN_SETUP_MS,
) -> list[Setup]:
    """The setups of a video from features of frames at `times_ms` (ascending), each with
    its view and its picture's homography onto that view's anchor frame. `grab` (features
    of the frame at a time) narrows down when the camera was bumped."""
    usable = [i for i, f in enumerate(feats) if f is not None]
    if not usable:
        return [Setup(0, duration_ms)]
    views = _views(usable, feats)
    first = feats[usable[0]]
    assert first is not None
    probe = reg.probe_grid(first.size)
    # Frames in time order with their view; runs of one view are split by the motion.
    seen = sorted((i, v, h) for v, (_, members) in enumerate(views) for i, h in members.items())
    runs: list[list[tuple[int, int, Arr]]] = []
    for item in seen:
        if runs and runs[-1][-1][1] == item[1]:
            runs[-1].append(item)
        else:
            runs.append([item])
    setups: list[Setup] = []
    for run in runs:
        view = run[0][1]
        anchor = feats[views[view][0]]
        assert anchor is not None
        part = _split(run, times_ms, anchor, probe, grab, shift_px, duration_ms)
        if setups:
            t_old = times_ms[setups[-1].samples[-1]]
            t_new = times_ms[part[0].samples[0]]
            setups[-1].to_ms = part[0].from_ms = (t_old + t_new) // 2
            part[0].start = "moved"
        setups.extend(part)
    setups[-1].to_ms = duration_ms
    return _merge_short(setups, min_setup_ms)


def _split(
    run: list[tuple[int, int, Arr]],
    times_ms: list[int],
    anchor: Features,
    probe: Arr,
    grab: Callable[[int], Features | None] | None,
    shift_px: float,
    duration_ms: int,
) -> list[Setup]:
    """Setups within a run of frames of one view: where the smoothed probe points wander
    more than shift_px from the setup's own position."""
    view = run[0][1]
    raw = np.stack([_offsets(h, probe) for _, _, h in run])  # (n, P, 2)
    n = len(run)
    smooth = np.stack(
        [np.median(raw[max(0, k - SMOOTH) : k + SMOOTH + 1], axis=0) for k in range(n)]
    )
    groups: list[list[int]] = [[0]]  # positions in the run
    for k in range(1, n):
        here = np.mean(smooth[groups[-1]], axis=0)
        gap = float(np.percentile(np.linalg.norm(smooth[k] - here, axis=1), 90))
        if gap <= shift_px:
            groups[-1].append(k)
            continue
        # Moved: the next frame must agree (one odd frame is a passer-by, not the camera).
        if k + 1 < n:
            ahead = float(np.percentile(np.linalg.norm(smooth[k + 1] - smooth[k], axis=1), 90))
            if ahead > shift_px:
                continue
        groups.append([k])
    setups: list[Setup] = []
    for g in groups:
        members = [run[k][0] for k in g]
        hs = np.stack([run[k][2] / run[k][2][2, 2] for k in g])
        setups.append(
            Setup(
                times_ms[members[0]],
                duration_ms,
                members,
                to_anchor=np.median(hs, axis=0),
                view=view,
            )
        )
    for a, b, ga, gb in zip(setups, setups[1:], groups, groups[1:], strict=False):
        before = np.mean(smooth[ga], axis=0)
        after = np.mean(smooth[gb], axis=0)
        b.shift_px = round(float(np.percentile(np.linalg.norm(after - before, axis=1), 90)), 1)
        last, first = ga[-1], gb[0]
        step = float(np.percentile(np.linalg.norm(raw[first] - raw[last], axis=1), 90))
        t_old, t_new = times_ms[run[last][0]], times_ms[run[first][0]]
        if step > BUMP_PX:
            b.start = "bump"
            at = _when(t_old, t_new, before, after, anchor, probe, grab)
        else:
            b.start = "drift"
            at = (t_old + t_new) // 2
        a.to_ms = b.from_ms = at
    return setups


def _when(
    old_ms: int,
    new_ms: int,
    before: Arr,
    after: Arr,
    anchor: Features,
    probe: Arr,
    grab: Callable[[int], Features | None] | None,
) -> int:
    """When the camera was bumped, between a frame still in the old setup and one in the
    new: the frame in the middle is nearer one or the other, again and again."""
    lo, hi = old_ms, new_ms
    if grab is not None:
        for _ in range(HALVINGS):
            if hi - lo <= 1000:
                break
            mid = (lo + hi) // 2
            f = grab(mid)
            r = reg.register(f, anchor) if f is not None else None
            if r is None:
                break
            at = _offsets(r.h, probe)
            if np.linalg.norm(at - before) <= np.linalg.norm(at - after):
                lo = mid
            else:
                hi = mid
    return (lo + hi) // 2


def _merge_short(setups: list[Setup], min_ms: int) -> list[Setup]:
    """Setups shorter than min_ms go to a neighbour (the longer one); their frames do not
    (the camera was moving)."""
    out = list(setups)
    while len(out) > 1:
        lengths = [s.to_ms - s.from_ms for s in out]
        k = min(range(len(out)), key=lambda i: lengths[i])
        if lengths[k] >= min_ms:
            break
        short = out.pop(k)
        if k == 0:
            out[0].from_ms = short.from_ms
        elif k == len(out):
            out[-1].to_ms = short.to_ms
        else:
            before, after = out[k - 1], out[k]
            if before.to_ms - before.from_ms >= after.to_ms - after.from_ms:
                before.to_ms = after.from_ms = short.to_ms
            else:
                after.from_ms = before.to_ms = short.from_ms
    out[0].from_ms = 0
    out[0].start = "start"
    out[0].shift_px = 0.0
    return out


def main_setup(setups: list[Setup], view: int | None = None) -> int:
    """The longest setup (of a view): its court stands for the whole video (the view)
    where one is enough."""
    pick = [i for i, s in enumerate(setups) if view is None or s.view == view]
    return max(pick, key=lambda i: setups[i].to_ms - setups[i].from_ms)
