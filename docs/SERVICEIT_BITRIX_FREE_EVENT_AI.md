# ServiceIT internal new-card AI event path

2026-11-03: **OFFLINE IMPLEMENTATION; LIVE UNVERIFIED**. Scope:
`serviseit.bitrix24.ru`, smart process 1062/category 21, repository
`Ramzes2023/Service-IT-LeadGen`. Historical cards 3 and deleted 7 are excluded.
No DPAPI secrets were read, production variables changed, resources deployed,
real GigaChat requests made or corporate CRM cards written in this session.

## Architecture

Official Bitrix outgoing ONCRMDYNAMICITEMADD webhook → NEW owner-only standalone
Apps Script project → HMAC-signed ID-only GitHub repository_dispatch → Actions
on main → exact crm.item.get → manager AI fields → exact crm.item.update →
exact readback. The event path never calls crm.item.list. Existing Gmail apps,
source integrations and deployed infrastructure are unchanged.

Owner-only describes project editing. The HTTP callback must allow anonymous
external callers and execute as owner. The reviewed manifest already specifies
ANYONE_ANONYMOUS, USER_DEPLOYING and external-request scope. Every callback
checks exact Bitrix domain and the real outgoing application_token. The bridge
forwards no customer fields. Category is verified from the exact CRM read.

GitHub verifies repository, branch, schema, HMAC, timestamp and ID. Before
processing, it creates an encrypted one-use receipt with a keyed opaque filename
on serviceit-ai-encrypted-ledger. Before updating CRM, it checkpoints exact
expected fields with GitHub SHA compare-and-swap. No checkpoint acknowledgment
means no CRM write. Report content is encrypted, never plaintext in the public
receipt branch. Retain the ledger encryption key for recovery.

Repeated events with expected fields now perform GET-only readback comparison.
Owner-triggered workflow_dispatch with item_id provides the same GET-only
recovery after event expiry, even with the write gate OFF. Neither path repeats
AI or a CRM update. Missing/mismatched receipts remain in review. An ambiguous
dispatch without a write receipt still requires owner diagnosis.

## Honest reports and approved GigaChat

Unverified cards receive nine fields: status, summary, hypothesis, quote,
recommendation, limitations, full report, manager brief and next action. Actual
sanitized CRM context is labeled unverified. No invented buyer verification,
source citation, qualification, score or contact permission is produced.

Complete preliminary NOT_RUN_SOURCE_UNVERIFIED HOLD reports are preserved
without an update. Partial reports retain existing text and fill missing fields.
Other existing analysis/qualification blocks processing for review. Business
fields, stage, manager, contacts and tasks are outside the update allowlist;
successful writes verify their preservation.

Optional public-title GigaChat requires explicit corporate privacy approval and
a public-host allowlist. Only validated title text is sent; full CRM, contact
and INN data are excluded. Default is no model call. Failed model attempts are
reported honestly and never blindly replayed.

The event worker now accepts trusted GitHub secret
SERVICEIT_CORPORATE_APPROVALS_JSON, an object keyed by numeric item ID. Values
follow approval_check/verified_fields in serviceit_enrichment_worker.py:
portal/type/category/item identity, exact before fingerprint, expiry within one
hour, explicit write/exclusive-edit/automation approval, privacy approval,
bound company/source evidence, and nine VERIFIED gates with evidence references:
company registry, source, existing customer, CRM dedup, stop list, legal basis,
contact, private small Moscow company and manager criteria.

Only then does the existing GigaChat provider receive bounded validated evidence
and the deterministic engine produce score/version/badge. GigaChat remains
primary. The existing provider configuration boundary is retained; a future
provider change requires review of report and privacy contracts.

The universal worker supports fresh exact-snapshot approval with explicit
replace_preliminary_hold_authorized to upgrade preliminary HOLD. B2B release
also needs verified_source_reassessment and preliminary_stop_reviewed when a
preliminary stop exists. Business stop fields remain unchanged. CRM text never
substitutes for independent evidence.

**Remaining integration limitation:** the cloud event consumes trusted approval
available at first processing. It does not continuously collect corporate
evidence or automatically promote an already fenced HOLD when approval arrives
later. That requires a separately reviewed trusted adapter and durable promotion
protocol. Rerunning an event is not authorization to overwrite a report/repeat AI.

