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
    return {"status":"MANAGER_APPROVAL_DRAFT_ONLY", "item_id":card,
            "interest":interest,"summary":summary.strip(),
            "action":"MANAGER_REVIEW_REQUIRED",
            "task_due_at":None,"crm_writes":0,"tasks_created":0}
