#!/usr/bin/env bash
# Move a WiePlay environment's secrets from the VPS's root-only file into the password vault
# (rbw, folder services/wieplay), check each copy, then delete the file. Prints no secret.
#
#   rbw unlock && scripts/vault-secrets.sh dev|prod
#
# Entries: wieplay-<env>-jwt-signing-key, wieplay-<env>-service-token, wieplay-<env>-db-role,
# wieplay-<env>-env (mirror of /opt/wieplay-<env>/.env). An entry that exists is kept as is.
set -euo pipefail

env="${1:-}"
case "$env" in
  dev) role=wieplay_dev ;;
  prod) role=wieplay ;;
  *) echo "usage: scripts/vault-secrets.sh dev|prod" >&2; exit 2 ;;
esac
host="${WIEPLAY_DEPLOY_HOST:-hetzner}"
folder=services/wieplay
src="/root/wieplay-secrets/$env.env"
rbw unlocked >/dev/null 2>&1 || { echo "!! rbw is locked: run rbw unlock first" >&2; exit 1; }
# `rbw add` writes to the server itself; a sync that needs a fresh login is not fatal.
sync() { rbw sync 2>/dev/null || echo "(rbw sync skipped: it wants the master password again)"; }
sync

# Value of one key in the VPS file. Dev's database password predates the generic key name.
remote() { ssh "$host" "sudo grep -m1 -E '^($1)=' $src | cut -d= -f2-"; }
db_key='WIEPLAY_DB_PASSWORD|WIEPLAY_DEV_DB_PASSWORD'
ssh "$host" "sudo test -f $src" || { echo "!! $src not found on $host" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
chmod 700 "$work"

add() {   # add <name> [user]; the entry comes from $work/content (line 1 = password)
  if rbw get --folder "$folder" "$1" >/dev/null 2>&1; then
    echo "= $1 exists in the vault, kept"
  else
    # Piped, rbw add reads the password from the first line and the notes from the rest
    # (it opens an editor only on a terminal).
    rbw add --folder "$folder" "$1" ${2:+"$2"} < "$work/content"
    echo "✓ $1 added"
  fi
}

kid="$(remote WIEPLAY_JWT_KID)"
{ remote WIEPLAY_JWT_PRIVATE_KEY
  echo "Ed25519 private key Wiedisync $env signs WiePlay login tokens with. One line, \\n escapes, as in the Directus .env (WIEPLAY_JWT_PRIVATE_KEY). kid = $kid (WIEPLAY_JWT_KID)."
  echo "Public key (WiePlay: WIEPLAY_BRIDGE_PUBLIC_KEYS = {\"$kid\": \"<pem>\"} in /opt/wieplay-$env/.env):"
  remote WIEPLAY_JWT_PUBLIC_KEY
  echo "Rotate: new kid; add its public key to WiePlay first, then switch Directus."
} > "$work/content"
add "wieplay-$env-jwt-signing-key" "$kid"

{ remote WIEPLAY_BRIDGE_SERVICE_TOKEN
  echo "Service token WiePlay $env sends to the Wiedisync bridge (X-WiePlay-Service-Token); WIEPLAY_BRIDGE_SERVICE_TOKEN in /opt/wieplay-$env/.env."
  echo "Directus stores only its sha256 (WIEPLAY_SYNC_TOKEN_HASH):"
  remote WIEPLAY_SYNC_TOKEN_HASH
} > "$work/content"
add "wieplay-$env-service-token"

{ remote "$db_key"
  echo "Postgres login role for WiePlay $env. NOSUPERUSER, owns database $role only, cannot read Wiedisync tables. Host kscw-postgres:5432. Source of truth: WIEPLAY_DATABASE_URL in /opt/wieplay-$env/.env."
} > "$work/content"
add "wieplay-$env-db-role" "$role"

{ echo "see notes"
  echo "Mirror of /opt/wieplay-$env/.env on the VPS ($(date +%d.%m.%Y); WIEPLAY_TAG changes with every deploy):"
  ssh "$host" "sudo cat /opt/wieplay-$env/.env"
} > "$work/content"
add "wieplay-$env-env"
rm -f "$work/content"
sync

# Compare digests, never values: vault copy vs. VPS file. rbw may still be rewriting its
# local copy right after an add, so read with a few retries.
vault_value() {
  for _ in 1 2 3 4 5; do
    rbw get --folder "$folder" "$1" 2>/dev/null && return 0
    sleep 1
  done
  return 1
}
same() {   # same <entry> <key regex>
  local here there
  here="$(vault_value "$1" | tr -d '\n' | sha256sum | cut -d' ' -f1)"
  there="$(ssh "$host" "sudo grep -m1 -E '^($2)=' $src | cut -d= -f2- | tr -d '\n' | sha256sum | cut -d' ' -f1")"
  [ "$here" = "$there" ] || { echo "!! $1 in the vault differs from $src; file kept" >&2; exit 1; }
}
same "wieplay-$env-jwt-signing-key" WIEPLAY_JWT_PRIVATE_KEY
same "wieplay-$env-service-token" WIEPLAY_BRIDGE_SERVICE_TOKEN
same "wieplay-$env-db-role" "$db_key"
echo "✓ vault copies match the VPS file"

ssh "$host" "sudo shred -u $src && sudo rmdir --ignore-fail-on-non-empty /root/wieplay-secrets"
echo "✓ $src deleted"
