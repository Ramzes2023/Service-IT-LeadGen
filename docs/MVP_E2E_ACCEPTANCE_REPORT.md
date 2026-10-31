# Local integrated MVP acceptance — 2026-10-31

## Independent quality-pass evidence

Full suite before fixes: 219 passed in 7.80s using worktree basetemp. After nine added regression cases and gate fixes: **228 passed in 12.87s**, including all 194 baseline tests. Default Windows temp run failed with 35 passed / 184 setup errors (permission errors); it is not reported as a passing run. Existing tests were not weakened.

Synthetic E2E exercises FNS fixture validation -> evidence absent/present/expired -> repeatable deterministic scoring -> manager review card -> in-memory Lead/signals/tasks/history -> duplicate/new-signal/retry/stop -> synthetic interest/Deal gate. Manager review requires boolean True. Persistent simulated company stops cancel pending retries and block Deals. Repeat updates refresh score without duplicate tasks. No authorized portal was available: **Bitrix24 integration is not verified**.

Real ignored local input replay: `python -m leadpilot.review_pipeline --output exports/quality_manager_queue.json --csv exports/quality_manager_queue.csv` returned 50 seen / 50 FNS-size-verified / 0 current evidence / 0 manager-review-ready / 0 qualified / 0 CRM-ready. No contacts, network, fresh buyer requests or fabricated real interest. No schema bridge was necessary. Metadata from the previous vacancy audit cannot substitute for original matching evidence.

Tracked-file scan: 89 paths, zero operational-data-path or credential/webhook-pattern flags. Both new output paths are ignored; no company rows logged. UTF-8 files validated; PowerShell reads must explicitly specify UTF8 on this host. No commits/push/PR or outbound actions. CI runner issue #7 may remain unresolved and was not checked remotely.

Incomplete acceptance and permissions are enumerated in CODEX_MVP_IMPLEMENTATION_NOTES.md: genuine current evidence, lawful channels, manager acceptance criteria, authorized isolated Bitrix portal/mapping/roles, and later Beeline, inbound bots, full funnel and finance-approved RVP. Later modules are not implemented. The narrow manager scope and complete phased scope are mapped in CODEX_MVP_SCOPE_AUDIT.md and MVP_ACCEPTANCE_TRACEABILITY.md.

## Reproduce safely

From repository root in PowerShell, using the supplied host environment read-only:

```powershell
$TaskPython = 'C:\Users\ramze\Service_IT_GitHub_Repo\Service-IT-LeadGen\.venv\Scripts\python.exe'
& $TaskPython -m pytest -q --basetemp=exports/pytest-acceptance-new
git diff --check
& $TaskPython -m leadpilot.review_pipeline --output exports/operator_queue_new.json --csv exports/operator_queue_new.csv
```

Use a new basetemp/output name per run. Default Windows pytest temp was inaccessible (WinError 5); keeping temp under ignored exports fixes this environment issue without changing baseline tests. No installation or source-repository modification is required.

## Inputs and states

Required: PR16 `SME_FIRST_FNS_SIZE_ONLY` report, validated again by PR17. Optional `--requests` is the original PR18 `PUBLIC_1C_REQUESTS_MANUAL` document, never its derived approved output. Optional `--folders ... --offsets ...` are original PR17 saved official pages. Missing is NOT_SUPPLIED, not a successful source scan. Missing files, stale FNS and malformed evidence fail closed.

Optional `--facts`: exact envelope `{"mode":"PUBLIC_CORPORATE_FACTS","items":[...]}`. Row fields: `inn`, `kind` (`it_expansion` or `retail_opening`), `source_url`, `event_on`, `observed_on`, `excerpt` (20–700 chars), `review_state` (`PENDING`). These are supplied factual claims, not independently verified facts; service relevance is explicitly a hypothesis. An opening never proves a hardware purchase.

Optional `--contacts`: exact envelope `{"mode":"PUBLIC_CORPORATE_CHANNELS","items":[...]}`. Row fields: `inn`, `channel` (`email`, `phone`, `bid_url`), `value`, `source_url`, `corporate_domain`, `observed_on`, `review_state` (`PENDING`), `purpose` (`corporate_review`, `published_bid`), `opt_out` (boolean). Corporate role mailboxes only; canonical +7 phone accepted as an unverified corporate claim, never personal-name enrichment. Bid URL must equal the cited permitted procurement page. Corporate source/domain match is syntactic, not proof of ownership. No directories or FNS contact columns are imported. Empty channels are valid unresolved state. No consent is inferred, even for bid channels; a manager may review published bid instructions separately from cold marketing.

