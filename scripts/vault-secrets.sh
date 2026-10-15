#!/usr/bin/env bash
# Move a Nomal environment's secrets from the VPS's root-only file into the password vault
# (rbw, folder services/nomal), check each copy, then delete the file. Prints no secret.
#
#   rbw unlock && scripts/vault-secrets.sh dev|prod
#
# Entries: nomal-<env>-jwt-signing-key, nomal-<env>-service-token, nomal-<env>-db-role,
# nomal-<env>-env (mirror of /opt/nomal-<env>/.env). An entry that exists is kept as is.
set -euo pipefail

env="${1:-}"
case "$env" in
  dev) role=nomal_dev ;;
  prod) role=nomal ;;
  *) echo "usage: scripts/vault-secrets.sh dev|prod" >&2; exit 2 ;;
esac
host="${NOMAL_DEPLOY_HOST:-hetzner}"
folder=services/nomal
src="/root/nomal-secrets/$env.env"
rbw unlocked >/dev/null 2>&1 || { echo "!! rbw is locked: run rbw unlock first" >&2; exit 1; }
# `rbw add` writes to the server itself; a sync that needs a fresh login is not fatal.
sync() { rbw sync 2>/dev/null || echo "(rbw sync skipped: it wants the master password again)"; }
sync

# Value of one key in the VPS file. Dev's database password predates the generic key name.
remote() { ssh "$host" "sudo grep -m1 -E '^($1)=' $src | cut -d= -f2-"; }
db_key='NOMAL_DB_PASSWORD|NOMAL_DEV_DB_PASSWORD'
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

kid="$(remote NOMAL_JWT_KID)"
{ remote NOMAL_JWT_PRIVATE_KEY
  echo "Ed25519 private key Wiedisync $env signs Nomal login tokens with. One line, \\n escapes, as in the Directus .env (NOMAL_JWT_PRIVATE_KEY). kid = $kid (NOMAL_JWT_KID)."
  echo "Public key (Nomal: NOMAL_BRIDGE_PUBLIC_KEYS = {\"$kid\": \"<pem>\"} in /opt/nomal-$env/.env):"
  remote NOMAL_JWT_PUBLIC_KEY
  echo "Rotate: new kid; add its public key to Nomal first, then switch Directus."
} > "$work/content"
add "nomal-$env-jwt-signing-key" "$kid"

{ remote NOMAL_BRIDGE_SERVICE_TOKEN
  echo "Service token Nomal $env sends to the Wiedisync bridge (X-Nomal-Service-Token); NOMAL_BRIDGE_SERVICE_TOKEN in /opt/nomal-$env/.env."
  echo "Directus stores only its sha256 (NOMAL_SYNC_TOKEN_HASH):"
  remote NOMAL_SYNC_TOKEN_HASH
} > "$work/content"
add "nomal-$env-service-token"

{ remote "$db_key"
  echo "Postgres login role for Nomal $env. NOSUPERUSER, owns database $role only, cannot read Wiedisync tables. Host kscw-postgres:5432. Source of truth: NOMAL_DATABASE_URL in /opt/nomal-$env/.env."
} > "$work/content"
add "nomal-$env-db-role" "$role"

{ echo "see notes"
  echo "Mirror of /opt/nomal-$env/.env on the VPS ($(date +%d.%m.%Y); NOMAL_TAG changes with every deploy):"
  ssh "$host" "sudo cat /opt/nomal-$env/.env"
} > "$work/content"
add "nomal-$env-env"
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
same "nomal-$env-jwt-signing-key" NOMAL_JWT_PRIVATE_KEY
same "nomal-$env-service-token" NOMAL_BRIDGE_SERVICE_TOKEN
same "nomal-$env-db-role" "$db_key"
echo "✓ vault copies match the VPS file"

ssh "$host" "sudo shred -u $src && sudo rmdir --ignore-fail-on-non-empty /root/nomal-secrets"
echo "✓ $src deleted"
