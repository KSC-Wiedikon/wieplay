#!/usr/bin/env bash
# Build the Nomal image on this machine, ship it to the VPS and roll it out.
#
#   scripts/deploy.sh dev|prod
#
# On the VPS, /opt/nomal-<env>/.env must exist (template: deploy/hetzner/env.example).
# Steps: clean-tree check → docker build → docker load on the VPS → pre-deploy DB dump
# → alembic upgrade head → docker compose up -d → health check. Dev first, always.
set -euo pipefail

env="${1:-}"
case "$env" in
  dev) db=nomal_dev ;;
  prod) db=nomal ;;
  *) echo "usage: scripts/deploy.sh dev|prod" >&2; exit 2 ;;
esac
host="${NOMAL_DEPLOY_HOST:-hetzner}"
dir="/opt/nomal-$env"
cd "$(dirname "$0")/.."

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree has uncommitted changes; commit first so the tag means something." >&2
  exit 1
fi
tag="$(git rev-parse --short=12 HEAD)"

echo "→ building nomal-api:$tag"
docker build -t "nomal-api:$tag" .

echo "→ loading the image on $host"
docker save "nomal-api:$tag" | gzip | ssh "$host" 'gunzip | sudo docker load'

echo "→ compose file and tag"
scp -q deploy/hetzner/compose.yaml "$host:/tmp/nomal-compose.yaml"
ssh "$host" "sudo install -m 0644 /tmp/nomal-compose.yaml $dir/compose.yaml && \
  sudo sed -i 's/^NOMAL_TAG=.*/NOMAL_TAG=$tag/' $dir/.env"

echo "→ pre-deploy dump of $db (swept into the encrypted backups by backup-postgres.sh)"
ts="$(date -u +%Y-%m-%d_%H%M)"
ssh "$host" "sudo docker exec kscw-postgres pg_dump -U supabase_admin $db | gzip | \
  sudo tee /data/backups/nomal_predeploy_${env}_${ts}.sql.gz >/dev/null"

echo "→ migrations"
ssh "$host" "cd $dir && sudo docker compose run --rm api alembic upgrade head"

echo "→ restart"
ssh "$host" "cd $dir && sudo docker compose up -d"

port="$(ssh "$host" "sudo grep -E '^NOMAL_PORT=' $dir/.env | cut -d= -f2")"
for _ in $(seq 1 20); do
  if ssh "$host" "curl -fsS http://127.0.0.1:$port/api/health" >/dev/null 2>&1; then
    echo "✓ nomal-$env healthy on :$port (tag $tag)"
    exit 0
  fi
  sleep 3
done
echo "✗ nomal-$env did not become healthy; check: ssh $host 'cd $dir && sudo docker compose logs --tail 50'" >&2
exit 1
