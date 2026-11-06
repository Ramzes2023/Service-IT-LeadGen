"""Trusted FNS collector completion invokes CRM writer only on exact success.

No real Bitrix or FNS calls: event-only synthetic assertions.
"""
import copy
import pytest

from leadpilot.serviceit_fns_msp_crm_result_writer import (
    OFFICIAL_WORKFLOW, verified_official_workflow_run, MspWriteBlocked,
)
from leadpilot.serviceit_bitrix_dispatch_worker import REPOSITORY

GOOD = {
    "action": "completed",
    "workflow_run": {
        "name": OFFICIAL_WORKFLOW,
        "conclusion": "success",
        "event": "schedule",
        "head_branch": "main",
        "head_repository": {"full_name": REPOSITORY},
    },
}


def test_official_fns_main_completion_authorizes_receipt_sync():
    assert verified_official_workflow_run(copy.deepcopy(GOOD)) is True


@pytest.mark.parametrize("field,value", [
    ("name", "attacker-simulated-FNS"),
    ("conclusion", "failure"),
    ("conclusion", "cancelled"),
    ("event", "pull_request"),
    ("head_branch", "unreviewed"),
    ("head_repository", {"full_name": "attacker/other"}),
])
def test_untrusted_fns_run_never_authorizes_card_write(field, value):
    data = copy.deepcopy(GOOD)
    data["workflow_run"][field] = value
    with pytest.raises(MspWriteBlocked, match="COMPLETION_NOT_AUTHORIZED"):
        verified_official_workflow_run(data)


def test_missing_event_identity_rejected():
    with pytest.raises(MspWriteBlocked):
        verified_official_workflow_run({"action": "completed"})


def test_fns_workflow_file_listens_to_collector_with_no_global_crm_rights():
    from pathlib import Path
    doc = Path(".github/workflows/serviceit-fns-msp-crm-result.yml").read_text("utf-8")
    assert "workflow_run:" in doc
    assert OFFICIAL_WORKFLOW in doc
    assert "github.event.workflow_run.conclusion == 'success'" in doc
    assert "SERVICEIT_FNS_MSP_RESULT_WRITE" in doc
