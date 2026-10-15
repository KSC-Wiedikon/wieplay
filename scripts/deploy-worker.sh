#!/usr/bin/env bash
# Build the media worker image on the Lenovo server and restart it.
#
#   scripts/deploy-worker.sh dev|prod
#
# Runs on the Lenovo server itself (the worker runs there; no registry needed). The stack
# directory ~/stacks/nomal-worker-<env> holds compose.yaml (from deploy/lenovo/), .env and
# worker.token (0600, from Admin → Workers).
set -euo pipefail

env="${1:-}"
case "$env" in
  dev|prod) ;;
  *) echo "usage: scripts/deploy-worker.sh dev|prod" >&2; exit 2 ;;
esac
dir="${NOMAL_WORKER_STACK:-$HOME/stacks/nomal-worker-$env}"
cd "$(dirname "$0")/.."

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree has uncommitted changes; commit first so the tag means something." >&2
  exit 1
fi
[ -f "$dir/.env" ] && [ -f "$dir/worker.token" ] || {
  echo "!! $dir needs .env and worker.token (see deploy/lenovo/env.example)" >&2; exit 1; }
tag="$(git rev-parse --short=12 HEAD)"

echo "→ building nomal-worker:$tag"
docker build -f Dockerfile.worker -t "nomal-worker:$tag" .
install -m 0644 deploy/lenovo/compose.yaml "$dir/compose.yaml"
sed -i "s/^NOMAL_TAG=.*/NOMAL_TAG=$tag/" "$dir/.env"
(cd "$dir" && docker compose up -d)
sleep 5
(cd "$dir" && docker compose logs --tail 5)
echo "✓ nomal-worker-$env running $tag"
