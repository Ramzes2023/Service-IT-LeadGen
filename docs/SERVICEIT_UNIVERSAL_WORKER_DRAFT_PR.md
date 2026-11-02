# Draft PR prepared for issue #74 (not published)

Title: `ServiceIT: universal gated enrichment worker, durable reservations and reconciliation`

Base: `main`

Head: `codex/serviceit-all-cards-20261102-173516-c3d4a2`

New 1062/21 cards now have a reusable execution path from authenticated create
events or bounded reconciliation into atomic queue claims, corporate verification
gates, deterministic scoring, authorized GigaChat, and one allowlisted CRM update
with exact readback. Existing AI/manager values are preserved. B2B supplier notices
remain preliminary HOLD with nine honest fields and no artificial score.

Dedicated production reservations fence model charges and writes before dispatch.
Duplicate events, restarts, stale claims and ambiguous updates do not replay POSTs.
Cards #3/#7 and historical ledgers are excluded. No contacts, outreach, tasks,
stages, schema or customer-list writes. The Compose overlay shares the persistent
event volume and adds isolated worker secrets, health and operator inspection.

Validation: Python 3.12 compile; full network-blocked pytest; real SQLite and
MockTransport/provider E2E; Windows PowerShell smoke. See
`SERVICEIT_UNIVERSAL_WORKER_VALIDATION_20261102.md` for exact counts. Docker is not
installed locally; CI is configured for container build/Compose/cold-start checks.
CI has not run for these unpublished changes.

**Not production accepted.** Corporate verifier integrations are still missing;
the supplied adapter consumes trusted short-lived mounted approval evidence.
Deployment, HTTPS/event installation, corporate CRM/model permissions, exclusive
edit-window enforcement and a new authorized live canary remain external gates.
No production API/model calls or deployment were made. LIVE_UNVERIFIED.

Related: #74, #66. This does not close #74 or claim all-card live acceptance.

Publication blocked: local `.git` is read-only; connected GitHub tree creation
requires approval unavailable under this launcher's `never` approval policy.
