# One verified corporate DEMO lead for Mikhail — operator handoff

Status at authoring: **pending one bounded operator execution**. Do not claim the single-card demo is already complete until the final local PowerShell result says `SERVICEIT_ONE_DEMO_LEAD=PASS`.

## Published source status

On 01 November 2026, GitHub returned successful merges for the **stacked, independently accepted** PRs:

- PR #36 → `main`: merge commit `8949795dffeecb9789b09ea68ee0d26990a3c1c0`.
- PR #37 → `main`: merge commit `79724df1bb7d33b50723ce752163a84a1eb271d8`.
- PR #38 → `main`: merge commit `8aad700ebb8dc86dbf1a1875774768c860427a73`.

Before these merges, independent Windows acceptance: **561 passed, 26 subtests passed**. There is no evidence that GitHub Actions ran new post-merge integration tests or that a live daemon was deployed.

## Proven real Bitrix demo

The original bounded V4 corporate operator reported `SERVICEIT_BITRIX_LIVE_1062=PASS` and verified existing synthetic cards:

- #3, `DEMO-ITREQ-001`, `DT1062_21:CLIENT`, [open live card](https://serviseit.bitrix24.ru/crm/type/1062/details/3/).
- #5, `DEMO-ITREQ-002`, `DT1062_21:PREPARATION`, [open live card](https://serviseit.bitrix24.ru/crm/type/1062/details/5/).

Confirmed process `entityTypeId=1062`, category 21, 16 custom fields and six stages. These are fictional requests, not actual customer records, and existing other funnels must not be changed.

## Final bounded one-card operator

An independently packaged local operator named `SERVICEIT_MIKHAIL_ONE_CORPORATE_DEMO_LEAD_20261101.zip` is available to the owner in ChatGPT. It does not create leads or connect sources.

With the owner's existing authorized REST webhook on their Windows machine, the operator:

1. Read-only rechecks the process metadata, field schema, stage count, robots and business-process automation disabled.
2. Verifies **all 16 UF fields**, exact synthetic ID/URL/INN/key and no linked real contact or company for cards #3 and #5.
3. Updates **only card #3** with an explicitly marked fictional 1С:Розница/KKT project scenario in `comments`, explanatory evidence and manager action. It preserves the exact DEMO idempotency key, source, title, source date and manager-review stage.
4. After verified readback, deletes **only card #5** if its identity and original fields remain unchanged; never deletes an altered or linked record.
5. Holds ambiguous updates/deletions in a separate local durable ledger; never retries uncertain mutations blindly, and rechecks that only #3 remains.

The operator's four local mock/safety test cases passed. **There has been no live execution of this final single-card operator yet.** The actual live result must be verified by the owner; source code and executable are in the packaged local artifact, not part of PR #38.

## Permissions, operational worker and source onboarding

The corporate webhook's access to #3/#5 can be exercised, but it does not prove Mikhail's **own** login RBAC. Mikhail should independently open #3 using his account. Avoid the standard `Связаться с клиентом` activity form: do not save a call or send a message for the DEMO company.

A source-neutral, bounded synthetic outbox/delivery/reconciliation handler is in `main` after #37/#38. This is **not** a production daemon, automatic real-lead ingestion, source activation or corporate manager-approved workflow. A production worker requires separate authorized service identity and security policy, verified source mapping, persistent state and monitoring, RBAC, dedupe, rollback procedures, and explicit activation approval. Do not call `PRODUCTION_READY` merely because two test cards and the source code have been verified.
