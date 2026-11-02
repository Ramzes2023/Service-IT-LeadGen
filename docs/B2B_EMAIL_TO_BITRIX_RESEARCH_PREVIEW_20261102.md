# B2B-Center email → Bitrix24 1062/21: OFFLINE HOLD preview (2026-11-02)

## Proven evidence versus limits

Three new B2B-Center vendor emails received 2 Nov 2026 in Gmail together listed **41 procurement entries**. Original Gmail headers showed sender marketplace@b2b-center.ru and SPF/DKIM/DMARC pass. Buyer names in vendor mail are not buyer-INN/FNS proof. The message delivery date IS NOT the publication date. No real qualified private micro/small Moscow/MO buyer was independently confirmed. No raw private emails or tracked URLs are committed.

Original procedure examples:
- 4601288 Moscow Credit Bank: 1C implementation/support. Publication 18 Sep 2026 09:39 Moscow time; deadline 5 Nov 2026 12:00. Enterprise tender opportunity, not SME lead.
- 4660217 AO Tander: 1C data extraction to DBMS. Publication 28 Oct 2026 14:55 Moscow time; deadline 5 Nov 2026 15:00. Non-target geography/size.
- 4661430 AO Uralkhim: platform 1C ERP support licence. Additional stage published 29 Oct 2026 14:28 Moscow time; deadline 3 Nov 2026 11:00. This is not verified outsourced 1C maintenance.
- 4661617 AO Technical Inspection EES: direct 1C accounting/HR system maintenance under 223-FZ. Published 29 Oct 2026; deadline 16 Nov 2026 09:00. Separate public-procurement workstream; buyer INN 5407270810 from third-party listing pending official checking.

## Reuse existing Bitrix24, no new live records

Existing company test smart process entityTypeId=1062, categoryId=21, 16 existing user fields, six stages, synthetic cards 3 and 5, task 26163 and one verified synthetic GigaChat report readback. These facts DO NOT authorize production integration.

The module leadpilot/b2b_email_opportunity_bridge.py creates local, non-executable planned cards only. For each IT-relevant procurement it marks signal kind as tender-opportunity, records official canonical vendor URL (without query tracking), tender ID, organizer and per-tender subject, and writes explicit HOLD as status. It does not access Gmail, perform vendor HTML scraping, call FNS, write to CRM, contact customers or bid.

- Exact MIME duplicates skipped by SHA-256; different messages with same subject and tender URL retain separate version hashes.
- One proposal per unique B2B-Center tender ID, with all observed email revisions kept.
- Category matching applies to the subject of EACH individual procurement, never the entire mail. Building tenders are excluded from the IT opportunity queue.
- Publication date, verified deadline, legal buyer/INN/FNS, corporate contact and customer stop status remain unknown unless separately checked.

## Acceptance and safety rules

1. Obtain mailbox-owner approval for read-only Gmail machine integration and confirm provider permissions for automated use of delivered subscription messages. Existing ChatGPT Gmail access does not provide credentials to local Windows worker. No mass vendor web scraping without written permission; no tokens/passwords in Git or chat.
2. Independently test with locally authorised, NON-COMMITTED original EML and compare actual tender IDs/counts/fields. GitHub tests cover synthetic example email layouts only. Do not claim these tests prove live ingestion.
3. Verify original buyer versus organizer, publication/modified date, current open status, deadline including timezone, original buyer INN, FNS SME size and legal Moscow/MO region 77/50, duplicate/stop-list/existing corporate CRM client and permissible contact. A private qualified lead requires all of these and manager review. 44/223 and large-company tenders never auto-qualify.
4. Before any future REAL Bitrix24 writer, obtain separate manager approval, inspect live UF field semantics and RBAC, stage and existing automations, enable idempotent bounded write/readback with audit and rollback. This module provides only hold preview: NO BITRIX WRITES.

Run locally from repository root using authorised EML copies kept outside Git:

    python -m leadpilot.b2b_email_opportunity_bridge --output exports/b2b-review.json path/to/notice1.eml path/to/notice2.eml

Output must be a NEW JSON file under ignored exports. This is an OFFLINE research artifact, not a deployed Gmail connector or real CRM integration. Keep the PR in DRAFT until actual MIME acceptance and separate approvals.


## Follow-on guarded integration implementation, 02 Nov 2026

Implementation now includes:
- leadpilot/b2b_bitrix_readiness.py: bounded Bitrix24 READ ONLY metadata preflight. It validates the exact existing 1062/21 type, disabled automation and bizproc, all original 16 UF fields, 10 allowed review fields (including idempotency key), category 21 and NEW stage. Rejects unexpected required fields and unrecognised field types; no CRM item/lead/contact reads or writes.
- leadpilot/b2b_bitrix_hold_delivery.py: exactly-one manual HOLD writer protected by independent schema preflight, exact tender-ID duplicate lookup in review process, manual operator approval, durable exclusive local POST_UNCERTAIN fence before crm.item.add and exact crm.item.get readback; never automatically retries ambiguous writes. Always marks status tender-opportunity and NO CONTACT. This is NOT a lead writer and cannot launch as a scheduled job.
- leadpilot/b2b_bitrix_operator.py: a default OFFLINE EML parser/preview; actual opt-in requires --apply-one, --verified-original and an exact typed approval. One tender ID only, no mass import.
- scripts/RUN_SERVICEIT_B2B_TO_BITRIX_READONLY.ps1: secure hidden webhook prompt with four metadata-only REST reads; no CRM writes.
- scripts/RUN_SERVICEIT_B2B_ONE_HOLD_CARD.ps1: PowerShell 5.1 preview by default and two separate confirmations before one bounded HOLD write.

**GitHub CI is synthetic/mock-based, NOT evidence of direct portal connection.** The live corporate webhook only exists on the owner's Windows device and must never be sent via chat/Git. Real Gmail EMLs also must stay outside Git. No Bitrix24 writes have been executed in this PR.

### Minimum practical run from Windows (safe PREVIEW first)

Create an isolated Git worktree from the draft branch. Do not checkout over the user's current main working tree. Export the legitimate B2B-Center Gmail message from Gmail's 'Show original' / download-original action as .eml, kept outside Git. Run tests and read-only Bitrix metadata preflight:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\RUN_SERVICEIT_B2B_TO_BITRIX_READONLY.ps1

Then run one tender preview with an exact downloaded .eml path (replace the placeholder file name):

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\RUN_SERVICEIT_B2B_ONE_HOLD_CARD.ps1 -EmailFiles "C:\path\official-b2b-message.eml" -TenderId 4661617

No CRM operation takes place in either of these two default commands. Output from the second command must show the selected tender as tender-opportunity and HOLD. If exact EML parsing fails, do not use synthetic replacements and do not attempt CRM writes; first inspect the real MIME structure locally without uploading private raw mail.

Only once read-only preflight passes and the manager authorises creation of one *unqualified tender opportunity* (not sales lead), independently verify that original tender remains active and use the explicit -ApplyOneHold switch on the Windows operator. This mutation can trigger internal CRM events, so confirm disabled robots/bizproc and corporate permissions first. NEVER use -ApplyOneHold blindly or schedule it.

After one verified controlled write, any future recurring automatic Gmail→Bitrix ingestion needs a separate approved Gmail transport (OAuth or other permitted method), provider permission for automated use of subscription contents, and production review policy. ChatGPT's connected Gmail access does not grant Windows OAuth credentials.
