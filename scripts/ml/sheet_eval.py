"""The scoresheet in the scoring, measured on synthetic sets (PLAN §10.3a, research note §4.2).

    uv run python ../scripts/ml/sheet_eval.py [--sets 300] [--seed 1]      (from backend/)

Random sets to 25 at a 60 % side-out rate; each rally's serve evidence points at the right end
line with probability `serve` (else at the wrong one), sure in between; a gap of 10-20 s before
each rally, three timeouts a set; some rallies missed by the rally finder and some extra ones
(replays, a proposal that is not a rally). Our side at the start is known, the first server is
not (the scoring picks it); no scoreboard or log. Per observed rally, the winner is right when
it matches the truth (an extra rally is right when it is called no point).

Three ways to score each set: the official final only (today), the sheet lined up with the
rallies, and the sheet as a soft hint only (a set the checks doubt). Prints the share of
winners right and the flags. Made-up data only.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from wieplay.ml import scoring as sc  # noqa: E402
from wieplay.ml import sheet as sh  # noqa: E402


@dataclass
class Case:
    st: sc.SetIn  # without the sheet
    path: sc.SheetPath
    truth: dict[str, str]  # rally key -> us | them | none
    first: sc.Side


def synthetic(
    rng: random.Random,
    *,
    side_out: float = 0.6,
    serve: float = 0.92,
    miss: float = 0.03,
    extra: float = 0.02,
    no: int = 1,
) -> Case:
    first: sc.Side = "us" if rng.random() < 0.5 else "them"
    winners = sh.play(rng, first, side_out)
    side0: sc.Court = "near" if rng.random() < 0.5 else "far"
    timeouts = set(rng.sample(range(5, len(winners) - 2), 3))
    rallies: list[sc.RallyObs] = []
    truth: dict[str, str] = {}
    server = first
    t = 30_000
    servers: list[sc.Side] = []
    for i, won in enumerate(winners):
        servers.append(server)
        t += int(rng.uniform(10_000, 20_000)) + (60_000 if i in timeouts else 0)
        length = int(rng.uniform(4_000, 12_000))
        if rng.random() >= miss:
            near_is_us = side0 == "near"
            server_near = (server == "us") == near_is_us
            right = rng.random() < serve
            p = rng.uniform(0.7, 0.98) if right else rng.uniform(0.02, 0.3)
            p_near = p if server_near else 1 - p
            key = f"{no}-{i}"
            rallies.append(sc.RallyObs(key, t, t + length, p_near, proposal=0.85))
            truth[key] = won
        t += length
        if rng.random() < extra:  # a replay or a proposal that is not a rally
            t += int(rng.uniform(8_000, 15_000))
            key = f"{no}-x{i}"
            rallies.append(sc.RallyObs(key, t, t + 6_000, rng.random(), proposal=0.6))
            truth[key] = "none"
            t += 6_000
        server = won
    final = (winners.count("us"), winners.count("them"))
    st = sc.SetIn(no=no, rallies=rallies, official=final, our_side=side0)
    path = sc.SheetPath(tuple(winners), tuple(servers), tuple(False for _ in winners))
    return Case(st, path, truth, first)


def right(out: sc.SetOut, truth: dict[str, str]) -> tuple[int, int, int]:
    """(winners right, rallies, flagged)."""
    ok = flagged = 0
    for r in out.rallies:
        said = r.won_by if r.point else "none"
        ok += int(said == truth[r.key])
        flagged += int(r.note is not None)
    return ok, len(out.rallies), flagged


def evaluate(cases: list[Case]) -> dict[str, tuple[float, float]]:
    totals = {"official": [0, 0, 0], "sheet": [0, 0, 0], "hint": [0, 0, 0]}
    for c in cases:
        outs = {
            "official": sc.score_match([c.st]).sets[0],
            "sheet": sc.score_match(
                [sc.SetIn(**{**c.st.__dict__, "sheet": c.path, "first_serve": c.first})]
            ).sets[0],
            "hint": sc.score_match(
                [sc.SetIn(**{**c.st.__dict__, "sheet_hint": c.path.hint()})]
            ).sets[0],
        }
        for name, out in outs.items():
            ok, n, flagged = right(out, c.truth)
            totals[name][0] += ok
            totals[name][1] += n
            totals[name][2] += flagged
    return {k: (v[0] / v[1], v[2] / v[1]) for k, v in totals.items()}


SCENARIOS = [
    ("complete, no extra rallies", {"miss": 0.0, "extra": 0.0}),
    ("3 % missed, 2 % extra (replays)", {"miss": 0.03, "extra": 0.02}),
    ("5 % missed, 3 % extra, serve reads 85 %", {"miss": 0.05, "extra": 0.03, "serve": 0.85}),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sets", type=int, default=300)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    print("| Rally list | Official score only | With the sheet | Sheet as a hint | Flagged (sheet) |")
    print("|---|---|---|---|---|")
    for name, kwargs in SCENARIOS:
        rng = random.Random(args.seed)
        cases = [synthetic(rng, **kwargs) for _ in range(args.sets)]  # type: ignore[arg-type]
        res = evaluate(cases)
        print(
            f"| {name} | {res['official'][0]:.1%} | {res['sheet'][0]:.1%} | "
            f"{res['hint'][0]:.1%} | {res['sheet'][1]:.1%} |"
        )


if __name__ == "__main__":
    main()
