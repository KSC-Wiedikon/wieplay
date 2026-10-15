#!/usr/bin/env bash
# One-time setup of a Nomal environment on the VPS, end to end. Prints no secret.
#
#   scripts/setup-hetzner.sh dev|prod
#
# 1. deploy/hetzner/setup-env.sh on the VPS: secrets, database and role, NOMAL_* lines in
#    the Directus .env, /opt/nomal-<env>/.env
# 2. Wiedisync's scripts/directus-set-env.sh sets NOMAL_SYNC_TOKEN_HASH and recreates
#    Directus once (about a minute; health check, rolls back on failure)
# 3. scripts/deploy.sh <env>
# 4. scripts/vault-secrets.sh <env>, when rbw is unlocked
#
# Needs: ssh access to the VPS with sudo, a Wiedisync checkout next to this repo (or
# WIEDISYNC_DIR), Docker, and a clean working tree (deploy.sh tags the image by commit).
# The Cloudflare Tunnel route (docs/PLAN.md §14) can come before or after.
set -euo pipefail

env="${1:-}"
case "$env" in
  dev) directus_url=https://directus-dev.kscw.ch public=https://nomal-dev.kscw.ch ;;
  prod) directus_url=https://directus.kscw.ch public=https://nomal.kscw.ch ;;
  *) echo "usage: scripts/setup-hetzner.sh dev|prod" >&2; exit 2 ;;
esac
host="${NOMAL_DEPLOY_HOST:-hetzner}"
cd "$(dirname "$0")/.."
wiedisync="${WIEDISYNC_DIR:-$(cd .. && pwd)/wiedisync}"
set_env="$wiedisync/scripts/directus-set-env.sh"
[ -f "$set_env" ] || { echo "!! $set_env not found; set WIEDISYNC_DIR" >&2; exit 1; }
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "!! working tree has uncommitted changes (deploy.sh needs a clean tree)" >&2; exit 1
fi

echo "→ 1/4 secrets, database, env files on $host"
scp -q deploy/hetzner/setup-env.sh "$host:/tmp/nomal-setup-env.sh"
ssh "$host" "sudo bash /tmp/nomal-setup-env.sh $env; rc=\$?; rm -f /tmp/nomal-setup-env.sh; exit \$rc"

echo "→ 2/4 Directus: NOMAL_SYNC_TOKEN_HASH and one recreate"
ssh "$host" "sudo grep -m1 '^NOMAL_SYNC_TOKEN_HASH=' /root/nomal-secrets/$env.env | cut -d= -f2-" \
  | bash "$set_env" "$env" NOMAL_SYNC_TOKEN_HASH
# Configured bridge: a bad return URL is 400. Unconfigured it answers 503.
code="$(curl -s -o /dev/null -w '%{http_code}' -A nomal-setup "$directus_url/kscw/nomal/v1/authorize?return=x")"
if [ "$code" != 400 ]; then
  echo "!! bridge answered $code instead of 400; check the Directus .env and logs" >&2; exit 1
fi
echo "✓ bridge configured"

echo "→ 3/4 first deploy"
scripts/deploy.sh "$env"

echo "→ 4/4 vault"
if command -v rbw >/dev/null && rbw unlocked >/dev/null 2>&1; then
  scripts/vault-secrets.sh "$env"
else
  echo "rbw is locked or missing: run 'rbw unlock && scripts/vault-secrets.sh $env' later"
fi

code="$(curl -s -o /dev/null -w '%{http_code}' -A nomal-setup "$public/api/health" || true)"
echo
echo "Done. $public/api/health → $code (502/530 until the tunnel route exists)."
echo "Next: log in as a Wiedisync admin, Admin → Teams, switch the teams on."
