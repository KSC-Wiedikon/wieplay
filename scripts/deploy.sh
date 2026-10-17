#!/usr/bin/env bash
# Build the WiePlay image on this machine, ship it to the VPS and roll it out.
#
#   scripts/deploy.sh dev|prod
#
# On the VPS, /opt/wieplay-<env>/.env must exist (template: deploy/hetzner/env.example).
# Steps: clean-tree check → docker build → docker load on the VPS → pre-deploy DB dump
# → alembic upgrade head → docker compose up -d → health check. Dev first, always.
set -euo pipefail

env="${1:-}"
case "$env" in
  dev) db=wieplay_dev ;;
  prod) db=wieplay ;;
  *) echo "usage: scripts/deploy.sh dev|prod" >&2; exit 2 ;;
esac
host="${WIEPLAY_DEPLOY_HOST:-hetzner}"
dir="/opt/wieplay-$env"
cd "$(dirname "$0")/.."

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree has uncommitted changes; commit first so the tag means something." >&2
  exit 1
fi
tag="$(git rev-parse --short=12 HEAD)"

echo "→ building wieplay-api:$tag"
docker build -t "wieplay-api:$tag" .

echo "→ loading the image on $host"
docker save "wieplay-api:$tag" | gzip | ssh "$host" 'gunzip | sudo docker load'

echo "→ compose file and tag"
scp -q deploy/hetzner/compose.yaml "$host:/tmp/wieplay-compose.yaml"
ssh "$host" "sudo install -m 0644 /tmp/wieplay-compose.yaml $dir/compose.yaml && \
  sudo sed -i 's/^WIEPLAY_TAG=.*/WIEPLAY_TAG=$tag/' $dir/.env"

echo "→ pre-deploy dump of $db (swept into the encrypted backups by backup-postgres.sh)"
ts="$(date -u +%Y-%m-%d_%H%M)"
ssh "$host" "sudo docker exec kscw-postgres pg_dump -U supabase_admin $db | gzip | \
  sudo tee /data/backups/wieplay_predeploy_${env}_${ts}.sql.gz >/dev/null"

echo "→ migrations"
ssh "$host" "cd $dir && sudo docker compose run --rm api alembic upgrade head"

echo "→ restart"
ssh "$host" "cd $dir && sudo docker compose up -d"

port="$(ssh "$host" "sudo grep -E '^WIEPLAY_PORT=' $dir/.env | cut -d= -f2")"
for _ in $(seq 1 20); do
  if ssh "$host" "curl -fsS http://127.0.0.1:$port/api/health" >/dev/null 2>&1; then
    echo "✓ wieplay-$env healthy on :$port (tag $tag)"
    exit 0
  fi
  sleep 3
done
echo "✗ wieplay-$env did not become healthy; check: ssh $host 'cd $dir && sudo docker compose logs --tail 50'" >&2
exit 1
