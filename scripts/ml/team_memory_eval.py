"""A team's memory measured (PLAN §10.5b; docs/research/team-memory-2026-10.md): how well the
usual rotations learnt from a team's other games predict a new game's, and what the players'
memory changes on the cards of a game.

    cd backend && uv run python ../scripts/ml/team_memory_eval.py lineups SHEET.json SHEET.json ... \
        [--setters 7,17] [--seeds 300]
    cd backend && uv run python ../scripts/ml/team_memory_eval.py cards CARDS.json CARDS.json ... \
        [--sheets SHEET.json ...]

`lineups`: every typed sheet (`{"sets": [...the stored sheet sets...], "official": [[home,
away], ...], "our_side": "home" | "away"}`, read-only from the database: `select json_build_object('sets', sets, ...) from
scoresheet`) is in turn the new game; the memory is learnt from the other sheets only (another
video, never the same one). Per set it predicts our server at each of our serves (the sheet's
truth: zone I of our six when we serve, substitutions applied) from

- nothing but the memory (the usual starting rotation);
- the coach's setter zone for the set (the Sets bar's starting rotation);
- one observed server (the first of our serves, e.g. a number read on the server);
- simulated readings at our serves (a share of serves observed, each right with the reader's
  measured reliability, else a random roster number; averaged over `--seeds`),

and how many of the six places of the usual order are right.

`cards`: per game the players run's cards (`json_agg` of `player_cluster` rows: id, team,
kind, confidence, ocr_number, ocr_confidence, detections, tracks, number, assigned_by) and
the numbers the memory has from the other games (their sheets' six and substitutions): how
much footage sits on cards whose read number the memory confirms, and the answers "Identify
players" needs to name 50/60/70 % of our footage with and without them (each person card
answered whole, a pieces card half of its 24 biggest pieces, the most footage first; no by-eye
labels: an upper bound with the cards' own purity).

Sheets and cards hold club members' shirt numbers: keep them outside git and delete them
after the measurement (PLAN §5.5).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from wieplay.core import scoresheet
from wieplay.ml import on_court
from wieplay.ml import sheet as sh
from wieplay.ml import team_prior as tp

# The server's number read on the server's track (PLAN §10.5, Legends near serves): 41 of 47.
READ_RIGHT = 41 / 47
# Share of our serves with a reading on the server (near serves only, about half; of those a
# label on 77 of 90 and a reading on 47 of 77).
READ_SHARE = 0.5 * 47 / 90


def load_sets(path: Path) -> list[tuple[sh.SheetSet, sh.Decoded]]:
    doc = json.loads(path.read_text())
    official = [tuple(x) for x in doc.get("official") or []]
    if doc.get("our_side") == "away":  # the game's sets are home:away
        official = [(b, a) for a, b in official]
    out = []
    for c in scoresheet.check(doc["sets"], official or None):
        if c.status != "consistent" or not c.sheet.lineup.get("us"):
            continue
        out.append((c.sheet, c.decoded))
    return out


def starts_of(sets: list[tuple[sh.SheetSet, sh.Decoded]], setters: set[int]) -> list[tp.SetStart]:
    out = []
    for s, _ in sets:
        lu = tuple(s.lineup.get("us") or ())
        found = [n for n in lu if n in setters]
        out.append(tp.SetStart(lu, found[0] if len(found) == 1 else None))
    return out


def our_serves(s: sh.SheetSet, d: sh.Decoded) -> list[tuple[int, int]]:
    """(our rotation, the server's number) at each point we served (the sheet's truth)."""
    out = []
    for lu in on_court.point_lineups(s, d):
        if lu.serving == "us" and lu.server is not None:
            out.append((lu.rotation["us"], lu.server))
    return out


SURE = 0.9


def accuracy(post: tp.SetPosterior, serves: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    """(right, total, right among the sure ones (p >= SURE), sure ones)."""
    right = total = sure_right = sure = 0
    for r, n in serves:
        best = post.best(r)
        total += 1
        if best is None:
            continue
        right += int(best[0] == n)
        if best[1] >= SURE:
            sure += 1
            sure_right += int(best[0] == n)
    return right, total, sure_right, sure


def places_right(prior: tp.OrderPrior, lineup: tuple[int | None, ...], setter: int | None) -> int:
    best = prior.consensus()
    if best is None:
        return 0
    i = tp.setter_index(lineup, setter)
    if i is not None:
        order = tp.turned(lineup, i)
        return sum(int(a == b) for a, b in zip(order, best, strict=True))
    return max(
        sum(int(a == b) for a, b in zip(tp.turned(lineup, k), best, strict=True)) for k in range(6)
    )


def lineups(args: argparse.Namespace) -> None:
    setters = {int(x) for x in args.setters.split(",") if x}
    games = {p.name: load_sets(p) for p in args.sheets}
    rng = random.Random(7)
    roster = sorted(
        {n for sets in games.values() for s, _ in sets for n in s.lineup["us"] or () if n}
    )
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    places = [0, 0]
    for name, sets in games.items():
        memory = [x for other, o in games.items() if other != name for x in starts_of(o, setters)]
        prior = tp.learn(memory)
        print(f"{name}: memory of {len(memory)} sets, orders {prior.as_json()['orders']}")
        for (s, d), start in zip(sets, starts_of(sets, setters), strict=True):
            serves = our_serves(s, d)
            if not serves:
                continue
            places[0] += places_right(prior, start.lineup, start.setter)
            places[1] += 6
            zone = None
            if start.setter is not None:
                zone = (tp.setter_index(start.lineup, start.setter) or 0) + 1
            rows = {
                "memory only": tp.posterior(prior),
                "setter zone": tp.posterior(prior, setter_zone=zone) if zone else None,
                "first server": tp.posterior(prior, [tp.Seen(serves[0][0], serves[0][1])]),
            }
            for label, post in rows.items():
                if post is None:
                    continue
                acc = accuracy(post, serves)
                for i, v in enumerate(acc):
                    totals[label][i] += v
                print(f"  set {s.no} {label:14s} {acc[0]}/{acc[1]} right, sure {acc[2]}/{acc[3]}")
            # Simulated readings at our serves.
            sim = [0, 0, 0, 0]
            bare = [0, 0, 0, 0]
            for _ in range(args.seeds):
                seen = []
                for r, n in serves:
                    if rng.random() < args.read_share:
                        number = n if rng.random() < args.read_right else rng.choice(roster)
                        seen.append(tp.Seen(r, number))
                acc = accuracy(tp.posterior(prior, seen), serves)
                for i, v in enumerate(acc):
                    sim[i] += v
                acc = accuracy(tp.posterior(tp.OrderPrior(), seen), serves)
                for i, v in enumerate(acc):
                    bare[i] += v
            for i, v in enumerate(bare):
                totals["readings, no memory"][i] += v
            label = "readings"
            for i, v in enumerate(sim):
                totals[label][i] += v
            print(
                f"  set {s.no} {label:14s} {sim[0] / args.seeds:.1f}/{sim[1] / args.seeds:.0f} right, "
                f"sure {sim[2] / args.seeds:.1f}/{sim[3] / args.seeds:.1f}"
            )
    print("\nall sets:")
    for label, (right, total, sure_right, sure) in totals.items():
        pct = 100 * right / total if total else 0
        spct = 100 * sure_right / sure if sure else 0
        print(
            f"  {label:14s} servers right {pct:.1f} % ({right}/{total}); sure (p >= {SURE}) "
            f"{100 * sure / total if total else 0:.1f} % of serves, {spct:.1f} % of them right"
        )
    if places[1]:
        print(
            f"  usual order: {places[0]}/{places[1]} places right ({100 * places[0] / places[1]:.0f} %)"
        )


# --- cards ----------------------------------------------------------------------------------------

SHOWN = 24
PIECES_GAIN = 0.5
UNCERTAIN = 0.5


def gain(c: dict[str, Any]) -> float:
    if c["kind"] == "pieces":
        sizes = sorted((int(t["n"]) for t in c["tracks"]), reverse=True)[:SHOWN]
        return PIECES_GAIN * sum(sizes)
    return float(c["detections"])


def answers_to(
    cards: list[dict[str, Any]], named0: set[str], targets: list[float]
) -> list[int | None]:
    """Answers until each target share of the footage is named (an upper bound: each answer
    names its card whole, a pieces card the share PIECES_GAIN of the pieces shown)."""
    total = sum(c["detections"] for c in cards)
    named = sum(c["detections"] for c in cards if c["id"] in named0)
    queue = sorted((c for c in cards if c["id"] not in named0), key=lambda c: -gain(c))
    out: list[int | None] = []
    k = 0
    for target in targets:
        while named < target * total and k < len(queue):
            named += gain(queue[k])
            k += 1
        out.append(k if named >= target * total else None)
    return out


def cards(args: argparse.Namespace) -> None:
    sheets = {p.name: load_sets(p) for p in args.sheets or []}
    for path in args.cards:
        rows = [c for c in json.loads(path.read_text()) if c["tracks"] and c["team"] == "us"]
        others = [sets for name, sets in sheets.items() if name.split(".")[0] not in path.name]
        known: dict[int, int] = defaultdict(int)
        for sets in others:
            seen = set()
            for s, _ in sets:
                seen |= {n for n in s.lineup["us"] or () if n is not None}
                seen |= {u.number_in for u in s.subs if u.side == "us" and u.number_in is not None}
            for n in seen:
                known[n] += 1
        games = len(others)
        prior = tp.number_prior([tp.NumberFacts(n, games=g) for n, g in known.items()], games)
        players = [c for c in rows if not c.get("not_player")]
        total = sum(c["detections"] for c in players)
        sure0 = {c["id"] for c in players if (c.get("confidence") or 0) >= UNCERTAIN}
        memory = set(sure0)
        for c in players:
            if c["kind"] != "person" or c.get("ocr_number") is None or c["id"] in sure0:
                continue
            p = prior.get(int(c["ocr_number"]))
            if (
                p is not None
                and known.get(int(c["ocr_number"]))
                and tp.recognised(c.get("ocr_confidence"), p)
            ):
                memory.add(c["id"])
        share0 = sum(c["detections"] for c in players if c["id"] in sure0) / total
        share1 = sum(c["detections"] for c in players if c["id"] in memory) / total
        targets = [0.5, 0.6, 0.7]
        print(f"{path.name}: {len(players)} cards of ours, memory from {games} other game(s)")
        print(
            f"  footage of ours on sure cards: {100 * share0:.1f} % -> {100 * share1:.1f} % "
            f"({len(memory) - len(sure0)} cards named by the memory)"
        )
        batch = 1 if memory - sure0 else 0  # "Accept all" for the recognised ones
        after = [None if a is None else a + batch for a in answers_to(players, memory, targets)]
        print(f"  answers to 50/60/70 %: {answers_to(players, sure0, targets)} -> {after}")
        for c in sorted(players, key=lambda c: -c["detections"]):
            if c["id"] in memory - sure0:
                print(
                    f"    memory names #{c['ocr_number']} ({c['detections']} detections, "
                    f"read {c.get('ocr_confidence')}, prior {prior.get(int(c['ocr_number'])):.2f})"
                )


def main() -> None:
    global SURE
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("lineups")
    a.add_argument("sheets", type=Path, nargs="+")
    a.add_argument("--setters", default="")
    a.add_argument("--seeds", type=int, default=300)
    a.add_argument("--read-share", type=float, default=READ_SHARE, help="serves with a reading")
    a.add_argument("--read-right", type=float, default=READ_RIGHT, help="readings right")
    b = sub.add_parser("cards")
    b.add_argument("cards", type=Path, nargs="+")
    b.add_argument("--sheets", type=Path, nargs="*")
    a.add_argument("--sure", type=float, default=SURE, help="a server this probable is sure")
    args = parser.parse_args()
    if args.cmd == "lineups":
        SURE = args.sure
    {"lineups": lineups, "cards": cards}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
