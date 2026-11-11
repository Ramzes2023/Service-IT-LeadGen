"""Existing registered read-only workflow keeps original job and adds V16 audited job."""
from pathlib import Path

PATH = Path(".github/workflows/serviceit-vps-b2b-four-public-crossportal-readonly.yml")


def test_original_public_four_audit_unaffected_and_v16_guarded():
    t=PATH.read_text("utf8")
    assert "  audit:\n" in t and "  v16_audit:\n" in t
    assert "SERVICEIT_B2B_FOUR_PUBLIC_CROSSPORTAL_READONLY_368" in t
    assert "SERVICEIT_B2B_V16_FOUR_REAL_PREVIEW_READONLY_368" in t
    assert "github.actor == 'Ramzes2023'" in t
    assert "github.event.comment.user.login == 'Ramzes2023'" in t
    assert t.count("github.event.issue.number == 368") == 3
    assert "serviceit_vps_b2b_crossportal_four_v16_preview.py" in t
    assert "StrictHostKeyChecking=yes" in t
    assert "UserKnownHostsFile=" in t
    assert "docker exec -i serviceit-leadgen-vps-coordinator-1 python -B -" in t
    assert "crm.item.update" not in t
