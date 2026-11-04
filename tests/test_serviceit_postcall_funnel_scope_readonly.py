"""Read-only contract for manager post-call and funnel preflight."""
from pathlib import Path

def test_no_writes_in_manager_scope_audit():
    from leadpilot.serviceit_postcall_funnel_scope_readonly import EXPECTED, NEEDED
    assert EXPECTED == {"crm.item.fields", "crm.category.list",
                        "crm.activity.fields", "crm.activity.list"}
    assert "postcall_next_action" in NEEDED
    body=Path("leadpilot/serviceit_postcall_funnel_scope_readonly.py").read_text("utf-8")
    assert "crm.item.update.json" not in body
    assert "tasks.task.add.json" not in body
    assert "crm.deal.add.json" not in body
    assert "revenue_verified\":False" in body
