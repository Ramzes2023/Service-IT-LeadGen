"""Manager-approved call brief. No automated qualification or CRM writes."""
from datetime import datetime, timedelta, timezone

def draft(record, *, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(record, dict):
        raise ValueError("POSTCALL_SHAPE")
    if (record.get("manager_confirmed") is not True
            or record.get("activity_completed") is not True
            or record.get("entityTypeId") != 1062
            or record.get("categoryId") != 21):
        raise ValueError("POSTCALL_UNVERIFIED")
    card = record.get("item_id")
    if type(card) is not int or card < 8 or card in (3,7):
        raise ValueError("POSTCALL_CARD_SCOPE")
    interest = record.get("interest")
    if interest not in ("YES","NO","UNKNOWN"):
        raise ValueError("POSTCALL_INTEREST_UNVERIFIED")
    summary = record.get("summary")
    if (type(summary) is not str or not 5 <= len(summary.strip()) <= 500
            or any(ord(ch) < 32 for ch in summary)):
        raise ValueError("POSTCALL_NOTES_UNSAFE")
    actions={"YES":("SEND_PROPOSAL","ASK_DETAILS"),
             "NO":("DO_NOT_CONTACT",), "UNKNOWN":("MANAGER_REVIEW",)}
    action=record.get("next_action")
    if action not in actions[interest]:
        raise ValueError("POSTCALL_NEXT_ACTION_INVALID")
    due=record.get("next_due_at")
    if action=="DO_NOT_CONTACT":
        if due not in (None,""):
            raise ValueError("NO_CONTACT_CANNOT_SCHEDULE")
        due=None
    else:
        try: date=datetime.fromisoformat(due.replace("Z","+00:00"))
        except (AttributeError,ValueError):
            raise ValueError("ACTION_DUE_REQUIRED") from None
        if date.tzinfo is None or not now<=date.astimezone(timezone.utc)<=now+timedelta(days=90):
            raise ValueError("ACTION_DATE_NOT_VALID")
        due=date.astimezone(timezone.utc).isoformat()
    return {"status":"MANAGER_APPROVAL_DRAFT_ONLY", "item_id":card,
            "interest":interest,"summary":summary.strip(),
            "action":action,"task_due_at":due,
            "crm_writes":0,"tasks_created":0}
