"""Manager-approved summary proposal. No CRM writes."""
POSTCALL_VERSION = "draft-v1"

def summarize_input(summary, interest):
    if not isinstance(summary, str) or len(summary) > 400:
        raise ValueError("INVALID_NOTE")
    if interest not in ("YES", "NO", "UNKNOWN"):
        raise ValueError("INTEREST_UNVERIFIED")
    return {"summary": summary, "interest": interest,
            "review_required": True, "crm_writes": 0}
