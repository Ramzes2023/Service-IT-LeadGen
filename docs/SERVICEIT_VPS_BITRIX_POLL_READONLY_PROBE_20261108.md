# Bitrix24 1062/21: prove read-only polling before choosing event token

**Purpose:** Test whether the corporate portal reliably supports the exact
ten-minute polling needed for a VPS-only RouterAI worker. The test does NOT
write to Bitrix24, create or modify an item, touch the Postgres queue, call a
model, or access the GitHub ledger.

Why this matters:
- With polling, the outgoing `bitrix_app_token` is not needed for intake,
  **provided the full VPS Compose is separately adjusted to poll-only mode**
  and authenticated event intake is kept disabled.
- The corporate Bitrix24 list API has previously returned misleading empty
  results. A zero-result scan alone does not prove compatibility.
- The preflight therefore requires successful `crm.item.list` lookup for
  existing card ID 3 in smart process 1062/category 21 (read-only), and then
  scans only the last 10 minutes of newly created 1062/21 cards.
- Results contain only aggregate counts and fixed diagnostic codes, no IDs.

## Run on the VPS AFTER merging this PR and pulling main

In Ubuntu root shell (not Windows PowerShell):

```bash
APP=/home/serviceitdeploy/serviceit/staging/source
sudo -u serviceitdeploy -H git -C "$APP" pull --ff-only origin main

docker run --rm --network host --read-only \
  --user 10001:10001 --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount type=bind,src="$APP/leadpilot",dst=/app/leadpilot,readonly \
  --mount type=bind,src=/etc/serviceit/secrets/bitrix_webhook,dst=/run/secrets/serviceit_bitrix_webhook,readonly \
  --env SERVICEIT_BITRIX_WEBHOOK_FILE=/run/secrets/serviceit_bitrix_webhook \
  serviceit-vps:readonly \
  python -m leadpilot.serviceit_vps_poll_readonly_probe
```

The command runs in a temporary, read-only container using the existing
installed REST webhook. It does not restart or replace the two healthy
READONLY Compose services. The host Docker socket is not exposed in the
container.

On success, expect JSON with
`status=BITRIX_1062_21_POLL_READONLY_COMPATIBLE`,
`positive_control_card3=PASS`, `crm_writes=0`, `ai_calls=0`,
`queue_inserts=0`.

On failure, the script returns `BITRIX_POLL_READONLY_BLOCKED` and a fixed
diagnostic reason; DO NOT enable polling in response to a failure.
A nonzero candidate count is an observation only: IDs are not queued and
nothing is processed.

## What happens next

If positive control and ten-minute scan both pass, the separate full
RouterAI-only Compose could be adapted for **poll-only** operation that
does not mount `bitrix_app_token`; that requires review and separate
operator cutover authorization. Old GitHub writers must be disabled only
after agreed handoff, and shared `event_bridge_key` and `gh_ledger_key`
must match the old ledger to avoid duplicate writes.

If it fails, use the official outgoing Bitrix event with the genuine
`auth.application_token`, after one permitted canary and proper request
authentication checks. Never substitute the incoming REST webhook as the
application token.

**Current state:** the live VPS is still READONLY with 4/8 RouterAI-only
secrets installed (PG, Bitrix REST, GitHub PAT, RouterAI).
