# Service IT V2: Render isolation / no-production preflight

Status 2026-11-02: **FREE isolated Render preflight DEPLOYED, mail ingestion still OFF**. DRAFT PR. This is independent of VALYQON/TenderLens, existing Bitrix smart-process 1062/21, GitHub Issue #66, and the already installed Google Apps Script. Do not alter a production project.

## Verified live staging checkpoint — 2 November 2026

Owner explicitly approved the **existing** Render workspace `My Workspace`
(`tea-dbg89u1srm7s73cqe7r0`) and only FREE test resources.

- Web service `srv-dbkf51qfngtc73f43qmg`,
  [Render dashboard](https://dashboard.render.com/web/srv-dbkf51qfngtc73f43qmg):
  `serviceit-v2-gmail-ingress-canary`, Frankfurt, plan FREE, Docker deploy
  `dep-dbkf522fngtc73f43vh0` LIVE, auto-deploy OFF.
- PostgreSQL `dpg-dbkf4uu7bikc73c5ue90-a`,
  [Render dashboard](https://dashboard.render.com/d/dpg-dbkf4uu7bikc73c5ue90-a):
  `serviceit-v2-gmail-canary-db`, Frankfurt, Postgres 16, plan FREE,
  AVAILABLE; **expires 2026-12-02T20:28:43Z**.
- Public `https://serviceit-v2-gmail-ingress-canary.onrender.com/health`:
  `status=SERVICEIT_V2_APPS_RECEIVER_ALIVE`,
  `source_enabled=false`, `crm_writes=0`.
- Synthetic unsigned POST `/api/v2/gmail/ingest` returns HTTP 503
  (`INGRESS_UNAVAILABLE`) with source OFF. Live HTTPS safety
  [GitHub Actions 41520280149](https://github.com/Ramzes2023/Service-IT-LeadGen/actions/runs/41520280149)
  passed. Also live tests of malformed content type, oversize payload and
  disabled OpenAPI docs were added.
- Separate [Render Blueprint proposal](../deploy/serviceit-v2-render-blueprint.safe.yaml)
  is **NOT APPLIED**. This is *documentation only* until a safe existing-resource
  adoption process is available; never create a second Blueprint blindly.
- The connected Render Postgres MCP read-only query tool could **not** connect
  because PostgreSQL IP allowlist is empty. Do not make it public.
  The Render connector lacks a `fromDatabase` env-var action or Blueprint
  sync. Therefore the actual Render app **is not yet connected** to its DB.
  There is **NO signed HMAC-to-Render-DB test**, only disposable CI Postgres
  tests. Do not imply otherwise.

### Important: avoid Blueprint duplication

Official Render docs warn that a **new** Blueprint that matches existing names
can create *suffixed duplicate resources* rather than adopting the existing
ones. Existing-resource adoption should be performed through the correct
already-managed Blueprint flow or Render dashboard's **Generate Blueprint**
from existing resources with review; do not simply create a new Blueprint from
the YAML and click Deploy. References:
[Blueprint management](https://render.com/docs/infrastructure-as-code),
[Blueprint specification](https://render.com/docs/blueprint-spec).
Auto-sync must stay OFF. Do not risk extra resource allocation or billing.

## What this branch implements

- `Dockerfile.serviceit-v2-render`: dedicated non-root Python 3.12 image for **only** the FastAPI signed Gmail receiver, not CRM writer.
- `leadpilot/serviceit_v2_render_entrypoint.py`: listens on Render-defined `PORT`; safe default is `SERVICEIT_V2_APPS_RECEIVER_ENABLED=NO`. With this default the public `/health` may return an OFF status but **POST ingestion always fails closed**.
- When and only when explicitly switched to YES with correct mailbox, a separately selected dedicated DB URL, and an owner-generated HMAC secret, the launcher creates a private chmod 0600 runtime file. It removes the extra base64 environment copy in-process. `serviceit_v2_apps_receiver.py` independently verifies file permissions and authenticates every request.
- `tests/test_serviceit_v2_render_entrypoint.py`: no Google, no external network and no CRM mutation; validates default-off, wrong mailbox, invalid key and key-file permissions.
- CI builds both isolated images, and GitHub Actions has passed independently. Always cite the exact run for a specific result; do not confuse disposable CI with live Render PostgreSQL.

## What is strictly not done

Render FREE web service + PostgreSQL **were created**, and TLS fail-closed canary passed. **No Render DB connection to app yet**, no real mailbox HMAC key; no Apps Script script-properties update; no Gmail trigger; no Gmail-to-DB live transfer; no Bitrix writes, lead qualification or notification. Existing Google source is already installed/read back according to owner PowerShell V7 proof, but `checkB2BCenter()` execution in Google remains unverified.

## Owner and administrator gates

1. **COMPLETED:** owner confirmed `My Workspace`; only isolated FREE resources were created.
2. Select *test* vs *24/7 production* runtime and cost. Render's own [free-plan docs](https://render.com/docs/free) say free web services sleep after 15 minutes of inactivity; free Postgres expires after 30 days, and are not appropriate for production. Free plans are for disposable preflight only. Never incur any charge without owner consent.
3. The user must separately approve lawful sharing of actual commercial Gmail notices with Render (a third-party processor), retention, corporate policies, and whether B2B-Center emails can be processed. Initial canaries are **synthetic** only.
4. **PARTIAL:** dedicated FREE DB and scoped Docker web service exist, source OFF and auto-deploy OFF. Link the database's internal connection URL through an authorized Render-managed environment reference `fromDatabase`, not literal secrets in GitHub, chat or logs. Do not create another Blueprint or paid resource. Do not enable source forwarding until all gates pass.
5. Owner securely creates a random ASCII HMAC key (at least 32 characters, no spaces/newlines); stores its base64 encoding as Render secret `SERVICEIT_V2_APPS_HMAC_SECRET_B64` and the same raw key in the **private** existing Google project's Script Properties as `SERVICEIT_HMAC_SECRET`. Never post the key here or commit it. The Google receiver URL must use exact `https://<approved-host>/api/v2/gmail/ingest`. The receiver rejects HTTP and redirects.
6. **Synthetic HMAC canary first:** set YES *on the isolated server only*; send a synthetic authorized test envelope with a valid HMAC timestamp and verify response + Postgres row/dedup. Test invalid signature and replay age. No CRM write.
7. Owner explicitly runs existing Google `checkB2BCenter` (read-only consent) and validates expected markers, or uses a separately authorized Google execution mechanism. This is not equivalent to successful `clasp push`.
8. Under approved data-processing terms, manually run `pollAndForwardB2B` on a new genuine email and verify only that email's ID/derived opportunity stored with dedup. Only after rate/retry acceptance turn on the 5-min `installPollingTrigger`. Do not mark QUALIFIED_LEAD from an email. CRM paths remain gated in PR #84 and Issue #83.

## Production-safety note

Do not deploy a free DB then say this pipeline is durable 24/7; a production-approved paid or otherwise durable DB requires an explicit decision. Render's client workspace ID must be confirmed before creating anything. Ingestion remains read-only to Gmail and write-only to the isolated *research database*; there are no Bitrix24 CRM writes in this web service.

Rollback: disable Apps Script `SERVICEIT_SEND_ENABLED`, call `stopPollingTrigger()` if enabled, switch Render `SERVICEIT_V2_APPS_RECEIVER_ENABLED=NO`, preserve audit tables; **do not erase** the old Google backup or Bitrix cards.
