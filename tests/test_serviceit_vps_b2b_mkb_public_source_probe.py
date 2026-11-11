"""Ensure live MKB probe cannot update CRM or bypass public access guards."""
import ast
from pathlib import Path

SCRIPT=Path("ops/serviceit_vps_b2b_mkb_4675475_source_probe.py")
FLOW=Path(".github/workflows/serviceit-vps-b2b-four-public-crossportal-readonly.yml")

def test_mkb_source_probe_is_strictly_public_and_readonly():
    t=SCRIPT.read_text("utf8")
    ast.parse(t)
    assert "4675475_40" in t
    assert "EXPECTED_LIVE_V16_SHA" in t
    assert "from_public_page" in t
    assert "_robots(client, URL, ALLOWED)" in t
    assert "_get(client, URL, ALLOWED)" in t
    assert "trust_env=False" in t
    assert "follow_redirects=False" in t
    for forbidden in ("crm.item.update", "crm.item.add", "INSERT INTO",
                      "UPDATE serviceit_", "tasks.task.add", "subprocess",
                      "investigate_routerai", "pg_queue", "secret_file"):
        assert forbidden not in t

def test_registered_probe_exact_owner_and_no_live_install():
    text=FLOW.read_text("utf8")
    assert "  mkb_4675475_source_probe:\n" in text
    assert "SERVICEIT_B2B_MKB_4675475_SOURCE_READONLY_368" in text
    assert "github.actor == 'Ramzes2023'" in text
    assert "github.event.comment.user.login == 'Ramzes2023'" in text
    assert text.count("github.event.issue.number == 368") == 4
    assert "serviceit-vps-readonly" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "UserKnownHostsFile=" in text
    assert "crm.item.update" not in text
