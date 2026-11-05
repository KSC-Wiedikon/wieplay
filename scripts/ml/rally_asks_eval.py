"""How few rallies does WiePlay have to ask about? (PLAN §10.3, §10.0)

A game without a board log or a typed sheet has no sure rally, so every rally waits for a person.
This measures, on the games whose log says what really happened, what the video and the official
score alone get right, how much each answer adds, and how far WiePlay's doubt can be trusted.

Offline, nothing here writes to a database or a bucket. Input: dumps of `scripts/ml/fused_dump.sql`
(club data: keep them outside git, delete them after the measurement).

    cd backend && uv run python ../scripts/ml/rally_asks_eval.py DUMP.json [DUMP.json ...] \
        [--asks 0,5,10,20,40] [--strategies doubt,random] [--json OUT.json]

Each game is blinded first: only WiePlay's own rallies, everything a person answered removed, no
log, sheet or board as evidence. The log is the reference only. A simulated person then answers
the rally a strategy picks (its winner, or "no rally"), the game is decoded again, and so on.

    doubt    the rally whose answer the decode is least sure of
    line     the rally whose answer with its score line the decode is least sure of
    spread   the most doubtful rally of the set with the fewest answers so far
    random   any rally (the baseline; seeded)

`sides-` before a strategy answers who served first and our side once for the whole game first.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fused_eval as fe

from wieplay.core import scoring as core_scoring
from wieplay.ml import scoring as sc

Ref = dict[str, tuple[str, tuple[int, int] | None]]
THRESHOLDS = (0.6, 0.7, 0.8, 0.9, 0.95, 0.97, 0.99)


def blind(dump: dict[str, Any]) -> dict[str, Any]:
    """The game as if nobody had answered anything: WiePlay's rallies, none of a person's facts."""
    d = copy.deepcopy(dump)
    rallies = []
    for r in d["rallies"]:
        if r["kind"] != "rally" or r["source"] != "model":
            continue
        r.update(deleted_at=None, review="check", won_by=None, serving_manual=None)
        rallies.append(r)
    d["rallies"] = rallies
    d["serves"] = []
    for p in d["periods"]:
        p.update(first_serve=None, our_court_side_at_start=None)
    return d


def p_win(ro: sc.RallyOut) -> float:
    """How sure the decode is of this rally's answer, without its score line."""
    if not ro.point:
        return ro.p_none
    return ro.p_us if ro.won_by == "us" else ro.p_them


def answer_of(ro: sc.RallyOut) -> str:
    return (ro.won_by or "none") if ro.point else "none"


def outs_of(match: sc.MatchOut) -> list[sc.RallyOut]:
    return [ro for so in match.sets for ro in so.rallies]


def side_out(outs: list[sc.RallyOut]) -> dict[str, float | None]:
    """Side-out % per team: of the rallies the other team served, how many this team won."""
    out: dict[str, float | None] = {}
    for team in sc.SIDES:
        faced = [ro for ro in outs if ro.point and ro.serving == sc.other(team)]
        won = sum(1 for ro in faced if ro.won_by == team)
        out[team] = round(100 * won / len(faced), 1) if faced else None
    return out


class Game:
    def __init__(self, dump: dict[str, Any]) -> None:
        self.video = dump["video"]["id"][:8]
        self.game = dump["video"].get("game_id")
        blinded = blind(dump)
        self.set_ins, rallies, _, _ = fe.base_sets(blinded)
        ev = fe.log_evidence(blinded, rallies)
        if ev is None:
            raise ValueError(f"{self.video}: no log, so no reference")
        self.ref: Ref = fe.reference(ev, self.set_ins, {})
        truth = core_scoring.decide(self.set_ins, log_ev=ev, board=None).match
        self.truth_sides = {
            st.no: (so.our_side, so.first_serve)
            for st, so in zip(self.set_ins, truth.sets, strict=True)
        }
        self.truth_side_out = side_out(outs_of(truth))
        self.points = sum(1 for won, _ in self.ref.values() if won != "none")

    def decode(
        self, answers: dict[str, str], *, sides: bool = False, sure: float = sc.SURE
    ) -> sc.MatchOut:
        sets = []
        for st in self.set_ins:
            rallies = []
            for r in st.rallies:
                said = answers.get(r.key)
                if said == "none":  # "no rally": it leaves the set
                    continue
                rallies.append(replace(r, won_fact=said, proposal=None) if said else r)
            told = self.truth_sides[st.no] if sides else (None, None)
            sets.append(replace(st, rallies=rallies, our_side=told[0], first_serve=told[1]))
        return core_scoring.decide(sets, sure=sure).match


