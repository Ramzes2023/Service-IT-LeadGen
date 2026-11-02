# Service IT: independent Gmail → Apps Script → HMAC HTTPS → PostgreSQL

**Status: IMPLEMENTED CODE, NOT DEPLOYED.** Google Apps Script runs under the Gmail account owner, automatically via Google time trigger; **no ChatGPT, TinyFish, GitHub issue queue or GigaChat is involved at runtime**. The user-connected ChatGPT Gmail connector is NOT passed to the server. No Bitrix write route is automatically activated.

## Architecture and trust boundaries
1. B2B-Center sends its notices to Gmail according to the subscription (about every 3 hours).
2. Standalone Apps Script `integrations/apps_script/Code.gs` calls Advanced Gmail v1 under the user's Google consent, verifies the mailbox identity, searches only B2B-Center sender in the last 30 days (excluding spam and trash), obtains bounded original raw MIME, signs a JSON envelope with HMAC-SHA256 + Unix timestamp and POSTs over HTTPS.
3. At the approved public HTTPS domain, reverse proxy forwards `/api/v2/gmail/ingest` to the **localhost-only** Docker port 127.0.0.1:18744. No redirects are followed by Apps Script.
4. `leadpilot/serviceit_v2_apps_receiver.py` requires an approved mailbox, exact schema, freshness ±300 seconds, constant-time HMAC comparison with an owner-private key, safe source MIME and 256 KiB budget; persists via existing `serviceit_v2_store.ingest_message` transaction. Server responds with `INGESTED` / `ALREADY_INGESTED` ONLY after DB commit.
5. The same Gmail ID dedups in PostgreSQL, while separate B2B tender IDs dedup across notifications. An Apps Script 6h CacheService cache is only an optimization after server ACK, not a substitute for the database.
6. No corporate Bitrix24 CRM operation is performed by this ingress. Existing v2 guarded outbox and research-only buyer checks remain separate and off. Do not claim a tender is a verified SME buyer based on email.

## User account setup (one-time, free within Apps Script quotas)
1. Open the existing standalone Apps Script project **Service IT — Gmail Tender Automation**, created in Gmail account `ramzesisayev444@gmail.com`. Advanced service **Gmail API (v1)** must already be enabled (on screenshot it is present). Google documents free default Cloud projects for advanced services; do not activate Google Cloud free-trial prepayment just for this script.
2. Replace the entire editor `Код.gs` with GitHub file `integrations/apps_script/Code.gs`. The previous `checkB2BCenter` sample is replaced by the production-ready code.
3. In project settings enable **Show appsscript.json manifest**, then replace/update the manifest so it has exact scopes and Gmail API v1 shown in `integrations/apps_script/appsscript.json`. Keep `runtimeVersion: V8`. Explicit scopes: gmail.readonly + script.external_request + script.scriptapp (script triggers). Grant permissions in standard Google dialog **only after reading requested scopes**.
4. Select function `checkB2BCenter` and run manually. It reports only the number of recent B2B emails, `EXTERNAL_TRANSMISSION=0`, `CRM_WRITES=0`. A Google consent dialog may appear. DO NOT run `pollAndForwardB2B` or install trigger yet; without a deployed receiver it will fail closed.

## Isolated independent server — requires the owner/admin to approve the server
1. Obtain a trusted HTTPS hostname with valid TLS, public reachability to a dedicated reverse proxy, and a private Docker host; can cost money depending on hosting. Never assume VALYQON, another production VPS or Bitrix's own servers are approved.
2. Generate a random high-entropy ASCII key at the approved server, in an owner-private **no-newline** file `/opt/serviceit/secrets/apps-hmac` (readable by container UID 10001 only, mode 0600). Do **not** put this key in source code, GitHub, chat, issue comments or email. Share with the Apps Script owner over a separately approved secure channel to enter into **Script Properties**, not into `Код.gs`.
3. Privately configure `SERVICEIT_V2_PG_PASSWORD`, `SERVICEIT_V2_APPS_HMAC_HOST_FILE=/opt/serviceit/secrets/apps-hmac`, `SERVICEIT_V2_APPS_RECEIVER_ENABLED=NO` initially, `SERVICEIT_V2_GMAIL_EXPECTED_EMAIL=ramzesisayev444@gmail.com`. Use a private file not committed to GitHub.
4. Preflight with both compose files:

