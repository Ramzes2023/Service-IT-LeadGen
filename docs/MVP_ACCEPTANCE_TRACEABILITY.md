# MVP scope and acceptance — derived from manager email and technical brief v3.0

## What the manager actually asked for (email of 30 Oct 2026)
The **first stage is an MVP**, not a production implementation of all 25 sections. Pilot: **1С maintenance**, **Moscow and Moscow Oblast**, **small businesses**. Required: discover companies from specified parameters; fresh evidence of IT need (including vacancies); find lawfully available corporate contacts; score leads; transfer to Bitrix24. The manager also requested a proposal describing timeframes, source selection, constraints, and compensation. Their preference is no fixed development fee, with payment related to leads/sales; terms **have not yet been agreed**.

## The complete spec additionally requires
- INN hard deduplication, contacts as possible duplicate indicators, separate signal histories, channel-specific stop lists and explicit provenance.
- Rules-based versioned Lead Score; AI claims labelled as hypotheses. No unsolicited mass advertising/robocalls.
- CRM **Lead before confirmed interest**, then a Company / Contact / Deal in the separate **LeadGen** sales pipeline. This is not the same as putting raw prospects straight into a Deal.
- A manager brief with source, reason, proposed service, role/contact, lawful next action, follow-up and contact outcome.
- Bitrix24 calls/telephony, existing incoming bots, complete sales conversion, and RVP-based financial motivation with confirmed payments, roles, configurable percentages and audit history. Those are later full-scope workstreams, not demonstrated MVP features.

## What has actually been observed
| Component | Evidence | Not yet demonstrated |
|---|---|---|
| Python/FastAPI tests | local pytest and Windows PowerShell | GitHub CI currently red; do not describe as green |
| Real vacancies | source on user's Windows returned 30 jobs (15 Moscow, 15 MO) | further source availability / accuracy |
| Ingestion | 22 fresh signals, 15 companies; 8 stale excluded | independent verification of company size and CRM duplicates |
| GigaChat | 2 synthetic analyses, 1 usable, 1 needs review | accuracy on customer-approved real-data workflow |
| Bitrix24 | offline JSON export; read-only preflight code in PR #4 | **no test portal credentials, no CRM writes** |
| Contacts | not collected by the Trudvsem importer | lawful source, provenance, verification of permission |
| Pilot KPI | 30–50 candidate records/day, 10–20 qualified review leads/day are targets, not measured actuals | conversion to meetings, sales and monthly recurring revenue |
| Financial motivation | simple offline RVP example | real role-based payments/audit system |

## Safety-critical gate
Until rights to an isolated test zone are proven, **no CRM Lead or Deal write** is permitted. A deal category does not isolate ordinary Leads. The CRM administrator must approve how preliminary prospects are safely separated: either a test smart process, or precisely scoped test-lead role/workflow. Use a restricted test deal category only for confirmed interest.

## Next acceptance scenarios
1. Prove daily authorized source runs with fresh links and correct INNs, retry/idempotency, and source status.
2. Enrich only official corporate contacts with citation/date; verify company size instead of guessing.
3. Query Bitrix24 for duplicates **read-only** only with granted permission, respect stop lists.
4. Generate manager briefing, source and AI caveats; acceptance per lead on completed validation.
5. With authorized sandbox, create one synthetic test record and ensure it cannot touch other workflows.
6. Report per-source candidate -> verified -> manager contact -> commercial interest -> deal, including reasons for rejection.
7. Agree written delivery phases, company-owned credentials, limits, and remuneration before paid production development.

## Data-protection rules
No secrets, contact databases, raw real-company source exports, or unapproved employer specification are stored in GitHub. This repository is private but sensitive information must still be excluded. VALYQON is not a source or dependency: the project reuses **only an authorized personal GigaChat API configuration during synthetic tests**, without changing VALYQON code, database, or Docker.
