# Commercial source inbox: B2B-Center, SUPPMAN, ONLINECONTRACT

**Status as of 2026-10-31:** Research-only email notification intake implemented in draft PR. **Zero connected live feeds and zero qualified real SME leads.** No CRM changes.

## Why no direct crawler
- B2B-Center's official 24 Feb 2026 regulations, section 5.4, prohibit automated scraping/collection from the platform without **written operator consent**. https://www.b2b-center.ru/help/Регламент_Системы_B2B-Center/
- The platform does officially provide saved-search notices by email for registered users: https://www.b2b-center.ru/members/guide.html
- SUPPMAN has publicly visible direct lots and its EULA includes notifications configured in a registered user's personal cabinet. Terms: https://suppman.ru/about/agreement/ ; https://suppman.ru/about/terms-conditions/
- ONLINECONTRACT has public procurement search and a separate registered supplier environment, but a public supplier feed API has not been verified. https://onlinecontract.ru/
- The 2026 B2B-Center public search also contains mirrored EIS/44-FZ/223-FZ notices. Original private B2B-Center commercial items must be separated from third-party mirrors before counting any lead.

**Requests for official permission/REST, RSS, CSV and email options were sent to the official operator contacts on 31 Oct 2026**: B2B-Center info@b2b-center.ru and ONLINECONTRACT info@onlc.ru. That correspondence is not proof of authorization.

## Current feature
\`leadpilot.procurement_notice_inbox\` accepts a **local downloaded .eml** from a vendor-generated subscribed email; nothing is downloaded from the vendor website, Gmail, POP3 or IMAP. It extracts only a candidate official deep link and service-category matches.

Example, when legitimate source notices eventually arrive:

\`\`\`powershell
.\.venv\Scripts\python.exe -m leadpilot.procurement_notice_inbox --output exports\source-mail-audit-001.json exports\notice1.eml
\`\`\`

The report remains in \`exports/\` (Git ignored). No links without a recognized first-party hostname and source-specific notice path are accepted. Sender domains are **only hints**: an .eml From header can be spoofed; the importer **does not validate SPF/DKIM/DMARC or original website status**. Inbound mail must come through an operator-approved subscribed channel and be verified independently before it can count as an actual procurement.

The importer **does not**:
- poll a vendor website, invent API endpoints or bypass login;
- use a recipient email as a qualified customer;
- infer buyer INN, FNS SME size, buyer legal region, actual procurement deadline or open status from subject lines;
- treat historical notice or EIS mirror as active private buyer;
- create CRM leads or email the customer.

The fields include \`qualified_real_leads: 0\` regardless of email content. This is intentional until an independent **live source** and exact FNS + buyer evidence are available.

## Acceptance gate to be fulfilled before production
1. Obtain official source-provided RSS/API/authorized export or verified legitimate subscribed notification; record vendor entitlement/terms.
2. Confirm actual original notice URL, request to purchase our service, deadline, and **actual buyer** versus marketplace/organizer.
3. Verify legal buyer INN and FNS category in Moscow/Moscow Oblast, exclude current clients, stop-list and duplicates.
4. Obtain available business contact channel, buyer's actionable request and manager review.
5. Demonstrate repeatable collection with real **new**, not expired notices. Only then label the source \`CONNECTED\` and submit a main-branch merge request.

**Current counts:** \`B2B_LIVE_FETCH=0\`, \`SUPPMAN_LIVE_FETCH=0\`, \`ONLINECONTRACT_LIVE_FETCH=0\`, \`QUALIFIED_REAL_LEADS=0\`.

No Bitrix24, paid tariff, outreach or VALYQON changes.
