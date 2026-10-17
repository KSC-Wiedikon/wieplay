# ADR-002: Video pipeline for M1

- Status: accepted (15.10.2026); updated 16.10.2026 (workers reach the API through an SSH
  tunnel, see the update at the end)
- Context: `docs/PLAN.md` §5.2–§5.5, §7, §8; M1 in §17

## Decision

1. **Object storage over the S3 API, credentials only in the API.**
   - Hetzner Object Storage on the VPS; locally SeaweedFS (`compose.yaml`), because MinIO
     stopped publishing images.
   - Browsers upload with S3 multipart: the API creates the upload and signs each 64 MB part
     URL (1 h). On "complete" it lists the parts itself and checks count and sizes, so the
     browser never needs to read ETags.
   - Workers ask the API to sign exactly the keys their job may touch
     (`pipeline.may_sign`): the ingest job reads and deletes its staging objects; a transcode
     job writes only `media/<video>/<rendition>/init.mp4|seg_NNNNN.m4s`.
2. **The filming rules run before anything is stored** (`core/filming.py`).
   - The team policy: DU23-1 films trainings only.
   - Wiedisync's adult check: every rostered person must be 18 or older on the recording
     day, and a missing birthdate fails. Birthdates never reach WiePlay.
   - (Per-player consent was dropped on 16.10.2026: Swiss Volley allows recording matches,
     Volleyballreglement Art. 86; see PLAN §5.4.)
   - A refusal is a 409 with reason codes and an audit entry. No video row and no
     multipart upload are created.
   - The team page shows the same check for the next 21 days from a 6-hour cache.
3. **The queue is a Postgres table; workers pull over the tailnet** (`core/jobs.py`,
   `api/worker.py`).
   - Workers claim with `FOR UPDATE SKIP LOCKED`: capabilities must cover the job's needs,
     and the jobs it depends on must have succeeded.
   - A claim is a 5-minute lease kept alive by heartbeats. A reaper re-queues expired
     leases with back-off and fails a job after 3 attempts.
   - A permanent failure fails the video and cancels its other jobs.
   - Each worker has its own token (only its hash is stored, revocable in Admin).
   - Worker routes answer 404 to anything carrying `CF-Connecting-IP`.
   - (Since 16.10.2026 the workers pull through an SSH tunnel, not the tailnet; see
     *Update 16.10.2026*.)
4. **One media worker on the Lenovo server** (`nomal/worker/`, `Dockerfile.worker`,
   `deploy/lenovo/`).
   - It handles `ingest`, `transcode` ×2, `thumbs`, `purge` and the NAS inbox scan.
   - **Ingest** stores the originals on the NAS under
     `raw/<season>/<sport>-<team>/<date>_<kind>_<id>/` (human-readable), checksums and
     probes them, and deletes the staging objects.
   - **Transcode** reads the split camera files through an ffconcat list, without joining
     them first.
   - **Renditions** are H.264/AAC in fMP4 HLS with 4-second segments and closed 2-second
     GOPs:
     - 1080p at up to 50 fps (5 Mb/s, max 7);
     - 540p at up to 25 fps (1.2 Mb/s, max 1.6).
     - Only renditions at or below the source height are made, and nothing is upsampled.
   - **Encoder**: VAAPI (Radeon 780M), falling back to x264 by itself. Measured on a
     synthetic 1080p50 clip: VAAPI about 5.8× real time, x264 (6 threads) about 4.7×.
   - **The codec string** comes from the init segment's `avcC` box, because ffprobe
     reports no level for an init segment without frames.
   - **BANDWIDTH** is the peak per-segment rate, counting at least 1 second per segment so
     the short final segment cannot inflate it.
5. **Playback through the API** (`api/media.py`).
   - The API serves the master and media playlists from the database.
   - Each init segment, segment, sprite and poster request is checked against the session
     and answered with a 302 to a URL signed for 5 minutes for that one object.
   - The permission check is cached per (session, video) for 60 seconds; deleting a video
     clears that cache.
   - A view is audited once per video per session.
   - The CSP allows the storage origin for `media-src`, `img-src` and `connect-src`, and
     `blob:` for hls.js.
6. **NAS inbox.**
   - The worker reports files whose size and mtime have not changed for 2 minutes.
   - Coaches pick them in the upload flow, under the same rules as a browser upload.
   - Ingest moves them into `raw/` (a rename on the same share).

## Consequences

- The VPS publishes the API on its tailnet address too (`NOMAL_TAILNET_IP`), so the worker
  can reach it without Cloudflare. (Until 16.10.2026; since then `NOMAL_TAILNET_IP` is unset,
  see the update below.)
- The bucket needs CORS for the WiePlay origin and lifecycle rules: `incoming/` 14 days,
  `exports/` 30 days, `analysis/` 3 days. `python -m nomal.cli storage-setup` applies them.
- Before the bucket exists, the app runs without video features: `/api/video/config`
  reports `enabled: false`.
- Not in M1:
  - YouTube links (M4);
  - the nightly re-check of rosters against the adult rule, with the upload freeze
    (§5.3 point 4);
  - the retention job;
  - the audio mute switch;
  - exports.

## Update 16.10.2026: SSH tunnel instead of the tailnet

The VPS moved to the club's own Hetzner account and left the owner's private tailnet, so it
is on no tailnet now. Decision 3 holds (workers pull, worker routes refuse Cloudflare); only
the path changed:

- `NOMAL_TAILNET_IP` is unset in both VPS `.env` files. Compose falls back to `127.0.0.2`,
  so the APIs listen on the VPS's loopback only (`127.0.0.1:8090` dev, `:8091` prod).
- The Lenovo server connects out to the VPS with `nomal-api-tunnel.service`
  (`deploy/lenovo/`), an `ssh -N` with two local forwards that publishes the APIs on the
  Docker bridge (`172.17.0.1:18090` dev, `:18091` prod). ufw there lets only Docker networks
  reach those ports. The workers' `NOMAL_WORKER_API_URL` points there.
- Requests through the tunnel carry no `CF-Connecting-IP`, so the worker routes accept them.
  The security idea is unchanged: the worker routes are not reachable from the internet;
  only a machine with SSH access to the VPS can reach them. The VPS has no access to the
  home network.
- The Framework 16 GPU worker (PLAN §8.2) is not set up yet. It will need its own path to
  the API (for example its own SSH tunnel); that is decided when it is set up.
