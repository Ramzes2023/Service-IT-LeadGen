"""No-write production next B2B card audit contract."""
import ast
import contextlib
import importlib.util
import io
from pathlib import Path

import pytest


AUDIT = Path("ops/serviceit_vps_b2b_future_card_readiness.py")
WORKFLOW = Path(".github/workflows/serviceit-vps-b2b-future-card-readiness.yml")


def module():
    spec=importlib.util.spec_from_file_location("future_b2b_audit",AUDIT)
    object_=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(object_)
    return object_


def test_live_audit_code_parses_and_emits_only_sanitized_status():
    ast.parse(AUDIT.read_text("utf8"))
    fn=module().say
    out=io.StringIO()
    with contextlib.redirect_stdout(out):
        fn("READINESS","YES")
        fn("REAL_NEW_B2B_E2E","NOT_OBSERVED")
        fn("V11_VERIFIED_CARDS",4)
    assert out.getvalue().splitlines()==[
        "FUTURE_B2B_READINESS=YES",
        "FUTURE_B2B_REAL_NEW_B2B_E2E=NOT_OBSERVED",
        "FUTURE_B2B_V11_VERIFIED_CARDS=4",
    ]
    with pytest.raises(ValueError):
        fn("USER_EMAIL","someone@site.test")
    with pytest.raises(ValueError):
        fn("unsafe-attribute","YES")


def test_no_crm_mutation_or_external_ai_calls_anywhere():
    content=AUDIT.read_text("utf8")
    for forbidden in ("crm.item.update", "crm.item.add", "tasks.task.add",
                      "crm.contact.add", "investigate_routerai(client",
                      "source_findings(", "brave_mirror_search(client",
                      "insert_new(", "claim_one(", "finalize(", "mark_success("):
        assert forbidden not in content
    assert "preview_only=True" in content
    assert "serviceit_vps_poll_state" in content
    assert "serviceit_vps_b2b_inbox" in content
    assert "serviceit_vps_b2b_dossier_v11_receipts" in content
    assert "serviceit_vps_b2b_dossier_v12_continuity_receipts" in content
    assert "NEW_CARD_V11_STYLE_IN_MEMORY" in content
    assert "REAL_NEW_B2B_E2E" in content
    assert "POLL_MODULES_LIVE_SHA" in content
    assert '"BITRIX_POLL_FAILURE_"+name' in content
    assert "POLL_CALIBRATION_POSITIVE_MISSING" in content


def test_workflow_owner_guard_and_ssh_host_pin():
    content=WORKFLOW.read_text("utf8")
    assert "SERVICEIT_B2B_FUTURE_CARD_READINESS_READONLY_368" in content
    assert "github.actor == 'Ramzes2023'" in content
    assert "github.event.issue.number == 368" in content
    assert "!github.event.issue.pull_request" in content
    assert "StrictHostKeyChecking=yes" in content
    assert "UserKnownHostsFile=" in content
    assert "environment: serviceit-vps-readonly" in content
    assert "workflow_dispatch:" not in content
    assert "FUTURE_B2B_READINESS=YES" in content


def test_live_source_code_intends_future_card_without_duplicate_billing():
    coordinator=Path("leadpilot/serviceit_vps_worker.py").read_text("utf8")
    assert "INTERVAL_SECONDS = 300" in coordinator
    assert "from .serviceit_vps_b2b_dossier_v11_worker import worker_once" in coordinator
    assert "from .serviceit_vps_b2b_dossier_v10_worker import worker_once" not in coordinator
    v11=Path("leadpilot/serviceit_vps_b2b_dossier_v11_worker.py").read_text("utf8")
    assert "NOT EXISTS" in v11
    assert "B2B_RESEARCH_V4" in v11
    assert "before_fields JSONB NOT NULL" in v11
    assert "EXACT_NINE_READBACK_NOT_VERIFIED" in v11
