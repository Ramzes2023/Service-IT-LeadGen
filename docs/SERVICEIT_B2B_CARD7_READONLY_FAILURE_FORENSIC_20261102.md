# PR #78 live read-only BLOCKED: diagnostic without CRM writes

**Input actually observed:** 2026-11-02 the owner ran `scripts/RUN_SERVICEIT_B2B_CARD7_PRELIMINARY_BACKFILL.ps1 -InspectLive` (NO `-ApplyOnce`) on item #7 and received `B2B_CARD7_BACKFILL_BLOCKED_OR_UNCERTAIN`. There is no evidence of a write: the code's inspect path has no POST to `crm.item.update` and never calls `_reserve_once`. The generic error wording `crm_writes=UNCONFIRMED` is misleading specifically in read-only mode.

No live cause was exposed. Suspects that need evidence:
- exact historical source/hold field mismatch (title, organizer, source URL, stage, etc.)
- AI or score field already has nonempty data
- the nine AI metadata keys or `isMultiple` differ from the strict mock. Previous all-lead schema preflight accepted `None` for `isMultiple`, PR #78 required `False` exactly
- immutable ledger exists/corrupt or REST response/schema rejected.
None of these hypotheses is confirmed before running the forensic inspector.

## New dedicated source-only forensic command

`leadpilot/serviceit_b2b_card7_forensic_readonly.py` and
`scripts/RUN_SERVICEIT_B2B_CARD7_FORENSIC_READONLY.ps1`:

- **Cannot write to CRM by design**. Only `crm.item.get(entityTypeId=1062,id=7,useOriginalUfNames=Y)` and `crm.item.fields(entityTypeId=1062,useOriginalUfNames=Y)`; POST is used only for these read REST endpoints. No `crm.item.update`, `crm.item.add`, `event.bind`, GigaChat, tasks or contacts. No script switches `-Apply` at all.
- Strict check that remote item is exactly ID=7, entityTypeId=1062, categoryId=21.
- Compare protected fields against the exact user-authored Issue #66 notice pinned as fixture. Print **only field codes** with mismatches, not their values.
- Show for each of nine AI fields whether `NULL`, `EMPTY`, `POPULATED`, `FIELD_NOT_RETURNED`; likewise for Score, badge, version.
- Report **metadata reason** for each AI field; particularly `MULTIPLICITY_NOT_EXPLICIT_FALSE` if `isMultiple` missing, without altering any write guard or pretending it's safe.
- Reads separate PR78 SQLite ledger only `mode=ro` if present. If not present, does not create any files. Never resets two unrelated old badge ledgers `UNCERTAIN`.
- Success may still report issues; that is a diagnostic result, not authorization to write.
- CLI error always `crm_writes=0`; no raw secret/API response, company, organizer, URL, title, email or tender contents in stdout.

## Windows command after green CI and merged pinned SHA

```powershell
$Repo = "$env:USERPROFILE\Service_IT_GitHub_Repo\Service-IT-LeadGen"
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Script = Join-Path $Worktree 'scripts\RUN_SERVICEIT_B2B_CARD7_FORENSIC_READONLY.ps1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script -PythonPath $Python
```

Use the PR merge commit in a separate pinned worktree before running. Only enter corporate REST webhook in hidden SecureString prompt. Do NOT paste webhook in chat/GitHub.

**No `-ApplyOnce` until forensic result and scope/metadata reviewed.** Old PR78 backfill should not be retried or its ledger deleted. If mismatch found, repair the comparison contract based on actual metadata/source provenance with new mock tests; if the ledger is uncertain, reconcile only read-only.

Ultimately ticket #74 still aims to fill ALL new cards automatically from every source. PR78 backfill is a single old B2B-Center card remedy, not generic GigaChat/scoring production.
