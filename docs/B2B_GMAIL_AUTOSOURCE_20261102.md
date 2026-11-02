# Service IT B2B-Center Gmail recurring source — 2026-11-02

## Lead-first objective
The business goal is a verified private micro/small company in Moscow/Moscow Oblast with real IT/1C buying demand, corporate contact and verified INN/FNS SME record. Tender notices are separate review opportunities. Never call an unverified supplier procurement a QUALIFIED_LEAD.

## Delivered source transport
The new source is in leadpilot/b2b_gmail_incremental_source.py. It connects to Gmail over TLS IMAP port 993, selects INBOX read-only and fetches original message bytes with BODY.PEEK[]. A 14-day lookback and 60-message poll cap are in place. MIME SHA-256 identifies unique received emails, so distinct notification revisions remain distinct even when subjects/URLs are identical; tender IDs are deduplicated in a local SQLite index. URL tracking parameters are stripped. No original MIME, app password, tokens or private recipients are written to GitHub. Local research queue: %LOCALAPPDATA%/ServiceIT/B2B-Gmail-Source/b2b-review-queue.json.

P1 research: only a possible private legal form (ООО) AND 1C subject. It is still TENDER_OPPORTUNITY_HOLD, unverified for buyer INN, FNS size, geography, original source date, status, contact, and duplicate/STOP. Other matching tender notices are P2. Confirmed qualified leads: 0 until official provenance gates.

No CRM item, contact, task, outbound email, supplier response, or live Bitrix write happens in this transport. The owner previously verified actual Bitrix 1062/21 metadata and processed an actual Gmail .eml, but no authorized scheduled Gmail auth and no production CRM source writer are active yet. Do not claim full lead-to-CRM end-to-end operation.

## Owner activation on Windows PowerShell 5.1 (isolated Git worktree)
Source operator is scripts/RUN_SERVICEIT_B2B_GMAIL_SOURCE.ps1. It has separate explicit -Setup, -Poll, -Status and -InstallSchedule modes.

1. Run with -Setup. Only here is Gmail app password entered via hidden SecureString prompt. Credential is encrypted for the current Windows user with Export-Clixml / DPAPI and stored outside Git. Do not share the secret with the assistant or put it in history.
2. Run -Poll from the same Windows user. This performs the **live read-only IMAP** test, populates the local queue and prints safe summary, but cannot write any CRM record.
3. Only after a proven successful -Poll, run -InstallSchedule to create an hourly Windows Scheduled Task with a new name. It does not overwrite an existing task. Same Windows account is required to decrypt DPAPI credential and the PC must be powered on. It only refreshes research queue.
4. Read queue locally. If Gmail app password is not available (Workspace/Advanced Protection), DO NOT bypass account security; use a supported Gmail OAuth 2.0 integration instead.

Google recommends OAuth over app passwords; app passwords require 2-Step Verification and are discouraged:
https://support.google.com/accounts/answer/185833
https://support.google.com/mail/answer/7126229
https://developers.google.com/workspace/gmail/imap/imap-smtp

## Final production gates
Before automated sales lead creation: independent current original procedure status/publication/deadline, the buyer's valid INN and FNS MSP micro/small official registry membership, private buyer Moscow/MO, 1C/IT service requirement, lawful corporate contact, global CRM duplicate/client and stoplist check, written permission for processing subscription data and manager review. No source API credentials from ChatGPT's Gmail connector can be used in the Windows worker. Any procurement not passing these tests must remain HOLD, and public 44/223-FZ or large-company tenders must not count as SME leads.

The earlier one-shot Bitrix HOLD writer in this PR requires independent original verification, user approval and an exact CRM duplicate/readback gate. It is not scheduled and not invoked by the Gmail poller. Existing DEMO card #3, badge incident ledgers, GigaChat and Bitrix layout are not touched.

## Validation
Use CI synthetic IMAP mock tests, PowerShell 5.1 AST/ASCII checks, local exact user-provided email replay and owner-approved live Gmail -Poll followed by another -Poll to confirm idempotency. GitHub CI success alone is not live IMAP authentication or live Bitrix delivery. Keep PR DRAFT pending host acceptance and authorizations.


## November 2: Optional always-on route WITHOUT Gmail IMAP app password

