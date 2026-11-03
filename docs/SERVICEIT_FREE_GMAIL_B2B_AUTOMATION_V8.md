# Service IT — free B2B-Center Gmail automation (V8)

## Purpose and scope

This is the free, laptop-independent intake path for new B2B-Center tender emails:

1. Google Apps Script polls the owner's Gmail mailbox every five minutes.
2. It parses the B2B-Center HTML/plain notification and keeps only IT-relevant tender notices.
3. It sends a minimal, HMAC-signed repository_dispatch to this public GitHub repository.
4. A free GitHub Actions runner validates the signature and calls the existing guarded B2B HOLD writer.
5. The writer performs its existing Bitrix schema and exact-duplicate checks, reserves the source in the GitHub ledger, optionally asks GigaChat to analyze the tender title only, creates one category 21 HOLD card, and exact-reads it back.
6. The encrypted direct-ID receipt stores the internal Bitrix item ID for later exact-ID work.

No Render, paid VM, local Windows scheduler, or ChatGPT polling is required for this path.

## Data minimization

Apps Script sends only:
- tender ID;
- tender subject;
- organizer label from the B2B-Center notice;
- canonical public B2B-Center tender URL;
- Gmail message ID for deduplication;
- fixed schema/version fields.

It does **not** send the raw email, MIME/HTML body, attachments, contact names, personal email addresses, phone numbers, buyer INN, or FNS data. GigaChat receives only the tender subject and only when the owner separately approves it in the installer.

## Safety rules

- First installation sets a cutover timestamp. Existing mailbox messages are not backfilled.
- Existing tender IDs 4665948 (already present in CRM) and 4661617 (previously deleted) are fenced and will not be recreated.
- Apps Script reserves each tender ID before dispatch. An ambiguous GitHub dispatch is never blindly repeated.
- The GitHub action requires a 15-minute HMAC signature, exact repository/event scope, and the existing B2B writer's validation.
- The B2B writer creates HOLD only. It does not set a qualified-lead status, score, contact, call, task, or CRM stage transition.
- The AI result is explicitly an unverified hypothesis until the buyer, INN/FNS, source, deduplication, stop-list, legal basis, contact and manager criteria are independently verified.
- The existing manager fields are written with the HOLD card. Shared form layout changes are separate and require explicit Bitrix administrator approval plus encrypted backup/readback.

## One-time activation

Run scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V8.ps1 on the owner's Windows account. It:
- bootstraps Node.js LTS through winget if Node/npm/clasp prerequisites are missing;
- reads the dedicated ServiceIT Bitrix/GigaChat DPAPI files;
- uploads only the needed GitHub Actions secrets via stdin;
- generates/retains local DPAPI HMAC and Fernet keys without printing them;
- verifies the existing Google Apps Script account and unique project;
- backs up the current Apps Script project;
- pushes the V8 source and a private secrets.gs;
- pulls it back and compares SHA-256 hashes;
- enables the GitHub workflow only after successful readback.

The installer asks separately for approval to store the GitHub CLI token in the owner-only Apps Script project, send minimal tender metadata to GitHub, send only the tender title to GigaChat, and (optionally) modify the shared Bitrix manager layout. The Apps Script project must remain owner-only because its private secrets.gs contains the GitHub dispatch token and HMAC key.

After the installer opens Apps Script, select installPollingTrigger() and click Run once. Authorize Gmail read-only, external HTTPS and trigger creation. This installs one five-minute trigger; the laptop may then be turned off.

## What is and is not complete

This pipeline automates new B2B-Center email notices into HOLD cards and the manager-review fields. It does not turn an email into a verified buyer or qualified lead. Until the nine independent evidence gates and a trusted evidence adapter exist, score fields stay empty and the manager receives a verification action.

The old scheduled Bitrix crm.item.list poller is retained only for manual forensic use and is no longer scheduled. Legacy issue-comment ingestion is not enabled by this installer.
