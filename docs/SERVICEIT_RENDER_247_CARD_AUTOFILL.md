# ServiceIT AI cards — Render 24/7 cutover (2026-11-02)

**Status: code candidate only, not deployed or verified on Render or real CRM.**

Purpose: move the new-card 1062/21 poller away from the owner's Windows
laptop, which stops whenever the PC is off. This workstream changes only
new-card AI formatting/qualification; does NOT modify procurement sources,
other projects, CRM historical cards #3/#7, or the Kanban tile.

## Why Render background worker

Render's always-running **paid** background workers can poll external APIs.
The existing SQLite reservation/AI-charging/CRM-write fence requires a **paid
persistent disk** to survive restarts. Free web services sleep and are not
appropriate for this safety-critical ledger.

Blueprint `render.yaml` proposes one starter Python background worker in
Frankfurt and one 1 GB encrypted persistent disk. This incurs compute and
storage costs, subject to owner's explicit approval of the Render plan.

## Cloud startup gates

The deployed service defaults to `SERVICEIT_CLOUD_LIVE_ENABLED=NO` and
`SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=NO`; **no Bitrix REST calls** until
enabled. Do not deploy without agreeing to the Render charges.

Enter secrets into **Render Environment** (never git, chat, task arguments,
or logs):

- `SERVICEIT_BITRIX_WEBHOOK`: approved webhook for
  `serviseit.bitrix24.ru`, with minimal CRM read/update-field permissions.
- `GIGACHAT_CREDENTIALS` and `GIGACHAT_SCOPE`: an owner-approved corporate
  Authorization Key + its exact PERS/B2B/CORP scope.
- `SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=YES` only if corporate policy
  permits sending the **independently approved, sanitized evidence** to
  GigaChat; leave NO otherwise.
- `SERVICEIT_CLOUD_START_AFTER_ID`: the latest pre-cutover **existing**
  1062/21 CRM ID (>=7), verified read-only immediately before cutover.
  This prevents the cloud queue replaying previously processed cards. The
  value is immutable on the persistent disk after first live cycle.
- `SERVICEIT_CLOUD_LIVE_ENABLED=YES_1062_21_CLOUD` last, only after
  distinct-cloud-ledger and Windows-scheduler cutover have been reviewed.

`python -m leadpilot.serviceit_render_worker --check` is offline and
shows only redacted gate readiness; no CRM contact. The default `startCommand`
uses a bounded 300-second loop and a subprocess that runs the same guarded
`serviceit_windows_autofill --once` code on the cloud disk. Subprocess
timeout/status are deliberately coarse; no customer or secret text goes
to logs.

## Limitations and tasks still open (Issue #74)

1. This only moves the scheduler to an always-on host. It **does not** solve
   automatic independent verification of company registry, source, existing
   customer, CRM duplicates, stop lists, legal basis, contacts, region and
   manager criteria. Without a verified approval file, the current worker
   writes nine preliminary HOLD fields, and **GigaChat is NOT called and
   Score is NOT calculated**.
2. A preliminary HOLD cannot currently auto-promote after independent
   verification; the same item's write reservation prevents blind retries.
   Implement a distinct audit-backed upgrade/reconciliation protocol with
   a second monotonic phase and regression tests before production enable.
3. Real 12/12 field exact-readback for a new permitted CRM card, GigaChat
   provider call, and timer execution on Render remain unverified.
4. **Never** run the cloud and local Windows writer concurrently over the
   same ID interval. The cloud ledger is separate from the Windows DPAPI
   ledger; cutover must establish the correct high-watermark and disable the
   Windows scheduled task, and retain the backup/audit log.
5. Disabling the Windows task is an owner-side action **after the cloud
   cutover is validated**. Do not delete its state/credentials automatically.
6. Render installation in ChatGPT is **not** itself a deployment, and
   the Windows DPAPI-encrypted GigaChat secret cannot be automatically
   decrypted in Render. Set the two secrets privately in Render settings.

## Acceptance checklist

- Render paid-worker/disk approved and provisioned, single instance.
- Initial cloud START_AFTER_ID matches the live existing-card high watermark;
  `cloud_cutover_cursor` persisted, not reset on redeploy.
- Local Task Scheduler stopped at handover, no dual CRM writers.
- One authorized real new (ID > cutover) 1062/21 card is discovered exactly
  once, with original manager/source/stage values unchanged.
- Real independently verified source + 9 checks arrives from a trusted
  adapter; sanitized source reaches GigaChat only with privacy permission.
- Deterministic explainable Lead Score and 9 AI fields/3 score fields pass
  exact Bitrix readback.
- HOLD that later becomes verified is upgraded safely, without resetting
  write fences or reusing ambiguous responses.
- Alerting and secrets rotation documented; retain durable ledger.