The owner's ChatGPT Gmail connector already has permission to READ incoming official B2B-Center notices. This permission cannot be exported to a Windows service or GitHub Actions. Instead, the already-enabled once-hourly ChatGPT automation `6ae61cca18b481918b20bbf0f3bc5eaf` now watches new Gmail notices and for each NEW eligible procedure can create a **strictly typed, sanitized** comment on private [Issue #66](https://github.com/Ramzes2023/Service-IT-LeadGen/issues/66). This stage does not need a local Gmail app password and does not need the owner's PC to stay on. The source may be detected up to about one hour after arrival, not necessarily instantly; the newly updated action's future execution still needs live acceptance.

`leadpilot/b2b_github_to_bitrix.py` parses only comments of the following exact form (two lines, no markdown):
```text
SERVICEIT_B2B_NOTICE_V1
{"schema":"serviceit-b2b-notice-v1","source":"B2B-Center","email_message_id":"1a19b7630301bafb","tender_id":"4661617","source_url":"https://www.b2b-center.ru/market/podderzhka-1s/tender-4661617/","subject":"Поддержка 1С:ЗУП","organizer":"ООО «Тест»","buyer_inn":null,"fns_msp":null,"region":null,"original_publication_verified":false,"contact_approved":false,"qualified_lead":false}
```
The above is a SYNTHETIC EXAMPLE ONLY, not a historical procurement description. Never post fixture notice into production issue as if real. The actual automation must use canonical vendor URLs and the original **real Gmail message ID** for each notice. It must omit unsupported category matches.

New workflow `.github/workflows/b2b-gmail-to-bitrix-hold.yml` listens for `issue_comment: created` on issue #66, but **only when the workflow is merged onto the default branch**, and only for comments authored by owner Ramzes2023. It dry-runs exact source ID, official URL (no tracking), strict IT service subject and false/null verification flags. Every such message MUST be `TENDER_OPPORTUNITY_HOLD`, NEVER a qualified lead. A private LLC name (ООО) alone is not proof of official SME registry status.

### Required ONCE: corporate authorization (user/admin only)

This ChatGPT/GitHub connector CANNOT write repository Actions secrets or read the webhook that the owner entered into Windows PowerShell. Do not paste the webhook into chat, an issue, a commit or a public URL. The repository owner or Bitrix24 administrator must approve this continuous automation and perform:

1. Grant the corporate Bitrix webhook only appropriate CRM access to existing smart-process **entityTypeId 1062, category 21** with permission to read/list/add only where possible. Existing demo cards and AI fields should not be modified. Use a dedicated Bitrix24 integration identity, not a personal broad-permission webhook when possible.
2. In the private GitHub repo [Actions secrets settings](https://github.com/Ramzes2023/Service-IT-LeadGen/settings/secrets/actions), add exactly one **Repository secret**: `SERVICEIT_BITRIX_WEBHOOK`, value = complete approved corporate HTTPS REST webhook, entered **directly into GitHub UI**. Do not share with the assistant.
3. Only after code is merged to main, read-only acceptance, manager approval, and a controlled real HOLD item test, in [Actions variables settings](https://github.com/Ramzes2023/Service-IT-LeadGen/settings/variables/actions), create repository **Variable** `SERVICEIT_B2B_CRM_AUTO_ENABLED` with value `YES`. If absent or anything other than exact YES, GitHub will NOT attempt a CRM write. **Do not enable** until manager authorized source-to-CRM automation.
4. After activation, each new eligible official notice becomes at most one HOLD card in Bitrix smart-process 1062/21; a unique bot-authored ledger reservation is written on Issue #66 BEFORE any potential `crm.item.add`. Repo-wide concurrency prevents overlap. A failed write/readback keeps the lock; no automatic retries or mass fills.
5. Inspect Actions logs for the controlled first card's `ONE_TENDER_HOLD_VERIFIED`, then use corporate CRM readback and Kanban UI to confirm exact fields. If it fails, leave `SERVICEIT_B2B_CRM_AUTO_ENABLED` unset/disabled and investigate before restarting. Do not delete bot ledger comments or change source numbers to work around the lock.

**Important:** This is the implementation for automatic **tender-opportunity HOLD** staging, not actual `QUALIFIED_LEAD` generation. No INN/FNS/region/contact verification exists in the source email, and there is still **NO** independently approved automatic lead promotion path. For a true lead, separate trusted official FNS SME, buyer INN/region, corporate contact and global CRM duplicate/STOP checks are necessary, with documented human policy approval. No dummy lead creation, business outreach, task creation, bids or unsupported FNS claims.

Current live outcome remains `CRM_WRITES=0` until the corporate secret, explicit enabled variable, a merged main workflow, and actual host acceptance are confirmed.
