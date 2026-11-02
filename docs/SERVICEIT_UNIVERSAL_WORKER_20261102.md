# Universal enrichment worker — issue #74

**LIVE_UNVERIFIED. This change is not a production rollout or acceptance of all-card automation.**
No corporate API/model call, event registration, canary, historical writer or old ledger was executed during development.

## Implemented execution path

Authenticated JSON/form intake reuses `serviceit_bitrix_new_card_events` and its
`new_cards` table. The new `serviceit_enrichment_store` adds tables, without
renaming, deleting or rewriting existing event records. `serviceit_enrichment_worker`
connects this queue to scoped REST, independent approval evidence, the existing
deterministic engine, supported `AIProvider`, nine-field renderer, bounded update
and exact readback. All source-created cards enter the same queue; unknown sources
do not receive invented verification.

One worker runs serially (maximum concurrency one), up to ten claims per 30-second
cycle. A bounded 50-item, ascending-ID `crm.item.list` catches missed events in
1062/21. Configure an administrator-approved first new ID >=8; the cursor is
persisted only after enqueue, so a crash repeats reads/inserts, not writes. Events
and reconciliation deduplicate on entity+item. Older IDs #3/#7 are hard excluded.

Claims use `BEGIN IMMEDIATE`, owner tokens and five-minute leases. Before every
external side effect, reservation checks owner, lease and RUNNING state atomically.
Expired pre-model/pre-write claims retry at most three times; reserved model work
goes to NEEDS_REVIEW and reserved writes to UNCERTAIN. HOLD is terminal and never
automatically retried. Model reservations deduplicate company/source/text across
cards and cap calls at ten per rolling 24 hours. Exceptions never manufacture an
AI report. Model output digest and reservations persist; model confidence cannot
change score. Privacy approval precedes sending only sanitized evidence text.
Completion also checks the current lease and RUNNING state atomically: a delayed
owner cannot replace recovered UNCERTAIN/NEEDS_REVIEW with a late success. A lost
lease returns LEASE_LOST; the next recovery retains the external-operation fence.

Only nine AI fields or nine plus Score/version/badge may be sent to one
`crm.item.update`. Both UF aliases, writable scalar types, all twelve user-field
configurations and their limits are checked read-only. CRM automation and business
processes must be disabled, preventing unrelated automatic side effects. All twelve
fields must have an explicitly empty before-image, even for a nine-field HOLD.
Any populated value blocks the whole write. A second exact snapshot check precedes
reservation. No tasks, stages, contacts, customers, schema or record creation.

HTTP errors, 429, timeout, malformed success, readback mismatch and unknown response
after reservation are UNCERTAIN. Never resend that update. `reconcile_uncertain`
performs exact read-only target comparison and leaves the reservation intact.
It does not turn a target-only comparison into proof of a complete historical write.

## Trusted corporate verification boundary (still blocked externally)

The worker **does not implement or claim live global customer, FNS, source or legal
verification**. The existing repository also lacks approved production access for
these checks. CRM flags, supplier notices and source-batch JSON cannot grant them.

The supplied adapter reads a private, read-only mounted approval document
`/run/serviceit-approvals/<item-id>.json`. This is a concrete canary/adapter contract,
not an unattended verification service. A corporate verifier must generate it from
actual authorized checks before general production qualification can be automatic.
Never mount supplier uploads or a source-writable directory there. Only the trusted
corporate verifier/operator may write approvals. Do not commit them or raw logs.

Required common fields:

| Field | Required value |
| --- | --- |
| portal / entity_type_id / category_id / item_id | exact corporate identity / 1062 / 21 / fresh ID |
| before_hash | `fingerprint(normalized(crm.item.get item))` from the approved fresh snapshot |
| expires_at | UTC epoch, future and no more than one hour away |
| write_authorized | boolean true, actual owner permission for that card |
| exclusive_edit_window | boolean true, enforced operationally for the approved card |
| automation_side_effects_reviewed | boolean true; schema also independently confirms automation off |

For a verified lead: `company` uses the existing engine schema (inn/name/region/
industry), `signals` contains one primary signal (signal_type/signal_date/source_url/
evidence), `source_name` exactly matches CRM, and `privacy_approved=true`.
INN checksum, company name, URL, published date and source name bind the approval
to CRM. Evidence must be current, primary, lawful and safe. Each `checks` entry
has `state=VERIFIED` and a nonempty private `evidence_ref`: company_registry,
source, existing_customer, crm_dedup, stop_list, legal_basis, contact,
private_small_moscow, manager_criteria. The last two preserve Mikhail's private
micro/small Moscow/MO target and remaining corporate criteria. These are independent
attestations, not fields copied from external notices. No permission or no evidence
means typed HOLD and zero update/model call.

For the narrowly classified B2B unverified supplier notice, common write approval
plus `preliminary_hold_authorized=true`, the verified-safe `safe_notice_subject`
and existing service groups such as `["1C"]` allow only nine preliminary fields.
The subject is not a verified tender quote. Model is NOT_RUN, no score/version/badge,
buyer, INN, publication verification or permission to contact is fabricated. New
B2B cards already receive these nine fields in the existing source create path;
the worker preserves them rather than overwriting or calling the model again.

Bitrix update has no compare-and-swap contract here: a read-before-write alone
cannot eliminate a simultaneous manager edit. Hence the exclusive edit window is
a mandatory approval gate. Do not enable general unattended writes until the
corporate deployment can enforce that window or supplies a verified revision policy.
There is deliberately no overwrite/revision or automatic HOLD-release policy.

## Deployment and controls

Use an approved corporate Linux host, never an assumed VALYQON server or public
laptop callback. Overlay:

