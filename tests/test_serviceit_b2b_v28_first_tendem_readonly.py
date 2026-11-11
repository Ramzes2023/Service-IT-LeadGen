"""V28 first B2B card can use only approved verified Tendem evidence."""
from pathlib import Path
import ast

def test_readonly_exact_source_boundary():
    code=Path("ops/serviceit_vps_b2b_v28_first_tendem_readonly.py").read_text("utf8")
    workflow=Path(".github/workflows/serviceit-vps-b2b-v28-first-tendem-readonly.yml").read_text("utf8")
    ast.parse(code)
    assert 'TENDER="4673595"' in code
    assert 'PUBLIC_URL="https://tendem.ru/tenders/16548471"' in code
    assert 'source=="B2B-CENTER-"+TENDER' in code
    assert 'notice["tender_id"]==TENDER' in code
    assert 'env.get(B_EN)=="NO"' in code
    assert 'cur.execute("SET TRANSACTION READ ONLY")' in code
    assert 'index_hints=INDEX' in code
    assert 'len(pages)==len(facts)==1' in code
    assert 'facts[0].get("tender_id",{}).get("value")==TENDER' in code
    assert 'nova' not in code
    assert 'crm.item.update' not in code
    assert 'crm.item.add' not in code
    assert 'tasks.task.add' not in code
    assert 'investigate_routerai' not in code
    assert "github.actor == 'Ramzes2023'" in workflow
    assert "github.event.issue.number == 489" in workflow
    assert "SERVICEIT_B2B_V28_FIRST_TENDEM_READONLY_489" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "git diff --quiet" in workflow
    assert 'V28_FIRST_PUBLIC_TENDEM_EXACT=YES' in workflow
    assert 'V28_FIRST_CRM_WRITES=NO' in workflow
