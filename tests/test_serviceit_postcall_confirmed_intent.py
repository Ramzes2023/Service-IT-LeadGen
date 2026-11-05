"""Post-call manager intent must never treat call completion as consent or sale."""
from datetime import datetime, timezone, timedelta
import pytest
from leadpilot.serviceit_postcall_confirmed_intent import draft_verified_intent, PostCallBlocked

NOW=datetime(2026,11,5,12,0,tzinfo=timezone.utc)

def fixture():
    card={"id":123,"entityTypeId":1062,"categoryId":21,
          "createdTime":"2026-11-04T10:00:00Z","assignedById":42}
    activity={"ID":777,"OWNER_ID":123,"OWNER_TYPE_ID":1062,
              "TYPE_ID":2,"COMPLETED":"Y",
              "LAST_UPDATED":"2026-11-05T10:00:00Z"}
    approval={"manager_confirmed":True,"manager_user_id":42,
              "activity_id":777,"interest":"YES",
              "summary":"Клиент просил уточнить условия по 1С.",
              "next_action":"ASK_DETAILS",
              "next_due_at":"2026-11-06T12:00:00Z",
              "confirmation_ref":"crm:manager:approval:1"}
    return card,activity,approval

def test_only_manager_confirmed_real_new_call_can_generate_intent():
    a,b,c=fixture()
    v=draft_verified_intent(a,b,c,now=NOW)
    assert v["status"]=="MANAGER_POSTCALL_INTENT_VALIDATED_NOT_WRITTEN"
    assert v["interest"]=="YES" and v["action"]=="ASK_DETAILS"
    assert v["crm_writes"]==v["tasks_created"]==0
    assert v["payment_verified"] is False
    assert v["deal_conversion_authorized"] is False
    assert v["outreach_authorized"] is False
    assert len(v["next_action_fields"])==2

@pytest.mark.parametrize("section,key,val",[
    (0,"id",3),(0,"categoryId",22),(0,"entityTypeId",2),
    (0,"createdTime","2026-11-01T10:00:00Z"),
    (0,"assignedById",0),
    (1,"OWNER_ID",999),(1,"OWNER_TYPE_ID",2),
    (1,"COMPLETED","N"),(1,"TYPE_ID",4),
    (1,"LAST_UPDATED","2026-11-06T12:00:00Z"),
    (2,"manager_confirmed",False),
    (2,"manager_user_id",43),
    (2,"activity_id",999),
    (2,"interest","YES_UNCONFIRMED"),(2,"next_action","SEND_OUTREACH"),
    (2,"summary","AI"),
    (2,"confirmation_ref","bad"),
    (2,"next_due_at","2026-11-03T12:00:00Z"),
])
def test_unverified_identity_or_confirmation_blocked(section,key,val):
    data=list(fixture())
    data[section][key]=val
    with pytest.raises(PostCallBlocked):
        draft_verified_intent(*data,now=NOW)

def test_do_not_contact_has_no_followup_due():
    a,b,c=fixture()
    c.update(interest="NO",next_action="DO_NOT_CONTACT",next_due_at=None)
    v=draft_verified_intent(a,b,c,now=NOW)
    assert v["next_due_at"] is None
    assert v["outreach_authorized"] is False
    c["next_due_at"]="2026-11-06T12:00:00Z"
    with pytest.raises(PostCallBlocked):
        draft_verified_intent(a,b,c,now=NOW)