```sh
docker compose --env-file /opt/serviceit/secrets/private.env -f deploy/serviceit-v2.compose.yml -f deploy/serviceit-v2.apps-ingress.compose.yml --profile apps-live config --quiet
```

5. Start isolated PostgreSQL only. After independent HMAC secret + hostname + TLS + legal/data-processing approval, explicitly enable `SERVICEIT_V2_APPS_RECEIVER_ENABLED=YES`, start `--profile apps-live up -d --build`. The receiver is bound only to localhost; configure the proxy for HTTPS and `POST /api/v2/gmail/ingest` only. Do not expose `18744` to the Internet.
6. In Apps Script **Project Settings → Script properties**, set `SERVICEIT_RECEIVER_URL` to `https://YOUR-APPROVED-DOMAIN/api/v2/gmail/ingest`, `SERVICEIT_HMAC_SECRET` to the matching secret (private), and **initially** `SERVICEIT_SEND_ENABLED=NO`. Never enter a hypothetical or unverified URL.
7. Under real owner supervision: turn `SERVICEIT_SEND_ENABLED=YES` only for a controlled canary; manually run `pollAndForwardB2B` once and verify new Gmail source IDs and exact tender counts in PostgreSQL (not CRM). `serviceITStatus()` shows configuration and last run without revealing the secret. Then run it again; the DB must show no new duplicate messages.
8. Once a live new B2B notification, DB dedup, worker restart and rate-limited 5-minute run are verified, call `installPollingTrigger` ONCE. It checks authorized Gmail and verified endpoint + local enable flag. It schedules `pollAndForwardB2B` every 5 minutes under the owner's Google account, entirely independent of ChatGPT.
9. For emergency pause, run `stopPollingTrigger` — deletes only this handler's triggers and switches transmission off. The CRM is not changed.

## Security, limitations and acceptance
- Script properties are accessible to people who can edit the Apps Script project. Keep the project PRIVATE. Only transfer accepted business email content to an approved processor; Gmail `gmail.readonly` can expose other emails if the code were modified, so audit code and restricted sharing.
- HMAC authenticates the Apps Script sender, NOT the original vendor. Original `From:` field alone cannot establish SPF/DKIM authentication; do not promote a lead automatically without corroborated vendor/buyer evidence.
- Apps Script Gmail query is restricted by sender, but a sender filter is not proof of provenance. The server strictly checks the MIME sender and refuses nonmatching sources.
- A failed HTTP request is NOT acknowledged. The next poll will retry and the server's unique Gmail IDs prevent duplicate rows. Apps Script does not store or display email bodies, passwords or tokens in logs.
- The 30-day Gmail query uses up to 10 pages/1000 message IDs; excess backlog causes an explicit failure, not silent skipping. Long outages beyond the 30-day search window require an approved manual backfill. Apps Script runtime and daily quotas apply.
- 6-hour Apps Script CacheService speeds up repeated successful polls; server DB is authoritative, not cache. Any failure after DB commit but before ACK is harmless under server-side deduplication.
- The HMAC key must stay private; requiring `SERVICEIT_SEND_ENABLED=YES` is an explicit operational gate, not a permission system.
- No production deployment, TLS endpoint, real Gmail trigger, HMAC secret or real end-to-end test has occurred yet. Last GitHub Actions baseline runs were failing with no job steps/logs; code safety MUST be validated separately before production.

Official docs: https://developers.google.com/apps-script/guides/services/advanced ; https://developers.google.com/apps-script/guides/triggers/installable ; https://developers.google.com/apps-script/concepts/scopes ; https://developers.google.com/apps-script/reference/utilities/utilities ; https://developers.google.com/apps-script/reference/url-fetch/url-fetch-app
