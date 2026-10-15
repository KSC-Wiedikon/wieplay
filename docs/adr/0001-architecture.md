# ADR-001: Architecture for M0

- Status: accepted (15.10.2026)
- Context: `docs/PLAN.md` revision 4, §3–§8

## Decision

1. **Separate service and repo.** Nomal is its own FastAPI + React app at `nomal.kscw.ch`, not
   another build of Wiedisync. It keeps Directus permissions out of Nomal's hot path and lets the
   ML side be Python.
2. **Same origin for web and API.** The API serves the built SPA, so Nomal's session cookie is
   host-only and Nomal's API needs no CORS.
3. **Login through a Wiedisync bridge, not credentialed CORS.**
   - The Wiedisync session cookie is host-only on `directus.kscw.ch`.
   - The bridge (`kscw-endpoints/src/nomal-bridge.js`) turns it into a 60-second,
     single-use, Ed25519-signed token on a top-level redirect.
   - Nomal verifies it with the bridge's public key, records the token id, and starts its own
     8-hour session.
4. **Team enablement lives in Nomal.** *Change from the plan, which put a toggle in
   `teams.features_enabled`.*
   - Full Wiedisync admins switch teams on in Nomal's admin page, per team lineage, so a team
     stays on across the 1 June rollover.
   - The bridge returns team metadata for all teams, but personal data only for the team ids
     Nomal asks for.
   - Result: no Wiedisync migration or permission change, and coaches can't enable their own
     team.
5. **Mirror, don't share tables.**
   - Nomal has its own `nomal` database on the same Postgres server.
   - It stores Wiedisync ids and a minimal mirror (no email, phone, address or birthdate),
     replaced on every sync.
   - Members not on an enabled team are purged.
6. **Service calls stay internal.**
   - Nomal → bridge calls use `X-Nomal-Service-Token` over the VPS's Docker network.
   - The bridge refuses them when they arrive through Cloudflare.

## Consequences

- One small Wiedisync PR (KSC-Wiedikon/wiedisync#8) and its env (`NOMAL_*`) are needed before
  login works outside local development.
- Local development needs no Wiedisync: `NOMAL_DEV_BRIDGE=true` serves a fake bridge with
  made-up teams and personas, and the sync reads the same fixtures.
- The token format is a cross-language contract. `backend/tests/test_bridge_contract.py`
  verifies a token produced by the JavaScript bridge.
