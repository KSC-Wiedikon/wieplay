#!/usr/bin/env bash
# One-time setup of a Nomal environment on the VPS. Runs as root; started by
# scripts/setup-hetzner.sh, which also does the Directus recreate and the first deploy.
#
#   setup-env.sh dev|prod
#
# Creates, without printing any secret:
#   - /root/nomal-secrets/<env>.env (0600): signing key pair, service token and its
#     sha256, database password. scripts/vault-secrets.sh moves it into the vault.
#   - the database and its login role on kscw-postgres (no other privileges)
#   - NOMAL_JWT_PRIVATE_KEY, NOMAL_JWT_KID, NOMAL_RETURN_URLS, NOMAL_LOGIN_URL in the
#     Directus .env. They take effect when Directus is recreated, which
#     scripts/setup-hetzner.sh does once, together with NOMAL_SYNC_TOKEN_HASH.
#   - /opt/nomal-<env>/.env (0600)
# Refuses to run when any of these exists already.
set -euo pipefail
umask 077

env="${1:-}"
case "$env" in
  dev)
    db=nomal_dev port=8090 public=https://nomal-dev.kscw.ch issuer=wiedisync-dev
    directus=directus-kscw-dev directus_env=/opt/directus-kscw-dev/.env
    directus_url=https://directus-dev.kscw.ch login=https://wiedisync-dev.kscw.ch/login ;;
  prod)
    db=nomal port=8091 public=https://nomal.kscw.ch issuer=wiedisync-prod
    directus=directus-kscw directus_env=/opt/directus-kscw/.env
    directus_url=https://directus.kscw.ch login=https://wiedisync.kscw.ch/login ;;
  *) echo "usage: setup-env.sh dev|prod" >&2; exit 2 ;;
esac
[ "$(id -u)" = 0 ] || { echo "!! run as root" >&2; exit 1; }

secrets="/root/nomal-secrets/$env.env"
dir="/opt/nomal-$env"
for f in "$secrets" "$dir/.env"; do
  if [ -e "$f" ]; then echo "!! $f exists: $env is set up already. Nothing changed." >&2; exit 1; fi
done
if grep -q '^NOMAL_JWT_KID=' "$directus_env"; then
  echo "!! $directus_env has NOMAL_* lines already. Nothing changed." >&2; exit 1
fi
# -i only where a heredoc feeds psql: otherwise docker exec would read the ssh session's stdin.
pg() { docker exec kscw-postgres psql -U supabase_admin -X -q -v ON_ERROR_STOP=1 "$@"; }
pg_in() { docker exec -i kscw-postgres psql -U supabase_admin -X -q -v ON_ERROR_STOP=1 "$@"; }
if [ "$(pg -d postgres -tA -c "SELECT (SELECT count(*) FROM pg_database WHERE datname = '$db')
    + (SELECT count(*) FROM pg_roles WHERE rolname = '$db')")" != 0 ]; then
  echo "!! database or role $db exists. Nothing changed." >&2; exit 1
fi

# On any failure, undo what this run did, so it can simply run again.
tmp="$(mktemp -d)"
ts="$(date -u +%Y%m%d-%H%M%S)"
made_secrets="" made_db="" made_directus="" made_nomal=""
cleanup() {
  local rc=$?
  rm -rf "$tmp"
  [ "$rc" = 0 ] && return
  echo "!! setup failed (exit $rc); undoing this run's changes" >&2
  if [ -n "$made_nomal" ]; then rm -f "$dir/.env"; fi
  if [ -n "$made_directus" ]; then mv "$directus_env.bak-nomal-$ts" "$directus_env"; fi
  if [ -n "$made_db" ]; then
    pg -d postgres -c "DROP DATABASE IF EXISTS $db" -c "DROP ROLE IF EXISTS $db" || true
  fi
  if [ -n "$made_secrets" ]; then rm -f "$secrets"; fi
  echo "!! undone; fix the cause and run again" >&2
}
trap cleanup EXIT

