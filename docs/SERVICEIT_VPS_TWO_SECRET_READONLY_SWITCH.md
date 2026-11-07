# Service-IT VPS: two-secret READONLY Compose (2026-11-07)

This migration replaces two manual Docker containers with a Compose stack
without transferring the remaining AI keys, modifying Bitrix CRM, accepting
events, calling AI, or enabling LIVE processing.

## Verified starting point (operator reports)

- VPS: Ubuntu 24.04, Docker and Caddy active.
- Both local and public HTTPS /health returned gateway running and zero CRM writes.
- Both /etc/serviceit/secrets/pg_password and bitrix_webhook exist; owner UID 10001.
- PostgreSQL leadgen has table serviceit_vps_new_cards and its index.
- Manual coordinator --once verified nine AI fields with zero writes and calls.
- The two currently running standalone containers are serviceit-vps-gateway
  and serviceit-vps-coordinator-readonly.
- Five GitHub-held secrets have NOT yet been transferred to VPS.

## Run AFTER merging the associated PR

Run in the SSH Ubuntu terminal (NOT Windows PowerShell):

```bash
APP=/home/serviceitdeploy/serviceit/staging/source
sudo -u serviceitdeploy -H git -C "$APP" pull --ff-only origin main
test -f "$APP/deploy/serviceit-vps-two-secret-readonly.compose.yml"
bash "$APP/deploy/serviceit-vps-two-secret-readonly-switch.sh" \
  "$APP/deploy/serviceit-vps-two-secret-readonly.compose.yml" \
  CONFIRM_READONLY_SWITCH
```

The script first validates both old containers, local /health, existing
secret files, the local Docker image and Compose config.
On failure in preflight it does not stop anything.
After the switch it checks local /health, a successful live READONLY metadata
preflight in coordinator logs, and public HTTPS /health.

On a failure after stopping the two old containers it attempts rollback:
remove only the new Compose stack and start the two old containers again.
No Postgres data volumes or Caddy configuration are changed.

## Verify

```bash
APP=/home/serviceitdeploy/serviceit/staging/source
docker compose -f "$APP/deploy/serviceit-vps-two-secret-readonly.compose.yml" ps
curl -fsS https://leadgen.serviseit.ru/health
```

Expected: READONLY_COMPOSE_SWITCH=PASS, crm_writer_enabled=false.
Old containers remain stopped, not deleted.

## Manual rollback

```bash
APP=/home/serviceitdeploy/serviceit/staging/source
docker compose -f "$APP/deploy/serviceit-vps-two-secret-readonly.compose.yml" down
docker start serviceit-vps-gateway serviceit-vps-coordinator-readonly
```

Verify local and public /health after rollback.

## Explicit limits and further gates

- Remaining GitHub Action Secrets are NOT fetched, copied or updated by this PR.
- bitrix_app_token and github_ledger_pat are still missing.
- No historical card changes, no new-card intake, no CRM writes.
- Old GitHub cloud writers keep their current state; no double writer is started.
- Complete deploy/serviceit-vps.compose.yml requires nine valid secret files.
  Do not fabricate placeholders or enable LIVE gates before separate approval.
- Exposed passwords and the previously pasted private key must be rotated/
  revoked before production use; verify the old SSH fingerprint no longer works.
