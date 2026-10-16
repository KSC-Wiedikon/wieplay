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
not follow that yet.

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
  models/rallies/rally-v1-<job8>.pkl                              trained finders
  models/rallies/serve-v1-<job8>.pkl                              trained serve models
```

Each file has a row in `analysis_artifact` with its SHA-256; the worker refuses a model file
whose hash does not match (pickle runs code). The two small series per video (serve evidence
every 0.5 s, activity every second, tens of KB) are in Postgres (`analysis_track`), not on
the NAS: the API scores with them. The signals hold no pictures and no sound:
ball positions, motion and loudness numbers only.
