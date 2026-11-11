"""V19 independent postwrite audit cannot cause an external write."""
from pathlib import Path
import ast

def test_real_v19_postwrite_only_reads_canonical_bitrix():
    s=Path("ops/serviceit_vps_b2b_v19_independent_postread.py").read_text("utf8")
    ast.parse(s)
    assert "SET TRANSACTION READ ONLY" in s
    assert "crm.item.get" in s
    assert "EXACT_NINE_READBACK" in s
    assert "DURABLE_PG_DIGEST" in s
    assert "ONLY_THREE_NARRATIVES_CHANGED" in s
    assert "INDEPENDENT_ACCEPTANCE" in s
    assert "compute_digest(nine,bytes(salt))" in s
    for bad in ("crm.item.update","crm.item.add","tasks.task.add",
                "INSERT INTO", "UPDATE serviceit_", "DELETE FROM",
                "DROP TABLE", "investigate_routerai"):
        assert bad not in s

def test_v19_postread_owner_only_exact_marker():
    s=Path(".github/workflows/serviceit-vps-b2b-v19-independent-postread.yml").read_text("utf8")
    assert "SERVICEIT_B2B_V19_INDEPENDENT_POSTREAD_489" in s
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.comment.user.login == 'Ramzes2023'" in s
    assert "github.event.issue.number == 489" in s
    assert "!github.event.issue.pull_request" in s
    assert "StrictHostKeyChecking=yes" in s
    assert "V19_POSTREAD_INDEPENDENT_ACCEPTANCE=YES" in s
    assert "workflow_dispatch:" not in s
