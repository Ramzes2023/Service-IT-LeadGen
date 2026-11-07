# Corporate Service-IT VPS: polling-only RouterAI, 7 secret files

Status: **code and offline CI candidate; NOT a production cutover**.
On 2026-11-08, the operator executed the Bitrix24 positive-control probe
from a temporary readonly VPS container. Sanitized result:

```json
{"status":"BITRIX_1062_21_POLL_READONLY_COMPATIBLE",
 "positive_control_card3":"PASS",
 "new_card_candidates":0,
 "new_card_window_minutes":10,
 "queue_inserts":0,"crm_writes":0,"ai_calls":0}
```

This confirms a read-only list API works with the positive control, not
that a new Bitrix card has already been processed.

## Architecture choice

- VPS checks **new 1062/category 21** Bitrix cards using the EXISTING
  incoming REST webhook via `crm.item.list`, not incoming Bitrix events.
- `SERVICEIT_VPS_INTAKE_ENABLED` is hardcoded `NO` in production Compose.
  The public `/integrations/serviceit/bitrix24/new-card` endpoint remains
  rejected (HTTP 503); no `bitrix_app_token` is mounted or needed.
- No GigaChat fallback; RouterAI only.
- Normal operation 600-second loop; the existing production discovery window
  is based on the explicit activation time and a maximum 7-day lookback.
  It validates pagination, individual timestamps and historical card 3 as
  positive control. This protects against an API silently returning an
  empty list or ignoring a time filter, but cannot guarantee an external
  portal never loses events.
- The queue uses Postgres unique card IDs; exact worker reuses the SAME
  GitHub encrypted per-card reservations as older GitHub Actions workers.
- Default Compose: `READONLY`, `POLL_ENABLED=NO`, `INTAKE=NO`,
  `CUTOVER_AUTHORIZED=NO`, `OLD_GH_WRITERS_DISABLED_ACK=NO`.
- LIVE is disallowed unless explicit, separate owner cutover, old-writer
  shutdown proof, `POLL_ENABLED=YES`, RouterAI-only and its privacy approval.
  Without an actual verified future activation UTC timestamp, `poll_new`
  fails closed BEFORE claiming or processing a card.

## Secret inventory (4/7 present on the VPS)

Present: `pg_password`, `bitrix_webhook`, `github_ledger_pat`,
`routerai_key`. Both GitHub PAT read tests and actual RouterAI synthetic
completion passed from the VPS.

Still required:
- `event_bridge_key`: event HMAC used by exact worker. Must be coordinated
  with current cloud event handlers; do not unilaterally rotate.
- `gh_ledger_key`: existing Fernet key for the GitHub encrypted per-card
  one-use ledger. MUST match the old workers to prevent duplicate writes.
- `brave_api_key`: approved public research enrichment API key. Retrieve
  from provider or reissue with explicit owner coordination. An existing
  GitHub Actions secret cannot be read back.

VPS local-only installer is
`deploy/serviceit-vps-interactive-secret-install.py`.

List only the seven required polling-mode secret names and their
permission/status metadata on VPS (never reads or prints values):

```bash
APP=/home/serviceitdeploy/serviceit/staging/source
python3 "$APP/deploy/serviceit-vps-interactive-secret-install.py" inventory-poll-only
```

The older `inventory` command remains for legacy full inventory; it
includes now-optional `bitrix_app_token` and `gigachat_credentials`.
Do not paste secret contents in chats, shell commands, GitHub Issues,
workflow logs or commits.

## Before any live operation

1. Obtain and locally install the last 3 genuine credentials; verify
   inventory **without printing values**. Confirm old ledger encryption
   key agreement. If retrieval is impossible, coordinate a planned rotation.
2. Verify GitHub fine-grained PAT supports encrypted-ledger **Contents
   write**, not merely reading the branch, using an explicitly approved
   controlled test with no real CRM card or reservation.
3. Retain the running existing two-secret READONLY containers and do not
   overlap their host port with the full Compose.
4. Audit current GitHub workflows and any Windows schedule; disable ALL
   overlapping writers at a documented cutover, keeping rollback state.
5. Verify the target nine Bitrix fields on a fresh read, STOP/dedup logic,
   legal privacy scope and approved domains. Do not bypass HOLD gates.
6. Rebuild the reviewed Docker image and perform health check, schema
   check, dry-run poll and one **approved genuine future card** readback.
7. Only then enable the exact authorized LIVE gates, with human-controlled
   revert that never replays UNCERTAIN writes.

No part of this PR changes Bitrix24 items or permissions, active VPS
containers, existing GitHub workflows/secrets, or historical cards.
