# Service IT V2: Render isolation / no-production preflight

Status 2026-11-02: **CODE READY TO REVIEW, NOT DEPLOYED**. DRAFT PR. This is independent of VALYQON/TenderLens, existing Bitrix smart-process 1062/21, GitHub Issue #66, and the already installed Google Apps Script. Do not alter a production project.

## What this branch implements

- `Dockerfile.serviceit-v2-render`: dedicated non-root Python 3.12 image for **only** the FastAPI signed Gmail receiver, not CRM writer.
- `leadpilot/serviceit_v2_render_entrypoint.py`: listens on Render-defined `PORT`; safe default is `SERVICEIT_V2_APPS_RECEIVER_ENABLED=NO`. With this default the public `/health` may return an OFF status but **POST ingestion always fails closed**.
- When and only when explicitly switched to YES with correct mailbox, a separately selected dedicated DB URL, and an owner-generated HMAC secret, the launcher creates a private chmod 0600 runtime file. It removes the extra base64 environment copy in-process. `serviceit_v2_apps_receiver.py` independently verifies file permissions and authenticates every request.
- `tests/test_serviceit_v2_render_entrypoint.py`: no Google, no external network and no CRM mutation; validates default-off, wrong mailbox, invalid key and key-file permissions.
- CI builds both isolated images, but existing GitHub Actions may still fail before a runner even starts; **do not claim CI passes from code presence**.

## What is strictly not done

No Render service/PostgreSQL created; no workspace selected by owner; no public URL, TLS canary or real mailbox HMAC key; no Apps Script script-properties update; no Gmail trigger; no Gmail-to-DB live transfer; no Bitrix writes, lead qualification or notification. Existing Google source is already installed/read back according to owner PowerShell V7 proof, but `checkB2BCenter()` execution in Google remains unverified.

## Owner and administrator gates

1. **Select the correct Render workspace explicitly**. Render connector prohibits selecting one by guess. Only create resources in the owner's specifically confirmed workspace, not a default.
2. Select *test* vs *24/7 production* runtime and cost. Render's own [free-plan docs](https://render.com/docs/free) say free web services sleep after 15 minutes of inactivity; free Postgres expires after 30 days, and are not appropriate for production. Free plans are for disposable preflight only. Never incur any charge without owner consent.
3. The user must separately approve lawful sharing of actual commercial Gmail notices with Render (a third-party processor), retention, corporate policies, and whether B2B-Center emails can be processed. Initial canaries are **synthetic** only.
4. Provision a dedicated DB, confirm its private URL stored as Render secret `DATABASE_URL` (never in GitHub, logs or chat), and create a scoped isolated web service on the branch with **Docker runtime**, `Dockerfile.serviceit-v2-render`, correct `PORT`, `SERVICEIT_V2_APPS_RECEIVER_ENABLED=NO`, `SERVICEIT_V2_GMAIL_EXPECTED_EMAIL=ramzesisayev444@gmail.com`. Do not enable source forwarding or paid resources automatically. Keep auto-deploy OFF while credentials are under review.
5. Owner securely creates a random ASCII HMAC key (at least 32 characters, no spaces/newlines); stores its base64 encoding as Render secret `SERVICEIT_V2_APPS_HMAC_SECRET_B64` and the same raw key in the **private** existing Google project's Script Properties as `SERVICEIT_HMAC_SECRET`. Never post the key here or commit it. The Google receiver URL must use exact `https://<approved-host>/api/v2/gmail/ingest`. The receiver rejects HTTP and redirects.
6. **Synthetic HMAC canary first:** set YES *on the isolated server only*; send a synthetic authorized test envelope with a valid HMAC timestamp and verify response + Postgres row/dedup. Test invalid signature and replay age. No CRM write.
7. Owner explicitly runs existing Google `checkB2BCenter` (read-only consent) and validates expected markers, or uses a separately authorized Google execution mechanism. This is not equivalent to successful `clasp push`.
8. Under approved data-processing terms, manually run `pollAndForwardB2B` on a new genuine email and verify only that email's ID/derived opportunity stored with dedup. Only after rate/retry acceptance turn on the 5-min `installPollingTrigger`. Do not mark QUALIFIED_LEAD from an email. CRM paths remain gated in PR #84 and Issue #83.

## Production-safety note

Do not deploy a free DB then say this pipeline is durable 24/7; a production-approved paid or otherwise durable DB requires an explicit decision. Render's client workspace ID must be confirmed before creating anything. Ingestion remains read-only to Gmail and write-only to the isolated *research database*; there are no Bitrix24 CRM writes in this web service.

Rollback: disable Apps Script `SERVICEIT_SEND_ENABLED`, call `stopPollingTrigger()` if enabled, switch Render `SERVICEIT_V2_APPS_RECEIVER_ENABLED=NO`, preserve audit tables; **do not erase** the old Google backup or Bitrix cards.
