# Issue #74 validation and delivery record

## Local evidence

- Python 3.12.10, isolated `.venv`, dependencies from requirements.txt.
- `python -m compileall -q leadpilot`: PASS.
- `python scripts/serviceit_offline_pytest.py`: **1206 passed, 1 skipped,
  26 subtests passed**. OS network connections denied before test collection;
  Windows asyncio internal loopback wakeup socket pair explicitly supported.
- The skip is the existing symlink safety test on Windows without symlink
  privilege; the test remains required/exercised on Linux CI. Two existing
  warnings concern Starlette TestClient deprecation and runpy import ordering.
- Forty-three worker tests exercise actual SQLite, HTTP MockTransport/provider,
  authenticated event intake, verified twelve-field write/readback, B2B nine-field
  preliminary HOLD, concurrent claims, restart recovery, stale-owner fencing,
  repeated events, cross-card model dedup, corporate gates, privacy, injection,
  wrong quotes, schema aliases/limits, manager edits, poll cursor, HTTP errors,
  canary isolation, health and offline default. Synthetic sources are fixtures,
  not connected platform acceptance.
- PowerShell 5.1 event-queue, secure-input and all-lead-score smoke: PASS.
- `git diff --check`: PASS.

## Independent finish audit

The repeat full offline suite passed after fixing a late-completion race:
an expired owner could previously replace crash recovery's UNCERTAIN or
NEEDS_REVIEW state. Completion now atomically requires a current lease and
RUNNING status, and the worker returns LEASE_LOST instead of claiming success
when its completion is fenced. Three regression cases exercise late write/model
completion and expiry during a successful POST. The durable reservation remains
untouched and a recovered ambiguous write is never retried.

A direct pytest invocation hit this launcher's restricted system temporary
directory during fixture setup; the repository offline runner uses a disposable
workspace directory and completed the entire suite successfully. This was an
environment setup error, not a passing direct pytest run.

Repeated Python compile and Windows PowerShell event-queue, secure-input and
batch-score smoke passed. Local staging and commit were retried and both failed
on the read-only .git/index.lock boundary. No protection was bypassed.

## Delivery limitations

Docker executable is not installed locally. Build, Compose validation and container
cold-start/receiver health smoke are configured in GitHub CI and must be checked
there. No local Docker pass is claimed.

Local `git add` and `git commit` failed because this launcher's filesystem profile
makes `.git` read-only (`index.lock: Permission denied`). No permission bypass or
alternate local git directory was attempted. GitHub connector `github_create_tree`
also returned **“MCP tool call requires approval, but approval policy is never”**.
Thus no remote commit, branch publication or draft PR was created. `gh` issue reads
also returned HTTP 401; the GitHub connector successfully read issues #74/#66 and
PR #75. The original worktree remains modified, explicitly disclosed, because its
index/HEAD cannot be advanced. No secrets, databases or private raw logs were
uploaded. A local `serviceit-universal-worker.patch` preserves the reviewed changes
for application in a checkout with normal Git write permission.

Requested branch: `codex/serviceit-all-cards-20261102-173516-c3d4a2`.
Unchanged local base commit: `0f4ebc6b34a40b19c54d669d696869bbe4a408b3`.
Commit/PR: **NOT_CREATED — filesystem/connector policy blocked**.

Development performed **zero production CRM or GigaChat calls**. No deploy,
event.bind, source permission changes, outreach, old ledger access or historical
card writer execution. Live corporate acceptance remains **LIVE_UNVERIFIED**.

## Remaining acceptance boundary

This is a working gated queue/worker/writer implementation, not completion of
unattended verified qualification. The supplied corporate verifier adapter uses
trusted mounted approval evidence; automatic generation from approved source,
FNS, customer/dedup/stop/legal systems still requires authorized integration.
No approval means terminal HOLD with no model/write. See the deployment runbook
for the exact adapter contract and component/source next actions.

Local ignored logs: `.integration-tmp/offline-release.txt`,
`.integration-tmp/offline-suite*.txt`, `.integration-tmp/full-suite.txt`.
These contain test diagnostics only and are not uploaded.
