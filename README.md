# Nomal

*Nomal* is Swiss German for "again".

In-house video analysis for KSC Wiedikon: coaches and players upload match and practice
footage, tag what happens, and watch clips and highlight reels, behind their Wiedisync
login. Volleyball first, basketball next. Automatic rally detection, scoring, ball and player
tracking come later as GPU jobs.

**Status:** milestone M0 (foundations). Read [`docs/PLAN.md`](docs/PLAN.md) first, then
[`docs/adr/`](docs/adr/).

## Layout

| Path | What |
|---|---|
| `backend/` | FastAPI API, sync with Wiedisync, Alembic migrations, tests (Python 3.12, uv) |
| `web/` | React app (Vite, Tailwind v4), built into the API image |
| `docs/` | Plan, ADRs, research, brand assets |
| `deploy/hetzner/` | Compose file and env template for the VPS |
| `scripts/` | `deploy.sh`, brand icon generation |

## Run it locally

No Wiedisync needed: a fake bridge signs logins and serves made-up teams.

```bash
docker compose up -d                       # Postgres on 127.0.0.1:55488
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

## Checks

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
cd web && npm run lint && npm test && npm run build
docker build -t nomal-api:local .
```

The backend tests need the local Postgres (`docker compose up -d`); they use the `nomal_test`
database (`docker exec nomal-local-db-1 psql -U nomal -c 'CREATE DATABASE nomal_test'` once).

## Deploy

`scripts/deploy.sh dev|prod` builds the image here, loads it on the VPS, dumps the database,
runs migrations and restarts.

Once per environment, `scripts/setup-hetzner.sh dev|prod` creates the secrets, the database,
the Wiedisync bridge env and `/opt/nomal-<env>/.env`, then deploys and moves the secrets into
the vault (`scripts/vault-secrets.sh`). Add the tunnel hostname separately (`docs/PLAN.md` §14.4).

## License

[AGPL-3.0](LICENSE)
