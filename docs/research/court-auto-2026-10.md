# The court found automatically, and when it is sure (October 2026)

The owner asked on 20.10.2026: "everything gets analyzed and automatically assigned: court, …".
Until then every hall needed a coach to click the court once. The hall's memory carried a
coach's court to the hall's next videos, but the finder for a hall seen for the first time
(v2, PLAN §10.6) "still failed on real floors": sports halls carry handball, basketball,
badminton and volleyball lines in several colours. Only one video on prod (Legends) had a
court; H1–H3 had none. This note measures v2, describes the finder v3 that replaced it, and
sets the *sure* flag: a sure court is used by the analysis without a coach; an unsure one is
shown to a coach to confirm or fix ("Check the court").

## Ground truth

- **21 halls**: the empty-hall picture (median of 15 key frames of the main camera setup,
  1280 x 720) that the court job keeps on the NAS for each of the 21 analysed prod videos of
  H3 (2025/26 and 2026/27): 16 different halls, mostly the club's own camera behind the end
  line, also a side view, a corner view and two cameras on a gallery.
- **Labels**: the court's landmarks (corners, the centre line's and attack lines' ends) placed
  by hand on 14 halls, by Claude during development, at 3–4x zoom, snapped to the paint's
  centre line. The far end line is the faint line a few pixels *below* the net's bottom band
  (the far court shows under the net); two first labels had it on the net and were redone.
  Legends' coach calibration was checked the same way (it is 10 px off at the near corners
  after the camera sagged; the labels follow the paint).
- **7 halls without a usable label**: the court cannot be told by eye with certainty or is not
  painted. H1–H3 (KWI A, the climbing-wall hall) has *no volleyball lines*: its yellow court
  is badminton (6.1 m: the centre circle, 3.6 m across, gives the scale), and the players'
  feet over the match spread exactly over the 9 m the net posts give. For these halls the
  measure is "never sure"; any sure court there would have counted as wrong.
- **Held out**: medians of the five 3-minute clips of spike S0 (players in the picture, other
  camera spots), one of them a hall that is not among the 21 (SUC). Fetching more H3 games
  from YouTube for new halls was blocked by YouTube's bot check.
- The pictures, labels and debug images stay off the repository (real footage; the repo is
  public).

A found court is **right** when every labelled landmark is within 20 px of its label (in
practice the right ones are within 16 px, the wrong ones 60 px or more off). The error in
metres maps the labels through the found court's homography, as the court job's `vs_coach`
does: it includes what a homography cannot follow of a wide lens (Legends' own labels fit a
homography only to ±24 cm).

## Before: finder v2

| | v2 | v3 |
|---|---|---|
| Right best guess, labelled halls | 1 / 14 | 13 / 14 |
| Sure (set without a coach) | 0 / 21 | 12 / 21 (57 %) |
| Wrong among sure | – | 0 / 12 |
| Offered as a guess but wrong | 3 | – (unsure courts go to "Check the court") |
| Held-out clips: sure / wrong among sure | – | 2 / 5, 0 |
| Time per picture (Lenovo, one core) | 2–7 s | 1–2 s |

On the 12 sure courts: landmarks a median 2.3 px from the labels (the worst 16 px, a wide
lens's near corner), 20 cm RMS on the court (median; 4–29 cm), the worst landmark 38 cm
(median). For comparison, Legends' coach calibration is 44 cm RMS from the same labels, v3's
court 26 cm.

Why v2 failed, measured on the pictures: its line pixels (a ridge filter at a fixed bar) took
the floor's grain and the lights' reflections on glossy floors, and its texture filter then
dropped the thick near lines together with the noise around them, so the volleyball court's
own lines were often missing; its colour classes split one paint (bright near, mixed with
the floor far) into several and merged far lines of different paints.

## Finder v3 (`wieplay/ml/court_find.py`)

1. **Painted lines of any colour**: ridges of the image's curvature (the Hessian's eigenvalues
   on the Lab channels at three scales: curved across a line, flat along it), thinned to the
   line's centre, kept where clearly above the picture's and the neighbourhood's level. The
   paint of each line pixel is its Lab against the floor around it (lightness half weight), with
   a confidence from the contrast: a faint far line's colour says little.
2. **Lines**: straight pieces (probabilistic Hough), the vanishing point of the hall's long
   lines, lines through it (side lines) grouped by their direction from it, and lines across.
3. **Courts**: every pair of side lines of one paint with every pair of lines across of that
   paint, as every pair of the court's cross lines (0, 6, 9, 12, 18 m): 10 000–50 000 courts,
   cut to those a camera filming a match could see (vectorised camera recovery: above the
   floor, near the court, a sane focal length).
