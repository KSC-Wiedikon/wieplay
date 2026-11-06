# How few rallies should WiePlay ask about? (November 2026)

The owner's check on 05.11.2026: no game had been finished in the review. A game without the
board's log or a typed sheet has no sure rally (PLAN §10.3), so every rally waits for a person.

## What prod had on 05.11.2026

- 21 videos of one team. The newest game is from 02.10.
- 2,937 rallies to check, all in the 19 games without a log or sheet: 74 to 265 per game, about
  155 on average, none of them sure.
- No game marked "Rallies done".
- 0 scoresheet photos, 2 typed sheets (games 409 and 509, both also with a log).

## The measurement

`scripts/ml/rally_asks_eval.py`, offline, on dumps of `scripts/ml/fused_dump.sql`. Games 409 (119
points) and 509 (173 points), the only ones with a trusted log, are *blinded*: only WiePlay's own
rallies, nothing a person answered, no log, sheet or board as evidence. The log is the reference
only. A simulated person answers the rally a strategy picks (its winner, or "no rally"), the game
is decoded again, and so on. On game 509 the paper's values are put back in the sheet: its
correction was made from the log.

| The coach does | 509 winners | 509 score line | 409 winners | 409 score line |
|---|---|---|---|---|
| Nothing | 153 (88 %) | 89 (51 %) | 102 (86 %) | 39 (33 %) |
| 20 answers, any rally | 156 (90 %) | 92 (53 %) | 103 (87 %) | 38 (32 %) |
| 20 answers, most doubtful | 158 (91 %) | 70 (40 %) | 106 (89 %) | 33 (28 %) |
| 20 answers, most doubtful per set | 160 (92 %) | 77 (45 %) | 109 (92 %) | 32 (27 %) |
| 40 answers, most doubtful per set | 164 (95 %) | 78 (45 %) | 112 (94 %) | 14 (12 %) |
| 60 answers, most doubtful per set | 170 (98 %) | 54 (31 %) | 116 (97 %) | 41 (34 %) |
| **20 answers, as built** (below) | **158 (91 %)** | **79 (46 %)** | **107 (90 %)** | **35 (29 %)** |
| Every rally answered | 173 (100 %) | 75 (43 %) | 119 (100 %) | 39 (33 %) |
| Every rally, and every missed rally | 173 (100 %) | 130 (75 %) | 119 (100 %) | 111 (93 %) |
| Typed sheet, no answers | 159 (92 %) | 147 (85 %) | 110 (92 %) | 107 (90 %) |
| Typed sheet, 20 answers | 165 (95 %) | 153 (88 %) | 117 (98 %) | 113 (95 %) |
| Typed sheet, 40 answers | 168 (97 %) | 159 (92 %) | 117 (98 %) | 113 (95 %) |
| **Typed sheet, 20 answers, as built** | **165 (95 %)** | **153 (88 %)** | **116 (97 %)** | **112 (94 %)** |

"As built": the real `ask_rallies` after each answer, answered in time order as the review walks
them. The other rows pick one rally at a time by doubt, a little better than a person can follow.

Side-out % against the log, in percentage points (us / them): nothing asked 3.3 / 5.3 and
3.1 / 6.3; every rally answered 1.9 / 3.1 and 0.0 / 1.2; typed sheet alone 1.4 / 0.8 and 0.1 / 1.7.

From the read sheet (§10.3, measured on 19.10.2026, one photo and one scan, not confirmed):
winners 88 % and 94 %, score line 76 % and 84 %. The reader is much weaker on other sheets.

## What it says

1. **Answers hardly spread.** Twenty answers fix three to seven winners. There are about 11 to
   12 % wrong winners to start with, so the answers mostly fix the rally they are about.
2. **The score line does not follow the winners.** With every winner right it stays at 33 to
   43 %. The finder misses 9 to 14 % of the points (24 in game 509, 11 in game 409), and the decode
   can only guess where they belong. Answers about those gaps alone do not help either: it takes
   both, and the decode's own suspicion of a gap is often wrong.
3. **WiePlay's doubt barely tells right from wrong.** Of the rallies whose answer it rates at least
   70 % (without the score line, `TEMPERATURE` 2), 3 of 58 and 3 of 25 are wrong. At `TEMPERATURE` 1,
   those it rates at least 90 % are wrong 8 of 84 and 1 of 30 times, and at least 95 % holds only
   34 and 7 rallies. So no "sure winner" shortcut exists: the sure rule stays as it is. Asking by
   doubt is still about twice as good as asking at random.
4. **Evidence beats questions.** A typed sheet does as much for the winners as 20 answers and is
   the only thing here that fixes the score line. A board log is better still (§10.3).

Limits: two games, one team; the serve reader may have trained on them; the sets and, on game 509,
the corrected rally bounds are kept as they are, so a real blind game is a bit worse off.

## Can "sure" be trusted with a sheet and answers?

The first count said no (3 of 25, 1 of 12), but it mixed WiePlay's own sure rallies with the ones
the simulated coach had answered. Separated, on the games above, up to 60 answers:

- **WiePlay's own sure rallies: no wrong winner, in any run.**
- Their score line was wrong in one rally on game 509 with the paper's sheet, at 7:6 against the
  log's 7:5: the scorer's known slip in set 4, gone with the corrected sheet. On game 409, one rally
  from 40 answers on, past the limit, one point off.
- **A rally a person answered counts as sure too, and there it means only its winner.** Its score
  line was often wrong: at 20 answers 12 of 14 and 16 of 17 without a sheet, 5 of 10 and 1 of 11 with
  the paper's sheet. The recap's score bug took such a rally's score as a fact. Now it takes the score
  of an answered rally only where the log or the sheet names it (`core/recap.py` `_line_sure`).
  The rest of the app shows a score from the decode as WiePlay's anyway (`score_source` "model").

## What was built

The owner's decision on 05.11.2026: a limit, and the sheet first. Their old games have no paper
sheets left.

- **`ASK_RALLIES` = 20** (`core/scoring.py` `choose_rally_checks`): a game without a trusted log
  asks about the most doubtful rallies per set, sets in turn, as measured (most doubtful per set).
  Chosen again on every scoring, so each answer leaves room for the next. A rally a person touched
  and left open stays asked (an undo). A game with a trusted log asks about every unsure rally:
  there are few (32 and 56 on these games).
- **The limit holds with a sheet too.** A typed sheet alone leaves 130 to 170 rallies unsure in a
  game; without the limit, entering it would bring those questions back and punish the very thing
  we ask for. It also holds for a sheet read from a photo.
- **The others stay WiePlay's guesses** (`tagging.rally_guess`): review "auto", `score_sure` not
  true, no "check" flag, marked "Guess". They count in the stats, which name them; "Sure only"
  leaves them out.
- **A game with guesses is finished without "Rallies done"**, which still means that every rally
  is right: it is refused while guesses remain, and such a game is not training data. A person can
  still correct any guess in the rally list.
- **The sheet first**: the review of a league game without a log asks for the scoresheet above the
  first rally, without blocking it. "No scoresheet" answers it in one tap.
- `wieplay.cli asks` chooses again in every game nobody marked done, without scoring again.
