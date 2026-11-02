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
