# Local SQLite only: PR78 failed before reservation, PR81 passed all prechecks

## Confirmed live results (owner, 2026-11-02)

- PR80 post-apply read-only: item #7 scope verified; **all nine AI fields EMPTY** in both Bitrix original and camelCase GETs; fixed original ledger `NOT_RESERVED`; original source and stage unchanged; Score/version/badge EMPTY.
- PR81 exact replay: `first_snapshot=PASS`, `schema=PASS`, `ledger=PASS`, `ledger_not_reserved=PASS`, `second_snapshot=PASS`, `entire_item_equal=PASS`, `pinned_fields_stable=PASS`. No changing fields. `ledger_parent_exists=false`; closest existing ancestor `os.access(R|W|X)=true`.

**Inference**, not proven root cause: if no one removed/moved/reset the ledger, the prior PR78 `-ApplyOnce` stopped before the `_reserve_once` transaction was persisted and did not reach `crm.item.update`. The last untested local step is creation of `%LOCALAPPDATA%/ServiceIT/Existing-B2B-Hold-AI` and SQLite reservation transaction. `os.access` on an ancestor is NOT proof that this exact nested folder and SQLite are writable.

## New PR local probe

- `leadpilot/serviceit_card7_sqlite_local_probe.py`: **NO network, NO Bitrix webhook, NO GigaChat and NO historical CRM write**.
- Local side effect: if necessary, creates exactly the parent directory of the original ledger, then a randomly named **temporary SQLite sibling** in that same directory. Uses exclusive create, full synchronous transaction, test table and synthetic row, exact readback, closes DB and removes temporary file and its sidecars. It does **not** create, open, remove, migrate or edit `backfill-v1.sqlite3`.
- If real journal file or symlink is present, refuses to run rather than risk ambiguity.
- Output is sanitized `LOCAL_SQLITE_PROBE_PASS` or `LOCAL_SQLITE_PROBE_BLOCKED`, `stopped_at` one of MKDIR/CREATE_TEMP_FILE/SQLITE_OPEN/SQLITE_CREATE_AND_COMMIT/SQLITE_READBACK, booleans for cleanup and local directory, and `crm_writes=0`.
- Windows PowerShell5.1 `scripts/RUN_SERVICEIT_CARD7_LOCAL_SQLITE_PROBE.ps1` never prompts for webhook. Does not accept `-ApplyOnce`. Tests use synthetic tmp folders, Windows real SQLite CI and no-customer mocks.

## Interpretation

- `LOCAL_SQLITE_PROBE_BLOCKED`: exact stage indicates filesystem/SQLite permission/config fault. Resolve root cause without re-running CRM write.
- `LOCAL_SQLITE_PROBE_PASS`: local SQLite works under current conditions. Previous error may have been transient or code-specific; **does not prove that a new attempt to write is safe**. Don't repeat historical `-ApplyOnce`. Use a separately designed generalized writer with verified state and authoritative owner approval.
- `LOCAL_SQLITE_PROBE_NOT_RUN_EXISTING_LEDGER`: original ledger exists. Do not delete it; use PR80 read-only reconciliation.

The ultimate project goal is Issue #74: all new source cards automatically source-checked, scored, sent through approved GigaChat and written back with validated nine AI fields. This local probe is strictly incident analysis and must not be misrepresented as completion of event/GigaChat production deployment.
