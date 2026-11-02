# ServiceIT release preflight — read only

This step follows the merged universal worker in PR #85, but it **does not
activate production**. The command checks local permissions/configuration and,
when explicitly opted in on the authorized worker host, the actual Bitrix24
1062/21 schema and an ID-only sample of new cards.

## Run on approved corporate host

Before using it, an administrator must independently provision and approve:
- HTTPS event receiver/application token, signed event delivery and a persistent
  private SQLite queue;
- dedicated read-only authorization for the schema and category checks;
- REST credential and GigaChat credential via Docker secrets, private approvals
  directory, and explicit privacy/legal approval;
- independent real verifier for primary source, FNS/INN, existing customers,
  global CRM duplicates, stop list, permission to contact and manager criteria;
- exclusive update window and controlled new-card live canary.

**Local, offline diagnostic** (does not read secrets or connect):
```bash
python -m leadpilot.serviceit_production_readiness --config-only
```

**Optional, actual read-only CRM schema diagnostic** from the worker container,
only after authorized operator configuration:
```bash
SERVICEIT_PREFLIGHT_READONLY=YES python -m leadpilot.serviceit_production_readiness --live-readonly
```

The command uses only these REST methods: `crm.type.getByEntityTypeId`,
`crm.item.fields`, `userfieldconfig.list`, `userfieldconfig.get`, and
`crm.item.list`. The REST webhook is read from
`/run/secrets/serviceit_rest_webhook`. There are no customer/CRM writes,
AI invocations, historical card updates, event bindings or contact actions.

The local check examines, but never logs, these settings:
`SERVICEIT_WORKER_LIVE_ENABLED=YES_1062_21`,
`SERVICEIT_POLL_MIN_ID` (must be >=8),
`SERVICEIT_APPROVAL_DIR`, worker REST/model secret mounts,
`SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=YES`,
`AI_ENABLE=1`, `AI_PROVIDER=gigachat`, and
`SERVICEIT_PREFLIGHT_READONLY=YES`.
This is **not** a secret installation or credential request.

The results are deliberately fail-closed and redacted: only fixed status labels,
category ID, number of schema fields and sample count. A positive read-only probe
means solely that those specific checks passed. It **does not** verify model
access, 9-gate corporate verification, primary-source authenticity, live
webhook delivery, manager approval, exclusive edit windows or a live canary.

## Acceptance sequence

1. Validate the PR #85 main CI and preflight CI without production credentials.
2. Authorized owner/admin provisions host, HTTPS and Bitrix event binding; observe
   one real event and its durable queue record.
3. Corporate FNS/source/customer/legal verifier provides short-lived, independently
   supported approval evidence; do not generate proof from synthetic/demo records,
   supplier email subjects or unverified local FNS exports.
4. On a **new** authorized card with ID >=8, demonstrate one genuine GigaChat
   response, deterministic Score, exact 12-field CRM update and two-pass readback.
   Separately prove one B2B HOLD preserves no Score and no contact action.
5. Duplicate event, restart and ambiguous timeout must not cause a second POST.
6. Manager accepts Kanban presentation under their own corporate account.

**Current result: LIVE_UNVERIFIED.** Source integration is separate from a
service-category classifier; no automatic FNS, primary tender, customer/dedup,
legal clearance or server deployment is established merely by this PR.
Do not close Issue #74 until evidence from these real gates is recorded.
