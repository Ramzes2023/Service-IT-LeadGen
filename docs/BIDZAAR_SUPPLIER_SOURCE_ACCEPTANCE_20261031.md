# Bidzaar supplier source acceptance — 2026-10-31

**NO-GO for connected-source acceptance. NO LIVE TENDERS = NO CONNECTED SOURCE.**

```text
BIDZAAR_SUPPLIER_API_DOCS=NOT_PUBLICLY_VERIFIED
BIDZAAR_SUPPLIER_API_ACCESS=AUTH_REQUIRED
LIVE_SOURCE=BLOCKED_NOT_CONNECTED
LIVE_ACCEPTANCE=BLOCKED
QUALIFIED_REAL_LEADS=0
```

## Phase 0: official evidence, entitlement and limits

Read-only web research on 2026-10-31 inspected the following official pages.
These are marketing/legal documentation observations, **not live tender responses**.

| Official evidence URL | Observed result and consequence |
| --- | --- |
| https://bidzaar.com/blog/api-dlya-postavshchikov | Dated 2026-07-30. Announces supplier tender delivery through REST-compatible integrations and company-profile token. Does not expose verified method, endpoint, auth header, schema, pagination or limits. |
| https://bidzaar.com/app/companies/public/api-aggregator-integrator | Destination of announcement's “try” link. Web reader returned an empty application shell, no supplier reference. No private-interface inspection or login attempted. |
| https://bidzaar.com/for-suppliers | Advertises free basic supplier registration; does not establish supplier API entitlement or API pricing. No registration or purchase performed. Marketing tender totals are not retrieved leads. |
| https://bidzaar.com/connector-api | Explicit buyer integration for publishing procedures and retrieving outcomes. **Disqualified as supplier feed.** Its linked documentation/test registration is not a substitute. |
| https://bidzaar.com/user-agreement | Published 2026-09-25; supplier appendix dated 2026-07-01. Supplier license covers search/participation. API clauses define Connector integration and private-key acceptance/security obligations. They do not establish supplier-feed entitlement or unrestricted automated extraction. |

Terms were read before following the supplier application link/searching for
documentation. Definitions and sections 3, 8, 9, 13 and supplier appendix were
reviewed. Integration key generation requires acceptance in the company profile;
key confidentiality and authorized company representation matter. This is an
engineering entitlement assessment, not a new legal authorization. Current terms
and supplier-specific API/export rights still need company/vendor confirmation.

Two bounded official-domain documentation searches found no usable supplier REST
reference. No speculative endpoint, Swagger path, scope or authentication scheme
was probed. No company credential or verified entitlement was supplied in this
execution. No secret files or unrelated product configurations were searched.
API tariff and pagination/rate/time limits remain **UNKNOWN**, not “free” merely
because the basic account is free. No public tender-card probe was needed: supplied
example URLs do not establish currency or API access. No CAPTCHA bypass, broad
crawl, account registration, subscription or recurring polling occurred. The web
reader exposed documentation text; no access challenge was worked around.

## Phases 1–4: concrete bounded offline implementation

`leadpilot/bidzaar_supplier.py` implements a project-owned authorized normalization
contract and separate local research queue. It is not the vendor schema and makes
no HTTP request. See `docs/BIDZAAR_NORMALIZATION_CONTRACT.md` for exact fields,
limits and invocation. Existing imports/CLI/SQLite/CRM behavior is unchanged; the
new module has its own CLI and reuses exact-INN and FNS evidence validators.

Nine service groups have text-based traceability; 1C/IT/KKT/retail have MVP priority.
Request type, external paid nature, provenance, original URL, status, source dates,
timezone, buyer-vs-organizer INN, buyer-vs-execution region and ownership are distinct.
Only an asserted open original request with matching text can be a local candidate;
none becomes a qualified real lead. All evidence is explicitly supplied/unverified.
FNS categories cannot come from procurement titles, size or contract values.
Existing FNS snapshot rules apply; local file authenticity still needs review.
Stop list, existing clients and manager approval are pending. No lead score is
invented. Dedup groups tender observations and firms without dropping new tender
IDs. No raw contact fields are accepted; sensitive recognized text is rejected.

