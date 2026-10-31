# Bidzaar offline normalization V1

This is a **project-owned local contract**, not an official Bidzaar response
schema or an API endpoint. No HTTP client is implemented until the supplier
specification, entitlement and permitted use are verified. No polling is enabled.

Use only a company-authorized, minimized export. Do not put credentials or raw
contacts in input, Git or logs. The authorization marker is an operator assertion,
not proof of vendor entitlement or authenticity. All supplied claims remain held.

```powershell
.\.venv\Scripts\python.exe -m leadpilot.bidzaar_supplier --input authorized-normalized.json --fns exports/fns-size-only.json --output exports/bidzaar-review.json
```

`--fns` is optional. It accepts the existing `SME_FIRST_FNS_SIZE_ONLY` format
through the repository's validator (exact INN, micro/small, Moscow/MO, snapshot
not older than 60 days). Neither a checksum nor a locally supplied FNS assertion
proves buyer identity or file authenticity. No new FNS network lookup is made.
Medium/large/unknown/state organizations stay in a separate held research queue;
they are never counted as small-business leads. Execution region does not establish
buyer region. Ingushetia is secondary, not a Moscow/MO qualification.

Input is UTF-8 JSON, at most 1 MB and 100 records. Exact envelope fields:
`mode: BIDZAAR_AUTHORIZED_NORMALIZATION_V1`,
`authorization: COMPANY_AUTHORIZED_EXPORT`, `items: [...]`.
Each item has exactly these fields (no inferred vendor field mapping):

| Fields | Contract |
| --- | --- |
| id, url | Lowercase UUID; exact official HTTPS `/app/process/light/{id}` or `/process/light/{id}` notice; no query, credentials or fragment |
| title, excerpt, category | Minimized source text, maximum 1200 characters each; no contacts/secrets. Category is retained as an assertion, never buying intent |
| origin | bidzaar_original / aggregated_government / aggregated_other / unknown |
| kind | procurement / vacancy / article / marketing / supplier_registry / unknown |
| paid_external | JSON boolean asserting a request to buy external goods/services, subject to evidence review |
| status | open / closed / cancelled / unknown |
| posted_at, deadline_at | Timezone-aware ISO timestamp, or null if absent; dates alone cannot establish precise expiry |
| observed_at | Required timezone-aware timestamp, no future observations |
| buyer_name, buyer_inn | Actual buyer assertion, not organizer; empty string for unknown. Only 10-digit legal-entity INNs with checksum pass preliminary validation |
| inn_role | buyer / organizer / unknown |
| buyer_region, execution_region | Distinct assertions; region codes 77, 50, 06 or empty/other |
| ownership | private / state / unknown |
| access | authorized / paid_required / unknown; never authorizes purchasing access |

Service matching uses only title/excerpt, returns matched terms for traceability,
and is a candidate heuristic requiring manager review. Nine groups: 1C; IT/user
support; KKT/fiscal/marking; retail equipment; signature/CryptoPro/EDI;
Bitrix24/CRM; servers/networks; ERP/API; chatbots/AI. MVP priority is 1C,
IT support, KKT and retail equipment. The user-supplied nine-group scope extends
the existing pilot's narrower 1C focus; no claim of nine live service matches.

Dedup retains latest observation per tender ID and first/last observation times.
Conflicting buyer identity, origin or URL fails the whole batch. Exact INNs group
firms while keeping separate tender IDs for repeated new requests. Full historical
snapshots and cross-run persistence are not implemented; supply combined bounded
observations for reconciliation. Newness window: 30 days; observation freshness:
24 hours. At the deadline the request is expired; all comparisons use aware UTC.

Output is exclusive-create JSON under `exports/`, never CRM input. It includes
local unverified counts separately from all-zero real live counts, evidence,
status/deadline, reason now, proposed offering, safe manager action, holds and
`NOT_SCORED`. No contacts, financial values, contact/interest claims or fabricated
scores are generated. Unknown buyer/FNS, non-original, paid access, state ownership,
stale/closed notices and pending stop/client lists remain held.

`decode_contract` rejects non-200, non-JSON, CAPTCHA/login HTML, oversized,
malformed or duplicate-key JSON without echoing input. This is a defensive local
decoder, **not a tested live transport**. A future vendor adapter must enforce
timeouts, response streaming bounds, no redirects/retries after access failures,
documented rate limits, secret-safe diagnostics and original-response provenance.

All rows have `SOURCE_NOT_LIVE_VERIFIED`. `LIVE_SOURCE=BLOCKED_NOT_CONNECTED`,
`LIVE_ACCEPTANCE=BLOCKED`, `QUALIFIED_REAL_LEADS=0` cannot be overridden by input.
Tests use synthetic records only. Never report fixture counts as real customers.
