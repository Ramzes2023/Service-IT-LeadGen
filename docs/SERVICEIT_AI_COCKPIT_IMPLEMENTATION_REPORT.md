# ServiceIT AI Cockpit implementation — CODE_READY

Implemented without live Bitrix24/GigaChat calls, messages, deployments, package installation, commits, branches or pushes. Public API documentation was read; the corporate portal was never called. No secret/config/customer files were read. No claim of live UI acceptance is made.

## Delivered

- `leadpilot/serviceit_ai_report.py`: immutable typed reusable report; deterministic score/factor consistency; verbatim evidence; explicit NOT_RUN; unverified service hypothesis; source/AI injection rejection; Russian multiline sections, manager checklist, provider/model/time. Untrusted model prose is deliberately not repeated as factual customer demand. The generic builder accepts evidence references rather than arbitrary clickable URLs/contacts.
- `leadpilot/bitrix_1062_ai_cockpit.py`: injectable bounded REST adapter (200 calls per invocation, 15-second requests, 512 KB responses, redirects disabled, no retry); schema inspection/provisioning; native personal layout read/modify/set/readback; one-item report population. A future human can separately inspect, provision, configure, and populate. No combined workflow or shared-layout write is exposed.
- `leadpilot/ai.py`: optional suppression of 401 completion replay and new single-attempt GigaChat cockpit entry point; existing callers retain their defaults.
- `scripts/RUN_SERVICEIT_AI_COCKPIT_DEMO.ps1`: friendly explicit modes; default has zero network; later live webhook input occurs through Python's hidden local prompt. No secret files are sourced. Existing process GigaChat configuration is used only for explicitly opted-in population.
- `tests/test_serviceit_ai_cockpit.py`: 69 mock-only tests covering quality, malicious inputs, exact quote, generic report, types/scopes/duplicates, original metadata preservation, automation-disabled gates, multiline content, backups, layout moves/preservation/idempotency, category/personal scope, concurrent change, readback failure, credential refusal, persistent atomic reservation and timeout fencing.
- `docs/SERVICEIT_AI_COCKPIT_MANAGER_DEMO_RU.md`: exact operator commands, manager UI steps, native limitations, privileges, reconciliation rules and documentation links.

Nine new fixed string fields are planned in CRM_13, including `UF_CRM_13_SVCIT_AI_FULL_REPORT` with ROWS=18. All 16 original fields are required and preserved. Only the new fields are updated on exact 1062/21/3. Score/version are displayed but not rewritten by the report workflow. STOP_REASON, company/contact associations, task 26163, stages and original review metadata are not mutated. Freshness/identity/STOP/review protections reuse existing qualification checks. The fixed AI_TEXT is explicitly synthetic evidence; it is not represented as text retrieved from a customer publication or from an existing CRM evidence field.

The schema belongs to the whole smart process; its creation cannot be scoped to one card/category. The **layout** is restricted to personal P and category 21; the **report update** is restricted to item 3. Internal smart-process id 13 and entityTypeId 1062 must both match fresh metadata. Native string settings and field schemas must read back compatibly; ambiguous/null layouts, pagination, incompatible fields and server normalization fail closed.

The separate `cockpit-report-v1.sqlite3` never opens the already VERIFIED old qualification ledger. Fixed operation keys are reserved atomically before mutations (before AI for population); timeout/unknown status leaves UNCERTAIN and cannot trigger a second POST. Schema/layout/report snapshots precede writes. Existing matching fields/layouts are reconciled read-only; the single report refresh remains fenced after success. Never switch state directories or reset/delete a ledger to bypass a fence. No automated rollback or UNCERTAIN reset is supplied.

## Validation

Using the exact interpreter from `SERVICEIT_PYTHON_FOR_TESTS`:

```powershell
& $env:SERVICEIT_PYTHON_FOR_TESTS scripts/serviceit_offline_pytest.py
```

