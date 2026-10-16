# Nomal

*Nomal* is Swiss German for "again".

In-house video analysis for KSC Wiedikon: coaches and players upload match and practice
footage, tag what happens, and watch clips and highlight reels, behind their Wiedisync
login. Volleyball first, basketball next. Automatic rally detection, scoring, ball and player
tracking come later as GPU jobs.

**Status:** M0 (foundations) live on dev; M1 (video in, video out) built and tested locally.
Read [`docs/PLAN.md`](docs/PLAN.md) first, then [`docs/adr/`](docs/adr/).

## Layout

| Path | What |
|---|---|
| `backend/` | FastAPI API, sync with Wiedisync, Alembic migrations, tests (Python 3.12, uv) |
| `backend/nomal/worker/` | The media worker: ingest, HLS transcode, thumbnails, NAS inbox (ffmpeg) |
| `web/` | React app (Vite, Tailwind v4), built into the API image |
| `docs/` | Plan, ADRs, research, brand assets |
| `deploy/hetzner/` | Compose file and env template for the VPS (API) |
| `deploy/lenovo/` | Compose file and env template for the media worker; systemd unit for its SSH tunnel to the API |
| `scripts/` | `deploy.sh`, `deploy-worker.sh`, `setup-hetzner.sh`, `vault-secrets.sh`, brand icons |

## Run it locally

No Wiedisync needed: a fake bridge signs logins and serves made-up teams.

```bash
docker compose up -d                       # Postgres on :55488, S3 (SeaweedFS) on :55490
cd backend
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn --factory nomal.api.app:asgi --reload --port 8077

# second terminal
cd web
npm install
npm run dev                                # http://localhost:5173, proxies /api to :8077
```

Log in with any persona on the fake bridge page. Log in as `admin` and open *Admin → Teams*
to switch teams on and run the sync.

Video needs the S3 settings in `backend/.env` and a worker:

```bash
# backend/.env
NOMAL_S3_ENDPOINT=http://127.0.0.1:55490
NOMAL_S3_BUCKET=nomal-local
NOMAL_S3_ACCESS_KEY=nomal-local
NOMAL_S3_SECRET_KEY=nomal-local-secret
NOMAL_S3_PATH_STYLE=true

uv run python -c "import boto3; boto3.client('s3', endpoint_url='http://127.0.0.1:55490', \
  aws_access_key_id='nomal-local', aws_secret_access_key='nomal-local-secret', \
  region_name='us-east-1').create_bucket(Bucket='nomal-local')"
uv run python -m nomal.cli storage-setup            # CORS + lifecycle rules
uv run python -m nomal.cli create-worker local ingest,transcode,thumbs,nas,inbox > /tmp/nomal-worker.token
NOMAL_WORKER_API_URL=http://127.0.0.1:8077 NOMAL_WORKER_TOKEN_FILE=/tmp/nomal-worker.token \
  NOMAL_WORKER_WORK_DIR=/tmp/nomal-work NOMAL_WORKER_RAW_ROOT=/tmp/nomal-raw \
  NOMAL_WORKER_INBOX_ROOT=/tmp/nomal-inbox uv run python -m nomal.worker
```

Uploads only start once every player has agreed to filming: as `coach-h3`, record paper
consent for the H3 players on the team page. The fake bridge's DU23-1 roster includes a
fictional 15-year-old, so DU23-1 shows *filming blocked*.

## Checks

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
cd web && npm run lint && npm test && npm run build
docker build -t nomal-api:local .
docker build -f Dockerfile.worker -t nomal-worker:local .
```

The backend tests need the local Postgres (`docker compose up -d`); they use the `nomal_test`
database (`docker exec nomal-local-db-1 psql -U nomal -c 'CREATE DATABASE nomal_test'` once).
The worker tests encode a tiny clip and are skipped when ffmpeg is not installed.

## Deploy

`scripts/deploy.sh dev|prod` builds the image here, loads it on the VPS, dumps the database,
runs migrations and restarts. `scripts/deploy-worker.sh dev|prod` builds and restarts the
media worker on the Lenovo server (run it there). The worker reaches the API through an SSH
tunnel: install `deploy/lenovo/nomal-api-tunnel.service` and its ufw rule there once (steps in
the unit's header, `docs/PLAN.md` §8.1).

Once per environment, `scripts/setup-hetzner.sh dev|prod` creates the secrets, the database,
the Wiedisync bridge env and `/opt/nomal-<env>/.env`, then deploys and moves the secrets into
the vault (`scripts/vault-secrets.sh`). Add the tunnel hostname separately (`docs/PLAN.md` §14.4).

## License

[AGPL-3.0](LICENSE)
