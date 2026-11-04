from datetime import datetime,timezone
import pytest
from leadpilot.manager_after_call import draft

NOW=datetime(2026,11,4,12,tzinfo=timezone.utc)

def record(**changes):
    item=dict(entityTypeId=1062,categoryId=21,item_id=88,
              manager_confirmed=True,activity_completed=True,
              interest="YES",summary="Менеджер подтвердил обсуждение 1С.",
              next_action="SEND_PROPOSAL",next_due_at="2026-11-05T12:00:00+00:00")
    item.update(changes)
    return item

def test_human_confirmed_due_action_is_preview_only():
    r=draft(record(),now=NOW)
    assert r["interest"]=="YES"
    assert r["task_due_at"] is not None
    assert r["crm_writes"]==r["tasks_created"]==0

@pytest.mark.parametrize("change",[
    {"manager_confirmed":False},{"item_id":7},
    {"activity_completed":False},{"interest":"ASSUMED"},
    {"next_action":"DO_NOT_CONTACT"},{"next_due_at":None},
])
def test_unverified_or_contradictory_outcome_is_rejected(change):
    with pytest.raises(ValueError):
        draft(record(**change),now=NOW)