Second-pass result: **861 passed, 26 subtests passed**. This runner disables socket operations before test collection and disables third-party plugin autoload. Includes all 69 cockpit tests and existing regression coverage. Targeted cockpit run: **69 passed** with sockets disabled. `git diff --check` and PowerShell AST syntax parsing passed. The first second-pass targeted command named a nonexistent `tests/test_ai.py` and collected nothing; the corrected cockpit invocation and full suite both passed. No test failure was ignored.

## Independent second-pass source audit

Found and fixed a quote-verification flaw in the shared AI adapter: truncating to 300 characters before checking could accept an invented suffix. The complete model quote must now be a nonblank verbatim substring within the length bound; it is never shortened into apparent validity. Invalid quotes produce needs_review. Shared AI authorization/completion exceptions now suppress chained transport details that could expose credentials or source text in tracebacks. Existing retry defaults remain unchanged; cockpit completions still never replay a 401.

The report builder explicitly requires GigaChat provenance and a string, nonblank, verified model quote. Population rejects any existing nonempty report field before backup/reservation/model invocation, preventing a new state directory from overwriting a completed or partial remote report. Tests exercise this fresh-ledger case and secret-free formatted tracebacks.

The only configuration SET sends the complete deep-copied GET layout, including existing sections and unknown options, with a second GET for concurrent-change detection and exact readback. Scope is fixed P, categoryId 21; C and other-user layouts fail before transport. Provisioning uses native string userfieldconfig.add, never update/delete or field-type conversion. Existing incompatible fields block. All mutations have pre-write backups and persistent reservations. Report updates contain only the nine new fields; original score/version, STOP/review metadata, associations, stages and task 26163 remain outside the write allowlist. No task, creation, duplicate-creation or outreach method is available. The old qualification ledger and code were not modified by this workflow.

Limits: mock validation cannot establish actual portal response shapes, RBAC, effective inherited personal-layout completeness, UI rendering or provider acceptance. The final GET/SET interval is not a server-side compare-and-swap. Local one-shot guarantees require preserving the same state directory; an uncertain request with no visible remote report cannot be safely reconciled by inventing a new ledger. This is demo source ready for independent validation, not production readiness. No Bitrix REST, GigaChat, .env or live secrets were used during this second pass; no commit or push was made.

Automatic approval review rejected cleanup of `tests/.cockpit-tmp`, `tests/.cockpit-tmp2`, and `tests/.cockpit-tmp3` as “blocked by policy”, including a narrower per-entry cleanup. These generated mock-only test artifacts remain and should be excluded from publication/removed by the local operator. They contain synthetic fixture backups and test SQLite ledgers, not live credentials or customer data.

Second-pass cleanup of `docs/audit-targeted-temp` was also rejected by automatic approval review as “blocked by policy”; this additional generated mock-test directory remains for local operator cleanup. No alternate deletion mechanism was attempted.

## Exact next invocation

For the human operator, from this worktree:

```powershell
.\scripts\RUN_SERVICEIT_AI_COCKPIT_DEMO.ps1
```

This is offline and returns CODE_READY. Then follow the Russian guide for a separately authorized `-InspectLive` preview, `-Mode provision -ApplyDemo`, `-Mode layout -ApplyDemo`, and `-Mode populate -ApplyDemo -GigaChatOptIn -SyntheticPrivacyOptIn`, always with `-InspectLive` for live modes. Do not run live modes inside this development session.

Remaining corporate acceptance: validate scopes and RBAC, actual REST response shapes and writable fields, create fields, apply the webhook owner's personal configuration, run one fresh synthetic GigaChat inference with exact write/readback, and inspect the native card visually. Personal layout is not automatically visible to Mikhail. No shared/admin workflow or real-production lead adapter exists. Independent company/customer/global duplicate/stop/legal gates are required before any future production adapter.

Current live status: **LIVE_FIELD_CREATED=false; PERSONAL_LAYOUT_APPLIED=false; DEMO_AI_REPORT_VERIFIED=false** for this implementation run.
