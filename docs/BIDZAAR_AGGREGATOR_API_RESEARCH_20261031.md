# Bidzaar aggregator-integrator purchases API — documented alternative

**Research date: 2026-10-31. Status: access NOT GRANTED; live procurement records = 0; qualified real leads = 0.** Not a connected production source, not a supplier API.

## What changed

A third, technically distinct Bidzaar integration channel was found in a document entitled **«Интеграционное API Бидзаар»**, **version 05.07.2024, revision 2** (publicly indexed PDF). This is an API **for tender aggregators**, NOT:

1. the buyer/organizer **Bidzaar Connector API** used to publish procedures; or
2. the July 30, 2026 **supplier API** advertised as obtaining relevant tenders with a company-profile token.

The older aggregator specification documents a real purchases endpoint:

\`GET https://bidzaar.com/api/aggregator_integrator/{integratorId}/procedures/purchases\`

Parameter names: \`PageNumber\` (deprecated/ignored in the revision), \`PageSize\` (up to 1000 in vendor document, client intentionally caps at 100), \`FromDate\` UTC timestamp. The document says:

- **integratorId UUID must be obtained by request to space@bidzaar.com**;
- results are ordered by \`lastModified\`;
- to advance, use the last record's \`lastModified\` as \`FromDate\`; returned records may include equality-boundary duplicates, so deduplication is required;
- dates earlier than 14 days trigger an error. Our client uses a conservative 13-day window;
- aggregator Swagger URL: https://bidzaar.com/api/aggregator_integrator/swagger/index.html — **not accessible during our research**, and the 2024 document's current validity has not been confirmed.

Vendor rights, cost, eligibility as a **private internal** lead-research system, response schema, business data fields and retention policy are **unknown**. A valid UUID/known endpoint is NOT authorization to use the API.

The publicly indexed 2024 PDF was found in cached search results on a temporary Yandex Object Storage link; its download link may be expired. The visible text and method should be reconfirmed by Bidzaar before deployment. Do not rely on the link's availability or a guessed current API scheme.

## Action already taken

On 2026-10-31 a direct inquiry was **sent** to the vendor's documented \`space@bidzaar.com\` address asking for: valid integrator credentials issuance process (not secrets by email), current spec, eligibility, cost, legal permitted use, schema and filters. A separate question about the supplier-profile API was included. An earlier inquiry to \`info@bidzaar.com\` also exists.

No registration, vendor token, paid access, scraping, buyer outreach or Bitrix24 writes have been performed.

## What was implemented

\`leadpilot/bidzaar_aggregator_feed.py\` contains a **real HTTP GET probe** for the exact documented path (not a placeholder endpoint). It is **disabled until vendor authorization**. A successful probe reports only the count and common JSON field names; it does not retain tender text, create leads, contact buyers or write CRM.

It enforces fixed HTTPS origin, UUID validation, GET-only, short timeout, disabled redirects, bounded response bytes, JSON-array validation, 13-day max history, and no identifier/response body in errors. Only one batch is fetched; cursor persistence and periodic polling are *not* enabled pending a verified vendor response schema, entitlement, rate limits and storage permission.

When and only when the vendor confirms this account's eligibility and issues an ID through an approved secure channel, set locally (never in GitHub/issues/chat):

\`\`\`powershell
$env:BIDZAAR_AGGREGATOR_ID = '<VENDOR_ISSUED_UUID>'
$env:BIDZAAR_AGGREGATOR_ALLOWED = 'YES'
.\.venv\Scripts\python.exe -m leadpilot.bidzaar_aggregator_feed --page-size 25
\`\`\`

Do not run this against unapproved IDs. The sample above is not a request for the user to do registration or API activation; it is a documented operator procedure for the later authorized run.

Unit tests under \`tests/test_bidzaar_aggregator_feed.py\` verify the documented query, consent gate, no-request without ID, schema checks, redaction and rejection controls. These tests contain only fictitious data and do not establish real API availability.

## Actual remaining acceptance

1. Vendor confirms current API method, rights and free/paid terms for intended internal use; ID issued by Bidzaar.
2. Receive one authorized **real** 200 response; verify exact response schema and buyer vs organizer identity, original link, posted/deadline/status, region.
3. Implement safe cursor iteration and dedup, respecting \`lastModified\` equality and limits, and permitted storage.
4. Filter all nine Service IT service categories, exact buyer INN, FNS micro/small Moscow/Moscow Oblast, current client and suppression lists; keep public/state/large/unknown separate.
5. Demonstrate actual qualified requests and counts. **Only then consider merge and downstream CRM**.

Until then: \`LIVE_SOURCE=BLOCKED\`, \`REAL_TENDERS=0\`, \`QUALIFIED_REAL_LEADS=0\`, \`CRM_WRITES=NO\`.

Official contextual references:
- https://bidzaar.com/blog/api-dlya-postavshchikov
- https://bidzaar.com/user-agreement
- https://bidzaar.com/connector-api
