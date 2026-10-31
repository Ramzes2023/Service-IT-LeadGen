# Integration readiness — 2026-10-31

## Independent senior review acceptance (supersedes earlier local test totals)

Recommendation: **GO for host review of the offline integration candidate;
NO-GO for production deployment, live CRM writes/outreach, or claiming CI ready.**
The changes were inspected independently, including manager briefs and CLI,
review_pipeline and its FNS reader, MemoryCRM, both read-only Bitrix handlers,
CRM preview, SQLite schema and the GitHub workflow, alongside their tests.
Existing uncommitted integration edits were retained. No original-repository
files, main, commits or pushes were changed. No real external API was called.

Independent defects corrected:

- Blueprint/preflight JSON now escapes Unicode for Windows cp1252 stdout.
- Invalid sandbox category IDs return a fixed configuration error rather than
  echoing the input through Python's integer-conversion exception.
- Legacy read-only diagnostics handle JSON null, numbers, lists and strings
  as safe API errors instead of raising TypeError.

Structured offline acceptance:

| Boundary | Inputs and acceptance |
| --- | --- |
| New manager queue | Synthetic FNS, requests, facts and role channels; exact INN joins, checksum, freshness, shared channels, stops and imported approval rejection exercised by queue and integration tests. Size-only inputs remain held. |
| Legacy manager CLI | Synthetic SQLite lead, fixed date; creates one local brief, explicit permission false, and leaves database bytes unchanged. Existing score, AI-quote validation, suppression and export tests retained. |
| Bitrix blueprint/read diagnostics | Offline blueprint under cp1252; MockTransport metadata fixtures, write-method rejection, safe permission failures, malformed JSON response and invalid configuration tests. No live preflight. |
| MemoryCRM | Synthetic manager review, retry, repeat/new signals, exact INN, stale channel/FNS, company stop and confirmed-interest gates; one lead/task for repeated same signal. Simulation grants no portal permission. |
| End-to-end | Synthetic demonstration with socket connect and HTTP send patched to fail; one simulated lead/task, no CRM writes or outbound. |

Final independent command used the supplied HOST_PYTHON executable:

```text
-m pytest -q -p no:cacheprovider tests --basetemp=C:\Users\ramze\AppData\Local\Temp\serviceit-independent-20261031-c
270 passed in 3.82s (Windows, Python 3.12)
-m compileall -q leadpilot tests
git diff --check
```

Compileall and diff check exited zero. An earlier C:\Windows\Temp basetemp
was denied (56 passed, 206 setup errors); the host user TEMP directory worked.
An intermediate run had 267 passes and one regression-fixture failure because
httpx json=None creates an empty body; explicit serialized JSON null fixed
the fixture. Neither earlier run is recorded as green.

Compatibility: no SQLite table/column changes were introduced. Legacy scoring
intentionally accepts demo INNs without checksum validation; its output explicitly
remains unverified and cannot substitute for the strict evidence schema.
The new manager wrapper preserves evidence cards and adds an envelope; it does
not reinterpret legacy scores as FNS evidence or purchasing intent. MemoryCRM
expects internally generated cards, not an authenticated public input schema.

Privacy review: tracked operational stores and private key files were absent;
.env.example contains blank credential values. Sample contacts are synthetic,
while the existing research-hints sample contains public legal identities and
source URLs, not customer contacts or permission. A working-tree secret-pattern
scan excluding .git and runtime exports/data found no private-key blocks,
long Bitrix webhook tokens or OpenAI-style keys. This is a scoped inspection,
not proof that all Git history or every possible secret format is clean.

Workflow semantics inspected from local YAML: pull_request targets base main,
including draft PRs and normal opened/synchronize/reopened events; push targets
main only. Pushing this integration branch alone does not trigger this workflow.
The workflow has read-only contents permission and no configured secret inputs.
It runs Python 3.12 on Ubuntu with tests-only discovery and runner-temp basetemp.
Windows has been executed locally; Linux portability has source/test review only.
No hosted workflow, runner allocation, billing status or GitHub settings were
verified. The earlier Issue #7 description remains operator-supplied context.

Remaining blockers are specific: a completed hosted Linux CI job; host independent
tests and final diff review before commit/push; original timely exact-entity need
evidence and manager acceptance; channel ownership/legal-use/stop review; approved
Lead isolation, portal field mappings, roles/robots, existing-client and duplicate
reads; company-owned credentials and explicitly authorized synthetic portal
acceptance. Local green tests close none of these live/organizational gates.

Candidate branch: `codex/serviceit-integration-20261031-115538-872`.
This is an offline integration candidate for one future **draft PR to main**,
not production acceptance. No commit, push, PR creation, customer contact, CRM
request or source-repository edit was performed during this integration.

## Ancestry and consolidation

The runner supplied the PR #19 codebase, including the historical dependent
PR #5–19 work (source adapters, FNS selection, exact-INN evidence review and
offline handoff). Local history includes PR19 implementation `62389f1` and
portable collection fixes `f5ed0e0` / `bab703f`. The runner then merged PR #2
manager briefs at `cfa63fe` and PR #4 read-only Bitrix sandbox at `076f9e9`.
These merge commits are locally visible; PR numbering/status for #5–19 is
operator-supplied context, not independently fetched GitHub metadata.

After host independent validation, propose one reviewable integration draft
against main. Reference #2, #4 and #5–19 with their original evidence; compare
the final diff against main before deciding which old drafts are superseded.
Close or archive superseded stacked drafts after review, rather than mass
merging every historical branch. The host owns commits and pushes.

