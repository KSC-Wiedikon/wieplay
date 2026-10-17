#!/usr/bin/env bash
# Connect an environment to its Hetzner Object Storage bucket. Prints no secret.
#
#   scripts/s3-env.sh dev|prod <bucket> <vault entry> [location]
#   e.g. scripts/s3-env.sh dev kscw-wieplay-dev wieplay-dev-s3 nbg1
#
# The vault entry (rbw, folder services/wieplay) holds the S3 credentials from the Hetzner
# Console: username = access key, password = secret key.
# Steps: WIEPLAY_S3_* into /opt/wieplay-<env>/.env on the VPS (backup first) → restart the API →
# bucket CORS + lifecycle rules (wieplay.cli storage-setup) → put/get/delete round trip.
set -euo pipefail

env="${1:-}" bucket="${2:-}" entry="${3:-}" location="${4:-nbg1}"
case "$env" in dev|prod) ;; *) echo "usage: scripts/s3-env.sh dev|prod <bucket> <vault entry> [location]" >&2; exit 2 ;; esac
[[ "$bucket" =~ ^[a-z0-9][a-z0-9-]{2,62}$ ]] || { echo "!! bucket name looks wrong: $bucket" >&2; exit 2; }
[ -n "$entry" ] || { echo "!! vault entry missing" >&2; exit 2; }
host="${WIEPLAY_DEPLOY_HOST:-hetzner}"
dir="/opt/wieplay-$env"
folder=services/wieplay

rbw unlocked >/dev/null 2>&1 || { echo "!! rbw is locked: run rbw unlock first" >&2; exit 1; }
rbw sync 2>/dev/null || true
access="$(rbw get --folder "$folder" --field username "$entry")"
secret="$(rbw get --folder "$folder" "$entry")"
[ -n "$access" ] && [ -n "$secret" ] || { echo "!! $folder/$entry needs username (access key) and password (secret)" >&2; exit 1; }

echo "→ WIEPLAY_S3_* in $dir/.env"
# Values travel on stdin, never in argv.
printf '%s\n%s\n' "$access" "$secret" | ssh "$host" "sudo bash -c '
  set -euo pipefail
  read -r access; read -r secret
  f=$dir/.env
  cp -p \$f \$f.bak-s3-\$(date -u +%Y%m%d-%H%M%S)
  grep -v -E \"^WIEPLAY_S3_(ENDPOINT|REGION|BUCKET|ACCESS_KEY|SECRET_KEY|PATH_STYLE)=\" \$f > \$f.tmp || true
  {
    echo WIEPLAY_S3_ENDPOINT=https://$location.your-objectstorage.com
    echo WIEPLAY_S3_REGION=$location
    echo WIEPLAY_S3_BUCKET=$bucket
    echo WIEPLAY_S3_ACCESS_KEY=\$access
    echo WIEPLAY_S3_SECRET_KEY=\$secret
    echo WIEPLAY_S3_PATH_STYLE=false
  } >> \$f.tmp
  chmod 600 \$f.tmp; mv \$f.tmp \$f
'"
unset access secret

echo "→ restart"
ssh "$host" "cd $dir && sudo docker compose up -d --force-recreate api >/dev/null 2>&1"
port="$(ssh "$host" "sudo grep -E '^WIEPLAY_PORT=' $dir/.env | cut -d= -f2")"
for _ in $(seq 1 20); do
  ssh "$host" "curl -fsS http://127.0.0.1:$port/api/health" >/dev/null 2>&1 && break
  sleep 3
done

echo "→ bucket rules and a round trip"
ssh "$host" "sudo docker exec -i wieplay-$env-api-1 python -" <<'PY'
import uuid
import httpx
from wieplay.cli import storage_setup
from wieplay.core.config import get_settings
from wieplay.core.storage import S3Store

s = get_settings()
assert s.video_enabled, "video still disabled: check the .env"
assert storage_setup() == 0
store = S3Store(s)
key = f"incoming/selftest-{uuid.uuid4().hex}/1/check.txt"
put = httpx.put(store.presign_put(key, 60), content=b"wieplay", headers={"Content-Type": "application/octet-stream"})
got = httpx.get(store.presign_get(key, 60))
store.delete([key])
gone = httpx.get(store.presign_get(key, 60))
print(f"put {put.status_code}, get {got.status_code} ({got.content!r}), after delete {gone.status_code}")
assert put.status_code == 200 and got.content == b"wieplay" and gone.status_code == 404
print("storage origin for the CSP:", s.storage_origin)
PY
echo "✓ $env uses bucket $bucket ($location)"