## Owner-local installation

After review, successful Python CI and merge, from the reviewed repo root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\INSTALL_SERVICEIT_BITRIX_EVENT_BRIDGE.ps1 -Apply
```

Without -Apply the installer exits before secrets/production access. The separate
owner-run installer reads the existing same-user DPAPI REST webhook from
LOCALAPPDATA/ServiceIT/AI-Autofill/bitrix-rest.dpapi. It checks repository origin,
GitHub owner/repository and Google owner identity; starts the event gate OFF;
generates/reuses a dedicated DPAPI HMAC key; uploads secrets via gh stdin; and
creates the encrypted receipt branch if absent.

It installs only a NEW standalone clasp project. Existing local clasp identity
causes refusal before deployment, preventing reuse of Gmail or another app.
A failed installation requires owner review of the partial project before a
new attempt. Private source is removed locally in finally; the owner-only
deployed project necessarily retains credentials for dispatch.

The owner deploys as themselves with anonymous access. Fresh anonymous
/exec?probe=... must return the expected HMAC health proof. A login page, stale
disabled version, different project or blocked anonymous access fails closed.
Health signatures are domain-separated from event signatures.

**Bitrix outgoing webhook UI requires an authorized portal user's action;
administrator action is required if that user's permissions or portal policy
do not allow creation.** No impersonation or permission bypass is provided.
In the official webhook builder, create/save the outgoing webhook using the new
/exec handler and dynamic-item-add event. Only then paste its freshly generated
application_token into the hidden LOCAL prompt. The inbound REST URL is not an
outgoing token. No token is invented or printed.

Only after all acknowledgments does the installer enable the future-event gate
for a canary. The owner creates one genuinely NEW approved 1062/21 test card.
The installer checks nine fields, triggers a correlated GET-only recovery run,
and requires successful EXACT_TARGET_READBACK against encrypted expected fields.
Nonempty fields alone cannot establish LIVE. Failure attempts to disable the
gate. If GitHub is unavailable, the owner must verify the variable state manually.
Failed canaries are never blindly resubmitted.

## Offline test results

| Check executed here | Result |
| --- | --- |
| Python 3.12 compileall, leadpilot and tests | PASS |
| Node offline Apps Script suite | PASS, 11 tests |
| Windows PowerShell 5.1 installer AST syntax | PASS |
| git diff --check | PASS |
| Python offline pytest | BLOCKED: pytest is not installed |
| Dependency installation into workspace .venv | BLOCKED: network socket access denied |

Added Python coverage: signed-event end-to-end manager fields; trusted approved
GigaChat boundary; lost update acknowledgment with GET-only recovery; failed
checkpoint; missing receipt; complete/partial HOLD preservation; explicit HOLD
upgrade; empty page contradicting positive total without cursor advancement.
These Python tests compile but **have not executed here**. Empty-page handling
concerns the pre-existing universal poller; the event path performs no polling.

Run where repository dependencies are installed:

```powershell
python scripts/serviceit_offline_pytest.py -q tests/test_serviceit_bitrix_dispatch_worker.py tests/test_serviceit_windows_autofill.py tests/test_serviceit_enrichment_worker.py tests/test_serviceit_bitrix_nested_items.py tests/test_serviceit_empty_group_broad_fallback.py
node integrations/bitrix_event_bridge/tests/test_bridge.cjs
```

## Exact activation blockers

1. Execute Python regressions successfully, review and merge before installation.
2. Corporate authorization for new-card AI writes, ID forwarding and optional
   model use. Independent nine-gate evidence is required for qualification;
   none was accessed or asserted here.
3. Owner-local identity/DPAPI preflight and a new web app demonstrably executing
   anonymously as owner.
4. Authorized Bitrix user/admin creation of the official outgoing webhook and
   its real generated application_token, supplied only locally.
5. Genuine NEW 1062/21 event canary and exact encrypted manager-report receipt
   readback. No live canary has run in this session.
6. Fully automatic GigaChat after later corporate approval remains blocked on
   the trusted evidence adapter and durable approval-continuation protocol.

Apps Script/Actions quotas mean no delivery SLA. Script-property receipt growth,
encrypted receipt retention and failed dispatches need owner monitoring; lossless
delivery at arbitrary volume is not established. GigaChat may have separate
usage costs. No paid infrastructure was provisioned.
