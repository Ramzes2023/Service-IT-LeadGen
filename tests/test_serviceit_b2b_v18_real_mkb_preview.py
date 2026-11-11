"""V18 exact-card production preview safety contract."""
from pathlib import Path
import ast

SCRIPT=Path("ops/serviceit_vps_b2b_v18_real_mkb_preview.py")
FLOW=Path(".github/workflows/serviceit-vps-b2b-v18-mkb-real-preview.yml")


def test_no_writer_side_effects_and_one_named_real_card():
    s=SCRIPT.read_text("utf8")
    ast.parse(s)
    assert "CARD=17" in s
    assert 'TENDER="4675475"' in s
    assert "SET TRANSACTION READ ONLY" in s
    assert "crm.item.get" in s
    assert "validate_identity(item,CARD)" in s
    assert "source_findings(item,notice,exact,client,enable_v18=True)" in s
    assert "source_only_investigation(findings)" in s
    assert "compose(notice,item,findings,report)" in s
    assert "V18_REAL_MKB_" in s
    for bad in ("crm.item.update","crm.item.add",
                "tasks.task.add", "INSERT INTO", "UPDATE serviceit_",
                "investigate_routerai(","requests.post(","contact_outreach"):
        assert bad not in s


def test_owner_and_version_pinning_for_real_preview():
    s=FLOW.read_text("utf8")
    assert "SERVICEIT_B2B_V18_MKB_4675475_READONLY_PREVIEW_489" in s
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.comment.user.login == 'Ramzes2023'" in s
    assert "github.event.issue.number == 489" in s
    assert "!github.event.issue.pull_request" in s
    assert "workflow_dispatch:" not in s
    assert "StrictHostKeyChecking=yes" in s
    assert "UserKnownHostsFile=" in s
    assert '"${#shas[@]}" == 6' in s
    assert "V18_REAL_MKB_CRM_WRITES=0" in s
