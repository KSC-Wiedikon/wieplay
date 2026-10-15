# Nomal: plan

> *Nomal* is Swiss German for "again": watch it again, learn from it.
> In-house video analysis for KSC Wiedikon, replacing what clubs pay Hudl or Balltime for.

| | |
|---|---|
| Status | **Plan, revision 4** (15.10.2026). Nothing built yet; brand icons generated. |
| Owner | Luca Canepa (sole developer) |
| Repo | `KSC-Wiedikon/nomal` (AGPL-3.0, like Wiedisync; private for now) |
| Live host (planned) | `nomal.kscw.ch` (prod), `nomal-dev.kscw.ch` (dev) |
| Depends on | Wiedisync (accounts, teams, rosters, games): `KSC-Wiedikon/wiedisync` |

This is the plan of record. Decisions that change it get a short ADR in `docs/adr/` and an
edit here. It contains **no secrets, IPs, vault paths or home hostnames**: those belong in
the private ops notes (§15).

**Flags** (things that break a stated constraint or need a decision) are marked ⚠ and
collected in §19.

---

## Contents

1. [Goals, non-goals, tiers](#1-goals-non-goals-tiers)
2. [Decisions so far](#2-decisions-so-far)
3. [Architecture](#3-architecture)
4. [Login and the Wiedisync bridge](#4-login-and-the-wiedisync-bridge)
5. [Permissions, privacy and revDSG](#5-permissions-privacy-and-revdsg)
6. [Data model](#6-data-model)
7. [Video pipeline](#7-video-pipeline)
8. [Jobs and workers (how the GPU gets work)](#8-jobs-and-workers-how-the-gpu-gets-work)
9. [Tagging and playback (tier 1)](#9-tagging-and-playback-tier-1)
10. [Automatic analysis (tier 2)](#10-automatic-analysis-tier-2)
11. [Tier 1 as training data for tier 3](#11-tier-1-as-training-data-for-tier-3)
12. [Feature benchmark: Balltime and competitors](#12-feature-benchmark-balltime-and-competitors)
13. [Brand and UI](#13-brand-and-ui)
14. [Repo, stack, environments, deploy](#14-repo-stack-environments-deploy)
15. [Operations and handover](#15-operations-and-handover)
16. [Budget](#16-budget)
17. [Roadmap](#17-roadmap)
18. [Risks](#18-risks)
19. [Open questions and flags](#19-open-questions-and-flags)

---

## 1. Goals, non-goals, tiers

**Goal.** Coaches and players upload match and practice footage, tag what happens, and
watch clips, highlight reels and condensed matches without dead time, all behind their
existing Wiedisync login. Volleyball first; basketball is in the first batch of teams, so
everything sport-specific is data (taxonomies), not code.

**First teams (season 2026/27)**: about 75 matches (H3, D1, Herren 1) plus DU23-1 trainings.
The budget still assumes 100 videos per season.

| Team in Wiedisync | Sport | League | Note |
|---|---|---|---|
| H3 | Volleyball | 2. Liga | |
| D1 | Volleyball | 2. Liga | Not the basketball "Lions D1" |
| DU23-1 | Volleyball | U23 | **Trainings only** (decided): U23 opponents may be under 18, §5.3 |
| Herren 1 | Basketball | 1. Liga regional A (`H1LRA`) | Called "H1" in conversation; Wiedisync also has a *volleyball* H1 (2. Liga), which is not in scope |

### Tiers

| Tier | What | Runs where |
|---|---|---|
| **1 · MVP** | Upload a match (original file, or a YouTube link from the club's channel) · timestamp-synced manual tagging with keyboard shortcuts (event type + player number) · rally markers and **skip-dead-time playback from manual markers** · clips and per-player highlight reels · clip/reel export with ffmpeg · Wiedisync login | API + web on Hetzner; media work on the Lenovo server |
| **2 · Automatic** | **Dead-time skipping** (rallies vs. timeouts, set breaks, warm-up) · **automatic scoring** (volleyball from the serving side, scoreboard OCR when the scoreboard is in frame; basketball later) · ball tracking (TrackNet-style) · player detection + tracking (YOLO + tracker) · court homography → serve placement. Coach corrections become training data. | CPU stages on the Lenovo server; GPU stages on our own GPU at home first (Framework 16, always on), an EU or Swiss provider later; queued like everything else |
| **3 · Later / maybe** | Per-touch action classification and player attribution | Same GPU path, trained on tier 1 + 2 data (training on the Framework 16) |

### Non-goals (to stay finishable by one person in the evenings)

- No live streaming, no live tagging during the match. Footage is processed afterwards.
- No public links of any kind, no social sharing, no embedding outside Nomal.
- No face recognition and no cross-video person re-identification, ever (§5.7).
- No full DataVolley replacement. Statistics stay simple (counts and efficiencies per skill).
- No multi-camera sync, no camera hardware. Telestration comes after launch (§12).
- No minors: nothing under 18 is filmed for Nomal or stored by it (§5.3).
- No camera purchases: each team films with its own camera.

---

## 2. Decisions so far

From the Q&A on 15.10.2026 (second round supersedes the first):

| # | Topic | Decision |
|---|---|---|
| D1 | Capture | Each team films with **its own dedicated camera**, minimum **1080p50**; the club buys none. A **YouTube link** is accepted as a source **for the club's own uploads only**: the club's channel or a club member's own channel; the **original file is preferred** (YouTube re-encodes and may drop 50 fps) and can replace the YouTube source later. |
| D2 | Teams, volume | H3, D1, DU23-1 (volleyball), Herren 1 (basketball). ~100 videos per season in total. **DU23-1: trainings only** (decided 15.10.2026, §5.3). |
| D3 | Delivery | Hetzner Object Storage. **HLS**: the authenticated API serves the playlist and every segment URL is signed with a **TTL of minutes** at the moment the player asks for it. No long-lived link per video anywhere. A copied link dies quickly; a 2 h match plays without interruption (§7.6). |
| D4 | Compute | **Lenovo server** = always-on media worker (ingest, transcode, clips/exports, CPU analysis). **Inference starts internal** (decided 15.10.2026): on the **Framework 16** (RTX 5070 Laptop GPU, 8 GB, CUDA; always on), with a researched plan to move to an **EU or Swiss provider** later (§8.6). US providers are out. The Framework 16 is also the development and training machine; the RTX 4090 PC is not part of the plan. The worker interface is backend-agnostic (queue → worker), so internal, provider and a future GPU box are interchangeable (§8). |
| D5 | Permissions | Team-scoped defaults accepted (§5.1). **Adults only**: a hard check against member birthdates; nothing under 18 is filmed or stored. |
| D6 | OpenVolley | Not now. The data model stays open for importing points later (§6.6, §10.8). |
| D7 | Repo | `KSC-Wiedikon/nomal`, AGPL-3.0 (created, private). |
| D8 | Docs | This plan lives in the repo and is committed (secret-free by rule). |
| D9 | Brand | Own Nomal brand (§13): assets in `docs/brand/`, `-vb` / `-bb` variants (default `vb`), wordmark "nomal" in Bricolage Grotesque ExtraBold. |
| D10 | Budget | **~CHF 210 per season** running cost in total (cloud GPU ~80, backup storage ~50, test GPU ~35, reserve). Lines that would exceed it are flagged (§16). |
| D11 | Tier 2 scope | Includes dead-time skipping (like Balltime) and automatic scoring. |
| D12 | Defaults accepted | Download policy (coaches only, watermarked, 30 days), retention (season + 1), audio kept with per-video mute, players who leave lose access, no per-player consent (16.10.2026, §5.4). |
| D13 | Opponents | Opponents in adult leagues are adults (the club's position, 15.10.2026); no per-upload confirmation (§5.3). |
| D14 | Camera models | Not collected: Nomal reads resolution, frame rate and codec from each file (§7.1). |

---

## 3. Architecture

### 3.1 Picture

```mermaid
flowchart LR
  subgraph Users
    B[Browser / Android WebView]
  end

  subgraph CF[Cloudflare]
    T[Tunnel: nomal.kscw.ch<br/>HTML, JSON, 302s only]
  end

  subgraph H[Hetzner VPS]
    API[nomal-api<br/>FastAPI + built SPA]
    DX[Directus<br/>Wiedisync API + bridge]
    PG[(Postgres<br/>Wiedisync db + nomal db)]
  end

  OS[(Hetzner Object Storage<br/>renditions, staging, exports, analysis copies)]

  subgraph Home[Home LAN / tailnet]
    LS[Lenovo server<br/>media worker]
    FW[Framework 16, RTX 5070<br/>internal GPU worker, dev + training]
    NAS[(Synology NAS<br/>raw originals, datasets, models)]
  end

  subgraph Later[Later: EU or Swiss GPU provider]
    ML[nomal-ml container<br/>billed per second/minute]
  end

  B -- "app + API (cookie)" --> T --> API
  B -- "login redirect" --> DX
  B -- "HLS segments (signed, minutes)" --> OS
  B -. "YouTube embed (club and member channels)" .-> YT[(YouTube)]
  API --- PG
  DX --- PG
  API -- "sync rosters/games (localhost)" --> DX
  LS -- "claim jobs (tailnet)" --> API
  FW -- "claim GPU jobs (tailnet)" --> API
  LS <-- "signed PUT/GET" --> OS
  LS <--> NAS
  FW <--> NAS
  LS -. "later: submit job spec, poll" .-> ML
  ML -. "signed GET/PUT" .-> OS
```

The rule that shapes everything: **Cloudflare only carries small things** (the SPA, JSON,
302 redirects). Its terms do not allow serving video through the normal proxy or a Tunnel
on non-Enterprise plans, so video bytes go straight between object storage and browsers,
the Lenovo server and the GPU.

### 3.2 Components

| Component | Runs on | Does | Tech |
|---|---|---|---|
| **nomal-web** | Built into the `nomal-api` image, served at `nomal.kscw.ch` | Library, player, tagging, reels, admin | React 19, TypeScript, Vite, Tailwind v4, shadcn, TanStack Query, hls.js |
| **nomal-api** | Hetzner VPS, Docker, bound to `127.0.0.1`, reached via the existing Cloudflare Tunnel; worker routes only over the tailnet | Auth, permissions, metadata, upload orchestration, media redirects, job queue, audit log, budget ledger | Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, Pydantic 2, psycopg 3 |
| **Postgres `nomal`** | Existing Postgres container on the VPS, separate database + own non-superuser role | All Nomal state incl. the job queue | Postgres 16 |
| **Object storage** | Hetzner Object Storage, same region as the VPS, private bucket | HLS renditions, thumbnails, upload staging, exports, temporary analysis copies | S3 API |
| **Media worker** | **Lenovo server** (always on; Ryzen 7 PRO 8700GE 8C/16T, 30 GB RAM, Radeon 780M with VAAPI H.264 encode, ~690 GB free) | Ingest (NAS inbox + browser uploads), transcode, thumbnails, audio, CPU analysis (whistles, motion, scoreboard OCR), analysis copies, clip/reel export, retention | `nomal-worker` container, ffmpeg. **CPU-capped** (it also runs the LAN's DNS and other services) |
| **GPU worker (now)** | **Framework 16** at home (RTX 5070 Laptop GPU, 8 GB; always on) | Pulls GPU jobs from the queue over the tailnet and runs `nomal-ml run <spec>` locally; busy guard (§8.2) | `nomal-worker` + `nomal-ml` image (CUDA 12.8+, PyTorch), NVIDIA Container Toolkit |
| **GPU dispatcher (later)** | Same container on the Lenovo server | Hands GPU jobs to an EU or Swiss provider once that backend is switched on (§8.6); enforces the CHF cap | Part of `nomal-worker` |
| **Framework 16 (dev)** | Same machine | Development, model training/fine-tuning, local test runs; a training run pauses the GPU worker | Blackwell GPU: needs a PyTorch build with CUDA 12.8 or later |
| **NAS** | Synology at home (≈18 TB free) | Raw originals (master copies), ML datasets, model weights, weekly mirror of the bucket | NFS/SMB share `nomal` |
| **Wiedisync bridge** | Inside Wiedisync's `kscw-endpoints` (Directus) | Mints the login token; serves roster/game sync to nomal-api | Small endpoints (§4) |

### 3.3 Why this shape

- **Same origin for app and API** (`nomal.kscw.ch` serves both): Nomal's own session cookie
  is host-only, Nomal's API needs no CORS, and the Android WebView just works.
- **Separate service, shared Postgres server**: keeps Wiedisync's Directus permissions out of
  the hot path and reuses the existing backup pipeline. No cross-database foreign keys:
  Nomal stores Wiedisync IDs and a synced mirror (§6.2).
- **Pull-based home worker**: the Lenovo server is behind NAT; it polls for work, nothing on
  the internet can reach it.
- **GPU as a pluggable backend**: the queue does not know where a GPU job runs. Our own GPU
  now, an EU or Swiss provider later, or a future box take the same job spec (§8.3).
- **Python everywhere except the browser**: API, workers and ML share one package.

---

## 4. Login and the Wiedisync bridge

### 4.1 Constraint found in Wiedisync

The Wiedisync login cookie (`kscw_session`) has been **httpOnly and host-only on
`directus.kscw.ch`** since 28.09.2026. No other host, including `nomal.kscw.ch`, can read it.
Wiedisync's other apps call Directus with credentialed CORS, which needs entries in the
Directus `CORS_ORIGIN` env and in `cors-credentials.js`.

Nomal avoids credentialed CORS entirely with a **redirect + signed token** flow (a minimal,
OIDC-shaped authorization flow).

### 4.2 Flow

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser
  participant N as nomal-api (nomal.kscw.ch)
  participant D as Directus bridge (directus.kscw.ch)
  participant W as Wiedisync login (wiedisync.kscw.ch)

  B->>N: GET /api/me
  N-->>B: 401
  B->>N: GET /api/auth/login?next=/v/123
  N-->>B: 302 to D /kscw/nomal/v1/authorize?state=S + short state cookie
  B->>D: GET authorize (top-level, kscw_session cookie sent: SameSite=Lax)
  alt not logged in to Wiedisync
    D-->>B: 302 to W /login?next=(authorize URL, allow-listed)
    B->>W: log in
    W-->>B: back to authorize
  end
  D-->>B: 302 to N /api/auth/callback?token=JWT(60 s, single use)&state=S
  B->>N: GET callback
  N->>N: verify Ed25519 signature, iss/aud/exp, state, jti unused
  N-->>B: Set-Cookie nomal_session (host-only, Secure, HttpOnly, Lax, 8 h) + 302 to next
```

- **Token**: EdDSA (Ed25519) JWT, `iss=wiedisync-prod|wiedisync-dev`, `aud=nomal`, 60 s,
  `jti` recorded by Nomal so it can be used once. The private key lives only in the Directus
  env; Nomal holds the public key(s), chosen by `kid` (rotation = add new, retire old).
- **Return URLs are allow-listed** in the bridge. No open redirect.
- **Claims are minimal**: member ID and UUID, display name, global roles, sport-admin scopes,
  team relations for the current and previous season. No email, no birthdate.

```json
{
  "iss": "wiedisync-prod", "aud": "nomal", "sub": "<members.uuid>", "mid": 123,
  "name": "First Last", "roles": ["user"], "sport_admin": [],
  "teams": [{"team": 92, "season": "2026/27", "rel": ["player", "captain"]}],
  "state": "…", "jti": "…", "iat": 0, "exp": 60
}
```

- **Session**: server-side row (hashed ID in the cookie), 8 h, renewed by repeating the
  redirect silently. Role changes reach Nomal within 8 h at most; admins can sign everyone out.
- **Logout** ends the Nomal session; the UI links to Wiedisync logout.

### 4.3 Server-to-server sync

`nomal-api` keeps a small read-only mirror of the Wiedisync data it needs (§6.2):

- `GET /kscw/nomal/v1/teams` (metadata of every current and previous-season team) and
  `GET /kscw/nomal/v1/sync?teams=<ids>` (only the enabled teams) on the bridge, with an
  `X-Nomal-Service-Token` header, called over the VPS's Docker network (no Cloudflare hop). A
  full snapshot each time; at this size there is no need for incremental sync.
- Returns only **Nomal-enabled teams** (current + previous season), their rosters (member
  ID, UUID, name, jersey number, positions, relation, guest flag), and their games and
  trainings (ID, date, time, home/away names, league, hall name, **official set scores** from
  `games.sets_json`). Never birthdates.
- Runs hourly, on admin demand, and right before an upload is accepted.

### 4.4 Changes needed in Wiedisync (small, separate PR there)

| Change | Where | Notes |
|---|---|---|
| `GET /kscw/nomal/v1/authorize` | new `kscw-endpoints/src/nomal-bridge.js` | Reads `req.accountability`, builds claims, signs, redirects. Logs issuance with `writeUserLog`. |
| `GET /kscw/nomal/v1/teams`, `GET /kscw/nomal/v1/sync?teams=` | same file | `X-Nomal-Service-Token` (not `Authorization: Bearer`, which Directus would claim), internal network only, read-only, versioned contract with tests. |
| `GET /kscw/nomal/v1/adult-check?team=&date=` | same file | Pass/fail per rostered person for a recording date (§5.3); birthdates never leave Wiedisync. |
| Allow-listed cross-host `next` on `/login` | `LoginPage` / `AuthRoute` (`?next=` exists for in-app paths) | Only the bridge's authorize URL. |
| ~~Team toggle in `teams.features_enabled`~~ | — | **Changed in M0 (ADR-001):** full admins enable teams **in Nomal**, per lineage. The bridge returns personal data only for the team ids Nomal asks for, so Wiedisync needs no migration and coaches cannot enable their own team. |
| Env | Directus `.env` (prod + dev) | `NOMAL_JWT_PRIVATE_KEY`, `NOMAL_JWT_KID`, `NOMAL_RETURN_URLS`, `NOMAL_SYNC_TOKEN_HASH`, and on dev `NOMAL_LOGIN_URL=https://wiedisync-dev.kscw.ch/login`; recreate the container (restart does not re-read env). |
| Later: "Video" link on game cards | `GameCard` | Plain link to `nomal.kscw.ch/games/<id>`. |
| Docs | `INFRA.md`, `SECURITY.md`, `PERMISSIONS.md` | Same commit, per Wiedisync rules. |

No `CORS_ORIGIN` change is needed. A Cloudflare Tunnel public hostname `nomal.kscw.ch`
(and `nomal-dev.kscw.ch`) pointing at the API's local port is.

---

## 5. Permissions, privacy and revDSG

*Not legal advice. The goal is a design a Swiss data-protection review would find
reasonable; have the club's privacy contact read §5 before launch.*

### 5.1 Who can do what

Relations come from the login token and Nomal's `team_grant` table (analysts). "Team" means
the team's **lineage** (§6.3) unless a season is named.

| Action | Player | Analyst¹ | Coach / team manager | Sport admin (that sport) | Admin / superuser² |
|---|---|---|---|---|---|
| Watch team videos, tags, clips | own seasons³ | ✓ | ✓ all seasons | ✓ | ✓ (logged, banner) |
| Create own private reels, comment on clips | ✓ | ✓ | ✓ | ✓ | |
| Share a reel with the team | | ✓ | ✓ | ✓ | |
| Upload / link a video, edit roster snapshot | | ✓ | ✓ | ✓ | |
| Tag, mark rallies, correct scores and model output | | ✓ | ✓ | ✓ | |
| Download / export MP4 (watermarked) | | | ✓ | ✓ | |
| Delete a video | | | ✓ | ✓ | ✓ |
| Grant analyst | | | ✓ | ✓ | |
| Enable a team for Nomal | | | | | in Wiedisync |

¹ A player a coach picks to help tag; Nomal-local grant per team lineage.
² Board members without an admin scope get no access.
³ Players see the seasons they were rostered in; they lose access when they leave.

The rules live in **one module** (`nomal/core/permissions.py`) with a table-driven test
that enumerates this matrix. Every API route goes through it.

### 5.2 No public links

- Every media request goes through `nomal-api` with the session cookie. Segments get a
  302 to an object-storage URL **signed for 5 minutes, for one 4-second segment** (§7.6).
- Exports (MP4) are for coaches only, **burned-in watermarked** ("KSCW intern · <name> ·
  <date>"), expire after 30 days, and are downloaded through the same redirect (signed at click).
- No share-by-link feature exists. Sharing means "visible to the team in Nomal".

### 5.3 Adults only: nothing under 18 filmed or stored

Enforced in code, not just stated:

1. **Hard check against `members.birthdate`.** For a recording date, a rostered person
   (players, guests, coaches, team managers) passes only if a birthdate exists and they are
   18 or older on that date. **No birthdate = fails** (the year of birth alone is not accepted).
   The check runs inside Wiedisync (`adult-check`), so birthdates never reach Nomal.
2. **Before filming**: the team page in Nomal shows each upcoming game as *filming allowed* or
   *blocked*, with the reason ("2 roster members without birthdate"), so a coach knows before
   setting up the camera. The filming guide says: no camera if blocked.
3. **Before storing**: every upload, inbox file and YouTube link re-runs the check for the
   recording date. Blocked means refused before any bytes are stored (browser uploads are
   checked before the first part is signed; refused inbox files are deleted from the share).
4. **Nightly**: if someone who fails the check joins an enabled team, new uploads freeze, the
   coach and admin are notified, existing videos are flagged for review.
5. **Opponents**: Nomal cannot verify opponents' ages. The club's position (15.10.2026): adult
   leagues have no under-18 opponents, so adult-league matches are filmed without a per-upload
   confirmation. Opponents appear by jersey number only, their data stays team-internal, and
   footage is deleted or trimmed on request. Recorded in `docs/PRIVACY.md`.

**DU23-1: trainings only** (decided 15.10.2026). Its opponents are U23 teams that very likely
field under-18 players, which Nomal cannot check, so DU23-1 matches are not filmed for Nomal.
Its trainings pass the same hard check as everyone else: every rostered person needs a
birthdate and must be 18 or older on the day, otherwise the training is blocked too. Nomal
enforces this with a per-team policy (`team_policy.matches_allowed = false`, §6.10): uploads
linked to a game are refused for that team, and its upcoming games show *filming not allowed*.

### 5.4 Consent and information

**No per-player consent** (decided 16.10.2026, replacing the opt-in of the first plan).
Swiss Volley allows anyone to record a match (Volleyballreglement 2026/27, Art. 86 "video
recording": third parties may record; the home team provides the opponent's camera spot
behind the court, centred, about 3 m high). The club films its own matches and trainings
on that basis and informs its members instead of asking each one: the privacy page and
`docs/PRIVACY.md` say what is filmed, who sees it, the processors (§5.6) and GPU processing
"on club hardware, or at a provider in Switzerland or the EU named on the privacy page", so
the planned move (§8.6) needs an updated privacy page and a notice to members. A member who
objects asks a coach or admin, who can delete a video or untag them. The adult gate (§5.3)
and the team policy stay.

The club's privacy policy on `kscw.ch` gets a Nomal section; Nomal has its own short privacy
page (what, why, who sees it, how long, processors, contact, rights).

### 5.5 Retention

| Data | Where | Default retention |
|---|---|---|
| Upload staging | Object storage `incoming/` | Until ingested; lifecycle rule deletes after 14 days regardless |
| Analysis copies (GPU input) | Object storage `analysis/` | Deleted when the GPU job finishes; lifecycle rule after 3 days |
| HLS renditions, thumbnails | Object storage `media/` | Recording season + 1 (a 2026/27 match until 31.05.2028). After the recording season, the 1080p rendition is dropped and 540p kept (§16 cost lever) |
| Raw originals | NAS `nomal/raw/` | Same as renditions; short labelled windows may stay in the ML dataset (§11) |
| Exports | Object storage `exports/` | 30 days |
| Tags, rallies, scores, rosters | Postgres | As long as the video; member link removed when a player withdraws or leaves and asks |
| ML datasets, models | NAS | Until the model using them is retired; listed in a dataset manifest |
| Audit log | Postgres | 12 months |
| Sessions, login nonces | Postgres | Purged after 30 days |

A nightly `retention` job enforces this and logs what it deleted.

### 5.6 Processors and where data goes

| Processor | Data | Where | Note |
|---|---|---|---|
| Hetzner (VPS, Object Storage) | Everything Nomal stores | Germany / EU | Adequate country; DPA with Hetzner |
| Cloudflare | HTML, JSON, redirects (no video bytes) | Global edge | Already the club's provider |
| GPU processing | Analysis copies (video), no names, no IDs | **Now: club hardware at home** (no third party). Later: an EU or Swiss provider | The move needs a DPA (§8.6) |
| YouTube (Google) | Embedded playback of videos from the club's and members' channels only | Google | Only on YouTube-sourced videos, via `youtube-nocookie.com` |

**Decision (15.10.2026): GPU inference starts internal**, on club-controlled hardware at home,
so no video leaves the club for processing. The later move (§8.6) goes to a **Swiss or EU
provider only**, under a DPA. US providers are excluded: Modal, for example, is a US company;
on its free plan jobs run in US regions, even region-pinned jobs (Team plan, about USD
250/month) pass its US control plane per its docs, and no public Swiss-US Data Privacy
Framework certification or DPA was found (15.10.2026). After the move the same minimisation
applies: the provider receives only temporary, court-cropped analysis copies of the live
segments, without audio unless a task needs it, with no names or IDs, deleted after the job.

### 5.7 Things Nomal never does

- No face recognition, no face embeddings, no person re-identification across videos.
  Player attribution uses **jersey numbers and position on court only**. (Biometric data used
  for unique identification is sensitive personal data under the revDSG; avoid it entirely.)
- Detection is masked to the court polygon plus a margin, which also excludes spectators.
- No third-party analytics. The only third party in the browser is the YouTube embed, only on
  YouTube-sourced videos.

### 5.8 Audit log

Records logins, video views (once per video per session), uploads, deletions, exports and
downloads, permission and consent changes, admin "view as", GPU submissions (which video,
which provider), retention deletions. Coaches see the log for their team's videos; members can
request their own entries.

### 5.9 Audio

Camera audio carries whistles (used for dead-time detection) and also bench talk. Default:
kept in renditions; a per-video "mute for viewers" switch strips it from playback. The NAS
original keeps it.

---

## 6. Data model

### 6.1 Overview

```mermaid
erDiagram
  wd_team ||--o{ wd_team_member : roster
  wd_team }o--|| team_lineage : "belongs to"
  wd_team ||--o{ wd_game : plays
  wd_member ||--o{ wd_team_member : "is on"
  team_lineage ||--o{ team_grant : analysts
  wd_team ||--o{ video : owns
  wd_game |o--o{ video : "recorded in"
  video ||--o{ video_source_file : "made of"
  video ||--o{ video_rendition : has
  video ||--o{ video_roster : snapshot
  video ||--o{ period : "sets / quarters"
  video ||--o{ segment : "rallies and dead time"
  video ||--o{ sync_point : "external clocks"
  video ||--o{ event : tags
  segment |o--o{ event : contains
  taxonomy ||--o{ event : "typed by"
  event ||--o{ event_revision : history
  segment ||--o{ segment_revision : history
  video ||--o{ clip : has
  reel ||--o{ reel_item : lists
  video ||--o{ job : processing
  job |o--o| model_run : produces
  model_run ||--o{ analysis_artifact : outputs
  video ||--o| calibration : "court homography"
  video ||--o| scoreboard_region : "OCR area"
  job ||--o{ compute_usage : "GPU seconds"
```

Sketches below are **not final DDL**; they fix names, keys and the shape of the hard parts.
IDs are UUIDv7 (time-ordered, client-generatable) except mirrored Wiedisync IDs (integers).
Timestamps are `timestamptz`. Video times are **integer milliseconds of media time**, plus
the frame number where it matters.

### 6.2 Wiedisync mirror (read-only, replaced by sync)

```text
wd_member        id int PK (= members.id), uuid, display_name, number, positions[], active, synced_at
wd_team          id int PK (= teams.id), lineage_id → team_lineage, name, sport
                 ('volleyball'|'basketball'), season '2026/27', league, team_key, listed, synced_at
                 -- every listed team (metadata only); personal data only for enabled lineages
wd_team_member   (team_id, member_id, rel, is_guest)  rel ∈ player|coach|team_responsible|captain
wd_game          id int PK (= games.id), team_id, date, time, home_name, away_name,
                 our_side 'home'|'away', league, hall_name, home_score, away_score,
                 official_sets jsonb  -- from games.sets_json, used by auto scoring (§10.3)
wd_training      id int PK (= trainings.id), team_id, date, start_time, hall_name
adult_check      (team_id, date) → passed bool, failing_count, checked_at   -- no birthdates
sync_state       last_run, last_ok, counts, error
```

No email, phone, address or birthdate ever reaches Nomal.

### 6.3 Seasons and team lineage

Wiedisync **clones every team row on 1 June** (new IDs). A video belongs to the team row of its
season (`video.team_id`). For "this team across seasons" Nomal has `team_lineage (id, sport,
label)`, matched at sync by `(sport, teams.team_id)`, falling back to `(sport, name)`; an admin
can fix a wrong match. **Whether a team uses Nomal is a flag on its lineage**
(`team_lineage.enabled`, set by full admins in Nomal; ADR-001), so it carries over to the new
season's team row. Sport is always part of the key: "H1" exists in both sports.

### 6.4 Videos

```text
video             id, team_id → wd_team, kind 'match'|'training'|'other',
                  game_id → wd_game NULL, training_id → wd_training NULL,
                  title, sport, recorded_at, recording_started_at NULL (wall clock, for imports),
                  source 'file'|'youtube', youtube_id NULL, youtube_channel_id NULL,
                  replaced_youtube_offset_ms NULL,
                  status 'awaiting_upload'|'uploading'|'queued'|'processing'|'ready'|'failed'|'deleted',
                  duration_ms, fps_num, fps_den, width, height, has_audio, audio_muted,
                  capture jsonb  -- camera make/model, codec, bitrate, VFR flag, from metadata
                  meets_standard bool, standard_issues text[]  -- e.g. '25fps', '720p'
                  retention_until, created_by, created_at, deleted_at, deleted_by
video_source_file video_id, part_no, nas_path, bytes, sha256, duration_ms, offset_ms
video_rendition   video_id, name '1080p50'|'540p25', codec, bitrate, segment_seconds,
                  segment_count, storage_prefix, bytes, job_id
video_roster      video_id, side 'us'|'them', number, member_id NULL (us only),
                  position NULL, is_libero    UNIQUE(video_id, side, number)
inbox_file        id, nas_path, size, probed jsonb, recorded_at_guess, status, video_id NULL
```

- Cameras split long recordings into several files: `video_source_file` keeps the parts and
  their offsets; ingest joins them losslessly.
- `meets_standard = false` (e.g. 25 fps, 720p) still plays and can be tagged, but is skipped
  by tier 2 and shown with a hint.
- `video_roster` is a **snapshot** (numbers differ between teams, guests play); tags resolve
  number → member through it, so later roster changes never rewrite history.

### 6.5 Taxonomy (sport-specific, data not code)

```text
taxonomy         sport, version, code, name_key (i18n), hotkey, category,
                 default_pre_ms, default_post_ms, lead_ms,
                 attributes_schema jsonb (JSON Schema: grade, zones, sub-types…),
                 starts_rally bool, ends_rally_on jsonb, active
                 PK (sport, version, code)
```

Seeded from reviewed YAML (`taxonomies/volleyball.yaml`, `taxonomies/basketball.yaml`).
Volleyball follows **DataVolley conventions** (skills S R E A B D F, grades `# + ! - / =`,
zones 1–9). A taxonomy change creates a new `version`; a mapping table translates for datasets.

### 6.6 Periods, segments, scores

```text
period           id, video_id, no (set or quarter), start_ms, end_ms,
                 our_court_side_at_start 'near'|'far', side_switch_at_score NULL (5th set: 8),
                 final_us, final_them, source, review
segment          id, video_id, period_id NULL,
                 kind 'rally'|'possession'|'timeout'|'set_break'|'warmup'|'stoppage'|'other_dead',
                 start_ms, end_ms,
                 serving 'us'|'them' NULL, serving_court 'near'|'far' NULL,
                 won_by 'us'|'them' NULL, score_us_after NULL, score_them_after NULL,
                 score_source 'tags'|'serve_inference'|'scoreboard_ocr'|'manual'|'import' NULL,
                 score_confidence NULL, score_conflict bool,
                 source 'manual'|'model'|'import', import_system NULL, import_ref NULL,
                 model_run_id NULL, confidence NULL,
                 review 'unreviewed'|'confirmed'|'corrected'|'rejected',
                 created_by, updated_by, timestamps, deleted_at, version
segment_revision same shape as event_revision (§6.7), written by trigger
sync_point       id, video_id, video_ms, system ('openvolley'|'scoreboard'|'manual'),
                 external_ts NULL, external_ref NULL, note
```

- **Skip dead time** = play only `rally`/`possession` segments. Everything between rallies is
  dead by default; explicit dead kinds (`timeout`, `set_break`, `warmup`) exist for reports
  ("points after a timeout") and for training the gap classifier.
- **Scores live on rallies** (`score_*_after`, `won_by`), with where they came from and how
  sure the system is. A coach correction is a revision, i.e. a training label.
- **Ready for OpenVolley later**: `source='import'`, `import_system`, `import_ref` on segments,
  and `sync_point` to map the e-scoresheet's wall-clock point times onto video time.

### 6.7 Events (the tags)

```text
event            id (client UUIDv7), video_id, segment_id NULL, sport, taxonomy_version, code,
                 t_ms, frame, time_precision 'live'|'adjusted'|'frame',
                 side 'us'|'them', number NULL, member_id NULL,
                 attributes jsonb   -- grade, zone_from, zone_to, subtype, made/missed, …
                 court_x NULL, court_y NULL   -- metres, from homography (tier 2)
                 clip_pre_ms NULL, clip_post_ms NULL, note NULL,
                 source 'manual'|'model'|'import', model_run_id NULL, confidence NULL,
                 review 'unreviewed'|'confirmed'|'corrected'|'rejected',
                 supersedes_id NULL, track_ref NULL,
                 created_by, created_at, updated_by, updated_at, deleted_at, version int
event_revision   id, event_id, version, changed_by, changed_at,
                 action 'create'|'update'|'delete'|'restore'|'confirm'|'reject', before jsonb, after jsonb
```

- Revisions are written by a **Postgres trigger**, so no code path can skip them.
- Deletes are soft: undo is trivial, rejected model output stays as a hard negative.
- The client generates the ID, so saving is an idempotent `PUT /api/events/{id}` (§9.6).

### 6.8 Clips, reels, comments, exports

```text
clip             id, video_id, start_ms, end_ms, title, note, event_id NULL,
                 owner, visibility 'private'|'team', created_at
comment          id, target_type 'clip'|'event'|'segment'|'video', target_id, t_ms NULL,
                 author, body, mentions int[], created_at, edited_at, deleted_at
reel             id, owner, lineage_id NULL, title, kind 'smart'|'manual',
                 query jsonb NULL, visibility 'private'|'team', created_at
reel_item        reel_id, position, video_id, start_ms, end_ms, event_id NULL, clip_id NULL, caption
export           id, kind 'clip'|'reel'|'condensed', source jsonb, params_hash,
                 status, object_key, bytes, requested_by, job_id, expires_at
```

- A **clip** is a time range. Watching it seeks inside the HLS stream: no file, no storage.
- A **per-player highlight reel** is a *smart reel*: a saved query ("member 123, attacks
  graded #, season 2026/27") evaluated on open, so it updates as tagging continues.
- An **export** renders a clip, reel or condensed match to MP4 with ffmpeg (§7.7).

### 6.9 Jobs, workers, ML provenance, budget

```text
job              id, kind, video_id NULL, params jsonb, priority, required_caps text[],
                 depends_on uuid[], status 'queued'|'leased'|'running'|'waiting_budget'|
                 'succeeded'|'failed'|'cancelled', attempts, max_attempts, not_before,
                 lease_owner → worker, lease_expires_at, heartbeat_at, progress, message,
                 backend NULL, backend_ref NULL, checkpoint jsonb, result jsonb, error,
                 created_at, started_at, finished_at
worker           id, name, caps text[], token_hash, paused, version, last_seen_at
model_run        id, job_id, model_name, model_version (git sha), weights_sha256,
                 dataset_manifest NULL, params jsonb, metrics jsonb, created_at
analysis_artifact id, video_id, model_run_id, kind 'ball_track'|'player_tracks'|'audio_events'|
                 'motion'|'scoreboard_ocr'|…, object_key or nas_path, format 'parquet',
                 schema_version, stats jsonb
calibration      id, video_id, hall_name, image_points, court_points, homography (3×3),
                 method 'manual'|'auto', reproj_error_cm, created_by, copied_from NULL
scoreboard_region id, video_id, hall_name, rect, layout jsonb (digit boxes: home, away, sets,
                 clock), created_by, copied_from NULL
compute_usage    id, job_id, backend, gpu_type, gpu_seconds, cpu_core_seconds, gib_seconds,
                 est_cost_chf, at
budget           period ('2026/27'), line ('cloud_gpu'|'test_gpu'), cap_chf
```

Per-frame data (ball positions, player boxes, OCR readings) is **not** stored in Postgres: a
2 h match at 50 fps is 360 000 frames. It goes into Parquet files referenced by
`analysis_artifact`; only derived facts (segments, scores, serve placements, proposed events)
become rows.

### 6.10 People, access, privacy

```text
session          id_hash, member_id, claims jsonb, created_at, expires_at, last_seen_at, user_agent
login_nonce      jti, used_at
team_grant       lineage_id, member_id, grant 'analyst', granted_by, granted_at, revoked_at
consent          (unused since 16.10.2026, §5.4; dropped after M4) member_id, scope 'nomal', notice_version, status 'accepted'|'withdrawn',
                 via 'nomal'|'paper', recorded_by, at
youtube_channel  channel_id, label, owner ('club' | member_id), proposed_by, confirmed_by,
                 confirmed_at    -- allow-list: the club's and members' own channels
team_policy      lineage_id, matches_allowed bool, trainings_allowed bool, reason, set_by, set_at
                 -- e.g. DU23-1: matches_allowed = false (§5.3)
audit_log        id, at, actor_member_id, action, object_type, object_id, details jsonb
```

---

## 7. Video pipeline

### 7.1 Capture standard (goes into `docs/FILMING_GUIDE.md`)

Each team brings its own camera. Nomal does not need to know the model: it reads resolution,
frame rate, codec and bitrate from every file and flags videos below the standard (§6.4). The
guide states the standard; camera-specific tips are added only if a coach asks.

| Setting | Standard | Why |
|---|---|---|
| Position | **Volleyball: behind the court**, tripod centred on the end line, **2.5–3 m high** (at least 1.5 m). **Basketball: side of the court** (below) | Whole court visible, net divides the teams. Same spec as Hudl/Balltime (8–10 ft, min 5 ft), so footage works in either |
| Framing | Whole court + ~2 m margin, **all four far-court corners visible**, **fixed zoom**, never moved during the match | One homography per video; consistent ML input |
| Lens | **No wide-angle or fisheye mode** (basketball exception below) | Distortion breaks court calibration |
| Rate / resolution | **50 fps, 1080p minimum**; 4K50 if the camera can | Far-court ball tracking needs pixels; 50 fps for fast actions, and it matches 50 Hz hall lighting (60 fps can flicker under it) |
| Shutter | **1/500 s or faster**; if a hall's lights show flicker bands at that speed, use the fastest speed without banding | Motion blur is what kills ball tracking |
| Exposure, focus, white balance | Locked (manual) | Auto modes pump during play |
| Audio | On | Whistles drive dead-time detection |
| Scoreboard | In frame if possible without losing court | Enables scoreboard OCR (§10.3) |
| Start | From warm-up, continuous; split files are fine | Ingest joins parts |
| Check | Nomal shows *filming allowed / blocked* for the game (§5.3) | Adults only |

**Basketball (Herren 1): side of the court, not behind it.** From behind the baseline (the
volleyball position) the far basket is 28 m away and players stand in a line one behind the
other, so the far half is small and hidden. For basketball the camera goes on the **long side
at the half-court line**, as high as the hall allows (gallery or top row of the bleachers),
framing both baskets. It stays fixed (no panning), so tier 2 can still calibrate the court
once per video. If the hall is too narrow to fit both baskets, a mild wide-angle setting is
acceptable for basketball only (never fisheye); the calibration then also corrects the lens
distortion (§10.6). See §19, O8.

### 7.2 Ways in

| Path | For | How |
|---|---|---|
| **NAS inbox** (preferred at home) | Anyone with SD-card access at home | Copy files into the `nomal-inbox` share. The Lenovo worker sees stable files (size unchanged for 2 min), probes them, groups split parts, registers *inbox files*. In Nomal the coach picks the game (suggested from recording time vs. games that day). Nothing crosses the internet twice. |
| **Browser upload** | Coaches elsewhere | Resumable S3 **multipart** upload straight to object storage (`incoming/<video>/`), parts signed by the API (64 MB parts → files up to ~640 GB; 4 in parallel; survives reloads). Several files per video (split recordings). The Lenovo worker downloads, archives to NAS, deletes the staging object. |
| **YouTube link** | The club's own channel | Accepted only if the video's channel is on the allow-list: the club's channel and members' own channels (each player can have one). A member proposes their channel, a coach or admin confirms it; every link's channel is checked with one YouTube Data API metadata call (no download). Later, members could prove ownership by signing in with Google. Plays through the YouTube IFrame API (nocookie) with the same tagging UI; tags, clips, reels and skip-dead-time (manual rallies) work. **No export, no tier 2** (no file; YouTube may also serve fewer than 50 fps). Nomal does not download from YouTube (YouTube's terms). The UI keeps nudging: "Upload the original for 50 fps, export and automatic analysis". When the original arrives, the video switches to `source='file'`, keeping all tags; if the two versions start at different points, the coach marks one shared moment (e.g. the first serve whistle) and Nomal stores the offset. |

Before any of these is accepted: permission, team policy and adult gate (§5.3). Refused
means no bytes stored. YouTube-hosted footage sits outside Nomal's access control (whoever has
the YouTube link can watch it), so the club and members should upload as *unlisted*; the
adults-only rule applies just the same.

Wiedisync already stores video links per game (`game_recordings`, often YouTube). Later the
sync can carry them, so a coach can pick an existing link as a source in one click when its
channel is on the allow-list.

### 7.3 Processing chain

```mermaid
flowchart LR
  A[inbox parts or<br/>incoming/ objects] --> I[ingest on Lenovo<br/>probe · join parts · checksum · raw to NAS]
  I --> T1[transcode 1080p50]
  I --> T2[transcode 540p25]
  I --> TH[thumbnails + sprite]
  I --> AU[audio 16 kHz]
  T1 & T2 & TH --> R[video ready]
  AU --> CPU[CPU analysis on Lenovo<br/>whistles · motion · scoreboard OCR]
  CPU --> DT[dead-time proposals]
  CPU --> AC[analysis copy<br/>live segments, court crop]
  AC --> G[GPU job<br/>serve side · ball · players]
  G --> SC[score + event proposals]
```

Each box is a `job` row (§8). `ready` needs both renditions and thumbnails; tier 2 jobs follow
automatically for matches that meet the standard, once those features exist.

### 7.4 Renditions

| Rendition | Use | Encoding (H.264 High, AAC) | ≈ size |
|---|---|---|---|
| `1080p50` | Tagging, desktop viewing, frame stepping | ~5 Mb/s VBR (max 7), closed GOP 2 s, 4 s fMP4 HLS segments | 2.3 GB/h |
| `540p25` | Phones, weak connections | ~1.2 Mb/s, GOP 2 s, 4 s segments | 0.6 GB/h |
| Thumbnail sprite + WebVTT | Scrub previews | 1 frame / 10 s, 160 px wide | few MB |

H.264 because every browser and the Android WebView decode it in hardware. Inputs vary by
camera (H.264/HEVC, 8/10-bit, sometimes variable frame rate); ingest normalises to constant
50 fps. The Lenovo server encodes with VAAPI on the Radeon 780M where quality allows, else
x264 with a CPU cap. Both are faster than real time for 1080p.

### 7.5 Storage tiers

| Tier | Where | Holds | Size per season (100 matches × ~1.75 h) |
|---|---|---|---|
| Hot | Hetzner Object Storage | HLS renditions, thumbnails, exports, staging, analysis copies | ≈ 0.5–0.6 TB |
| Master | NAS `nomal/raw/` | Original camera files (25–50 Mb/s) | ≈ 2–4 TB (NAS has ≈ 18 TB free) |
| ML | NAS `nomal/datasets/`, `nomal/models/` | Labelled windows, Parquet tracks, weights | tens of GB |
| Mirror | NAS `nomal/bucket-mirror/` | Weekly `rclone sync` of the bucket | = hot |

Raw originals are **not** copied offsite (2–4 TB/season; renditions are the fallback). The
database is backed up with Wiedisync's existing pipeline (§15.2).

### 7.6 Playback: HLS with short-lived segment URLs

As decided: the authenticated API serves the playlist, segment URLs are signed with a TTL of
minutes, and the player keeps going for the full match.

1. hls.js loads `GET /api/media/{video}/{rendition}/index.m3u8` from `nomal.kscw.ch` with the
   session cookie. The API checks permission and returns a VOD playlist whose segment URIs
   point back at the API: `/api/media/{video}/{rendition}/seg/{n}.m4s`.
2. As the player needs each 4-second segment, it requests that URI; the API checks the
   session (cached per session + video for 60 s) and answers **302** to an object-storage URL
   **signed for 5 minutes, for that one segment**. Signing is a local HMAC, no network call.
3. The browser fetches the bytes directly from object storage (bucket CORS allows
   `nomal.kscw.ch`).

So signing happens continuously as playback advances: a 2 h match (~1 800 segments per
rendition) plays without interruption, while any copied URL is either useless without the
session (API URL) or dead within 5 minutes and covers 4 seconds (storage URL). Cloudflare
carries only the tiny 302s.

Fallback, if per-segment redirects ever add noticeable start-up latency: the API returns the
playlist with **directly signed segment URLs** (TTL ~10 min), and an hls.js custom loader
re-fetches the playlist before the signatures expire or on a 403. Same security properties.

Seeking is exact; frame stepping uses `requestVideoFrameCallback` for the true media time.
iPhones: hls.js runs on iOS 17.1+ (Managed Media Source); older iOS uses native HLS, which
follows redirects. **Test on an iPhone in M1.**

### 7.7 Clips, reels, condensed matches, exports with ffmpeg

- **Watching** a clip, reel or condensed match never renders anything: the player seeks to
  `start_ms`, plays to `end_ms`, jumps to the next item (preloading it). Clip windows come from
  the taxonomy (e.g. attack −3 s/+2 s), adjustable per event.
- **Skip dead time** is the same mechanism over rally segments, with 2 s pre-roll (serve toss)
  and 1.5 s post-roll (reaction).
- **Export** (coach only) queues an `export` job on the Lenovo server: cut from the NAS
  original (best quality; falls back to the 1080p rendition), re-encode each piece for
  frame-accurate in/out points, concatenate, burn in the watermark and optional captions
  (player, opponent, date, score), upload to `exports/`, notify. A 2-minute reel takes seconds;
  a condensed match a few minutes.

---

## 8. Jobs and workers (how the GPU gets work)

### 8.1 Queue in Postgres, workers pull

No message broker. The `job` table *is* the queue; only `nomal-api` touches it. Workers call
four endpoints over the **tailnet** (worker routes are refused when a request arrives through
Cloudflare):

| Endpoint | Does |
|---|---|
| `POST /api/worker/claim` `{caps, max}` | Atomically leases the highest-priority runnable job whose `required_caps` ⊆ the worker's caps and whose dependencies succeeded (`SELECT … FOR UPDATE SKIP LOCKED`). Lease 5 min. Long-polls up to 30 s. |
| `POST /api/worker/jobs/{id}/heartbeat` `{progress, message, checkpoint}` | Extends the lease, stores progress and a resumable checkpoint. |
| `POST /api/worker/jobs/{id}/sign` `{op, keys}` | Signed PUT/GET URLs for the objects this job may touch. **Workers never hold storage credentials.** |
| `POST /api/worker/jobs/{id}/complete` / `fail` | Finishes the job; the API writes derived rows in one transaction. |

A reaper returns expired leases to the queue (`attempts+1`, back-off) or fails them after
`max_attempts`. Each worker has its own token (hashed in `worker`), revocable from the admin page.

### 8.2 Workers

| Worker | Caps | Notes |
|---|---|---|
| **Lenovo server** (`media`) | `ingest`, `inbox`, `transcode`, `thumbs`, `audio`, `cpu_analysis`, `analysis_copy`, `export`, `retention`, `nas` (+ `gpu_dispatch` once a provider backend exists) | Always on. Container limits (e.g. 6 CPUs, 8 GB) and `nice` so DNS and the other services on the box stay responsive. Mounts the NAS share. |
| **Framework 16** (`gpu`) | `cuda`, `ml`, `nas` | Internal GPU worker, always on. **Busy guard**: stops claiming jobs while a training run or other GPU-heavy work uses the GPU (checked from free VRAM, plus a `nomal-worker pause` switch); optionally only at night. ML jobs process the analysis copy in 5-minute chunks and checkpoint, so a pause or reboot loses at most one chunk. Laptop care: suspend disabled, battery charge limit ~80 % in firmware, good airflow when docked. A dev agent (`--env dev`) runs beside it, off by default. |

### 8.3 The GPU interface (backend-agnostic)

GPU work is **one job per video** (`ml_analyse`), not one per model, so models load once. Which
machine runs it is a backend choice; every backend runs the same image with the same spec:

```text
GpuBackend
  submit(spec) -> handle          # start the run
  poll(handle) -> state, progress, metrics
  cancel(handle)

spec (JSON)
  job_id, image "ghcr.io/ksc-wiedikon/nomal-ml:<version>", tasks ["serve_side","ball","players"]
  inputs   analysis copy + time map + calibration (NAS path, or signed GET for a provider)
  outputs  Parquet + result.json (NAS path, or signed PUT for a provider)
  limits   min_vram_gb 16, max_seconds, max_cost_chf

container entrypoint:  nomal-ml run --spec spec.json   -> writes outputs, exits 0/1
```

| Backend | How it runs the image | Use |
|---|---|---|
| `local-agent` | A GPU host runs `nomal-worker` with `cuda` caps, **pulls** `ml_analyse` from the queue like any worker, and runs `nomal-ml run` locally; inputs and outputs on the NAS | **Production now** (Framework 16) |
| `cloud-<provider>` | The Lenovo dispatcher claims the job and **pushes** it to a provider adapter that starts the image on a rented GPU; inputs and outputs are pre-signed object URLs | Later: an EU or Swiss provider (§8.6) |
| `fake` | Returns canned outputs | CI |

The `nomal-ml` container never calls Nomal's API: inputs and outputs are URIs in the spec, and
the agent or the dispatcher relays progress and results over the tailnet. Moving to a provider
touches only the adapter. For a provider, the analysis-copy input URLs are the only long-ish
signed URLs in the system: they point at a temporary object no user ever sees, deleted when the
job ends.

**Budget guard.** Every run records GPU, CPU and memory seconds and an estimated cost in
`compute_usage` (electricity for `local-agent`). For a provider backend, the dispatcher checks
the season's spend against the `budget` cap before submitting; when the cap would be exceeded,
the job waits in `waiting_budget` and admins are told.

### 8.4 Job kinds

| Kind | Caps | Output |
|---|---|---|
| `ingest` | `ingest`, `nas` | Probe, join parts, SHA-256, raw on NAS, staging deleted |
| `transcode` (per rendition) | `transcode` | HLS in `media/{video}/{rendition}/` |
| `thumbs`, `audio` | `thumbs` / `audio` | Sprite + VTT; 16 kHz mono FLAC on NAS |
| `cpu_analysis` | `cpu_analysis` | Whistle events, motion energy, scoreboard OCR (Parquet); dead-time proposals |
| `analysis_copy` | `analysis_copy` | Live segments ±3 s, cropped to the court, 1080p50, to `analysis/` |
| `ml_analyse` | `cuda` + `ml` (internal) or `gpu_dispatch` (provider), set by the active GPU backend | Serve side, ball track, player tracks → score and event proposals |
| `export` | `export` | MP4 in `exports/` |
| `retention`, `bucket_mirror` | `retention` / `nas` | Deletions (audited), weekly mirror |

### 8.5 Visibility

Admin page: queue (with age of the oldest queued job), workers, GPU spend this season vs. cap,
recent failures with logs. Uptime Kuma (already at `status.kscw.ch`) gets push monitors for the
worker heartbeats (Lenovo server, GPU PC) and for "a job has been queued > 24 h".

### 8.6 Moving to an EU or Swiss GPU provider

Decided 15.10.2026: GPU inference starts internal and moves to a Swiss or EU provider when that
is worth it. The move is prepared now, so later it is an adapter plus a configuration change.

**When to move** (any one of these):

- GPU jobs regularly wait: the oldest GPU job is older than 7 days, or more than 20 % of a
  season's matches waited longer than 48 hours.
- The Framework 16 fails, is replaced, or its 8 GB GPU becomes the bottleneck (bigger models,
  tier 3).
- Tier 3 needs more GPU time than the laptop provides next to development and training.
- Running it at home becomes a nuisance (noise, heat, training and inference fighting over
  the one GPU).

**Must have**: a Swiss or EU company; processing and storage only in Switzerland or the EU/EEA,
with no inputs routed through a non-European control plane; a DPA under revDSG/GDPR; runs a
container image from a private registry; billed per second or minute without a monthly minimum
(or an hourly instance that starts fast enough to be used per job); a GPU with at least 16 GB;
an API or CLI to start a run and poll it. **Nice to have**: a Swiss data centre, ISO 27001,
scale to zero, free egress, start-up credits.

**Steps**:

1. Shortlist from the research note (`docs/research/gpu-providers-eu-ch-2026-10.md`).
2. Benchmark the S0 match on the top two, paid from the test-GPU line: wall time, GPU-hours,
   CHF per video, start-up time. Outputs must match the internal run within tolerance.
3. Sign the DPA; add the provider to `docs/PRIVACY.md` and the privacy page; tell members (the
   privacy page already covers "a provider in Switzerland or the EU named on the privacy page").
4. Implement the `cloud-<provider>` adapter (submit, poll, cancel), analysis copies to object
   storage, and the CHF cap in the budget guard.
5. Run both backends in parallel on five matches and compare.
6. Switch the default backend in config; keep `local-agent` as the fallback. An ADR records it.

**Shortlist** (research note, 15.10.2026; prices excl. VAT, to verify):

| # | Provider | Why | Season cost at 0.5 / 0.8 / 1.5 GPU-h per video | Main risks |
|---|---|---|---|---|
| 1 | **Infomaniak** (Geneva), L4 24 GB in Swiss data centres | Swiss company and law, FADP/GDPR DPA, CHF billing, free bandwidth, OpenStack | ≈ CHF 18 / 26 / 45 (L4 "from €0.29/h", to confirm) | GPU flavors on request; VMs, not jobs (needs the VM adapter); delete, don't stop, or the GPU keeps billing |
| 2 | **OVHcloud AI Training** (France), L4 24 GB | Managed "run an image to completion" jobs, per-minute billing, mature certifications | ≈ CHF 45 / 68 / ⚠ 122 | Fits CHF 80 only up to ~0.9 h per video; processing in France; new-project GPU quota |
| 3 | **Exoscale** (Lausanne), A30 24 GB in Geneva | Swiss, strongest certifications (ISO 27001/27017/27018, SOC 2, BSI C5), per-second billing | ≈ CHF ⚠ 82 / ⚠ 119 / ⚠ 204 | Over CHF 80 unless ≤ 0.5 h per video or the A30 is clearly faster; manual GPU screening |

Fallbacks: Scaleway L4 (Paris, per minute, VM), SwissGPU (Ticino, very cheap, no API or DPA
found), Seeweb (Italy, hourly, DPA unconfirmed), Verda V100 (Helsinki, cheapest, old GPU).

**Adapters this implies**: `openstack-vm` (Infomaniak; also OVHcloud instances): create a
server from a GPU flavor with cloud-init that pulls the image, runs `nomal-ml run`, uploads the
outputs and deletes its own server with a scoped application credential; `ovh-job`
(`ovhai job run`); `exo-vm` (Exoscale CLI/API). The image reads its spec from `NOMAL_SPEC_URL`
(a signed GET), so a provider only has to pass one environment variable. **Cost safety**: the
dispatcher also deletes any `nomal-job-*` server older than the job's maximum runtime, so a VM
that fails to delete itself cannot bill for days.

Questions to put to the providers before signing up: research note §4.

---

## 9. Tagging and playback (tier 1)

### 9.1 Layout (desktop; tagging is a keyboard task)

```
┌─────────────────────────────────────────────┬──────────────────────────────┐
│                                             │ Set 2 · 14:12 · serve: us    │
│                 video                       │ Roster  us: 1 3 5 7 9 11 …   │
│                                             │         them: 2 4 8 12 …     │
│                                             ├──────────────────────────────┤
│                                             │ Entry:  7A#_          ⏎ save │
│                                             │ → #7 Attack · Kill · 12:31.4 │
├─────────────────────────────────────────────┴──────────────────────────────┤
│ timeline: rally bands · dead time greyed · tag markers · score line        │
├────────────────────────────────────────────────────────────────────────────┤
│ event table (synced to playhead, click = seek, inline edit)                │
└────────────────────────────────────────────────────────────────────────────┘
```

On phones Nomal is a viewer (library, condensed matches, reels, "my clips", comments). A
touch layout with roster and skill buttons exists for tablets and as a fallback.

### 9.2 Keyboard (configurable, cheatsheet on `?`)

Single-letter commands work when the entry buffer is empty; typing a digit or `a` starts a tag.

| Key | Action |
|---|---|
| `Space` | Play / pause |
| `←` / `→` | 1 frame back/forward when paused, 2 s while playing |
| `Shift+←` / `Shift+→` | 5 s; `Ctrl+←/→` previous/next rally |
| `,` / `.` | Speed down / up (0.25×–2×) |
| `Shift+D` | Toggle **skip dead time** |
| `R` | Start rally here; again = end rally (winner `u`/`t`, preset from the last tag) |
| `T` / `B` | Mark timeout / set break (optional; for reports and training) |
| digits + skill + grade, `Enter` | Create a tag (grammar below) |
| `Tab` (while typing) | Toggle side us/them |
| `Esc` | Cancel entry |
| `Ctrl+Z` / `Ctrl+Shift+Z` | Undo / redo |
| `[` / `]`, then `C` | Mark clip in / out, save clip |
| `G`, then `F` | Go to the selected event; snap its time to the current frame |

### 9.3 Entry grammar

Volleyball (DataVolley-flavoured): `[a]<number><skill>[grade][zones]`, e.g. `7A#` = our #7
attack, kill; `a12S=` = their #12 serve error; `5R+` = our #5 reception, positive. Live
preview with names from the roster snapshot; unknown numbers flagged but allowed.

Basketball (Herren 1, from M4): `[a]<number><code>`, e.g. `23S3+` = our #23 three-point shot
made, `23S2-` missed, `11RO` offensive rebound, `11AS` assist, `7TO` turnover, `7ST` steal,
`9BL` block, `14F` foul, `5FT+` free throw made.

**Time capture**: the tag time is the playhead at the *first keystroke*, minus the taxonomy's
`lead_ms`, marked `time_precision='live'`; `F` snaps to the exact frame (`'frame'`).

### 9.4 Rallies and score from tags (volleyball)

A serve tag opens a rally if none is open. A terminal grade (`#` on attack/serve/block, `=`
error) proposes ending the rally, the winner and the next score; the coach confirms with
`Enter` or corrects. Tagging a match therefore also produces the rally list, the dead time
between rallies and the score line, which tier 2 is trained and evaluated against.

### 9.5 Skip dead time from day one

From M2, any video with rally markers (from `R`, from tags, or later from tier 2) plays in
**condensed mode**: rallies only, with pre/post-roll, timeouts and set breaks skipped, a rally
strip to jump around, and the score shown per rally. Before tier 2 exists, a coach who only
wants condensed playback can press `R` twice per rally while watching at 2×; that is the
fastest useful manual pass.

### 9.6 Never lose work

Every change goes to an IndexedDB outbox first and is sent as an idempotent
`PUT /api/events/{id}` with the client-generated ID and `version`. Network loss shows a
"n unsynced" badge; reload resumes. Concurrent taggers: last write wins per event, every change
is in the revision log.

### 9.7 Statistics and reports (M3b)

Per match and season, per player and team, each number clickable into a reel of the
underlying tags:

- Box score: counts by skill and grade, efficiencies (e.g. attack: (kills − errors −
  blocked) / attempts; reception: positive %, plus a 0–3 pass-rating view mapped from the
  DataVolley grades).
- Side-out % and break-point % from the rally list and the serving side.
- Rotation analysis: points, side-out and break-point per rotation, from the starting rotation
  entered per set (`period`) and the serving sequence (a team rotates on every side-out).
- Zone heatmaps for serves and attacks (zones 1–9 from the tags).
- Search bar and saved filters ("#3 attack #", "set 3, rotation 2, reception -").
- CSV export.

A **regrade queue** (Hudl-style: the list advances, number keys set the grade, `U` undoes)
makes grading passes or attacks after a quick first tagging pass fast.

---

## 10. Automatic analysis (tier 2)

Everything produces **proposals** (`source='model'`, `review='unreviewed'`) that a coach
confirms or corrects in one **Review** tab per video (modelled on Hudl's, §12.3: rally
boundaries, set boundaries, starting rotations, serving sides and scores, calibration, later
unidentified players; keyboard-first, the list advances on each answer). Corrections are
revisions and become training data.
Nothing is shown as fact until reviewed or until its measured accuracy passes the bar below.
CPU stages run free on the Lenovo server; only what needs a GPU goes to the GPU backend.

### 10.1 Dead-time skipping (volleyball)

The Balltime-style "skip the downtime" feature. Live play = rallies; everything else is dead.

- **v0, CPU only (Lenovo)**: referee **whistle detection** on the audio (narrow-band energy
  around 2–4.5 kHz with a tonal check and a duration window), **motion energy** inside the
  court mask, and a small state machine: dead → serve whistle → serve (toss/hit within a few
  seconds) → rally → end whistle → dead. Gaps are classified by duration and position: before
  the first serve = `warmup`; ~30 s = `timeout`; minutes between sets = `set_break`; else
  `other_dead`.
- **v1, learned**: a small temporal model over per-second features (audio embedding, motion,
  and, once available, ball-visible ratio and ball speed), trained on the manual rally segments
  from tier 1 and the corrected v0 output.
- **Review UI**: rally strip with `Enter` accept, `←/→` nudge a boundary, `X` reject, `M`
  merge, `S` split.
- **Bar**: boundary F1 ≥ 0.9 at ±1 s on held-out matches before condensed mode is the default
  view for new videos.
- **Bonus**: the live segments define the **analysis copy** for the GPU (§8.4), which cuts GPU
  time to the live share of the match.

### 10.2 Dead-time skipping (basketball, later)

Basketball play is continuous; dead ball means the game clock is stopped (fouls, free throws,
timeouts, quarter breaks). With the scoreboard in frame, **game-clock OCR** gives this almost
for free (clock running = live). Without it, whistles + motion as in v0. Planned after volleyball.

### 10.3 Automatic scoring (volleyball)

Rally scoring: **the team that wins a rally serves the next one.** So the score follows from
knowing who serves each rally, plus the rules and the official result:

1. **Serving court per rally** (near/far): a small image classifier on frames around the
   serve, trained on the manual serve tags from tier 1 (every serve tag is a labelled example);
   later cross-checked against where the ball track starts (§10.4).
2. **Court side → team** per set from `period.our_court_side_at_start` (one click per video,
   or inferred from the first set's serve tags), with the rules: teams change sides after each
   set and, in a deciding fifth set, when the leading team reaches 8.
3. **Winner of rally i = serving team of rally i+1.** The last rally of a set has no next
   serve; its winner is whoever reaches the set-winning score.
4. **Constraint from the official result**: Wiedisync holds the official set scores
   (`games.sets_json`, synced from Swiss Volley). The inferred point sequence of each set must
   end at that score; a small alignment step finds the cheapest consistent sequence (missed or
   extra rallies, misread serve sides) and marks the uncertain rallies for review.
5. **Scoreboard OCR when the scoreboard is in frame**: the coach draws the scoreboard box once
   per hall (`scoreboard_region`, reused for later videos in that hall); the Lenovo worker
   samples it at 2 fps, reads digits (seven-segment/LED digits: a tiny digit classifier trained
   on our crops, PaddleOCR as an alternative), smooths over time, and turns changes into
   timestamped points. Where OCR is confident it wins; disagreements with serve inference are
   flagged.
6. **Edge cases** the constraint will surface: replayed rallies (no point), points from
   sanctions (point without a rally), scorer corrections on the scoreboard. The coach fixes
   these in the score line; each fix is a labelled correction.

Output: `won_by`, `serving`, `score_*_after`, `score_source`, `score_confidence` per rally;
the score line in the UI; scores in exports and reels ("set 3, 22:20"). **Bar**: ≥ 98 % of
rallies with the right winner on held-out matches with the set-score constraint; flagged
rallies ≤ 5 %.

Basketball scoring later via scoreboard OCR (score + clock): a jump of +1/+2/+3 also
proposes the shot type.

### 10.4 Ball tracking

- TrackNet-family heatmap model (TrackNetV3 or later; check licence) on 3-frame stacks,
  fine-tuned on our footage on the Framework 16. Labels: a "click the ball" tool on sampled
  frames (target 3–5 k frames across halls and lighting), plus negatives.
- Post-processing: outlier removal, gap interpolation, trajectory fitting; contact and bounce
  candidates from direction changes.
- Runs only on live segments of the analysis copy.
- **Bar**: precision ≥ 0.9 at recall ≥ 0.8 within 5 px on held-out frames, near and far court
  reported separately.

### 10.5 Player detection and tracking

- Ultralytics YOLO person detector (AGPL-3.0, compatible with Nomal's licence) + ByteTrack
  (MIT), masked to the court polygon + margin, at 25 fps on live segments (tracker fills in).
- Team assignment: side of the net (volleyball); kit-colour clustering (basketball).
- No identity across videos (§5.7). Track IDs are per video.

### 10.6 Court homography

- The camera is fixed, so **one homography per video**: a calibration UI (click 6–8 court
  landmarks on a frame, `cv2.findHomography`, reprojection error shown), pre-filled from the
  last calibration in the same hall. Runs on the Lenovo server (CPU). For basketball footage
  filmed with a mild wide-angle setting (§7.1), the calibration also estimates lens distortion
  and undistorts before the homography.
- Later: automatic line/keypoint detection initialised from the hall's last calibration.
- **Bar**: reprojection error < 15 cm on held-out landmarks.

### 10.7 Serve placement

A homography maps the **floor plane** only, and the ball is in the air. So placement uses
floor-level evidence: the bounce point when the ball lands, else the receiver's foot position
at the reception contact. Output: `court_x/court_y` on serve events and a serve chart per
server, filterable by result. **Bar**: ≥ 80 % agreement with manually tagged serve zones.

### 10.8 Later: OpenVolley import

OpenVolley's e-scoresheet logs every point with a wall-clock time plus rosters with numbers.
With one `sync_point` (first serve), it gives rally ends, scores and the serving team: perfect
labels for §10.1 and §10.3. The model is ready (§6.6); the importer is not planned yet.

### 10.9 GPU time per video

The budget assumes **~1.5 L4-hours per video**. The design aims lower, because CPU stages run
at home and the GPU sees only the live part of the match:

| Stage | Where | Estimate per 2 h match (to be measured in spike S0) |
|---|---|---|
| Whistles, motion, dead time, scoreboard OCR, calibration | Lenovo CPU | free (tens of minutes of CPU) |
| Analysis copy (live segments, court crop) | Lenovo CPU/VAAPI | free |
| Serve-side classifier | GPU | 1–2 min |
| Ball tracking, 50 fps on live segments | GPU | ~5–10 min |
| Player detection, 25 fps on live segments | GPU | ~10–15 min |
| Container start, model load, decode overhead | GPU | ~3–5 min |
| **Total** | | **≈ 0.4–0.6 L4-h**, design cap **0.8 L4-h** |

Spike S0 (§17) measures the real number on the Framework 16's RTX 5070 Laptop GPU (expected to
be roughly L4-class for these models) and on one shortlisted provider. With 8 GB of VRAM the
models run one after another in FP16 with small batches. The L4 figures also matter for the
later move (§8.6, §16.2).

---

## 11. Tier 1 as training data for tier 3

1. **Every label carries provenance**: `source`, `created_by`, `model_run_id`, `confidence`,
   `review`. A dataset query can say "frame-precise, human-made or human-confirmed, taxonomy v2".
2. **Time precision is explicit** (`live` / `adjusted` / `frame`). Live tags are weak labels;
   only frame-precise tags train temporal localisation. The UI makes snapping cheap (`G`, `F`).
3. **Corrections are data**: confirm, correct (before/after in the revision tables) or reject
   (soft delete). Rejections are hard negatives; corrections are the most valuable labels.
   This covers events, rally boundaries, dead-time kinds, serving side and scores.
4. **Stable vocabulary**: versioned taxonomy with mappings; `docs/TAGGING_GUIDE.md` defines each
   code, *including the exact frame to tag* ("attack = the frame the hand touches the ball").
5. **Link labels to tracks**: once tracks exist, each event gets a `track_ref` (player track
   nearest the ball at the contact frame, on the correct side), confirmed with the event. That
   is the player-attribution label.
6. **Datasets are snapshots**: `nomal-ml export-dataset` writes a manifest (query, IDs, taxonomy
   version, video SHA-256s) and cuts short labelled windows from the NAS originals into
   `nomal/datasets/<name>/`. Models record their dataset manifest. Once a raw file is deleted
   under §5.5, only these windows remain (minimisation).
7. **Evaluation split by match**, never by frame, with held-out matches from other halls.
8. **Training happens on the Framework 16** (RTX 5070 Laptop, 8 GB): fine-tuning TrackNet and
   small YOLO variants fits with modest batch sizes. Runs that do not fit use the test-GPU
   budget line (§16).

Tier 3 ships like tier 2: proposals in a review queue first, auto-accept only above a measured
precision bar per skill.

---

## 12. Feature benchmark: Balltime and competitors

Researched on 15.10.2026 from vendor help centres, manuals and pricing pages. Full notes with
sources: [`docs/research/competitors-2026-10.md`](research/competitors-2026-10.md).

### 12.1 What the market looks like

- **Balltime is now Hudl** (acquired 06.02.2025, sold as "Hudl Assist powered by Balltime AI")
  and is **volleyball only**. Its pipeline runs in the same order as Nomal's tier 2: downtime
  removal → actions → teams → players → ball → court positions.
- **Their recording spec is our setup**: centred on the end line, 8–10 ft high, 1080p, no
  wide-angle lens, all four far-court corners visible. Footage filmed for Nomal would also
  work there, and vice versa.
- **Downtime is most of a match**: Balltime cites about 67 % downtime indoors. A condensed
  2-hour match is about 40 minutes; the GPU (§10.9) only needs to see that part.
- **Nobody documents** separate timeouts and set breaks, or scoreboard OCR. Hudl's editable
  AI score is the closest thing to automatic scoring.
- **Price context** (USD, US list prices): Hudl club volleyball from $500 per team per year
  (Core; Plus $1,250, Premier $1,600), club basketball from $400. SportsVisio volleyball season
  pack $900 per team (22 games). DataVolley 4 €299–799 per year (no video AI). For four teams,
  Hudl Core alone is about USD 2,000 a year; Nomal's target is CHF 210.

### 12.2 Triage: what Nomal adds, and when

Difficulty for Nomal: **Easy** = days of evenings, no new ML; **Medium** = a couple of weeks
or depends on a tier 2 output; **Hard** = new ML or 3D from one camera; **Out** = against a
non-goal or policy.

| Feature | Who has it | Nomal | When |
|---|---|---|---|
| **Skip dead time / condensed match** | Balltime/Hudl, Pixellot (basketball) | Planned | Manual M2, auto M5 (VB), M9 (BB) |
| Timeouts and set breaks as separate segments | Nobody documents it | Easy (gap rules + `T`/`B` keys) | M2 manual, M5 auto |
| Merge several files into one match | Balltime/Hudl | Planned (split parts) | M1 |
| Rally list filtered by player, action, outcome, set, rotation | Balltime, Hudl, DataVolley | Easy | M2 |
| Search bar + saved filters ("#3 attack #") | Hudl | Easy (reuses the tag grammar) | M3b |
| Edit set boundaries and starting score | Hudl Review tab | Easy (`period` rows) | M2 |
| Starting rotation per set | Hudl, DataVolley | Easy | M3b |
| Box score, every stat links to its video | Hudl, DataVolley, SportsVisio | Planned | M3b |
| Side-out % and break-point % | Hudl Assist, DataVolley, SoloStats | Easy (rally list + serving side) | M3b |
| Rotation analysis | Hudl, DataVolley, SoloStats | Easy once starting rotations and serving are known (rotation advances on each side-out) | M3b from tags, automatic with M6 |
| Zone heatmaps (serve/attack zones 1–9) | Hudl, DataVolley | Easy (zones are in the tags) | M3b |
| Regrade queue with number keys (pass 0–3, attack outcome) | Hudl | Easy | M3b; reused for model review from M5 |
| CSV export of stats | Hudl (CSV), VolleyStation (XLSX) | Easy | M3b |
| Practice / drill breakdowns | Balltime/Hudl, VBStats, SoloStats | Easy (training videos + `drill` segments with a label) | M3 |
| Comments pinned to a moment, @mentions, audience (coaches / team / one person) | Hudl | Easy | M3 |
| Clips, playlists, highlight builder across matches | Most | Planned | M3 |
| Automatic per-player highlight reels | Balltime/Hudl, VS Next, SportsVisio, Pixellot, Trace | Planned as smart reels from tags; automatic once tier 3 tags exist | M3 |
| Click a court diagram for attack/serve start and end → lines and charts | Hudl (fill-in), ovscout2, DataVolley | Medium (court widget; floor coordinates) | After launch; automatic with M8 |
| Setter distribution | Hudl, DataVolley | Medium (needs set and attack zones) | After launch |
| Telestration on a paused frame (arrow, circle, line, freehand) | Hudl, Veo, Spiideo, Pixellot | Medium (vector overlay stored with a time; burned in on export) | After launch |
| Season trends and goals | Hudl, Synergy | Easy–medium | After launch |
| DVW export/import (DataVolley) | DataVolley, Volleymetrics, ovscout2 | Medium (format is documented by the openvolley tools) | When a coach asks |
| AI-written match summary | Hudl Plus, SportsVisio | Easy technically (a language model over aggregated stats), but adds a processor | Optional; numbers only, no names |
| Automatic scoring | Balltime (editable), VS Next [mkt] | Planned | M6 |
| Scoreboard OCR | Nobody | Medium | M6 |
| Court calibration (6 points) | Hudl (6), ovscout2 (4) | Planned | M8 (simple UI could come earlier) |
| Ball trajectories | Balltime/Hudl | Medium–hard | M7 |
| Data overlays (court lines, trajectories) | Hudl | Medium (after calibration + ball) | M8 |
| Unidentified-athlete queue (assign a track to a roster player) | Hudl | Medium (needs tracks) | M9 |
| Automatic actions and stats | Balltime/Hudl, VS Next, SportsVisio, Pixellot | Hard | Tier 3 |
| Jersey-number recognition | Pixellot [mkt] | Hard | Tier 3, maybe |
| Ball speed, contact and jump height | Balltime/Hudl, Pixellot | Hard (3D from one camera) | Out for now |
| Workload metrics from video | VS Next [mkt], Pixellot | Hard, and borderline physical profiling | Out |
| Livestreaming, auto-tracking or panoramic cameras, multi-angle | Hudl Focus, Veo, Pixellot, Spiideo | Out (no hardware, non-goal) | — |
| Vertical social reels, recruiting profiles, export to YouTube | Balltime/Hudl, VS Next | Out (no public links; players don't download) | Revisit only as a coach-approved export |
| Live tagging, live data to the bench, analyst service, league exchange | DataVolley, Hudl, Volleymetrics | Out (non-goals) | — |

### 12.3 Patterns worth copying

- **Hudl's Review tab**: one place per video for everything that needs a human: set
  boundaries, starting rotations, court calibration, missing coordinates, unidentified
  athletes, regrades. Number keys and auto-advance make it fast. Nomal's review screens (§10)
  become one **Review** tab per video with the same order and the same keyboard-first style.
- **ovscout2** (open source, R/Shiny) is the closest prior art to tier 1: fixed end-line
  camera, 4-corner calibration, typed codes, the video pausing for input after key touches,
  and .dvw output. Worth reading before M2.
- **Pixellot** recognises players by height, kit colour and jersey number without face
  recognition: the same line Nomal draws in §5.7.

### 12.4 What Nomal does that they don't (or don't document)

- Timeouts and set breaks as their own segments, not just "downtime".
- Automatic scoring checked against the **official set scores** from Wiedisync, plus
  scoreboard OCR.
- Data stays with the club (Hetzner, NAS), adults only, no face recognition.
- Every correction is stored as training data with full provenance (§11).

---

## 13. Brand and UI

### 13.1 Assets

The source assets are in **`docs/brand/`** (added 15.10.2026), each in a **`-vb`**
(volleyball) and a **`-bb`** (basketball) variant. **Default is `vb`** (login, library home,
anything not tied to one sport). `docs/brand/preview.png` shows them at every size.

| File | What |
|---|---|
| `nomal-icon-vb.svg`, `nomal-icon-bb.svg` | App icon: the mark on a white rounded square (512 × 512) |
| `nomal-mark-vb.svg`, `nomal-mark-bb.svg` | The mark alone (a trail of dots and the ball), transparent, for the app bar, login and export watermark |

The **wordmark** is lowercase "nomal" set as text in **Bricolage Grotesque ExtraBold** (there is
no wordmark SVG; an outlined one can be made from the font for the export watermark). The font
is under the SIL Open Font License and is **self-hosted** (e.g. via `@fontsource`), not loaded
from Google Fonts, so viewers' IP addresses are not sent to Google.

### 13.2 Colours

| Token | Hex | Use |
|---|---|---|
| `navy` | `#13203A` | Primary text on light, app bar, dark-mode background |
| `light` | `#EEF1F5` | Light-mode background, text on navy |
| `vb-yellow` | `#FFC72C` | Volleyball accent: fills, highlights, focus rings, active states |
| `vb-blue` | `#1F5FBF` | Volleyball accent: links, primary buttons in light mode |
| `bb-orange` | `#F26B1D` | Basketball accent |

The sport accent is switched by a `data-sport="vb|bb"` attribute on the page root, set from the
current team or video; the logo and favicon follow (default `vb`).

Contrast (WCAG 2.x), computed for these exact values:

| Combination | Ratio | Rule |
|---|---|---|
| navy on light | 14.3 | ✓ body text |
| vb-blue on light / white on vb-blue | 5.4 / 6.1 | ✓ links, primary buttons (white label) |
| navy on vb-yellow | 10.4 | ✓ text on yellow fills |
| navy on bb-orange | 5.3 | ✓ **use navy, not white, on orange buttons** |
| white on bb-orange | 3.05 | large text only |
| bb-orange on light | 2.7 | ✗ never as text on light; fills and icons with a navy outline only |
| vb-yellow on light | 1.4 | ✗ never as text or thin icons on light |
| vb-yellow / bb-orange on navy | 10.4 / 5.3 | ✓ dark-mode accents |
| vb-blue on navy | 2.7 | ✗ dark mode uses yellow for volleyball accents |

### 13.3 Icons

Generated on 15.10.2026 by `scripts/brand/build-icons.mjs` (headless Chromium; see
`scripts/brand/README.md`) into `docs/brand/icons/{vb,bb}/`:

- `favicon.svg` and `favicon.ico` (16/32/48): **the ball alone** (approved 15.10.2026). The
  trail of dots is only a few pixels at tab size and turned the full icon into a white square
  with a speck.
- `icon-192.png` and `icon-512.png`.
- `icon-maskable-192.png`, `icon-maskable-512.png` and `apple-touch-icon.png` (180), full
  bleed; the artwork already sits inside the maskable safe zone.
- `site.webmanifest` with the `vb` icons and theme colour navy.

The page swaps the favicon link for basketball. Generated files are committed, so builds don't
need the toolchain.

**Dark backgrounds**: the mark's trail dots are navy and disappear on navy, so the script also
writes `mark-on-dark.svg` with the dots in `light` (#EEF1F5) (approved 15.10.2026). Use it on
the navy app bar and in dark mode.

### 13.4 Behaviour conventions

Borrowed from Wiedisync's kscw-ui rules: the `Button` height scale (44 px touch targets),
tables for lists of records, Swiss formats (`dd.mm.yyyy`, 24 h, locale `de-CH`), sentence-case
labels, no native browser dialogs, dark mode from tokens, English first with German at minimum.
The colours are Nomal's own.

---

## 14. Repo, stack, environments, deploy

### 14.1 Repo layout

```text
nomal/
  README.md, LICENSE (AGPL-3.0)
  docs/
    PLAN.md                  this file
    adr/                     one file per decision change
    brand/                   logo assets (provided), generated icons
    research/                competitor and GPU-provider research (2026-10)
    ARCHITECTURE.md          written in M0 from §3–§8, kept current
    PRIVACY.md               processing record, processors, retention, rights, residual risks
    FILMING_GUIDE.md         §7.1 for coaches, one page per camera model
    TAGGING_GUIDE.md         codes, grammar, exact-frame definitions
    RUNBOOK.md               deploy, restore, rotate keys, add a worker, switch GPU backend
  backend/                   one Python package (uv)
    nomal/core/              models, schemas, permissions, signing, taxonomy loader
    nomal/api/               FastAPI app, routes, auth, media redirects, job queue
    nomal/worker/            agent loop, media jobs, CPU analysis, GPU dispatcher + backends
    nomal/ml/                nomal-ml entrypoint, models, training, dataset export
    migrations/              Alembic
    tests/
  web/                       React app (Vite), built into the API image
  taxonomies/                volleyball.yaml, basketball.yaml
  scripts/brand/             icon generation
  deploy/
    hetzner/compose.yaml     nomal-api (prod + dev)
    lenovo/compose.yaml      media worker (+ provider dispatcher later)
    gpu/compose.yaml         internal GPU worker (local-agent, NVIDIA runtime)
    ml/Dockerfile            nomal-ml (CUDA) image
  .github/workflows/         lint, test, build images, release
```

### 14.2 Stack

| Area | Choice | Why |
|---|---|---|
| API | FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, psycopg 3 | Typed, shares code with workers/ML |
| Python tooling | uv, ruff, mypy (strict on `core`), pytest | Fast, one lockfile |
| Web | React 19, TS, Vite, Tailwind v4, shadcn, TanStack Query, hls.js, i18next | Same as Wiedisync |
| Upload | Uppy with S3 multipart | Resumable |
| Media | ffmpeg (pinned build in the image), ffprobe | — |
| ML | PyTorch (CUDA 12.8+ builds for Blackwell), Ultralytics YOLO, ByteTrack via `supervision`, OpenCV, Polars/PyArrow | Licences compatible with AGPL |
| YouTube | YouTube Data API v3 (metadata only, API key), IFrame Player API | Channel check, playback |

### 14.3 Environments

| | Dev | Prod |
|---|---|---|
| Web + API | `nomal-dev.kscw.ch` | `nomal.kscw.ch` |
| Wiedisync | `directus-dev.kscw.ch` (nightly scrubbed prod clone, IDs match) | `directus.kscw.ch` |
| Database | `nomal_dev` | `nomal` |
| Bucket | `…-nomal-dev` (lifecycle: 30 days) | `…-nomal` |
| Workers | Same Lenovo worker, second agent with `--env dev`, lower priority | Primary |
| GPU backend | `local-agent` (dev agent on the Framework 16), or `fake` | `local-agent` on the Framework 16 (a provider later, §8.6) |
| Local | `docker compose`: Postgres + MinIO + a fake bridge that issues dev tokens | — |

Everything goes to dev first, as in Wiedisync.

### 14.4 Deploy

- GitHub Actions: lint + tests on every push; on a tag, build `nomal-api`, `nomal-worker` and
  `nomal-ml` images to GHCR (private).
- VPS, once per environment: `scripts/setup-hetzner.sh dev|prod`.
  - It creates the secrets, the database and its login role, the `NOMAL_*` lines in the
    Directus `.env` and `/opt/nomal-<env>/.env`.
  - It recreates Directus once and runs the first deploy.
  - It moves the secrets into the vault (`scripts/vault-secrets.sh`).
  - The tunnel route (`kscw-club` → `http://127.0.0.1:8090` dev, `:8091` prod) is separate.
- VPS, every release: `scripts/deploy.sh dev|prod`.
  - It builds the image locally (no registry yet), loads it on the VPS and dumps the database.
  - Then it runs Alembic, restarts, and checks `/api/health`.
- Container limits on the VPS (it also runs prod Directus): API `mem_limit: 512m`, `cpus: 1.0`.
- Lenovo server: `docker compose pull && up -d` (systemd timer for the tag in a `stable` file).

### 14.5 Testing

- Unit: permissions matrix, entry grammars, taxonomy loader, retention, token verification
  (expired, wrong audience, replayed `jti`), score inference on synthetic rally sequences
  (incl. side switches and the fifth-set switch at 8), budget guard.
- Integration (pytest + Postgres + MinIO): upload → ingest → transcode on a 20 s fixture clip
  with two split parts → HLS playable; job leasing under concurrency; lease expiry; the GPU
  dispatcher against the `fake` backend.
- E2E (Playwright): log in via the fake bridge, tag a fixture video with the keyboard, toggle
  skip dead time, build a smart reel; a non-member gets 403 on playlist and segment; a segment
  URL is refused after its TTL.
- Contract test for the Wiedisync bridge payloads (fixtures shared with the Wiedisync PR).

---

## 15. Operations and handover

### 15.1 What someone taking over needs

- `docs/RUNBOOK.md`: deploy, roll back, restore the DB, rotate the JWT key and worker tokens,
  add or replace a worker, **switch the GPU backend**, raise a budget cap, clear a stuck job,
  what each alert means.
- `docs/ARCHITECTURE.md` and the ADRs: why things are the way they are.
- Secrets live in the password vault, referenced by entry name in the private ops notes, never
  in the repo. Env files on hosts are `0600`.
- Ops details that must not be public (home hostnames, tailnet names, bucket names,
  credentials) live in the private infra notes next to Wiedisync's `INFRA.md`.

### 15.2 Backups

| What | How |
|---|---|
| `nomal` DB (tags, scores: the irreplaceable part) | Add the `nomal` database to Wiedisync's existing `backup-postgres.sh` (6-hourly dump → GPG → Google Drive + R2 + NAS). Restore test extended with row floors on `event`, `segment`, `video`. |
| Renditions | Weekly mirror to NAS; re-encodable from raw while raw exists. |
| Raw originals | NAS only (RAID). Conscious decision: no offsite copy. |
| Models + datasets | NAS; small enough to sync offsite if wanted. |

### 15.3 Monitoring

Uptime Kuma: `nomal.kscw.ch/api/health`, worker heartbeat, oldest-queued-job age, GPU spend vs.
cap. Errors: structured JSON logs; frontend errors to the club's Sentry (new project).

---

## 16. Budget

Target: **~CHF 210 per season, all running costs.** Your breakdown: cloud GPU ~80, backup
storage ~50, test GPU ~35, reserve (~45). Assumed rates: 1 USD ≈ CHF 0.80, 1 EUR ≈ CHF 0.94,
electricity ≈ CHF 0.30/kWh (check current rates). Prices from public pricing pages and
third-party trackers on 15.10.2026; verify before ordering.

### 16.1 GPU now: internal

Inference runs on the Framework 16 (§8.2), which is on anyway. The extra cost is the GPU's
electricity: roughly 0.12–0.15 kW more under load × 40–80 GPU-hours per season (100 videos ×
0.4–0.8 h) ≈ 5–12 kWh ≈ **CHF 2–4 per season**. Not counted: wear on a laptop that exists
anyway.

### 16.2 GPU later: an EU or Swiss provider

The CHF 80 line becomes the budget for the move (§8.6): about **CHF 0.80 per video**, all-in.
Season cost for 100 videos from the provider research (CHF, excl. VAT, including per-job
start-up overhead; the same runtime is assumed on every GPU until S0 measures it):

| Provider | 0.5 GPU-h/video | 0.8 GPU-h/video (design cap) | 1.5 GPU-h/video (your assumption) |
|---|---|---|---|
| Infomaniak L4 (Swiss; price to confirm) | 18 | 26 | 45 |
| OVHcloud AI Training L4 (France) | 45 | 68 | ⚠ 122 |
| Scaleway L4 (France) | 49 | 71 | ⚠ 122 |
| Exoscale A30, Geneva (Swiss) | ⚠ 82 | ⚠ 119 | ⚠ 204 |

So the 1.5 GPU-h assumption fits CHF 80 only at Infomaniak (or the cheap fallbacks). OVHcloud
and Scaleway need the design target of ≤ 0.8 h; Exoscale needs ≤ 0.5 h or a GPU clearly faster
than an L4. Swiss VAT (8.1 %) may come on top.

### 16.3 Everything

| Line | Your budget | Now (internal GPU) | After the move | Note |
|---|---|---|---|---|
| GPU inference | 80 | **≈ 2–4** (electricity) | ≈ 18–68 at ≤ 0.8 h/video (Infomaniak to OVHcloud) · ⚠ up to 122 at 1.5 h on OVHcloud | §16.1–16.2 |
| Hot storage (Hetzner Object Storage) | — | ⚠ **≈ 68–80** | ≈ 68–80 | **Not in your breakdown.** Base ~€5–6/month for 1 TB + 1 TB traffic; year 2 holds two seasons (~1.1 TB) unless past seasons' 1080p rendition is dropped (§5.5) |
| Backup storage | 50 | ≈ 0–5 | ≈ 0–5 | DB rides Wiedisync's existing backups; rendition mirror and raw stay on the NAS. Proposal: **use this line for hot storage** |
| Test GPU | 35 | 35 | 35 | Provider benchmarks (S0, §8.6) and training that doesn't fit 8 GB |
| Other electricity | — | ≈ 5–10 | ≈ 5–10 | Lenovo server is on anyway; Framework 16 training |
| VPS, egress, domains, YouTube API | — | ≈ 0 | ≈ 0 | Existing server; traffic within the included quota |
| **Total** | **210** | **≈ CHF 110–135** | **≈ CHF 125–200** · ⚠ ≈ 230–250 at 1.5 h/video on OVHcloud | |

Starting internal leaves roughly CHF 75–100 of the CHF 210 unused per season. Suggestion: keep it
as the reserve for the move rather than spend it elsewhere. Levers if a provider costs more than
planned: process only the matches coaches ask for, player detection at 10 fps, drop past
seasons' 1080p rendition, or keep internal as the main backend.

---

## 17. Roadmap

Sized for evenings (~6–8 h/week). Each milestone ends with something usable and a short
written check. Rough total to launch for the first 4 teams: **5–7 months** (S0 to M4,
including stats in M3b). Teams can start using M2 and M3 on dev before the formal launch.

**Status (15.10.2026)**: M0 code is done and tested on the Lenovo server.
- Backend: 32 tests.
- Web: 29 tests.
- An end-to-end run through the production image passed.
- Wiedisync bridge: KSC-Wiedikon/wiedisync#8, merged and deployed to dev.

**Live on dev since 15.10.2026**: https://nomal-dev.kscw.ch.
- `nomal_dev` database, Directus dev env and the `kscw-club` tunnel route are in place.
- The first sync covered H3, D1, DU23-1 and Herren 1.

Still open for M0: one real login by a person (the "done when" check), and the prod setup
(`scripts/setup-hetzner.sh prod`; the Wiedisync prod bridge code is already live, unconfigured).

D1 2026/27 shares a lineage with D2 2025/26. That is correct: Swiss Volley team 1393 was promoted
from 3. Liga and renamed (16 of 19 players stayed).

**M1 (15.10.2026), on branch `m1-video`**: built and tested on the Lenovo server (ADR-002).
- Backend: 49 tests, including real ffmpeg on a generated clip.
- End-to-end run against local S3 (SeaweedFS), the real worker and a synthetic 3-minute
  1080p50 match split into two camera files:
  - upload of 457 MB in 64 MB parts, then ingest, 1080p50 + 540p25 HLS and thumbnails,
    ready in 48 s;
  - both renditions decode through the API's 302s;
  - the same files through the NAS inbox.
- In the worker image, VAAPI encodes about 5.8× real time.
- On dev since 15.10.2026: the M1 API (video off until the bucket exists), the media worker
  on the Lenovo server (VAAPI, reaching the API over the tailnet, data on local disk until
  the NAS share exists), and the DU23-1 policy "trainings only".
- **Wiedisync's adult check on real (dev) rosters, 15.10.2026**: H3, D1 and Herren 1 pass.
  DU23-1 fails because 6 of its 15 rostered people are under 18. So DU23-1 trainings are
  blocked too, as long as minors are on that roster.
- Waiting on:
  - the Hetzner bucket and its keys (budget line O7);
  - the NAS shares `nomal/raw-*` and `nomal/inbox-*`;
  - the worker deployment;
  - the browser check on desktop, Android and an iPhone with a real 2-hour match (the
    "done when").

| # | Milestone | Contents | Done when |
|---|---|---|---|
| **S0** | Spike (before M1) | Film one match to the standard. On the Framework 16: off-the-shelf TrackNet + YOLO, look at the far court, run a draft `ml_analyse` and **measure GPU-hours per video**; one benchmark run on Infomaniak or OVHcloud (test-GPU line). Whistle detection on its audio. | Capture standard confirmed or changed (4K, mount height); GPU-h/video measured on the Framework 16 and one provider |
| **M0** | Foundations | Repo, CI, local compose; FastAPI + React skeleton with the Nomal brand (icons already in `docs/brand/icons/`); Alembic; Wiedisync bridge on **dev** + Wiedisync PR; tunnel hostname, DB, bucket; ADR-001 | Log in on `nomal-dev.kscw.ch` with a Wiedisync dev account and see your Nomal-enabled teams and games |
| **M1** | Video in, video out | Job queue + Lenovo media worker; NAS inbox + browser multipart upload (split files); ingest + transcode + thumbnails; HLS with per-segment signing; adult hard gate + *filming allowed/blocked*; consent (dropped 16.10.2026, §5.4); audit log; DU23 decision applied | A real 2 h match plays on desktop, Android and an iPhone; a segment URL dies after 5 min; a non-member gets 403; a blocked team cannot upload |
| **M2** | Tagging + condensed playback | Volleyball taxonomy; roster snapshot; grammar + hotkeys; timeline + event table; rally key + rally/score from tags; **skip dead time (manual)** with timeout/set-break keys; set boundaries + starting score; rally list filters (player, action, outcome, set); outbox; revision triggers | A full match tagged in ≤ 1.5× its length; condensed playback works; pulling the network cable loses nothing |
| **M3** | Clips, reels, export, comments | Virtual clips; smart per-player reels; highlight builder across matches; comments pinned to a moment with @mentions and audience; drill segments for trainings; ffmpeg export with watermark (incl. condensed match) | A player opens "my attacks this season" on a phone; a coach downloads a watermarked condensed match |
| **M3b** | Stats and reports | Box score with click-to-video; side-out/break-point %; starting rotations + rotation analysis; zone heatmaps; search bar + saved filters; regrade queue; CSV export (§9.7) | A coach answers "how do we side out in rotation 1?" in Nomal without a spreadsheet |
| **M4** | Launch | Basketball taxonomy + grammar for Herren 1; YouTube source (club channel); German UI; privacy page; filming/tagging guides; runbook; prod deploy; onboard the 4 teams | First prod match of each team tagged by its coach without your help |
| **M5** | Tier 2a: automatic dead time | CPU v0 (whistles + motion) on the Lenovo worker; rally review UI; analysis copies; internal GPU worker on the Framework 16 (`local-agent`, busy guard) + GPU-hour ledger; learned v1 once ~20 matches have manual rallies | F1 ≥ 0.9 at ±1 s; condensed mode is the default for new matches |
| **M6** | Tier 2b: automatic scoring (VB) | Serve-side classifier from tier-1 serve tags; side-switch rules; official-set-score constraint; scoreboard region UI + OCR; score correction UI | ≥ 98 % correct rally winners with ≤ 5 % flagged on held-out matches |
| **M7** | Tier 2c: ball | Ball-labelling tool; TrackNet fine-tune on the Framework 16; Parquet artifacts | Ball bar met on near court; far court measured |
| **M8** | Tier 2d: players + court | YOLO + ByteTrack; calibration UI; serve placement charts | Serve zones agree ≥ 80 % with manual tags |
| **M9** | Basketball tier 2 + tier 3 spike | Clock/score OCR for basketball dead time and scoring; event proposals from tracks + ball contacts; unidentified-athlete queue | Measured precision per skill; decide whether to continue |
| **Later** | Pick by demand | Court-diagram clicks for attack/serve lines; setter distribution; telestration; trends and goals; DVW import/export; optional AI match summary (§12.2) | Each one when a coach asks for it |
| **Move** | EU/Swiss GPU provider | Steps in §8.6: benchmark, DPA, adapter, parallel run, switch | When a trigger in §8.6 fires |

---

## 18. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| One developer, evenings | Stalls, bus factor | Small stack, ADRs, runbook, milestone-sized scope, non-goals |
| Tagging too slow for coaches | Nobody tags | Grammar, rally/score automation, measure in M2, recruit analysts, condensed mode first |
| Different camera per team | Inconsistent input, some below 50 fps | Ingest normalises; `meets_standard` flag; per-camera page in the filming guide; tier 2 skips sub-standard videos |
| Far-court ball tracking at 1080p | Tier 2 weak | Spike S0; 4K / higher mount; fast shutter |
| Internal GPU is a laptop (busy with training, hot, broken; 8 GB VRAM) | ML backlog, model size limits | Busy guard, chunked checkpoints, optional night window, FP16 and small models, test-GPU line for big training runs, move to a provider (§8.6) |
| GPU cost above plan after the move | Budget broken | S0 + provider benchmark, CPU stages at home, live segments only, budget guard, internal fallback |
| Provider data protection after the move | revDSG exposure | Swiss/EU only, DPA, minimised analysis copies, privacy page updated (§5.6, §8.6) |
| DU23-1 and U23 opponents | Breaks "nothing under 18" | Trainings only (decided); `team_policy.matches_allowed = false` enforced |
| Lenovo server is also the LAN's DNS and runs other services | Transcodes hurt the LAN | Container CPU/memory caps, `nice`, VAAPI |
| VPS shared with prod Directus | Nomal hurts Wiedisync | Container limits; video never touches the VPS |
| iOS playback quirks | iPhone users can't watch | Test in M1; playlist-refresh fallback (§7.6) |
| Leaked export | Privacy incident | Coach-only, watermark, 30-day expiry, audit |
| YouTube footage outside Nomal's control | Visible to anyone with the link | Club channel only, unlisted, original file preferred |
| Wiedisync changes (rollover, schema) | Broken sync | Versioned bridge contract + tests; lineage mapping |
| "H1" exists in both sports | Wrong team linked | Sport is part of every team key and every picker |
| Cloudflare terms on video | Account action | Video never through Cloudflare |
| Price changes | Cost creep | S3-standard storage; GPU backends are swappable |

---

## 19. Open questions and flags

| # | Question | Status / recommendation |
|---|---|---|
| O1 | DU23-1 and under-18 opponents | ✓ **Decided 15.10.2026: trainings only.** Its trainings still pass the hard birthdate check. |
| O2 | Opponents in adult leagues | ✓ **Decided 15.10.2026: treated as adults** (the club's position). No per-upload confirmation; opponents by number only; deletion on request. |
| O3 | "H1 (basketball)" = basketball **Herren 1** (league H1LRA), not volleyball H1 (2. Liga). | Assumed yes. |
| O4 | GPU provider | ✓ **Decided 15.10.2026: start internal, move to an EU or Swiss provider later** (§8.6; research in `docs/research/gpu-providers-eu-ch-2026-10.md`). |
| O5 | Internal GPU host | ✓ **Decided 15.10.2026: the Framework 16** (always on), which is also the dev and training machine. |
| O6 ⚠ | After the move, 1.5 GPU-h/video stays within CHF 80 only at Infomaniak (≈ CHF 45, price to confirm); OVHcloud ≈ CHF 122, Exoscale ≈ CHF 204 (§16.2). | Design to ≤ 0.8 h (§10.9); measure in S0 on the Framework 16 and on Infomaniak or OVHcloud. |
| O7 ⚠ | Hot storage (≈ CHF 68–80/season) is not in the budget breakdown. | Use the CHF 50 backup line for hot storage and drop past seasons' 1080p rendition. |
| O8 | Basketball camera: side of the court at the half-court line, as high as possible (§7.1). Where in Herren 1's halls is there a gallery or a high spot on the long side? | Check the home hall first; away halls vary. |
| O9 | YouTube sources | ✓ **Decided 15.10.2026: the club's channel and members' own channels**, on a coach-confirmed allow-list. Channel IDs are added as members propose them. |
| O10 | Camera models | ✓ **Not needed**: Nomal reads the specs from each file. |
| O11 | Brand | ✓ **Done 15.10.2026**: assets in `docs/brand/`, icons generated, `mark-on-dark.svg` and the ball-only favicon approved (§13.3). |
| O12 | Repo visibility | Private now; flip to public when you're happy, like Wiedisync. |
