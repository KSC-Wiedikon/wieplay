# Suggested actions without hand-tagged games (October 2026)

The owner asked on 20.10.2026: "can we automate tagging actions?". Until then WiePlay wrote
action suggestions (serve S, reception R, set E, attack A, block B, dig D, freeball F) only once
a coach had tagged two whole games and its held-out precision passed 0.70 (PLAN §10.4a). Prod
had no action tags, so nothing was ever suggested. This note measures the action finder on
hand-labelled rallies, adds cues, and sets the "sure" flag that lets a coach accept the safe
suggestions in one step, as in the rally review.

## Off the shelf

No open model fits a camera behind the end line. The volleyball action detectors on GitHub
(YOLO boxes for five classes trained on Roboflow's "volleyball-actions", CC BY 4.0) work per
frame on broadcast angles and have no dig or freeball. VNL-STES (CVPRW 2025) is broadcast too.
The backbone stays WiePlay's own: the ball's contacts plus volleyball's touch order (v0).

## Ground truth

- 65 rallies drawn at random, stratified by set and rally length: 40 of one match with a
  calibrated court and a trusted scoreboard log, 25 of a second match without a calibration
  (both teams in dark kits, a panning camera).
- Every contact was labelled by eye from 1080p frames (time ±0.1 s, skill, side of the net,
  sure or not), by Claude during development. 8 windows were not rallies. 3 rallies were
  dropped because their labels were written without the frames having been seen.
- **54 rallies, 353 contacts** remain (202 of them labelled sure).
- The labels and frames stay off the repository: real footage, and the repo is public.
- A suggestion is **fully right** when a labelled contact is within ±0.3 s and the skill and
  side match (`wieplay/ml/action_eval.py`).

## Results

Precision = share of suggestions fully right; recall = share of contacts with a fully right
suggestion. The first value is the calibrated match, the second the other one.

| Finder | Contacts found by a candidate | Action precision | Action recall | Action F1 |
|---|---|---|---|---|
| v0 as on main | 0.55 / 0.51 | 0.32 / 0.24 | 0.13 / 0.10 | 0.18 / 0.14 |
| + serve estimates | 0.71 / 0.70 | 0.47 / 0.46 | 0.23 / 0.23 | 0.30 / 0.31 |
| + player at the ball (pose) | 0.71 / 0.70 | 0.51 / 0.60 | 0.26 / 0.26 | 0.35 / 0.37 |

### What each cue adds

- **Serve estimates.** The ball tracker missed 17 of the first 30 serves, and the rules then
  called a later contact the serve. A near serve shows as a toss: a ball rising fast from low
  in the picture, with the hit 0.75–0.95 s after the toss's first frame (10 serves). A far serve
  is first seen at the net's height, 0.25–0.8 s after the hit. With the server known from the
  scoring, the first such candidate around the rally's start is the serve.
  - Serves right went from 17 of 54 to 38 of 52.
  - The rest of the rally followed: receptions right went from 4 to 17.
- **The player at the ball.** YOLO26s-pose (Ultralytics, AGPL-3.0, ONNX, CPU) runs on a
  640 px crop around the ball at each candidate. Three numbers come from the person nearest the
  ball:
  - the distance from the ball to their wrist or body;
  - how far a wrist is above the shoulders;
  - how many people stand around.

  As a contact detector, a logistic fit on one match and tested on the other gives an area
  under the ROC curve of 0.76 and 0.81. The ball's path alone gives 0.65 and 0.68. Precision
  rose by 4 and 14 points.
  - From behind, a reception's ball shows at the receiver's shoulders and the arms are often
    hidden. So the pose says little about the skill (accuracy 0.38–0.42 alone), and it is used
    for "a contact or not" only.
  - The side from the feet (65 of 90) was worse than the ball's own side evidence (79 of 107),
    so it was dropped.
- **The touch order and the scoring** stay as v0 has them: the server's side and the winner
  from the joint decode. A learned classifier over the same features (gradient boosting,
  leave one match out) did not beat the rules on this little data.
- **Roboflow's CC BY 4.0 crops** were not tried. Its broadcast views and five classes do not
  match this camera, and the pose cue already covers "someone is at the ball".

### Posteriors and "sure"

The rules' lattice is sampled: 300 paths at temperature 0.5, seeded by the rally. Each
suggestion gets the share of paths that put the same skill and side at its contact, and the
likeliest other answer. These posteriors order the suggestions but are not calibrated enough
on their own:

| Posterior | 0–0.2 | 0.2–0.4 | 0.4–0.6 | 0.6–0.8 | 0.8–1 |
|---|---|---|---|---|---|
| Fully right | 1 of 4 | 6 of 27 | 17 of 35 | 22 of 42 | 47 of 66 |

Even above 0.95 a set or an attack was wrong one time in three. "Sure" therefore needs the
rally's server to be sure (the joint decode is sure of the rally) and one of two cases:

- a near serve timed from its toss (all 12 right; the toss times it within ±0.28 s);
- the reception right after a serve, on the other side, 0.6–1.6 s after it, with a posterior
  of at least 0.85 and someone's hands at the ball (all 7 right; the receptions marked wrong
  came 2.5–3 s after the serve, where the real reception had been missed).

With the shipped pose weights, **19 of 174 suggestions are sure (11 %), and 0 of them are
wrong**. That covers about 5 % of all contacts. The thresholds were set on these same rallies,
so the zero is optimistic. The first coach answers on prod (`vs_coach` per video) are the
real check.

Far serves are never sure: their time is estimated within ±0.5 s.

## What the coach gets

- Suggestions in every analysed game from the start.
- In the Actions queue, "Needs you: N" first, each action with WiePlay's other likely answer
  ("Or maybe: Dig, them", "Maybe no contact here").
- "Accept all sure actions".
- The measured numbers above, shown as they are.

About four in ten contacts get a suggestion (a quarter a fully right one), and the coach still tags what is missing: most
blocks, attacks the tracker loses, and digs. Every answer still trains v1 once a game is marked
"Actions reviewed".

## Cost

- The pose cue: about 0.12 s per candidate on the Lenovo, roughly 3 minutes per match the
  first time. It is then cached next to the signals, so a rerun after a scoring takes seconds.
- Sampling the posteriors: under 10 s per match.
- Everything runs on the Lenovo. No image is kept, only three numbers per candidate.
