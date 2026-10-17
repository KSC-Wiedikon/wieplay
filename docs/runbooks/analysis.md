# Runbook: automatic analysis (rally finder, scoring)

What it is: [PLAN §10.1](../PLAN.md#101-dead-time-skipping-volleyball) and
[§10.3](../PLAN.md#103-automatic-scoring-volleyball). Every ready volleyball video gets an
`analyse` job on the Lenovo worker; coaches correct the proposed rallies and mark a game done;
a `train_rallies` job learns from every game marked done (the rally finder and the serve
model). The API scores every analysed video (who served, who won, the score, sets, gap labels)
by itself, in about a second.

## Switch it on (once per environment)

Dev first, then prod. Nothing here touches the database by hand.

1. **NAS folder** for signals and models, owned like `raw/`:

   ```bash
   mkdir -p /mnt/nas/nomal/analysis-dev      # prod: /mnt/nas/nomal/analysis
   ```

2. **Worker settings**, in `~/stacks/nomal-worker-<env>/.env` (template:
   `deploy/lenovo/env.example`):

   ```bash
   NOMAL_NAS_ANALYSIS=/mnt/nas/nomal/analysis-dev
   NOMAL_WORKER_CAPS=ingest,transcode,thumbs,nas,inbox,analyse   # keep ",import" where set
   ```

   `deploy/lenovo/compose.yaml` refuses to start without `NOMAL_NAS_ANALYSIS`, so set it
   before the next `scripts/deploy-worker.sh <env>`.

3. **Capability in the API** (on the VPS, in that environment's API container):

   ```bash
   python -m nomal.cli add-worker-cap <worker name> analyse
   ```

   Or Admin → Workers. A worker without the capability never gets analysis jobs; one with
   it but without `NOMAL_NAS_ANALYSIS` logs an error and leaves them queued.

4. **Deploy** the API first (migration `0006`, the new claim filter), then the worker
   (`scripts/deploy-worker.sh <env>`). The worker log says `background lane nas,analyse`.

New volleyball videos that meet the capture standard are analysed once they are ready
(`NOMAL_ANALYSIS_AUTO=false` on the API turns that off; coaches can still start it from the
video). Analysis runs on its own lane next to the media jobs and below them in CPU priority,
so uploads never wait for it.

## Backfill: the imported H3 games

```bash
python -m nomal.cli analyse --team <H3 team row id> --dry-run   # what would be queued
python -m nomal.cli analyse --team <H3 team row id>
python -m nomal.cli analyse --all-ready                         # every ready volleyball video
```

Videos analysed before are skipped (`--force` runs them again: their untouched proposals
are replaced, nothing a coach did is touched). One analysis at a time per worker; a 2 h 12 min
match took 29 min in the worker image with 6 CPUs (2.8 CPU-hours, 1.5 GB; PLAN §10.1), so the
22 imported games take about 10 hours, longer while transcodes run next to them. Progress:
Admin → Jobs, or `GET /api/videos/{id}/analysis`.

## The ball's path and the court (trajectory job)

After each analysis a short `trajectory` job (seconds; same `analyse` capability and lane)
smooths the ball track, stores it as `ball-track-<job8>.json.gz` next to the signals and in
the bucket (`media/<video>/analysis/`, served by `GET /api/videos/{id}/ball-track`), and
looks for the court in the median of 15 frames. A court found with confidence ≥ 0.6 pre-fills
the coach's "Set up the court" dialog; otherwise the dialog starts from the team's last
calibration (or empty). Videos analysed before this job existed get it with:

```bash
python -m nomal.cli trajectories --dry-run
python -m nomal.cli trajectories            # --force: also videos that have a track
```

A 2 h 12 min match: 98 kB of track (gzip; 268 kB as JSON), 1.4 s to smooth, ~10 s for the
court frames and 1–3 s to look for the court. The court finder is a first version: on multi-sport floors it finds the side
lines and the near end line but can mistake another sport's lines for the court's depth; it
then reports a low confidence and offers nothing (16.10.2026: the Legends hall, 0.29). A
camera bumped during a match moves the court in the picture; one calibration per video does
not follow that yet. Since 17.10.2026 the court job below does both, and trajectory jobs
leave the court to it (`"court": false` in their params; older workers still send their
finder's guess, which is kept as a proposal).

## The court set automatically (court job)

After each trajectory job a `court` job (same `analyse` capability and lane, a few minutes)
reads a key frame every 30 s of the originals and finds:

- **the camera's setups**: where the picture moved by more than 4 px (at 1280×720) — a *bump*
  (someone knocked the tripod; placed to a few seconds), a *drift* (the head sagging: every
  H3 game does, 10–35 px a match), or another *view* (the camera carried elsewhere);
- **the empty hall** of the longest setup (median of 15 key frames), kept as
  `court-<job8>.npz` next to the signals (a JPEG inside; the hall's memory for later videos);
- **the court**: through the *hall's memory* (other videos' calibrated pictures — a coach's
  always, the job's own from confidence 0.75 — the team's and the hall's first, at most 16;
  matched with SIFT and RANSAC, the court's lines then fitted to this picture), else the
  *finder v2* (paint colours, the net, the bounces). Every setup gets the court through its
  picture's homography onto the main setup's.

The API keeps the court as the video's proposal and, **when sure** (hall memory ≥ 0.6,
finder ≥ 0.7) and no coach set the court, as its calibration with `checked` false: the
video page says "Court set automatically: check", Tendencies already uses it. A coach's
calibration always wins: a later court job only adds the setups to it (the coach's points
hold for the setup where the video was paused). In the dialog the coach checks or fixes one
setup at a time on a frame of its own; the setups nobody set follow the fix. Saving also
makes the video part of the hall's memory (a video without an empty-hall picture gets a
court job for it).

```bash
python -m nomal.cli court --all-ready --dry-run   # what would be queued
python -m nomal.cli court --all-ready             # videos without a court job result
python -m nomal.cli court --video <id> --force    # again, e.g. after a coach calibrated a
                                                  # first video in that hall
python -m nomal.cli court-eval                    # per video: method, confidence, setups,
                                                  # and against the coach's calibration
```

Start a hall's memory by setting up the court by hand on one video of each hall (the
finder v2 does not find the court on the real halls yet), then `court --all-ready --force`
for the others. Timing (Lenovo, under load): 240 key frames in 20–180 s depending on the NAS,
the hall's memory ~1 s a reference, the finder 10–15 s. Real check (17.10.2026, H3 games):
Stäfa calibrated by hand (2.9 cm RMS) → Uster set automatically at 0.92, 2.2 cm RMS against
a hand calibration of Uster; Legends and Volero H4 (other floors) correctly not matched;
Legends' bump found at 1:11:30 (25 px), its court carried over to the setup after it.

**When something goes wrong**: a wrong automatic court is fixed in the dialog (it then
counts as the coach's). A court job stuck on the NAS logs "no frame at … s" and goes on;
too few frames fails the job, the video plays on. Delete a hall reference by removing that
video's calibration.

## Players (players job)

**Beta, off by default** (`NOMAL_PLAYERS_AUTO=false` on the API): nothing queues the job by
itself. A coach clicks "Find players (beta, takes ~2 h)" on the video page (`POST
/api/videos/{id}/players/find`), or an admin runs the CLI below. With `NOMAL_PLAYERS_AUTO=true`
it follows each court job. The `players` job (same `analyse` capability and background lane,
priority −12 so a backlog's analyses go first) finds the people on the court in the rally
windows, tracks them, groups them into clusters and reads shirt numbers (a suggestion). The
coach names the clusters once on the video's "Players in this video" page; the actions then
get their player (PLAN §10.5).

- **Weights**: in the worker image at `/opt/nomal-weights` (YOLO26s ONNX, PaddleOCR detector
  and recogniser), built by `Dockerfile.worker`'s first stage from SHA-256-pinned downloads
  (`scripts/ml/player_weights.py`; `MANIFEST.json` lists sources, licences, hashes). Local
  runs: `NOMAL_WORKER_PLAYERS_WEIGHTS=/path`. Without the OCR files the job still runs (no
  number suggestions).
- **A GPU worker later**: start it with caps `gpu,nas`, `NOMAL_WORKER_PLAYERS_PROVIDERS=
  CUDAExecutionProvider,CPUExecutionProvider` and an onnxruntime-gpu image; set
  `NOMAL_PLAYERS_CAPS=gpu,nas` on the API so new jobs ask for it.
- **On demand** for videos with a ball track:

  ```bash
  python -m nomal.cli players --all-ready --dry-run
  python -m nomal.cli players --video <id>    # one video
  python -m nomal.cli players --all-ready     # --force: also videos that have players
  ```

  A rerun replaces the clusters; a coach's number stays where the same number is read on
  the same team.
- **Cost** (PLAN §10.9): a 2 h 12 min match, 26 k frames of rally windows: 1 h 51 min on
  the Lenovo with 4 threads beside the prod backfill (~1.9× its analyse job), 5.2 CPU-hours;
  the API's index ~1.7 MB, 76 contact sheets (~250 kB each).

## The learning loop

1. A coach tags or corrects a game's rallies (every proposal accepted, corrected, merged or
   rejected), then marks them done (`POST /api/videos/{id}/rallies-reviewed`; refused with
   the count while proposals are open). A game reopened later trains again once it is
   marked done again.
2. Ten minutes after the last such mark (`NOMAL_ANALYSIS_TRAIN_DELAY_SECONDS`), one
   `train_rallies` job runs. It needs the signals of the reviewed games (their `analyse` job
   must have run) and at least **two** reviewed games to learn v1.
3. It scores v0 and v1 on every reviewed game while that game is held out of the training.
   v1 is used only when its mean boundary F1 (±1 s) beats v0's; then it proposes new rallies
   for every game nobody has done yet.
4. Train by hand: `python -m nomal.cli train-rallies`. Scores per game:

   ```bash
   python -m nomal.cli rally-eval            # the latest training; --json for everything
   ```

## Scoring (servers, winners, sets, gaps)

Nothing to switch on: it runs in the API whenever a video gets new rallies or series (analysis,
training), a coach marks the rallies done, changes a set or calls a serve in review. It needs
the two small series the worker sends with each analysis (`analysis_track`); videos analysed
before migration `0007` have none until their next analysis or the next training (it adds the
missing series of every game), and are scored from the rally times only meanwhile (sets and
gap lengths, no serve sides).

```bash
python -m nomal.cli score --video <id>       # score again now and print the sets
python -m nomal.cli score --all-ready        # every ready volleyball video (after a deploy)
python -m nomal.cli train-rallies            # also brings the missing series of old games
```

What a coach does: in the Sets bar, "First serve" and "We start" for set 1 when Nomal is not
sure (the scoring card says so); the rules carry it to the other sets. In the review, `1`/`2`
call the serving team, gap labels are answered like rallies (`W B T D O` relabel). Nothing the
model writes overwrites a coach's winner, serve, set or label: its server and winner are in
their own fields (`model_serving`, `model_won_by`), flagged rallies say why (`score_note`).

The scores per video (`GET /api/videos/{id}/analysis` → `scoring`): per set the official
score, the score line's final and where the serve evidence alone ends (`evidence`,
`matches_official`), flagged rallies, gaps, and on reviewed games the serve-side and winner
accuracy of a run blind to the coach (`vs_coach`). The serve model's held-out scores are in
the training run's `metrics.serve`; `analyse` jobs use it once it beat the rule.

## Suggested actions (serve, reception, set, attack, block, dig, freeball)

What it is: [PLAN §10.4a](../PLAN.md#104a-suggested-actions-volleyball). Nothing to switch on:
an `actions` job (seconds, same `analyse` capability and lane) follows every trajectory job
and, 90 s later, every scoring a coach causes (sets, serve calls, rallies done). It reads the
video's signals and asks the API for the rest: the rallies with their servers, winners and
sides, the court (the coach's calibration, or the court finder's when confident), the current
model. Deploy the API first (migration `0010`), then the worker. Videos analysed before:

```bash
python -m nomal.cli actions --all-ready --dry-run
python -m nomal.cli actions --all-ready          # or --video <id>
```

**The bar.** Suggestions are written only once the action finder's held-out score on games
whose actions a coach reviewed passes it: two games or more, precision of fully right
suggestions ≥ 0.70, contact recall ≥ 0.6. Until then the jobs run and measure themselves
where coaches tagged, and the pages say why there are no suggestions. Settings (API):

```bash
NOMAL_ACTION_SUGGESTIONS=auto            # auto (the bar) | on (write anyway, testing) | off
NOMAL_ACTION_SUGGEST_MIN_PRECISION=0.70
NOMAL_ACTION_SUGGEST_MIN_RECALL=0.6
```

`GET /api/videos/{id}/analysis` → `actions.gate` says where it stands (reason, games, score).
After switching to `on`, `python -m nomal.cli actions --all-ready` fills the games in.

Suggestions are tags with source "model" and review "unreviewed": they show dashed on the
tagging page and are answered in Review → Actions (Enter, X, S R E A B D F, # + ! - / =, a
number then Enter, Tab, ←/→, Shift+Enter, U). They never count in stats, reels or the score
until accepted. A run never writes into a rally with a person's tags; it replaces its own
untouched suggestions elsewhere and leaves rejected ones out.

The learning loop is the rallies': "Actions reviewed" (refused while suggestions are open)
queues a `train_actions` job ten minutes later; it needs two such games, scores the rules (v0)
and the learned model (v1) on each game left out and keeps v1 only when its action F1 beats
v0's (then every game nobody has done gets new suggestions).

```bash
python -m nomal.cli train-actions                # train now
python -m nomal.cli action-eval                  # per-game scores of the latest training
```

Scores per video: `GET /api/videos/{id}/analysis` → `actions` (suggestion counts, the newest
run's counts per skill, and in rallies a coach tagged `vs_coach`: precision, recall and F1 of
the contacts at ±0.3 s, skill and side accuracy, action F1). A model that does not load (a
new scikit-learn) falls back to the rules with a warning; `train-actions` trains a new one.

## When something goes wrong

- **Analysis failed**: the video still plays; the error is on the job (Admin → Jobs) and,
  for coaches, in `GET /api/videos/{id}/analysis`. Rerun from the video or with
  `python -m nomal.cli analyse --video <id>`.
- **"this worker has no analysis root"**: step 2 is missing on that worker.
- **Decoding**: the CPU decodes by default (`NOMAL_WORKER_ANALYSIS_HWACCEL=none`); with
  `vaapi` a failing iGPU falls back to the CPU by itself. The run's timing says which one
  ran (`hwaccel`).
- **A rally model does not load** (e.g. a new image with another scikit-learn): analysis
  falls back to v0 and logs a warning; `train-rallies` trains with the new version.
- **Deleting a video** removes its analysis folder together with its originals (purge job).

## Files on the NAS

```text
analysis[-dev]/
  videos/<season>/<team>/<date>_match_<id8>/signals-<job8>.npz   per-frame ball and motion,
                                                                  audio numbers, whistles
  videos/…/ball-track-<job8>.json.gz                              the ball's path (bucket copy
                                                                  is what browsers get)
  videos/…/players-<job8>.npz                                     every player box per analysed
                                                                  frame, tracks, windows
  videos/…/players-<job8>/index.json.gz, sheet-<n>.jpg            the API's box index and the
                                                                  gallery's crops (bucket copies
                                                                  under media/<video>/analysis/)
  models/rallies/rally-v1-<job8>.pkl                              trained finders
  models/rallies/serve-v1-<job8>.pkl                              trained serve models
  models/actions/actions-v1-<job8>.pkl                            trained action proposers
```

Each file has a row in `analysis_artifact` with its SHA-256; the worker refuses a model file
whose hash does not match (pickle runs code). The two small series per video (serve evidence
every 0.5 s, activity every second, tens of KB) are in Postgres (`analysis_track`), not on
the NAS: the API scores with them. The signals hold no pictures and no sound:
ball positions, motion and loudness numbers only.
