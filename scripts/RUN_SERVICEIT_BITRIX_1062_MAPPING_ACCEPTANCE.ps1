# Offline acceptance for synthetic handoff mapping and manager decision bridge.
# No webhook prompt, CRM REST, live writes or sources.
[CmdletBinding()]
param([string]$PythonPath = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $Repo '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'SERVICEIT_MAPPING_EXISTING_VENV_REQUIRED'
}
Push-Location -LiteralPath $Repo
try {
    Write-Output 'SERVICEIT_1062_MAPPING_AND_MANAGER=START; CRM_WRITES=NO'
    # Validate syntax before pytest collection: prevents cascading import errors.
    & $PythonPath -m compileall -q leadpilot tests
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_1062_SYNTAX_PREFLIGHT_FAILED' }
    Write-Output 'SERVICEIT_1062_SYNTAX_PREFLIGHT=PASS'
    & $PythonPath -m pytest -q -p no:cacheprovider tests/test_bitrix_handoff_dryrun.py tests/test_bitrix_1062_mapping.py tests/test_bitrix_1062_manager_bridge.py tests/test_bitrix_1062_outbox.py tests/test_bitrix_1062_reconcile.py tests/test_bitrix_1062_rest_read.py tests/test_bitrix_1062_synthetic_add.py tests/test_bitrix_1062_synthetic_delivery.py tests/test_bitrix_synthetic_text_guard.py tests/test_bitrix_1062_persisted_review.py
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_1062_MANAGER_TESTS_FAILED' }
    & $PythonPath -m py_compile leadpilot/bitrix_1062_mapping.py leadpilot/bitrix_1062_manager_bridge.py leadpilot/bitrix_1062_outbox.py leadpilot/bitrix_1062_reconcile.py leadpilot/bitrix_1062_rest_read.py leadpilot/bitrix_1062_synthetic_add.py leadpilot/bitrix_1062_synthetic_delivery.py leadpilot/bitrix_synthetic_text_guard.py leadpilot/bitrix_1062_persisted_review.py
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_1062_MANAGER_COMPILE_FAILED' }
    $Check = @'
from datetime import date
from leadpilot.bitrix_handoff_dryrun import build_offline_handoff, synthetic_fixture
from leadpilot.bitrix_1062_mapping import build_synthetic_1062_plan
from leadpilot.bitrix_1062_manager_bridge import build_synthetic_1062_manager_plan
now = date.today()
report = build_offline_handoff(synthetic_fixture(now), today=now)
mapped = build_synthetic_1062_plan(report, today=now)
if mapped["operation_count"] != 2 or mapped["field_count"] != 16 or mapped["crm_writes"] != "NO":
    raise RuntimeError("MAPPING_ACCEPTANCE_INVARIANT_FAILED")
key = mapped["requests_not_sent"][0]["idempotency_key"]
review = dict(idempotency_key=key, event_id="DEMO-REVIEW-ACCEPT01",
              decision="needs_research", reviewer_role="manager",
              reviewer_label="DEMO-MANAGER-1",
              reason="Синтетическая проверка, не одобрение клиента",
              reviewed_on=now.isoformat())
manager = build_synthetic_1062_manager_plan(report, [review], today=now)
assert manager["reviewed_count"] == 1 and manager["unreviewed_count"] == 1
assert manager["crm_writes"] == "NO"
assert manager["actions_not_sent"][0]["params_template_without_item_id"]["fields"]["stageId"] == "DT1062_21:PREPARATION"
print("SERVICEIT_1062_MANAGER_PREVIEW=PASS; SYNTHETIC_SIGNALS=2; REVIEWED=1; UF_FIELDS=16; REST_CALLS=0")
from pathlib import Path
from tempfile import TemporaryDirectory
from leadpilot.bitrix_1062_outbox import initialize, enqueue_synthetic_plan, claim_next, mark_uncertain, overview
from leadpilot.bitrix_1062_reconcile import reconcile_uncertain_key
with TemporaryDirectory(prefix="serviceit-1062-outbox-") as scratch:
    db = Path(scratch) / "outbox.sqlite3"
    initialize(db)
    result = enqueue_synthetic_plan(db, mapped)
    assert result["queued"] == 2
    assert enqueue_synthetic_plan(db, mapped)["duplicate_skipped"] == 2
    claimed = claim_next(db)
    assert claimed and claimed["crm_writes"] == "NO"
    mark_uncertain(db, claimed["idempotency_key"], reason_code="SIMULATED_TIMEOUT")
    fields = claimed["params_preview_only"]["fields"]
    item = dict(fields, id=791, entityTypeId=1062, categoryId=21, stageId="DT1062_21:NEW",
                title=fields["title"],
                UF_CRM_13_SVCIT_IDEMPOTENCY_KEY=claimed["idempotency_key"])
    def fake_read_only(method, params):
        assert method in ("crm.item.list", "crm.item.get")
        assert params["entityTypeId"] == 1062
        return {"items": [item]} if method == "crm.item.list" else {"item": item}
    assert reconcile_uncertain_key(db, fake_read_only, claimed["idempotency_key"])["status"] == "RECONCILED_REMOTE_READBACK"
    assert overview(db)["states"]["VERIFIED"] == 1
    assert overview(db)["crm_writes"] == "NO"
print("SERVICEIT_1062_OUTBOX_PREVIEW=PASS; QUEUED=2; DEDUPE=YES; UNCERTAIN_RECOVERY=READ_ONLY; REST_CALLS=0")
'@
    $Check | & $PythonPath -
    if ($LASTEXITCODE -ne 0) { throw 'SERVICEIT_1062_MANAGER_PREVIEW_FAILED' }
    Write-Output 'SERVICEIT_1062_PERSISTED_MANAGER_BINDING=PASS_OFFLINE_ONLY; REAL_BITRIX_WRITES=NO; SOURCES=NO'
} finally {
    Pop-Location
}
