# 2026-11-08 — keep existing GitHub cloud writer; RouterAI only

**Owner direction:** Do not rotate or replace existing credentials and do not
connect GigaChat. The corporate VPS successfully completed a synthetic
authenticated RouterAI chat request and a read-only ten-minute Bitrix24
1062/category21 poll positive-control (card #3 PASS, 0 candidates). This is
not a real end-to-end CRM write.

## Current conservative migration route

- Legacy GitHub variables observed by the owner:
  `SERVICEIT_BITRIX_EVENT_AI_ENABLED=YES_1062_21_CLOUD`,
  `SERVICEIT_BITRIX_POLL_ENABLED=YES_1062_21_READONLY`,
  `SERVICEIT_VPS_CUTOVER_ACTIVE` unset.
- The existing GitHub scheduled poll and batch worker can retain access
  to their existing `SERVICEIT_GH_LEDGER_KEY` and
  `SERVICEIT_BITRIX_EVENT_BRIDGE_KEY` **without exporting or rotating**.
- VPS is still READONLY and does not have the original ledger Fernet key.
  It MUST NOT become an independent CRM writer while the key is absent.
- This change removes GigaChat from the **two production cloud writers**
  (exact event and all-new batch). AI_PROVIDER is RouterAI only.
  `SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT=NO`; no GigaChat credential is
  passed and no special GigaChat CA bundle is required.
- Existing key validation, independent RouterAI privacy gate, encrypted
  one-use reservation, and exact-card readback remain unchanged.
- Failure of RouterAI should produce NEEDS_REVIEW, NOT a second provider
  call or an automatic repeat of an uncertain Bitrix write.
- The separate historical read-only standby audit is unchanged, because
  it performs no model or CRM calls; it can be retired later.

## What still requires verification

- The **actual last scheduled GitHub Actions runs** (result, time,
  new-card candidates, dispatches, errors) have not been verified from
  this PR. GitHub Variables being enabled is not evidence of a successful
  real run.
- Confirm the original GitHub Secrets and privacy approvals remain valid
  and the worker's own existing gates pass. Never show secret values.
- Until a new, authorized 1062/21 card is observed with nine-field
  write+readback, do not claim full automation is live.
- For eventual VPS cutover, obtain the original ledger key or perform a
  separately reviewed, coordinated transition preserving old-card
  protections. Do not create an unrelated new key and enable live.

No VPS container changes, no SSH key changes, no GitHub secret changes,
no CRM changes, and no workflows are manually dispatched by this PR.
