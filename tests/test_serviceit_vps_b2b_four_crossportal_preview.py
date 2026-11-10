"""Source-bounded B2B four-card preview must never write CRM or use AI."""
from pathlib import Path

SCRIPT=Path("ops/serviceit_vps_b2b_crossportal_four_preview.py").read_text("utf8")
WORKFLOW=Path(".github/workflows/serviceit-vps-b2b-four-public-crossportal-readonly.yml").read_text("utf8")


def test_four_original_pg_receipts_only_and_exact_crm_scope():
    assert "serviceit_vps_b2b_dossier_v11_receipts" in SCRIPT
    assert "WHERE b.state='VERIFIED'" in SCRIPT
    assert "v.status='VERIFIED'" in SCRIPT
    assert "len(records) != 4" in SCRIPT
    assert "validate_identity(item, card)" in SCRIPT
    assert "safe_notice(notice, card, source_id, item)" in SCRIPT
    assert "SET TRANSACTION READ ONLY" in SCRIPT
    assert '"entityTypeId":1062' in SCRIPT
    assert "source_findings(item, notice, exact, client)" in SCRIPT
    assert "source_only_investigation(findings)" in SCRIPT
    assert "checked_public_contacts(findings,buyer,inn)" in SCRIPT


def test_bounded_info_only_and_no_private_data_leakage():
    assert "CROSSPORTAL_FOUR_" in SCRIPT
    assert "CARD_" in SCRIPT
    assert "print(\"CROSSPORTAL_FOUR_\"" in SCRIPT
    assert "sys.stdout.write" not in SCRIPT
    assert "READONLY_FINISHED" in SCRIPT
    for prohibited in (
        "crm.item.update","crm.item.add","tasks.task.add",
        "crm.timeline.comment.add","conn.commit(",
        "investigate_routerai(","client.post(","send_email(",
    ):
        assert prohibited not in SCRIPT
    assert "LIVE_EXACT_CODE_SHA" in SCRIPT
    assert "SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED" in SCRIPT


def test_owner_only_readonly_workflow():
    assert "github.actor == 'Ramzes2023'" in WORKFLOW
    assert "github.event.issue.number == 368" in WORKFLOW
    assert "SERVICEIT_B2B_FOUR_PUBLIC_CROSSPORTAL_READONLY_368" in WORKFLOW
    assert "workflow_dispatch" not in WORKFLOW
    assert "environment: serviceit-vps-readonly" in WORKFLOW
    assert "StrictHostKeyChecking=yes" in WORKFLOW
    assert "UserKnownHostsFile=" in WORKFLOW
    assert "serviceit_vps_b2b_crossportal_four_preview.py" in WORKFLOW
    assert "docker exec -i serviceit-leadgen-vps-coordinator-1 python -B -" in WORKFLOW
    assert "crm.item.update" not in WORKFLOW