## Phase 5: real acceptance

| Live response acceptance measure | Verified count |
| --- | ---: |
| Raw tender records fetched | 0 |
| Direct Bidzaar originals | 0 |
| Aggregated government tenders | 0 |
| Matched nine service groups | 0 |
| Open before deadline | 0 |
| Exact buyer INN verified | 0 |
| FNS micro/small Moscow/MO buyers | 0 |
| Manager-review qualified real leads | 0 |

`LIVE_ACCEPTANCE=BLOCKED`, not PASS. No saved live response exists. Empty live
counts are a blocked result, not a successful zero-result API search. Local tests
and synthetic fixtures establish only parser/control behavior.

## Exact next dependency and draft PR scope

Company administrator must obtain the **official supplier-feed REST specification
and confirmed supplier API/export entitlement** from the existing Bidzaar company
profile/vendor. Required: read method/URL, field mapping and buyer identity meaning,
auth mechanism, pagination, rate limits, API price/terms and permitted downstream
research/storage. Provision a read token through an approved local secret mechanism,
never chat/Git/logs. Alternatively provide a legitimately authorized minimized
export and its provenance; export review still does not certify API connectivity.

Then implement the documented bounded read transport and verify one small live
sample with original-response evidence and freshness, before enabling any polling.
Public cards alone cannot satisfy this dependency. CRM/outreach remains out of scope.
The isolated branch is suitable for an **offline-only draft change**, not a claim
that the primary source is connected. No merge, main/production/VALYQON changes,
CRM writes, proposals or messages are authorized by this implementation.

## Phase 6 verification

Validation completed in a new `.venv/` inside this worktree, using Python 3.12.10
and the unchanged `requirements.txt`. The base Python environment was not modified.

```text
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests --basetemp=.test-tmp-bidzaar-full
336 passed in 12.23s
.venv\Scripts\python.exe -m compileall -q leadpilot tests
exit 0
git diff --check
exit 0
```

The full suite includes 63 Bidzaar-specific cases: contract/schema, bounded input,
URL provenance, nine service traces, category-vs-intent separation, jobs/articles/
marketing/registries, paid/aggregated/state records, unknown/organizer buyer,
checksum, expiry and aware timezones, newness/observation freshness, dedup and
repeated firm signals, exact FNS join/freshness/region mismatch, pending controls,
secret-safe errors, no network, exclusive local output and truthful all-zero live
reporting. A focused first run had 62 passes and one login-detector failure;
the detector was corrected before the final full green run. These are offline
tests only; no synthetic fixture is a real tender/customer.

Draft PR title: `Add held offline Bidzaar supplier export review; live access blocked`.
Draft description: Prepare a bounded, authorized local normalization/review path
while official supplier REST documentation and entitlement are unavailable. Keep
all real counts at zero; preserve provenance, service evidence and FNS/region holds.
Validation: 336 offline tests, compileall, diff check. No connected-feed claim,
CRM/outreach change or production deployment. No PR has been published.

Git staging was attempted once and failed creating the isolated worktree's
`index.lock` with Permission denied. No retry, commit, push or merge was attempted;
all six changed/new files remain available for the authorized HOST script to
review, commit and push. Automatic execution review rejected the temporary-test
directory cleanup command with “blocked by policy”; those directories remain
local and are ignored by the scoped `.gitignore` entry.

```text
BIDZAAR_SUPPLIER_DOCS=NOT_PUBLICLY_VERIFIED
BIDZAAR_SUPPLIER_AUTH=REQUIRES_ENTITLEMENT
BIDZAAR_LIVE_FETCH=BLOCKED
BIDZAAR_REAL_REQUESTS=0
BIDZAAR_TARGET_SME_LEADS=0
BIDZAAR_CRM_WRITES=NO
BIDZAAR_SAFE_NEXT_STEP=Obtain official supplier REST specification and confirmed company entitlement from the Bidzaar company administrator
```