# --- secrets ---------------------------------------------------------------------------
openssl genpkey -algorithm ed25519 -out "$tmp/private.pem"
openssl pkey -in "$tmp/private.pem" -pubout -out "$tmp/public.pem"
oneline() { sed -e ':a' -e 'N' -e '$!ba' -e 's/\n/\\n/g' "$1"; }   # PEM on one line, \n escapes
kid="nomal-$env-1"
private_key="$(oneline "$tmp/private.pem")"
public_key="$(oneline "$tmp/public.pem")"
token="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n')"
token_hash="$(printf '%s' "$token" | sha256sum | cut -d' ' -f1)"
db_password="$(openssl rand -hex 24)"

mkdir -p /root/nomal-secrets && chmod 700 /root/nomal-secrets
made_secrets=1
cat > "$secrets" <<EOF
# Nomal $env secrets ($(date -u +%d.%m.%Y)). scripts/vault-secrets.sh $env copies them into the vault and deletes this file.
NOMAL_JWT_KID=$kid
NOMAL_JWT_PRIVATE_KEY=$private_key
NOMAL_JWT_PUBLIC_KEY=$public_key
NOMAL_BRIDGE_SERVICE_TOKEN=$token
NOMAL_SYNC_TOKEN_HASH=$token_hash
NOMAL_DB_PASSWORD=$db_password
EOF
echo "✓ secrets written to $secrets (0600)"

# --- database: its own login role, nothing else -----------------------------------------
made_db=1
pg_in -d postgres <<SQL
CREATE ROLE $db LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '$db_password';
CREATE DATABASE $db OWNER $db;
REVOKE ALL ON DATABASE $db FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE $db TO $db;
SQL
pg -d "$db" -c "REVOKE ALL ON SCHEMA public FROM PUBLIC; GRANT ALL ON SCHEMA public TO $db;"
for wd in postgres directus_kscw_dev; do
  if [ "$(pg -d "$wd" -tA -c "SELECT has_table_privilege('$db', 'public.members', 'SELECT')")" != f ]; then
    echo "!! role $db can read $wd.members: stop and check the grants" >&2; exit 1
  fi
done
echo "✓ database $db owned by role $db (cannot read Wiedisync's members, prod or dev)"

# --- Directus: four NOMAL_* lines, live after the recreate --------------------------------
cp -p "$directus_env" "$directus_env.bak-nomal-$ts"
made_directus=1
{
  cat "$directus_env"
  printf '%s\n' "NOMAL_JWT_PRIVATE_KEY=$private_key" "NOMAL_JWT_KID=$kid" \
    "NOMAL_RETURN_URLS=$public/api/auth/callback" "NOMAL_LOGIN_URL=$login"
} > "$directus_env.tmp"
chmod --reference="$directus_env" "$directus_env.tmp"
chown --reference="$directus_env" "$directus_env.tmp"
mv "$directus_env.tmp" "$directus_env"
echo "✓ $directus_env: 4 NOMAL_* lines added (backup .env.bak-nomal-$ts); live after the recreate"

# --- Nomal ------------------------------------------------------------------------------
mkdir -p "$dir" && chmod 700 "$dir"
made_nomal=1
cat > "$dir/.env" <<EOF
NOMAL_TAG=pending
NOMAL_PORT=$port
NOMAL_ENV=$env
NOMAL_PUBLIC_URL=$public
NOMAL_DATABASE_URL=postgresql+psycopg://$db:$db_password@kscw-postgres:5432/$db
NOMAL_COOKIE_SECURE=true
NOMAL_SYNC_INTERVAL_MINUTES=60
NOMAL_BRIDGE_AUTHORIZE_URL=$directus_url/kscw/nomal/v1/authorize
NOMAL_BRIDGE_API_URL=http://$directus:8055/kscw/nomal/v1
NOMAL_BRIDGE_ISSUER=$issuer
NOMAL_BRIDGE_SERVICE_TOKEN=$token
NOMAL_BRIDGE_PUBLIC_KEYS={"$kid":"$public_key"}
EOF
chmod 600 "$dir/.env"
echo "✓ $dir/.env written (0600)"
