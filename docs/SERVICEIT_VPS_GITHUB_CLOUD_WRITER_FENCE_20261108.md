# ServiceIT — GitHub cloud-writer fence for a later VPS cutover (2026-11-08)

**State:** code-only migration safety preparation. No production cutover or CRM write is performed by this change.

VPS already has four healthy READONLY native containers, genuine Bitrix
readonly field/positive-control access and scoped RouterAI permission flags.
Old GitHub jobs still own writes at the time this was written.

## Which old GitHub jobs share the future native CRM write scope

The following 13 legacy job conditions now each require
`vars.SERVICEIT_VPS_CUTOVER_ACTIVE != 'YES'`:

- `serviceit-bitrix-all-new-batch-ai.yml` (already fenced)
- `serviceit-bitrix-new-card-ai.yml` (already fenced)
- `serviceit-free-cloud-ai-cards.yml` (legacy manual AI)
- `serviceit-crm-duplicate-auto.yml`
- `serviceit-finance-hybrid-live.yml`
- `serviceit-fns-msp-crm-result.yml`
- `serviceit-kanban-three-signal-title-auto.yml`
- `serviceit-leadgen-free-msp-internal-score.yml`
- `serviceit-own-signal-stop-auto.yml`
- `serviceit-postcall-manager-approved-auto.yml`
- `serviceit-stage3-all-new-task-automation.yml`
- `serviceit-stage4-all-new-signed-score-writer.yml`
- `serviceit-stage4-prelim-score-live.yml`

The fourteenth workflow, `serviceit-bitrix-poll-fallback.yml`,
blocks BOTH scheduled discovery/dispatch and the owner live-one-shot action
when the cutover variable is `YES`. Its manual read-only diagnostic still
works.

## Deliberately NOT changed

- Sources / intake: B2B-Center, Gmail/App Script and manually authorized
  source imports. Incoming NEW cards must continue being created after cutover.
- READONLY CRM diagnostics, the cloud reliability watchdog, and HTTPS
  monitor; these never claim to perform native production CRM writes.
- One-time administrator CRM layout / sales-stage provisioning: separate
  controlled admin changes, not concurrent new-card writers. Keep approval
  requirements in place.
- Any unrelated repository/Windows/Bitrix internal automation: must be
  independently inventoried before calling complete isolation.

## Critical: changing the variable does not drain in-flight jobs

A job whose condition was evaluated while the cutover variable was not
`YES` can finish AFTER the variable changes. GitHub cron/issue events
may continue to queue. Therefore never treat the variable as proof that
all old writers have stopped. On a separate approved cutover:

1. Read all 14 gates on the deployed `main` commit and verify that
   `SERVICEIT_VPS_CUTOVER_ACTIVE` is presently not `YES`.
2. Independently verify the current source configurations, Bitrix app
   automations, any external Windows tasks and existing in-flight runs.
3. Have owner authorize a scheduled maintenance window and any necessary
   rollback. Set the cloud cutover variable to `YES`; verify it by
   readback and audit skipped workflow jobs.
4. Wait for ALL preexisting overlapping GitHub jobs to finish; inspect
   their external CRM readback and encrypted ledger uncertain receipts.
   Never infer no execution from the absence of in_progress runs alone.
5. Record the fresh future-only UTC native activation time AFTER the drain.
6. Independently validate VPS nonsecret gates, exact RouterAI synthetic
   API and approved data scope, one-use PostgreSQL receipts, STOP/dedup,
   authorized employee for tasks and no surprises in existing CRM.
7. Separately approve and enable the native `LIVE` gate and individual
   modules. The optional corporate signed formal Score and FNS-MSP
   snapshot are not automatically cleared for production.
8. Test one newly created allowed card end-to-end with nine AI field
   exact readback, then tasks/title/Score only where independently
   authorized. Monitor and preserve a rollback path that never blindly
   replays uncertain external writes.

This PR only adds future-safe conditions. While
`SERVICEIT_VPS_CUTOVER_ACTIVE` is unset or not `YES`, existing
GitHub cloud jobs are unchanged in behavior.
