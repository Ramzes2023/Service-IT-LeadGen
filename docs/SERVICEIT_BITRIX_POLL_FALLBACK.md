# ServiceIT Bitrix24: no-outgoing-webhook permission fallback

2026-11-03: the owner can open Bitrix24's developer area but sees
"Доступ к интеграции запрещён". Do not bypass the portal ACL or forge an
outgoing application_token. Bitrix `event.bind` does NOT work under inbound
webhook authentication (WRONG_AUTH_TYPE), so the previous event/webapp path
is blocked without a portal user's ability to create an outgoing webhook.

## Alternative

An ALREADY authorized Bitrix24 **incoming CRM REST webhook** is used exclusively
for periodic `crm.item.list` discovery: only fields `id`, `categoryId`,
`createdTime` for process `entityTypeId=1062` and `categoryId=21`.
A reviewed GitHub Actions job runs at UTC minute 7/17/27/37/47/57
(best effort, GitHub schedules can be delayed or skipped).
It signs only new card numeric IDs in memory and dispatches the existing
`serviceit_bitrix_item_add_v1` event to the existing guarded code from PR #122.
The original event worker gets an exact `crm.item.get` before any model/update,
and reserves a one-use encrypted receipt. Existing 9-field exact readback stays.

Critical safety:
- The cron workflow is **OFF** until both `SERVICEIT_BITRIX_POLL_ENABLED`
  equals `YES_1062_21_READONLY` AND `SERVICEIT_BITRIX_EVENT_AI_ENABLED`
  equals `YES_1062_21_CLOUD`.
- Activation UTC is recorded only AFTER the owner's actual read-only
  `crm.item.list` preflight is successful, so historical cards never qualify.
- Every list result must respect the creation-time lower bound, category 21,
  type 1062 when returned, a strict pagination contract and bounded volume.
  Unexpected shapes/old IDs/page overflow block the entire batch; no writes.
- No full CRM fields, company names, emails, phone or INN appear in the
  GitHub dispatch, only signed numeric ID. Poll logs contain no IDs.
- If the GitHub job or webhook is unavailable for >2 hours, some new cards
  can be missed. This fallback is best-effort, not lossless event delivery.
- `crm.item.update` is ONLY performed by the existing event worker when
  all its gates pass; poller itself never calls it.
- Source integrations, manager, stage, contacts, old #3/deleted #7 and existing
  support pipeline remain outside scope.
- GitHub Actions minutes, quotas and GigaChat consumption can have fees or
  throttling depending on account, plan and usage. Check before ongoing use.

## Owner operation

First stop the waiting initial `INSTALL_SERVICEIT_BITRIX_EVENT_BRIDGE.ps1
-Apply` at the outgoing application_token prompt using Ctrl+C. Do NOT paste
the incoming REST webhook, fake token or Google URL. Do not retry the original
outgoing-webhook installer; it generated a standalone owner Google project
that remains independent, but its handler is not used in the fallback.

After the reviewed polling PR is merged, run this first in Windows PowerShell:

```powershell
$Repo = Join-Path $HOME 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
git -C $Repo pull --ff-only origin main
if ($LASTEXITCODE -ne 0) { throw 'GIT_SYNC_FAILED' }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Repo\scripts\ENABLE_SERVICEIT_BITRIX_POLL_FALLBACK.ps1"
```

Expect `SERVICEIT_BITRIX_LIST_READONLY=PASS`. If the authorized existing
REST webhook does not have `crm.item.list` permission, stop. This cannot be
fixed by code without additional portal permissions.

Only after PASS, start the same reviewed script with `-Apply`. It checks
GitHub secret **names** already provisioned by the halted outgoing installer,
privacy variable and GigaChat credentials when enabled, explicitly requests
the owner's `YES`, sets activation UTC and turns the 2 gates ON. No
outgoing Bitrix webhook, Apps Script callback or application_token is needed.

Activation is not proof of live success. Observe the next GitHub scheduled
poll and a genuinely NEW approved test card (1062/category 21). Verify that
the existing event worker completes and a correlated exact 9-field receipt
is confirmed with `crm.item.get`. If no test card arrives, report
`LIVE_UNVERIFIED`. Disable with owner-run commands:

```powershell
gh variable set SERVICEIT_BITRIX_POLL_ENABLED --repo Ramzes2023/Service-IT-LeadGen --body NO
gh variable set SERVICEIT_BITRIX_EVENT_AI_ENABLED --repo Ramzes2023/Service-IT-LeadGen --body NO
```

If the portal later grants rights for a genuine outgoing webhook, keep
the polling fallback disabled when activating the original event bridge.
Do not run both at once without an explicit deduplication and quota review.