```sh
docker compose -f deploy/serviceit-event-receiver.compose.yml \
  -f deploy/serviceit-enrichment.compose.yml config --quiet
docker compose -f deploy/serviceit-event-receiver.compose.yml \
  -f deploy/serviceit-enrichment.compose.yml up -d --build
```

First complete the receiver's existing private deployment runbook and HTTPS/TLS
reverse-proxy validation. It binds only loopback port 18780. Limit body size,
request rate and route; no proxy/access logging of event payloads or tokens.
Register Bitrix app/event only after actual callback/token/permission tests pass.
Inbound REST secret and outgoing application_token are different secrets.

The overlay uses the same durable named volume, UID/GID 10001, read-only root,
capability drop, no-new-privileges, healthcheck and restart policy. Set private
host paths SERVICEIT_APPROVAL_HOST_DIR, SERVICEIT_REST_SECRET_FILE and
SERVICEIT_GIGACHAT_SECRET_FILE. Docker secrets expose REST/model secrets only to
the worker. Receiver's application token retains the existing deployment's env
contract; container administration must be restricted. No secret files in Git.

Default worker invocation prints BLOCKED_OFFLINE without reading secrets or
opening a database. `--serve` requires SERVICEIT_WORKER_LIVE_ENABLED=YES_1062_21,
SERVICEIT_POLL_MIN_ID and the dedicated absolute new-card-events.sqlite3 path.
GigaChat additionally requires SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=YES,
AI_ENABLE=1, approved corporate scope/model and a mounted model secret. TLS
verification remains enabled; no unauthorized retry of model completions.

Local commands inside the worker container:

```sh
python -m leadpilot.serviceit_enrichment_worker --inspect
python -m leadpilot.serviceit_enrichment_worker --health
```

After all actual approval gates, `--canary-id <fresh-id>` processes only that
authorized item once, without polling or claiming another card. It still requires
the same live opt-in/secrets/approval document. Do not combine it with `--serve`.
Only VERIFIED yields exit code zero; inspect a preliminary HOLD's reason separately.

Inspection is bounded/redacted: card ID, state, fixed reason, attempts and reservation
states only. SQL `enrichment_audit` provides transition/reservation history;
`model_reservations` provides budget/dedup/digest audit. Private expected field values
are retained only in the protected ledger for exact readback. Restrict backup access.
Periodic stdout reports queue state counts and RUNNING_GATED/BLOCKED_READ_OR_CONFIG.
Health proves heartbeat freshness, **not readiness or live qualification**.

Stop/pause with `docker compose ... stop worker` (receiver can continue enqueueing).
For an update: stop worker, back up SQLite using its online backup API or stop both
services before a consistent volume snapshot, retain image digest, then deploy and
inspect state/health. Roll back the image while retaining the same volume, never
restore stale ledgers over newer reservations, delete volumes or reset UNCERTAIN.

SQLite uses short transactions, FULL synchronous commits, ten-second busy timeout,
explicit connection close and local filesystem locks on Windows/Linux. No network
filesystem or multi-host shared SQLite deployment is supported. Schema migration is
additive; receiver can enqueue before/after migration. Historic one-shot ledgers are
not opened. Read-only queue tools now ignore terminal jobs and understand states.

## Live component/source status and single next actions

| Component/source | Actual status | Next action |
| --- | --- | --- |
| Receiver + scheduler | code/mock tested; deployment/event delivery LIVE_UNVERIFIED | corporate admin provisions approved HTTPS host and app callback |
| GigaChat | supported provider wired; mock transport only in this mission | owner enables corporate privacy/scope and mounted credential |
| Safe writer/canary | code/mock tested; zero live writes | owner/verifier supplies bounded fresh-card approval after full prechecks |
| Corporate source/FNS/customer/dedup/legal verifier | missing authorized integration; adapter contract only | authorize and connect corporate verifier producing approval documents |
| B2B-Center | existing create adapter/workflow supports nine-field HOLD; prior owner creation evidence, not revalidated here | confirm existing workflow permissions/secrets and observe a new HOLD |
| Bidzaar, SUPPMAN/ONLINECONTRACT | no live connected integration established | obtain platform/API permission and approved evidentiary adapter |
| 1C/IT/Bitrix24/ККТ/ЭДО/equipment | service classifications; not proof of connected platforms | connect authorized primary evidence and corporate verifier |
| Manager Kanban/shared layout | not modified or accepted | manager validates presentation under own account |

## Authorized canary acceptance (not performed)

1. On approved host, validate deployment, durable backup/locks, HTTPS callback,
   app token distinct from REST credential, tenant/category/schema and permissions.
2. Owner selects **new** corporate 1062/21 card >=8, excludes all historical attempts,
   approves exact before-image, side-effect-free configuration, exclusive edit window,
   all corporate gates and privacy/model budget. Mount short-lived approval.
3. Send/observe actual create event, start bounded worker and check real GigaChat
   reservation/provider trace/quote; verify a single twelve-field update/readback.
4. Observe second delivery/restart: no model or update repeat. Test a separate new
   B2B HOLD with nine preliminary fields and zero score. Do not induce ambiguous
   production writes to test failure recovery; use mocks for that scenario.
5. Record redacted tenant-authorized acceptance artifact and manager visibility.
   Until all steps pass, record LIVE_UNVERIFIED, not “all cards completed”.

Official parameter references:
[crm.item.update](https://apidocs.bitrix24.ru/api-reference/crm/universal/crm-item-update.html),
[crm.item.list](https://apidocs.bitrix24.ru/api-reference/crm/universal/crm-item-list.html).
