# Reading the first referee's hand signals (spike, 18.10.2026)

**Status: spike done, not built.** Three of five referees were read at ≥ 95 % precision, two
well below it; over all five 91.4 % at 61 % coverage, short of the bar (≥ 95 % at ≥ 60 %). This
note keeps the method, the numbers, the cases and what would make it work.

**The owner's question** (18.10.2026, with FIVB signal 2, "team to serve: extend the arm to the
side of the team that will serve"): "can we have the model review the 1 referee hands? to check
where he/she points the hand at? so assigning the point? only 1SR. and to also check for red
cards, replays, change of decision etc."

## Bottom line

- **Finding the referee and reading the pose works.** On a crop around the referee's stand
  (from the court's net posts), an off-the-shelf 2D pose model (Ultralytics YOLO26m-pose, COCO
  keypoints, AGPL-3.0 like WiePlay) found the first referee in 98.7–99.9 % of the frames around
  the rally ends of every game, with both arms' keypoints confident in 79–98 % of them. In H3's
  footage the referee is 97–124 px tall at 1080p, larger than the 40–80 px expected: the stand
  puts them at about the camera's height, 12–15 m away.
- **Near or far is not reliable enough from behind the end line.** The bar was ≥ 95 %
  precision at ≥ 60 % coverage. Over five games the rule answered 61 % of the rally ends (with a
  reference) and was
  right in **91.4 %** (341 of 373 judged). Three referees were read well (96.5 %, 96.5 %, 98.4 %,
  at 49–71 % coverage), two badly (85 % and 79 %): their "far" was right only two times in three.
  **"Near" alone was right in 97.8 %** (177 of 181) in every game, but it answers only about
  28 % of the rally ends (the arm towards the camera is the one gesture few others look like).
- **Replays, cards, changes of decision: nothing to measure.** In 828 rally ends no card was
  shown, and none of the 26 rallies the scoreboard's log calls "no point" showed the replay
  signal. "Both arms up" is common, but it is the "ball out" signal (30 of 152 rally ends in
  one game); from behind, at this size, "ball out" (forearms up, palms in) and "double fault /
  replay" (both thumbs up) look alike.
- **Not built.** What would make it work (below): a camera on the side, or the official
  scoresheet (§10.3a) and the scoreboard's log (§10.10), which answer the same question better.
- **Side findings**: the serve evidence that §10.3 relies on misreads near jump serves taken at
  the right edge of the picture, at ≥ 95 % confidence (5 seen in one game, 17 in another), and
  in a third hall read 7 far serves as near; where it is that sure it agrees with the scoreboard's
  log on only 88 % and 82 % of the rallies, so it is a weak reference for anything that needs
  ≥ 95 %. **Fixed the same day** (rule 2, PLAN §10.3): 78.7 % → 95.4 % over five games, and
  ≥ 95 % now right in 97 % of the answers. The end whistle is found within 3 s before the rally
  finder's end for 2–56 % of the rally ends, depending on the hall.

## Data and references

Five H3 games (YouTube imports, 1080p60), chosen for different halls, referee posts and camera
positions. The originals on the NAS, the rally finder's proposals and the court calibrations
from the production database (read-only).

