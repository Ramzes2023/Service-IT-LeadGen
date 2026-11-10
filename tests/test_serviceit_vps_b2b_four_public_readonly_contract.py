"""Owner-only production-readonly forensic: no model invocation or CRM mutation."""
from pathlib import Path


def test_b2b_four_real_public_research_audit_is_readonly():
    src=Path(".github/workflows/serviceit-vps-b2b-four-source-coverage-readonly.yml").read_text("utf-8")
    assert "SERVICEIT_B2B_FOUR_PUBLIC_SOURCES_READONLY_314" in src
    assert "github.actor == 'Ramzes2023'" in src
    assert 'github.event.issue.number == 314' in src
    assert "StrictHostKeyChecking=yes" in src and "ssh-keygen -F" in src
    assert "serviceit_vps_b2b_deep_research import (" in src
    assert "source_findings(item,notice,env,client)" in src
    assert "SET TRANSACTION READ ONLY" in src
    assert "crm.item.get" in src
    assert "crm.item.update" not in src
    assert "crm.item.add" not in src
    assert "routerai" not in src.casefold()
    assert "investigate_routerai" not in src
    assert "GIGACHAT_CREDENTIALS" not in src
    assert "FOUR_PUBLIC_COMPLETE=YES" in src
