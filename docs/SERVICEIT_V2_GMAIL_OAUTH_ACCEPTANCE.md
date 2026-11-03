# Service IT V2 — Gmail OAuth desktop consent and 24/7 server acceptance

## Current scope
This runbook is for a separate independent Gmail API OAuth 2.0 grant; it DOES NOT reuse ChatGPT-connected Gmail permission. The worker may read only messages from B2B-Center (currently sender marketplace@b2b-center.ru), and never writes Gmail messages or automatically creates CRM records. Sender field/filter is not authentication; official message-origin policy must be evaluated independently before customer promotion.

Google official documentation: https://developers.google.com/identity/protocols/oauth2/native-app ; https://developers.google.com/workspace/gmail/api/auth/scopes ; https://developers.google.com/identity/protocols/oauth2 .

**Risk and compliance:** gmail.readonly is a restricted scope. Google OAuth verification and possibly security assessment apply according to app user type and deployment. An External / Testing app often issues seven-day refresh tokens; a successful local consent is NOT evidence of durable 24/7 production authorization. Read/app-data use must comply with Google policies and the source vendor's terms.

## A. On the owner's Windows computer — ONE-TIME user consent
1. Open Google Cloud console under the account that owns the new OAuth application. Create a project specifically for Service IT, enable Gmail API, configure Google Auth Platform / OAuth consent and select a Desktop app OAuth client. Download the Desktop-client JSON into a local private directory. Never upload the JSON into GitHub or this chat.
2. Check that Python 3.12, the repository's requirements-v2.txt packages and your own browser are available locally; use a clean clone or approved branch, not VALYQON. An owner-only path such as %LOCALAPPDATA%\ServiceIT\V2\Private must already exist. Keep OAuth client JSON separate from output.
3. Run this exact one-time command from the reviewed repository checkout (replace the local filenames, not the email address without checking):

```powershell
python -m leadpilot.serviceit_v2_oauth_local --client-json "$env:USERPROFILE\Downloads\client_secret_DESKTOP.json" --output "$env:LOCALAPPDATA\ServiceIT\V2\Private\serviceit-gmail-oauth.json" --expected-email "ramzesisayev444@gmail.com"
```

4. Google opens in a normal browser using localhost/PKCE and state verification. Select the exact approved Gmail mailbox, inspect the requested permission (ONLY View your Gmail, not modify or send), and approve if correct. The program independently calls the Google Gmail profile endpoint and rejects a different signed-in account. It saves the refresh token and OAuth client data only in a NEW local JSON file. It never prints tokens or writes the file to git.
5. On Windows the helper attempts to remove inherited NTFS permissions and grant the current Windows identity full control; it **fails closed** if the ACL command fails. The bundle is **not encrypted with DPAPI**; access protection is file ACL, so the PC disk and backups must be protected. On POSIX, the parent directory must be private and output uses mode 0600. Avoid cloud-synced or public folders.
6. If Google returns no refresh token or a permission other than the single readonly scope, helper aborts. Do not paste any Google token/secret into chat to debug.

## B. Dedicated approved Linux server — no user secrets in Docker env
1. Only after obtaining corporate hosting approval, securely transfer the local file directly to the server via an approved secret-manager/SSH channel. Never use GitHub Issues, a pull request, public URLs, email forwarding or shared artifacts for it.
2. Server administrator should place the file outside the repository, e.g. /opt/serviceit/secrets/gmail-oauth.json. Ensure the container worker can read it as UID 10001, and **no other normal users can** (Linux ownership UID 10001 / mode 0600). Do not try to reuse the VALYQON VPS without separate authorization.
3. In a private Docker --env-file, set SERVICEIT_V2_GMAIL_EXPECTED_EMAIL=ramzesisayev444@gmail.com, SERVICEIT_V2_OAUTH_SECRET_HOST_FILE=/opt/serviceit/secrets/gmail-oauth.json, a dedicated PostgreSQL password, and SERVICEIT_V2_GMAIL_AUTOPOLL_ENABLED=NO initially. Leave the old SERVICEIT_V2_GMAIL_CLIENT_ID/SECRET/REFRESH_TOKEN settings empty.
4. Check the configs without starting the live service:

```sh
docker compose --env-file /opt/serviceit/secrets/private.env -f deploy/serviceit-v2.compose.yml -f deploy/serviceit-v2.gmail-secret.override.yml --profile gmail-live config --quiet
```

5. Start ONLY database first. Then run a single private Gmail/DB acceptance by switching SERVICEIT_V2_GMAIL_AUTOPOLL_ENABLED=YES for approved canary operation, and using --once rather than automatic infinite loop. CRM writer still OFF; compare the new vendor Gmail message IDs and tender IDs with authorized originals before scheduling continuous ingestion.

```sh
docker compose --env-file /opt/serviceit/secrets/private.env -f deploy/serviceit-v2.compose.yml -f deploy/serviceit-v2.gmail-secret.override.yml up -d db
docker compose --env-file /opt/serviceit/secrets/private.env -f deploy/serviceit-v2.compose.yml -f deploy/serviceit-v2.gmail-secret.override.yml --profile gmail-live run --rm gmail-worker python -m leadpilot.serviceit_v2_worker --once
```

6. Run --once a SECOND time and verify zero duplicates. Only after observing a real new notification and a stable OAuth refresh grant enable continuous profile `--profile gmail-live up -d --build` and confirm source metrics, restarts and off-host backup. Read-only and HOLD-only remain in force. Google notification frequency on B2B-Center is approximately 3 hours according to saved subscription, so no new vendor message may arrive during a five-minute poll.

## C. Fail-closed controls
- No refresh token, wrong scope, incorrect Gmail account, wrong filesystem permissions or unable to refresh OAuth → no Gmail processing.
- No dedicated approved host or no authenticated vendor data processing policy → no production deployment.
- No Bitrix identity/source duplicate preflight, INN and original procurement evidence, manager authorization → no qualified lead promotion or unapproved CRM writes.
- Old ChatGPT automation and Issue #66 route MUST NOT be switched off until independent end-to-end mail→PostgreSQL and guarded CRM canaries are proven.

## Acceptance evidence the owner should see
1. Gmail profile email equals approved mailbox and OAuth refresh works on independent server.
2. Fresh vendor notification is persisted once and each tender is separated with canonical ID and source URL; errors and all revisions are durable.
3. Same email never creates duplicate opportunity. Same procurement across messages retains versions.
4. Crash/restart and one controlled access-token expiry preserve state, without CRM writes or loss.
5. Separate, approved real HOLD canary safely deduplicates historical card #7 and confirms an authorized new CRM readback, with records blocked on uncertainty.