| Game | Hall | Referee | Camera | Rally ends |
|---|---|---|---|---|
| KSCW H3 – Volero Legends, 02.10.2026 | KWI B | left post, side view | behind the end line, left of the centre (coach's calibration) | 191 |
| KSCW H1 – KSCW H3, 23.09.2026 | KWI A | right post | behind the end line | 135 |
| Volero 4 – KSCW H3, 18.12.2025 | Hirzenbach | right post | at the near left corner, very wide | 213 |
| Volewa Wald – KSCW H3, 27.11.2025 | Elba, Wald | right post | behind the end line, centred | 152 |
| KSCW H3 – Volley Uster H2, 06.02.2026 | KWI A | left post | behind the end line | 137 |

**References for who won each rally** (the side, near or far, that serves next):

1. **The club scoreboard's log** (§10.10), where it exists (home games since 02.10.2026): the
   point pressed after each rally, lined up with the rallies; trusted in every set of both
   games (each set ends on the official score). Independent of the video, the best reference.
2. **The next rally's serve side** from the serve evidence (§10.3), where it is ≥ 95 % sure,
   the next rally starts within 60 s and both rallies are in the same set. Available for every
   game, but noisy: on the Volero Legends game it agrees with the log on 122 of 138 rallies.
3. **By eye**: every disagreement between the referee and the reference was looked at on three
   frames of the next serve (who stands behind which end line with the ball); a case where the
   serve could not be seen counts as unclear and is left out.

The coach's own winners (nine rallies of one game) were too few to use.

## Method

1. **Where the referee is.** With a calibration, the court's camera model projects the stand
   next to each net post (0.8–1.3 m outside the side line, 1.2–3.2 m up); the first referee is
   the person standing elevated there, the second referee stands on the floor at the other post.
   On the Volero Legends game's coach calibration the projected left stand fell within 15 px of
   the referee; the court job's automatic calibrations put it within 25 px in two of the four
   other games and far off in the other two (their camera models were wrong), so a confirmed
   calibration is needed. For the other games the crop centre was set by hand once per video
   (what a confirmed calibration gives), and followed the camera's drift from the referee's own
   box.
2. **When.** From 3 s before to 6.5 s after each proposed rally end (cut at the next rally's
   start), 6 frames a second. The end is the referee's end whistle when one is found within
   3 s before the finder's end, else the finder's end minus 1 s (the ball lost).
3. **Pose.** The crop (2.6 × the referee's height, square) is scaled to 640 px and run through
   YOLO26m-pose on the Lenovo's CPU; the person closest to the expected box is the referee.
4. **Arm features per frame**, in torso lengths (shoulder to hip, 30–34 px): for each arm with
   confident shoulder, elbow and wrist, the wrist's and the elbow's offset from the shoulder,
   sideways along "outward" (away from the vanishing point of the side lines, which is where a
   hand moving towards the camera goes) and down.
5. **Frame classes.** *Near*: a wrist at shoulder height (less than 0.45 torso below it, the
   elbow too) and outward (more than 0.08). *Far*: a wrist at shoulder height (less than 0.3
   below, the elbow too), inward by 0.15–0.65 and the elbow not further inward than the wrist
   (a straight arm). *Court*: a wrist inward by more than 0.65 (an arm pointing across the court
   or resting on the post). Everything else is rest.
6. **The answer per rally end**: the longest run of near or far frames within 0–3 s after the
   end; at least two frames (0.33 s), longer than the other direction's longest run, and the
   other direction never two frames in a row. Otherwise no answer. Variants are in the table.

The rule was tuned on the Volero Legends game only; the other games are held out.

### Why near and far are hard to tell apart from here

From behind the end line the referee faces across the court, so the arm that points to the
team that will serve points along the camera's line of sight. With the Volero Legends game's
calibration (camera 3 m up, 4 m behind the end line, left of the centre), a 0.6 m arm at
shoulder height moves the hand only **14 px outward** (towards the camera: near) or **13 px
inward** (away: far) from the shoulder, 2.7 m up at the left post; an arm pointing across the
court moves it 37 px inward. The pose model's keypoints are good to a few pixels at this size,
so the geometry alone would do. The trouble is that other gestures make the same 2D shapes: an
arm raised or swung outward before the signal, a hand brought to the face or chest, the
forearms resting on the post, the "ball in" signal (the arm pointing down into the court), all
of them a wrist at shoulder height a dozen pixels from the shoulder, inward or outward.

## Results

The rule (tuned on the first game, the others held out). Precision counts the rally ends
answered near or far whose reference is known; coverage is the share of rally ends with a
reference that got an answer. Against the log, answers on rallies the log calls "no point" are
counted apart (they are not points; most are proposals that were half a rally).

| Game | Reference | Referee seen | Height | Coverage | Precision | Near right | Far right |
|---|---|---|---|---|---|---|---|
| Volero Legends | log (173 points) | 99.9 % | 105 px | 49 % | **96.5 %** (82/85) | 43/43 | 39/42 |
| H1 – H3 | log (119 points) | 99.9 % | 97 px | 71 % | **96.5 %** (82/85) | 37/38 | 45/47 |
| Volero 4 | serve + eye (121) | 98.7 % | 124 px | 55 % | **98.4 %** (60/61) | 24/24 | 36/37 |
| Volewa Wald | serve + eye (112) | 99.8 % | 100 px | 69 % | 85.3 % (64/75) | 42/42 | 22/33 |
| Volley Uster H2 | serve + eye (111) | 99.6 % | 110 px | 68 % | 79.1 % (53/67) | 31/34 | 22/33 |
| **All five** | | 98.7–99.9 % | | 61 % | **91.4 %** (341/373) | **177/181** | 164/192 |

Unclear by eye (left out): 0, 0, 5, 2 and 8 answers. On the log games the rule also answered on
1 and 5 rallies the log calls "no point".

**Variants** on the first game, against the log: a wider window (from 1 s before to 4.5 s after
the end) answers 53 % at 92.6 %; letting both directions show and taking the longer run, 54 % at
94.0 %; both together, 61 % at 81.7 %. A wider window catches the fault signal after the team to
serve; the stricter rule loses coverage. On the corner camera (Volero 4) the far signal moves the
hand 26 px, so a far/across boundary at 0.85 instead of 0.65 torso lengths answers 64 % at 97.3 %
there, but costs the first game 2.5 points of precision: the boundary has to follow each video's
geometry (its calibration).

**Near only** (a run of three frames towards the camera between 1 s before and 3.5 s after the
end, nothing else): 92.7 % over all five (228/246; the log games against the log), from 88 %
to 97 % per game; within the rule above, where a competing far signal stops it, the near
answers are right in 97.8 %.

**The serve evidence as a reference**: the first version of this spike scored against it alone
and found 83–86 % on the first game; most of the gap was the reference's error, not the
referee's. Every disagreement above was therefore looked at.

## Cases (described, no pictures)

- **A clean signaller** (the Volero Legends game): whistle, then within 0.8–1.5 s the arm on
  the camera's side out towards the camera, held about a second (near), or the other arm out
  away from it (far); then the fault (often the arm down into the court for "ball in", or the
  forearms up for "ball out"). Between rallies the forearms rest on the post's top. Most of
  this game's answers are right.
- **The fault shown first.** Sometimes the referee first pointed into the court (the fault, or the
  player) and showed the team to serve only 3–4 s after the whistle, outside the window; the
  inward gesture read as "far".
- **A swing before the signal.** Once the arm went out towards the camera in a swing, 0.5 s
  before the real "far" signal with the other arm.
- **Leaning on the post.** With the forearms on the post's top, lifting one hand off it reads
  as a short inward arm: "far".
- **One arm for both** (Volley Uster H2): the referee, in profile like the others, extended the
  far-side arm forward into the court at shoulder height after near-won and far-won rallies
  alike (the same 2D shape to the pixel); only the occasional arm towards the camera meant near.
- **A clean signaller with a corner camera** (Volero 4): the arm towards the camera held for
  2–3 s, then the fault; the camera's angle doubles the near/far offset (26–29 px). All but one
  answer right; the serve evidence, not the referee, was wrong on 17 near jump serves taken at
  the picture's right edge.
- **A referee whose far signal is the fault signal** (Volewa Wald): near is clean (42 of 42),
  but an arm forward into the court, short, came before ten near serves; the same referee showed
  "ball out" (both forearms up) at 30 of the 152 rally ends.

## Replays, cards, changes of decision

- **Replays** (double fault, "both thumbs up"): none of the 26 rallies the log calls "no point"
  (both log games) showed both arms raised. Of the 15 "no point" rallies of the Volero Legends
  game, 7 had no signal at all (proposals that were not rallies: the referee stayed leaning on
  the post) and 8 a normal team-to-serve signal (a rally cut in two by the finder, the signal at
  the real end). But "no signal" is no evidence either: 50 of the 173 real points had none in
  the window.
- **Cards**: none shown in these games. A raised card would be a colour blob at a wrist above
  the head; a wrist went above the head at 0–18 rally ends per game, and every one looked at was
  a fault signal ("ball touched", "ball out"), the referee stretching, or climbing down at a set
  end.
- **Changes of decision**: none identifiable; the scoreboard's log has them as an undo.

## Side findings

- **The serve evidence misreads near jump serves at the right edge of the picture.** In the
  Volero Legends game five serves the serve evidence called far at ≥ 95 % were near jump serves
  taken at the right edge of the near end line, the ball's first track high in the corner (the
  rallies starting after the ones ending at 2819, 2864, 3092, 3363 and 6717 s); in the Volero 4
  game (camera in the corner) 17 such serves. In the Volewa Wald game it went the other way too:
  seven far serves read as near. The serve side (§10.3, `wieplay/ml/serve.py`) should look at
  these; the scoreboard's log now shows where it errs. *Fixed on 18.10.2026 (rule 2, PLAN
  §10.3): the serve side now measures where the ball first shows and where it goes from the far
  court's centre in both axes, not its height alone, and passes over a slow ball (held, carried,
  bounced) for the next track. Against the logs and serves labelled by eye in three halls: 288
  → 349 of 366 right; Volero 4 49 % → 87 %, Volewa Wald 85 % → 97 %.*
