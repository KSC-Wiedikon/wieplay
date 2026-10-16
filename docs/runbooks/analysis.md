# Runbook: automatic analysis (rally finder)

What it is: [PLAN §10.1](../PLAN.md#101-dead-time-skipping-volleyball). Every ready volleyball
video gets an `analyse` job on the Lenovo worker; coaches correct the proposed rallies and
mark a game done; a `train_rallies` job learns from every game marked done.

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
  models/rallies/rally-v1-<job8>.pkl                              trained finders
```

Each file has a row in `analysis_artifact` with its SHA-256; the worker refuses a model file
whose hash does not match (pickle runs code). The signals hold no pictures and no sound:
ball positions, motion and loudness numbers only.
