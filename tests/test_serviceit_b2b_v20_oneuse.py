"""Owner-gated real V20 trial: no blind replay or mutation of unrelated fields."""
from pathlib import Path
import ast

def test_v20_live_pilot_explicit_owner_and_nine_field_postread():
    workflow=Path(".github/workflows/serviceit-vps-b2b-v20-oneuse-live.yml").read_text("utf8")
    script=Path("ops/serviceit_vps_b2b_v20_oneuse_live.py").read_text("utf8")
    ast.parse(script)
    assert "github.actor == 'Ramzes2023'" in workflow
    assert "github.event.issue.number == 489" in workflow
    assert "SERVICEIT_B2B_V20_ONEUSE_PILOT_489" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "git diff --quiet" in workflow
    assert "V20_PILOT_CRM_WRITES=(YES|NO)" in workflow
    assert 'env["SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED"]="YES"' in script
    assert 'env.get("SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED","NO")=="NO"' in script
    assert "with db(env)" in script
    assert "result=run_once(env,client,conn)" in script
    assert "V20_VERSIONED_ADDITIVE_VERIFIED" in script
    assert "crm.item.update" not in script and "crm.item.add" not in script
    assert "if state not in SAFE" in script
    assert "UNCERTAIN_STOP" in script
