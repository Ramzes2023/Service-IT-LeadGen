# B2B item #7: why PR78 stopped before reservation — no-write preflight

User ran `-InspectLive -ApplyOnce -ApproveExactItem7` with the original
PR78 operator, which exited `B2B_CARD7_BACKFILL_BLOCKED_OR_UNCERTAIN`.

**PR80 live result**:
- `item_id=7`, `scope_verified=true`;
- nine of nine expected AI fields EMPTY with BOTH original and camelCase
  Bitrix field aliases; all original HOLD/source fields unchanged;
- `backfill_ledger_state=NOT_RESERVED`, no matching ledger digest.
- Score, version, and badge EMPTY; no prior update can be confirmed.

Given the current verified fixed ledger path has NOT been deleted, renamed,
moved or reset, PR78 did **not reach** `_reserve_once`, which is called
before its only `crm.item.update`. Consequently the original PR78 code
did not send a write. **Do not retry the original command**; identify the
pre-reservation failure before considering any replacement strategy.

## Suspects (not yet confirmed)
- The PR78 implementation compares **every** returned Bitrix item field from
  the first and second GET, including server-managed `updatedTime` or
  other dynamic metadata. Any changing metadata blocks with
  `CRM_ITEM_CHANGED_BETWEEN_READS`, even if all protected source and AI
  fields stayed unchanged. This is a hypothesis.
- An intermittent second GET failed.
- Local filesystem permissions prevented journal directory creation
  before the reservation. This is possible even if two GETs passed.

## New no-write exact replay
`leadpilot/serviceit_b2b_card7_prewrite_stage_readonly.py`
and `scripts/RUN_SERVICEIT_B2B_CARD7_PREWRITE_STAGE_READONLY.ps1`:

- Uses the same validated original Issue #66 fixture and **same PR78
  protected-field/schema check functions**.
- Makes EXACTLY three allowlisted read-only REST calls in the original
  order: `crm.item.get` ID7/Y, `crm.item.fields` entity1062/Y,
  `crm.item.get` ID7/Y again. Does not call a writer or reserve a ledger.
- Checks `first_snapshot`, `schema`, immutable ledger state via
  SQLite URI `mode=ro`, `second_snapshot`, `entire_item_equal` and
  `pinned_fields_stable`.
- If only a **server-generated metadata field** changes, outputs just
  `changing_field_codes`, not its value. If a protected source field
  changes, also reports `PINNED_FIELD_CHANGED`.
- Reports whether the target journal parent directory exists and whether
  the closest existing parent appears writable via `os.access`. This
  is diagnostic only and **not** a guarantee that a future reservation
  will succeed.
- Output has only fixed stage reason codes and sanitized field names:
  never source text, contact details, URLs, original report or webhook.
- No SQLite files/dirs created or mutated; no GigaChat, CRM update, task,
  new card, old ledger reset or `ApplyOnce` option.

Green CI covers the stable case, synthetic changed updatedTime,
protected-field mutation, existing UNCERTAIN ledger, HTTP method allowlist
and the Windows PowerShell 5.1 hidden prompt.

**Result is a diagnostic, not authorization to send another update**. The
wider goal remains GitHub Issue #74: real source-validated GigaChat and Score
with safe end-to-end event-driven upsert for all cards. Single-card backfill
must not distract from the all-sources system.
