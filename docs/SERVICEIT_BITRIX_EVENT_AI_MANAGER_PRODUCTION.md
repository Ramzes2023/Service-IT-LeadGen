# ServiceIT — automatic AI inside Bitrix24 for EVERY NEW 1062/21 card

## Business result

The primary input is a NEW Bitrix24 smart-process card, not an external tender
source or an email. The trigger is the official `ONCRMDYNAMICITEMADD`
event for type 1062, constrained after fetch to category 21.

1. Bitrix24 sends an authenticated HTTPS creation callback with the item ID.
2. A single Render web instance stores only that exact ID in a persistent,
   private, idempotent queue; no CRM data is stored in the event envelope.
3. An always-on worker looks up that ID via `crm.item.get`, never by sweeping
   `crm.item.list` (which is not reliable for this portal).
4. For any unverified new card with empty analysis, the worker writes nine
   honest, useful review fields. The report includes the actual CRM title,
   company label, service direction and source, all explicitly marked as
   NOT VERIFIED, plus a manager checklist and next action.
5. If corporate approval for that exact card and all nine evidence gates is
   present, the existing worker calculates deterministic Score/version/badge,
   calls real GigaChat under separate privacy consent, validates a verbatim
   source quote, writes nine AI + three Score fields and reads everything back.
6. For unverified PUBLIC procurement notices only, optional separately approved
   GigaChat title-only mode creates a genuine but UNVERIFIED AI hypothesis with
   quotation and manager advice; it never produces Score/qualified lead.
7. Both completed HOLD and VERIFIED cards are visible to their existing
   responsible Bitrix24 manager in the same card. This integration DOES NOT
   silently create CRM tasks, calls, manager assignment, or stage changes.

## Authentication and data minimization

The public callback requires the actual Bitrix outgoing-event
`auth.application_token` and exact domain. An incoming REST webhook is a
different secret and MUST NOT be used as event authentication. Bitrix24 sends
the create event to the receiver; its additional auth data is not stored.

The separate inbound REST webhook is used by the worker ONLY to read/update
the exact Bitrix item and read field schema. GigaChat gets only a public title
if the corporate privacy flag and a domain allowlist both permit it, or
separately approved private evidence via the existing corporate verification
adapter. No raw CRM item, contact, phone or INN are sent to GigaChat in the
public-title path.

## Hosting

Use the paid Render Blueprint `render.yaml`, which specifies ONE Python
web-service instance with a dedicated persistent disk at
`/opt/render/project/src/storage`. The same service handles authenticated
callbacks and the event-only worker, so the disk is never shared across
separate Render instances.

Render's ephemeral free web filesystem is NOT suitable for this
exactly-once/fenced ledger.

Initial configuration is deliberately OFF:
- `SERVICEIT_EVENT_AUTOFILL_ENABLED=NO`
- `SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=NO`
- `SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED=NO`
- `AI_ENABLE=0`

Before activation, set the correct corporate secrets in the Render dashboard
(not in GitHub, issue comments or chat):
- `SERVICEIT_BITRIX_APPLICATION_TOKEN` — outbound Bitrix event token.
- `SERVICEIT_BITRIX_WEBHOOK` — approved inbound Bitrix REST URL.
- `GIGACHAT_CREDENTIALS` — only when approved for real model use.

Do not rotate these secrets during a live in-flight event without a cutover
plan and verification.

## One-time Bitrix24 subscription

With the corporate Bitrix24 administrator, create an **outgoing webhook**
for `onCrmDynamicItemAdd_1062` if that scoped event is offered by the
Bitrix24 UI, otherwise use `onCrmDynamicItemAdd` and trust only matching
entityTypeId=1062 in the receiver. The HTTPS receiver URL is:

`https://<your-render-service>.onrender.com/integrations/serviceit/bitrix24/new-card`

Configure the application_token matching the callback. A separate
`event.bind` path is possible only with an actual Bitrix24 app OAuth
context, not with the inbound REST webhook (which returns WRONG_AUTH_TYPE).

After service and token checks, change
`SERVICEIT_EVENT_AUTOFILL_ENABLED=YES_1062_21_EVENT`, deploy once and check
`/integrations/serviceit/bitrix24/worker-health`. Then create ONE new
approved canary in process 1062/category 21 and check exactly one write and
exact readback. Do not replay ambiguous writes.

## Optional public-title AI

Enable only after separate corporate approval of public tender titles going
to GigaChat:
- `SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=YES`
- `AI_ENABLE=1`
- `AI_PROVIDER=gigachat`
- `GIGACHAT_CREDENTIALS` set in Render secrets
- `SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED=YES`
- `SERVICEIT_PRELIMINARY_PUBLIC_HOSTS=b2b-center.ru,zakupki.gov.ru`
  (only the exact corporate-approved domains)

Only cards with `SIGNAL_KIND=tender-opportunity`, a HTTPS URL on an
approved hostname and safe title are sent. All other new cards get a
source-specific preliminary report without LLM or fake Score.

## Preconditions for true verified Score

For a full verified AI+Score update, the independent corporate approval
adapter still needs the exact item ID, before-image fingerprint, nonexpired
write authorization, privacy approval and all nine VERIFIED evidence checks:
company_registry, source, existing_customer, crm_dedup, stop_list,
legal_basis, contact, private_small_moscow, manager_criteria.

There is no automatic inference of those nine facts from card text or model
confidence. Without them the user gets useful HOLD/manager review, not a
fabricated Score or a permitted sales contact.

## Safety and testing

Event ID deduplication, exact 1062/21 identity, manager modifications,
model reservations, CRM update reservations, 3/7 historical exclusions,
single consumer, no external retry after ambiguous writes and readback
checks are mandatory.

Offline synthetic tests validate:
- signed event -> durable queue -> exact CRM read -> nine fields;
- duplicate event -> no duplicate update;
- CRM timeout -> UNCERTAIN, no blind replay;
- wrong token/type/category -> no write;
- only explicit corporate-approved cards go to verified worker;
- public-title model consent and host allowlist;
- malformed source and model output never become qualified leads;
- ephemeral disk rejected at startup.

No claim of LIVE operation is justified until Render is actually deployed,
the Bitrix outgoing event subscription is saved, credentials are configured,
and at least one genuine NEW card is processed with exact readback.
