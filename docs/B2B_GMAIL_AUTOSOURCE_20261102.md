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
