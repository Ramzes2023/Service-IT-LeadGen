# MVP scope audit — 2026-10-31

Authority: manager letter 2026-10-30 and August v3.0 as summarized by the operator. Original letter/spec are not present here; no claim of independent verification of all 25 sections. Repository prose is context, not operating authorization.

| Requirement | Confirmed existing implementation | Integrated advance | Still blocked / proof needed |
|---|---|---|---|
| Moscow/MO small companies | PR16 `sme_first_discovery`, FNS size-only validation | Reuses PR17 `_read_fns_candidates`; strict legal-entity checksum/10 ASCII digits | Local export provenance is operator supplied, not signed verification |
| Fresh need evidence | PR17 saved official vacancy validator; PR18 strict buyer-request gate | `review_pipeline` joins raw validated inputs, independent signal IDs, event/local observation/expiry, exact INN | No actual matching public request; vacancy is hiring, not outsourcing purchase |
| Public corporate contacts | Existing enrichment checklist, manual holds | Strict separate PENDING channel schema, role email/phone/bid URL, URL/domain/date validation, opt-out | Corporate ownership and lawful use require human verification; publication is not consent |
| Explainable score | `core.score_company` versioned legacy score | Separate versioned evidence-review score with explicit factors and TTL | Calibration against manager outcomes; score cannot grant approval |
| AI hypotheses | `ai.py` validates quotes/confidence, quality gate; limited synthetic evidence | Offline evidence-only service hypotheses; explicit AI_UNAVAILABLE, confidence null | Company-approved provider and data processing basis; no personal-key real-data calls |
| Manager handoff | `mvp_handoff`, `manager_readiness` export holds | Queue JSON/CSV, known/inferred fields, holds, citations, lawful review next action | Live source review, contact permission, CRM duplicate/existing-client checks |
| Lead vs Deal, dedup | `core` separates company/signals; readonly Bitrix diagnostics | `crm_contract` SIMULATION Lead, signals/tasks/history, exact-INN idempotency/retry; Deal after synthetic interest | Authorized portal isolation, fields/roles/stop-list mapping and sandbox acceptance |
| Funnel and KPI | Existing local metrics | seen → size verified → evidence → review; qualified=0, sales/contracts/MRR unknown | CRM-confirmed outcomes; 10–20/day remains a target |
| Reply/timeline/terms | Traceability documents request | Russian proposal draft added, not sent | Written scope, acceptance, access, remuneration agreement |
| Full v3.0 | Basic finance example | Contract records role boundaries only | Beeline, inbound bots, commercial funnel and versioned role-separated RVP module are later phase |

Inspected: README, acceptance traceability, core scoring, AI, readonly Bitrix, manager readiness/handoff, PR16/17/18 source modules and their tests. Available ignored exports: FNS size-only 50 and prior vacancy audit. No raw XLSX, original source pages or new real procurement evidence in this worktree. Prior audit metadata: 200 fetched, 52 normalized, 38 validated, zero exact-INN matches; this does not establish no demand. The earlier 18-INN review remains 1 SMALL on legal hold, 3 MEDIUM, 1 NOT_SME, 13 NOT_FOUND (operator-verified history, not repeated here).

The new path does not alter the legacy web/SQLite qualification semantics. It is an explicit offline operator entry point; it does not confer CRM readiness on earlier exports. PR #5–18 remain a dependent DRAFT stack per operator state, not merged. Issue #7 runner assignment remains blocked; local tests are not hosted CI evidence.