4. **Judging**: each of the seven lines on paint of the court's colour (a missing line costs
   most: a log score), no court paint 0.7–3 m beyond the corners (a bigger court's lines run
   on), the paint's agreement.
5. **The best eight different courts**: a camera with one radial distortion term fitted to
   the court's paint (robust least squares, pulled to the nearest paint pixel within a
   shrinking radius), judged again through it, and:
   - **the net**: its two bands (top band at the net's height, bottom band 0.95 m lower) on
     thin bright lines where this camera puts them above the centre line, swept over heights.
     A court whose camera wants the net at 1.9 m or 2.8 m loses; one whose net shows at
     2.43 m (men) or 2.24 m (women) wins. On the right courts the net was found at
     2.40–2.46 m (H3 plays at 2.43 m). The court job passes the team's net (Herren, Männer,
     H… → 2.43 m; Frauen, Damen, D… → 2.24 m; else both), which removes the depth ambiguity
     that a women's net height would otherwise allow.
   - **the filming guide's camera**: centred behind the end line, not above 6 m, the whole
     court in the picture; small penalties outside it.
6. **Sure** when the score is at least 0.7, it leads the next *different* court (landmarks in
   the picture more than 15 px apart) by at least 0.15, the net is not seen elsewhere and the
   camera is the guide's. Its confidence is then 0.8 or more; an unsure court's is below.

Tried and left out:
- *Ball bounces*: the ball tracker's floor contacts include hands, walls and the ceiling; on
  H1–H3 they cover the whole picture. Reported in the stats, not used.
- *Players' feet* (YOLO26s-pose on 40 frames, 17 s): they outline the court well (on H1–H3 they
  match the 9 m the net posts give), but the halls they would help most have no paint to fit a
  court to; worth a "net and feet" proposal later, not a sure court.
- *A global lens estimate* from the hall's straight lines (plumb lines through a Hough
  sharpness): no clear optimum on these pictures; the court's own lines fit the lens better.

## Where it is not sure (9 of 21)

| Hall | Why |
|---|---|
| H1–H3, H3–Voléro 4 (KWI A, climbing wall) | no volleyball lines painted; badminton and basketball only |
| Egg (two pictures) | camera on a gallery 5.8 m up: the net's bands against the light floor are too faint, a bigger court of the same black paint scores alike |
| Wetzikon tournament | camera 5.8 m up: right court, but a near rival (lead 0.13) |
| Stäfa (Tränkebach), Voléro friendly | side and corner views: no vanishing point of the court's long lines in the picture |
| Rämi, Lavater, Schauenberg | many overlapping lines of similar paint, a far net; the best court is wrong and stays unsure |

## In the product

- The court job (worker `court`, after the trajectory job) runs v3 per camera view on the empty
  hall, and the hall's memory as before. It chooses (worker/court.py `best_court`):
  the memory unless the finder is sure and the memory is not; a memory transfer is sure from
  0.75 of its own confidence (matches spread over the floor, lines on paint); two sure courts
  that disagree make it unsure (a coach decides).
- The API sets the court as the video's calibration only when sure (`checked` false), and
  gives every calibration `sure` and `usable` (checked by a coach, or sure): the chain uses a
  usable court and runs without one otherwise (nominal camera). A rerun that is no longer sure
  takes an unchecked court back to a proposal. Only sure or checked courts serve the hall's
  memory.
- "Check the court" (video page, setup): the court WiePlay found over the paused frame, with
  Confirm (`POST …/court/confirm`: its proposal, with the camera's setups, becomes the
  video's court, checked) or Fix (the set-up dialog, pre-filled with it). Sure and unchecked
  courts can be checked the same way but do not ask for it.
- Prod: rerun the court job for the analysed videos (`python -m wieplay.cli court --all-ready
  --force` on the API) to find the courts of the 21 videos; `court-eval` then reports sure
  courts and, on videos a coach calibrated, wrong ones among them.

## Open

- The threshold was tuned on the same 21 halls (12 sure, 0 wrong); the held-out clips agree
  (2 sure, both right) but are few. The first coach confirmations and fixes (`vs_coach` in
  `court-eval`) are the real test.
- Halls without volleyball paint (H1–H3) need a court from the net and the players' feet, as a
  proposal for the coach.
- High cameras: the net seen against the floor needs a dark-band (mesh) cue.
- The stored calibration stays a homography; the lens term found by the finder is in the
  run's stats only.
