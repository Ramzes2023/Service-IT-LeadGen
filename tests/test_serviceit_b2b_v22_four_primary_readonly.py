"""V22 four real B2B source preview can never write to Bitrix or PG."""
from pathlib import Path
import ast

def test_v22_four_readonly_owner_gate_and_no_paid_calls():
    workflow=Path(".github/workflows/serviceit-vps-b2b-v22-four-primary-readonly.yml").read_text("utf8")
    src=Path("ops/serviceit_vps_b2b_v22_four_primary_readonly.py").read_text("utf8")
    ast.parse(src)
    assert "github.actor == 'Ramzes2023'" in workflow
    assert "github.event.issue.number == 489" in workflow
    assert "SERVICEIT_B2B_V22_FOUR_PRIMARY_READONLY_489" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "git diff --quiet" in workflow
    assert "V22_FOUR_CRM_WRITES=NO" in workflow
    assert "V22_FOUR_BRAVE_ROUTERAI_CALLS=NO" in workflow
    assert "SET TRANSACTION READ ONLY" in src
    assert 'deep_search_enabled=False' in src
    assert 'deep_llm_context_enabled=False' in src
    assert 'browser_render=None' in src
    assert 'before=original_nine(first)' in src
    assert 'draft=prepare_addendum' in src
    assert "crm.item.update" not in src
    assert "crm.timeline.comment.add" not in src
    assert "investigate_routerai" not in src
    assert "brave_mirror_search" not in src
    assert 'source_only_investigation' in src