- **The serve evidence is a weak reference** for anything that needs ≥ 95 %: 88 % and 82 %
  agreement with the log on the two games that have one. Measure against the log (§10.10) or
  the scoresheet (§10.3a).
- **End whistles**: found within 3 s before the finder's end for 56 %, 49 %, 2 %, 55 % and 26 %
  of the rally ends (in the table's order); the Hirzenbach game has almost none. Without the
  whistle the window hangs on the finder's end, which can be seconds late.

## What would make it work

1. **A camera on the side** (or a second one), at the net's height on the scorer's side: the
   pointing arm then crosses the picture, 40–60 px left or right instead of 13 px towards or
   away, and the referee faces it. The same rule would then likely be enough.
2. **A camera at a back corner** on the far side from the referee (diagonal): the line of sight
   to the referee meets the court's length at about 35° instead of about 20°, and the near/far
   offset doubles (26–29 px instead of 13–14). The Hirzenbach game's camera stood that way; see
   its row (98.4 %).
3. **The official scoresheet** (§10.3a) gives every point's winner in order and the
   **scoreboard's log** (§10.10) every point's time; together they answer the question for every
   rally, replays included (a log's undo, a sheet's gap), with no referee in the picture.
4. **Learning per referee**: a small sequence model on the keypoints, trained on the rallies
   the log or a coach answered, could learn one referee's habits (the fault first, the swing);
   referees change every game, so it would have to learn from a few rallies of each game. Not
   worth it while 1–3 exist.
5. **Only the near signal** as evidence: right in 177 of 181 answers across five referees, but
   for only about 28 % of the rally ends, half of the rallies the near side won. It would mostly
   catch the near jump serves the serve evidence misreads. Below the coverage bar, so not built;
   it is the part worth building first if a lower coverage is useful, with the far/across
   boundary taken from each video's calibration and only for videos with a confirmed one.

## Cost

On the Lenovo's CPU (8 threads), YOLO26m-pose at 640 px takes 0.12 s a frame; with 57 frames
around each rally end that is 7.5 s per rally end, **about 24 minutes per match** (YOLO26s-pose:
0.05 s a frame, about 10 minutes, untested for accuracy). Decoding the crop is a small part. A
job would need the pose model in the worker image (exported to ONNX like YOLO26s, §10.5) and a
confirmed calibration to find the stand.

All frames and crops of real footage made during the spike were deleted afterwards; only these
numbers remain.