def pick(
    game: Game,
    outs: list[sc.RallyOut],
    answers: dict[str, str],
    strategy: str,
    rng: random.Random,
) -> str | None:
    """The next rally to ask about (the reference has to speak about it)."""
    open_ = [ro for ro in outs if ro.key not in answers and ro.key in game.ref]
    if not open_:
        return None
    if strategy == "random":
        return rng.choice(open_).key
    if strategy == "line":
        return min(open_, key=lambda ro: (ro.p_answer, ro.key)).key
    if strategy == "spread":
        set_of = {r.key: st.no for st in game.set_ins for r in st.rallies}
        asked: dict[int, int] = {}
        for key in answers:
            asked[set_of[key]] = asked.get(set_of[key], 0) + 1
        fewest = min(asked.get(set_of[ro.key], 0) for ro in open_)
        open_ = [ro for ro in open_ if asked.get(set_of[ro.key], 0) == fewest]
    return min(open_, key=lambda ro: (p_win(ro), ro.key)).key


def score(game: Game, match: sc.MatchOut, answers: dict[str, str], sure: float) -> dict[str, Any]:
    outs = [replace(ro, p_answer=ro.p_answer * so.sides_share) for so in match.sets
            for ro in so.rallies]  # fmt: skip
    s = fe.summary(outs, game.ref, sure)
    spoken = [ro for ro in outs if ro.key in game.ref]
    own_sure = sum(1 for ro in outs if ro.sure and ro.key not in answers)
    so = side_out(outs_of(match))
    return {
        "answers": len(answers),
        "winner_right": s["winner_right"],
        "points": s["points"],
        "line_right": s["line_right"],
        "answer_right": s["answer_right"],
        "told": s["told"],
        "sure_without_answer": own_sure,
        "wrong_sure": s["wrong_sure"],
        "sides_share": match.sides_confidence,
        "side_out_err": {
            team: round(abs((so[team] or 0.0) - (game.truth_side_out[team] or 0.0)), 1)
            for team in sc.SIDES
        },
        "bands": bands(spoken, game.ref, answers),
    }


def bands(outs: list[sc.RallyOut], ref: Ref, answers: dict[str, str]) -> list[dict[str, Any]]:
    """Trust: of the rallies nobody answered with p_win at least t, how many are wrong."""
    rows = []
    for t in THRESHOLDS:
        kept = [ro for ro in outs if ro.key not in answers and p_win(ro) >= t]
        wrong = sum(1 for ro in kept if answer_of(ro) != ref[ro.key][0])
        rows.append({"t": t, "rallies": len(kept), "wrong": wrong})
    return rows


def simulate(
    game: Game, strategy: str, checkpoints: list[int], sure: float, seed: int = 7
) -> list[dict[str, Any]]:
    sides = strategy.startswith("sides-")
    how = strategy.removeprefix("sides-")
    rng = random.Random(seed)  # noqa: S311 - a reproducible baseline
    answers: dict[str, str] = {}
    rows = []
    for n in range(max(checkpoints) + 1):
        match = game.decode(answers, sides=sides, sure=sure)
        if n in checkpoints:
            rows.append(score(game, match, answers, sure))
        key = pick(game, outs_of(match), answers, how, rng)
        if key is None:
            break
        answers[key] = game.ref[key][0]
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dumps", type=Path, nargs="+")
    parser.add_argument("--asks", default="0,5,10,15,20,30,40,60")
    parser.add_argument("--strategies", default="doubt,line,spread,random,sides-doubt,sides-spread")
    parser.add_argument("--sure", type=float, default=sc.SURE)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    checkpoints = [int(x) for x in args.asks.split(",")]
    results: dict[str, Any] = {}
    for path in args.dumps:
        game = Game(json.loads(path.read_text()))
        results[game.video] = {"game": game.game, "truth_side_out": game.truth_side_out}
        print(f"\n== game {game.game} ({game.video}): {game.points} points in the log, "
              f"side-out {game.truth_side_out}")  # fmt: skip
        for strategy in args.strategies.split(","):
            rows = simulate(game, strategy, checkpoints, args.sure)
            results[game.video][strategy] = rows
            print(f"-- {strategy}")
            print("asked | winner right | score line right | sure unasked | wrong sure | "
                  "sides | side-out err us/them")  # fmt: skip
            for r in rows:
                print(
                    f"{r['answers']:5} | {r['winner_right']:3}/{r['points']} "
                    f"({100 * r['winner_right'] / r['points']:3.0f} %) | "
                    f"{r['line_right']:3}/{r['points']} "
                    f"({100 * r['line_right'] / r['points']:3.0f} %) | "
                    f"{r['sure_without_answer']:4} | {r['wrong_sure']:3} | "
                    f"{r['sides_share']:.2f} | "
                    f"{r['side_out_err']['us']:4.1f} / {r['side_out_err']['them']:4.1f}"
                )
        first = results[game.video][args.strategies.split(",")[0]][0]
        print("-- trust with nothing asked: p(answer without its score line) at least t")
        for b in first["bands"]:
            print(f"   t {b['t']:.2f}: {b['rallies']:3} rallies, {b['wrong']:3} wrong")
    if args.json:
        args.json.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
