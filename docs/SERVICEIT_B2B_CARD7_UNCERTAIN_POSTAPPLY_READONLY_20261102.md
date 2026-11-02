# PR #78 B2B-Center card #7 – no-write reconciliation AFTER uncertain apply

## Actual user report

On 2 November 2026 owner ran the existing PR78 script with explicit
`-InspectLive -ApplyOnce -ApproveExactItem7`. The script emitted only
`B2B_CARD7_BACKFILL_BLOCKED_OR_UNCERTAIN` and
`crm_writes=UNCONFIRMED`. **The error does not prove the POST succeeded
or failed. Do not repeat the apply call, delete/change the ledger, or run
a different StateDir to bypass exactly-once controls.**

The prior PR79 forensic inspection (BEFORE this apply) reported
`issues=[]`, `backfill_ledger_state=NOT_RESERVED`,
all nine AI schemas writable and no previous data. This confirms the
previously empty state, **not** the state after the write attempt.

## New strictly read-only post-write inspection

`leadpilot/serviceit_b2b_card7_postapply_readonly.py` and
`scripts/RUN_SERVICEIT_B2B_CARD7_POSTAPPLY_READONLY.ps1`.

Reads only:
1. **Exactly the same** fixed historical local SQLite ledger
   `%LOCALAPPDATA%\ServiceIT\Existing-B2B-Hold-AI\backfill-v1.sqlite3`
   using SQLite `mode=ro`, `PRAGMA query_only=ON`. No file creation,
   reservation, mark-verified, or state migration.
2. `crm.item.get` for exact item **id=7 / entityTypeId=1062 /
   categoryId=21** with `useOriginalUfNames=Y`.
3. Same exact GET with `useOriginalUfNames=N` (camelCase).
4. `crm.item.fields` with `useOriginalUfNames=N` to determine the
   exact aliases for the nine userfields. No guesses about API field keys.

It recomputes the nine expected text values from the pinned user-authored
GitHub Issue #66 original notice and compares exact readback for each
of those nine fields **in both API naming modes**. It also compares
protected existing source/hold fields and reports whether Score/version/badge
remain empty. No raw field values, original email text, webhook URLs,
company names, tender URLs, or reports are shown in stdout.

One of these read-only decisions can appear:

- `LIVE_NINE_FIELDS_MATCH_LEDGER_UNCERTAIN` — all nine expected values
  visible in both formats, write effect confirmed even though ledger
  remains UNCERTAIN. The script does not modify the journal.
- `LIVE_NINE_FIELDS_EMPTY_LEDGER_UNCERTAIN` — ledger reserved (POST
  possible), all nine values still empty. Do **not** retry a POST.
- `LIVE_NINE_FIELDS_EMPTY_NO_RESERVATION` — fixed ledger not reserved
  and values empty. That prior PR78 code did not reach its reservation
  step, assuming no journal was deleted/moved.
- `PARTIAL_OR_ALIAS_CONFLICT_READONLY_REVIEW` — some values present,
  altered, or one API mode differs. Do **not** overwrite them.
- `LEDGER_DIGEST_MISMATCH_BLOCKED` or `LEDGER_FORENSIC_REQUIRED` —
  fixed one-shot journal must be investigated, not reset.
- `LIVE_NINE_FIELDS_MATCH_VERIFIED_LEDGER` — journal VERIFIED and nine
  values match; no follow-up write.

Even a favourable result sets
`safe_to_repeat_previous_apply=false` and
`live_writer_authorized=false` because this inspection is **never
authorization to run a new write**.

## Next owner action after PR passes CI

Execute the new operator from a pinned SHA/clean worktree, passing the
approved corporate Bitrix24 webhook **only in a hidden prompt**.
This operator has no `-ApplyOnce` option. In GitHub CI, test synthetic
empty/exact/partial Y/N readbacks, matching and contradictory fixed
SQLite ledger, bad item ID and API-method allowlist. No real Bitrix
request or customer record change occurs in GitHub CI.

Once the actual postapply status is known, review results before any
additional work. Longer-term #74 still requires a deployed and verified
source-validation, GigaChat and CRM writer for **all** sources. Fixing one
legacy item is not completion of that automation.
