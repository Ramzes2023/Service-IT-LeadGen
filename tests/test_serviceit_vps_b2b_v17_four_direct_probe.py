"""Regression contracts for the additive read-only four-card Kontur candidate audit."""
import ast
from pathlib import Path

SCRIPT = Path("ops/serviceit_vps_b2b_kontur_four_direct_preview.py")
FLOW = Path(".github/workflows/serviceit-vps-b2b-four-public-crossportal-readonly.yml")


def test_probe_readonly_signed_exact_four_and_same_v15_snapshot():
    source = SCRIPT.read_text(encoding="utf8")
    ast.parse(source)
    assert 'SET TRANSACTION READ ONLY' in source
    assert 'V15_SNAPSHOT_CRM_DRIFT' in source
    assert 'SIGNED_MATCHING_CARDS' in source
    assert 'V11_WRITES_INFLIGHT' in source
    assert 'LIVE_EXACT_CODE_SHA' in source
    assert '("4", "459")' in source
    assert "from_public_page(" in source
    assert 'mirror_host="zakupki.kontur.ru"' in source
    assert 'READY_TO_APPEND' in source
    assert 'KONTUR_' in source
    for prohibited in (
        'crm.item.update', 'crm.item.add', 'tasks.task.add',
        'investigate_routerai(', 'claim_one(', 'INSERT INTO serviceit_',
        'UPDATE serviceit_', 'requests.post(', 'client.post('
    ):
        assert prohibited not in source


def test_existing_registered_workflow_retains_two_jobs_and_adds_owner_only_one():
    flow = FLOW.read_text(encoding="utf8")
    assert "  audit:\n" in flow
    assert "  v16_audit:\n" in flow
    assert "  v17_direct_probe:\n" in flow
    assert flow.count("github.event.issue.number == 368") == 3
    assert "SERVICEIT_B2B_V17_FOUR_KONTUR_DIRECT_READONLY_368" in flow
    assert "github.actor == 'Ramzes2023'" in flow
    assert "github.event.comment.user.login == 'Ramzes2023'" in flow
    assert "github.ref == 'refs/heads/main'" in flow
    assert "environment: serviceit-vps-readonly" in flow
    assert 'e08f1dca84548cf782060e08f19e9704b5a0d5dd' in flow
    assert 'serviceit_vps_b2b_kontur_four_direct_preview.py' in flow
    assert 'StrictHostKeyChecking=yes' in flow
    assert 'UserKnownHostsFile=' in flow
    assert 'READONLY_FINISHED=YES' in flow
    assert 'SIGNED_MATCHING_CARDS=4' in flow
    assert 'crm.item.update' not in flow
