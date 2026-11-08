"""Read-only VPS acceptance audit: no production mutations and sanitized totals."""
import ast
from pathlib import Path

FILE = Path(".github/workflows/serviceit-vps-live-acceptance-readonly-audit.yml")
WF = FILE.read_text("utf8")


def _remote_script():
    start = WF.index("<<'PY'\n") + len("<<'PY'\n")
    end = WF.index("\n          PY", start)
    lines = WF[start:end].splitlines()
    assert lines and all(line.startswith("          ") or not line for line in lines)
    return "\n".join(line[10:] for line in lines)


def test_remote_python_is_syntactically_valid():
    ast.parse(_remote_script(), filename=str(FILE))


def test_gate_uses_explicit_owner_and_secure_ssh_host_identity():
    assert "workflow_dispatch:" in WF
    assert "github.actor == 'Ramzes2023'" in WF
    assert "github.ref == 'refs/heads/main'" in WF
    assert "AUDIT_NATIVE_LIVE_CRM_READONLY_1062_21" in WF
    assert "environment: serviceit-vps-readonly" in WF
    assert "serviceit-vps-native-shadow-readonly" in WF
    assert "-o StrictHostKeyChecking=yes" in WF
    assert "-o UserKnownHostsFile=" in WF
    assert "-o BatchMode=yes" in WF
    assert 'VPS_USER" != root' in WF
    assert "CLOUD_OLD_WRITER_FENCE=" in WF


def test_no_crm_mutations_or_live_container_restarts():
    for forbidden in (
        '"up",', '"restart",', '"build",', '"create",', '"rm",',
        "docker compose up", "crm.item.update", "tasks.task.add",
        "SERVICEIT_VPS_WRITER_MODE=LIVE", "migrate(",
        "docker run", "docker cp", "docker stop", "docker tag",
    ):
        assert forbidden not in WF
    assert 'cur.execute("SET TRANSACTION READ ONLY")' in WF
    assert "FROM serviceit_vps_new_cards" in WF
    assert "FROM serviceit_vps_native_receipts" in WF
    assert 'leadpilot.serviceit_vps_native_observer' in WF
    assert '"--once"' in WF
    assert "CRM_WRITES_BY_AUDIT=0;SOURCES_CHANGED=NO;LIVE_RESTARTS=0" in WF


def test_audit_is_sanitized_and_reports_important_modules():
    assert "VPS_COORDINATOR_HEALTH=" not in WF or "VPS_" in WF
    for name in (
        "NATIVE_KANBAN_TITLE_ENABLED",
        "NATIVE_OLGA_TASK_ENABLED",
        "NATIVE_OLGA_ID_605",
        "NATIVE_OWN_STOP_ENABLED",
        "NATIVE_CRM_DUPLICATE_ENABLED",
        "NATIVE_PROVISIONAL_SCORE_ENABLED",
        "NATIVE_FREE_MSP_SCORE_ENABLED",
        "NATIVE_POSTCALL_ENABLED",
        "NATIVE_FINANCE_ENABLED",
        "NATIVE_FNS_CRM_RESULT_ENABLED",
        "BRAVE_LIVE_KEY_MOUNT=",
        "PG_QUEUE_",
        "PG_RECEIPTS_",
        "BITRIX_READONLY_AGGREGATE=",
        "SHARED_KANBAN_UI_VIEW=NOT_VERIFIED_BY_SERVER",
        "EXACT_NEW_CARD_NINE_FIELDS_TASK_KANBAN_E2E=NOT_YET_VERIFIED",
    ):
        assert name in WF
    assert "print(proc.stdout)" not in WF
    assert "print(proc.stderr)" not in WF
    assert "print(observer)" not in WF
    assert "print(raw)" not in WF
    assert "print(setting(" not in WF