## Reconciled behavior

`manager-briefs` retains its SQLite source, score and original tests. Its output
now explicitly says size and purchasing intent are unverified and carries
unresolved permission/client/duplicate holds and channel suppressions. Legacy
scores never become FNS verification or CRM approval.

`evidence-briefs` calls the existing strict FNS pipeline through the manager
brief module. The standalone `leadpilot.review_pipeline` route remains intact.
Neither new CLI route initializes a database. FNS size is valid for 60 days;
signals and corporate observations use 30 days, with request deadlines able to
shorten validity. Evidence joins by exact checksum-valid legal-entity INN,
preserves source provenance and independent signal IDs, and never infers buyer
intent from a vacancy, expansion, company size or score. Domain concurrence is
not proof that sources are independent. Stops and shared channels hold review.

The in-memory CRM simulator revalidates FNS age, channel age, exact channel INN,
explicit stop state and review flags on each submission/retry. Its roles are
descriptive simulation boundaries, not authenticated portal RBAC. Production
roles and durable retries remain unimplemented. Synthetic manager events cannot
grant real authority. All receipts keep `crm_ready=false`.

`preflight_contract` requires literal boolean authorization and reuses PR #4's
deal metadata diagnostic. A successful category read explicitly leaves Lead
isolation, existing-client and duplicate checks unresolved and writes blocked.
Ordinary Leads are not isolated by a Deal category. No real preflight was run.

## Reproduce offline

Run from this isolated worktree, using the supplied host Python read-only:

```powershell
$TaskPython = 'C:\Users\ramze\Service_IT_GitHub_Repo\Service-IT-LeadGen\.venv\Scripts\python.exe'
# Use a fresh directory outside the worktree under the host temp directory.
& $TaskPython -m pytest -q -p no:cacheprovider tests --basetemp=$env:TEMP/serviceit-independent-20261031-fresh
& $TaskPython -m leadpilot.cli acceptance-demo
& $TaskPython -m leadpilot.bitrix_sandbox --blueprint
git diff --check
```

Use a fresh basetemp name. `acceptance-demo` creates only temporary synthetic
FNS/fact/channel documents, uses a fixed date and reserved `example.invalid`
URLs, and deletes its inputs. Its generated checksum-valid identifier is a
test value, not a claim about any actual entity. It exercises evidence briefs,
retry and idempotency with MemoryCRM: one simulated Lead, one task, zero real
writes/outreach. No AI provider or credentials are used. It does not prove
actual source accuracy, buyer need, or lawful contact use.

Optional operator-owned original evidence can be reviewed locally with
`python -m leadpilot.cli evidence-briefs --fns <size-only.json> --facts <facts.json> --contacts <channels.json>`.
Omit optional evidence to see unresolved demand/contact holds. Do not put real
inputs or stdout in Git. Existing SQLite route remains
`python -m leadpilot.cli --db <local.sqlite3> manager-briefs --today 2026-10-31`.
No copying of user research data is required for acceptance-demo.

## Validation evidence

Local final suite: **262 passed**, Python 3.12 host interpreter, explicit
`tests`, cache disabled, basetemp `.integration-tmp/pytest-final-3` (4.00s).
The merged baseline collected 250 tests; 12 new checks cover deterministic
demo, CLI no-DB behavior, stale/mismatched/missing channel state, FNS age,
strict authorization and size-only holds. Original tests were retained.
Initial runs failed in setup: the sibling RUN_FOLDER path was denied by the
filesystem sandbox; the next nested temp path lacked its parent. Creating the
ignored worktree temp parent resolved setup; neither failed run is a success.
Direct CLI execution also exposed the host's cp1252 stdout limitation; the two
new JSON routes now escape Unicode for portable output. The demo then completed
successfully and the full suite was rerun with the result above.

GitHub workflow targets main PRs/pushes, Python 3.12 on Ubuntu, read-only
repository permission, no secrets, explicit tests-only collection, disabled
pytest cache and a run-specific runner-temp directory. Linux execution has not
been performed here. **Issue #7 runner assignment/billing remains an external
blocker until a hosted runner is allocated and a job completes. No hosted
checks are claimed green.** Local tests cannot establish this.

## Missing proof and manager offer

Still required: original timely buyer-need evidence with exact entity identity;
manager source review and acceptance criteria; corporate contact ownership,
lawful-use basis and channel-specific permission/stop review; authorized
isolated Bitrix sandbox with approved preliminary Lead isolation; company-owned
credentials, field mappings, roles/automation isolation, existing-client,
duplicate and stop-list reads; permitted synthetic portal acceptance; completed
hosted CI. No customer data or secrets can be invented to close these gaps.
Qualified real leads, sales, contracts and MRR are not demonstrated.

The unsent [manager proposal](MVP_MANAGER_RESPONSE_DRAFT.md) covers Moscow/MO
small businesses and 1C maintenance, FNS/Trudvsem/public request sources and
limitations, staged estimates (2–3, 3–5, then 2–3 working days conditional on
access), and compensation options requiring agreement on rates, attribution,
rejection and payment. It remains a draft, not agreed commercial terms.
Telephony, bots and role-based RVP remuneration remain later v3.0 work.

Privacy: no real company/contact inputs were added; new demonstrator data are
synthetic. Existing `.gitignore` protections are preserved and isolated test
temp is additionally ignored. No bulk harvesting, outreach, customer API or
GigaChat calls were made. Historical acceptance counts in earlier reports refer
to their own snapshots and are superseded for this candidate by this run.