Unknown fields in manual inputs are rejected, including preapproval flags. These are local research assertions, not signed/authenticated evidence. A malicious rewrite of the entire original data cannot be detected cryptographically; consequently no imported assertion can grant legal use, CRM approval or contact permission.

Signal IDs bind INN/type/normalized URL. Multiple sources attach independently; same-INN company remains one row. Event TTL is 30 days; a request deadline shortens it; missing deadline prevents current status. Observation for adapted request/vacancy sources means local review of the input on the run date, NOT live fetch. Manual observations must be within 30 days. Stale adapted evidence is excluded or retained as expired according to upstream validator and TTL. FNS TTL remains 60 days.

Score `evidence-review-2026-10-v1`: size/region 20, current signal 30, explicit buyer-request 20, at least two source domains 10, supplied corporate channel 5; maximum currently 85 of 100. No AI points or frequency inflation. Source-domain concurrence is review context, not proof of independence. Recompute daily. Missing demand stays at target/review; missing channel cannot become manager_review_candidate. Every signal carries factual claim, hypothesis, strength, source, event/local observation dates and expiry. Source conflicts/identity always require review; shared channel/domain explicitly holds both companies.

`candidate_target` = size/region only; `evidence_review` = supplied evidence needs review; `manager_review_candidate` = current evidence plus supplied channel without collision/opt-out; it still requires legal, source, manager and CRM checks. `crm_approved` is deliberately unreachable in this offline path. No next-day cold-call queue or draft call notes is generated.

## Evidence versus simulation

Real local execution: 50 FNS-size-verified legal entities; no optional raw evidence supplied; 0 with current evidence, 0 ready for manager review, **0 qualified leads**, 0 CRM-ready. JSON and CSV are only under ignored exports. Prior demand audit was inspected as metadata, not imported as fresh authenticated evidence. No actual buyer request was found or invented. No conclusion of NO DEMAND.

Synthetic acceptance in `test_review_pipeline.py`: PR16/17/18 integration, two-source concurrence, exact-INN grouping, source provenance, score explanation, missing/expired evidence, forged approvals, corporate channel validation, URL attacks, opt-out, distinct bid channel, same phone/domain across INNs, formula escaping, restricted output and no overwrite, AI unavailable, CRM unavailable, retry expiry/idempotency, new signal on existing Lead, manager task/history links, Lead-to-Deal synthetic interest gate.

Final local run: **219 passed in 5.86s** (194 original + 25 new synthetic checks), exit 0. `git diff --check` exit 0. Real qualified leads remain zero. Default-temp failure and intermediate new-test correction are recorded in CODEX_MVP_IMPLEMENTATION_NOTES.md.

`MemoryCRM` is in-memory only and receipts begin SIMULATION/SIMULATED. It has no transport, credentials or production webhook. Manager-reviewed parameter is a synthetic event, not actual manager identity/authorization. It cannot prove real CRM readiness. Retry storage is memory-only, not a durable production queue. Existing customer, duplicate and consent checks are always PENDING in real reports. `preflight_contract` reuses existing read-only diagnostics only on explicit operator authorization and still returns CRM_BLOCKED; diagnostics do not query per-company duplicates or grant write permissions.

## Remaining acceptance blockers

Real fresh matching need; original page validation and identity; lawful contact basis and channel-specific manager approval; company-owned AI authorization if desired; isolated authorized Bitrix test zone and per-INN duplicate/client/stop-list reads; actual sandbox mapping and role tests. No real sales, contracts or MRR observed. Beeline/bots/RVP later. Hosted CI issue #7 is unresolved; do not describe CI green or stack merged. No production readiness claim.

Final independent rerun: `python -m pytest -q --basetemp=exports/pytest-quality-final` ? **228 passed in 7.27s**, exit 0. `git diff --check` ? exit 0. Direct whitespace validation of all pending untracked text files also passed.
