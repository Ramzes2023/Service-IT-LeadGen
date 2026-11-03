import json
from pathlib import Path
import httpx
import pytest
from leadpilot import serviceit_manager_layout_apply_status as audit

def event():
    return {
        "action": "created",
        "repository": {"full_name": audit.REPOSITORY},
        "issue": {"number": audit.ISSUE},
        "sender": {"login": "Ramzes2023"},
        "comment": {"user": {"login": "Ramzes2023"}, "body": audit.TRIGGER},
    }

def test_exact_applied_readback_is_the_only_success_with_one_write():
    secret = "PRIVATE_LAYOUT_AND_CUSTOMER_DATA"
    raw = json.dumps({"status": "MANAGER_LAYOUT_APPLIED_EXACT",
                      "added_fields": 1, "before_hash": secret, "layout": secret})
    out = audit.safe_summary(raw)
    assert out == "SERVICEIT_MANAGER_LAYOUT_APPLY_AUDIT_V1 STATUS=MANAGER_LAYOUT_APPLIED_EXACT CRM_WRITES=1"
    assert secret not in out

def test_missing_admin_and_backup_gates_report_zero_writes():
    for status in ("OWNER_LAYOUT_OPT_IN_MISSING", "ADMIN_SHARED_SCOPE_NOT_APPROVED",
                   "BITRIX_WEBHOOK_MISSING", "ENCRYPTED_BACKUP_CREDENTIALS_MISSING"):
        assert audit.safe_summary(json.dumps({"status": status})).endswith("CRM_WRITES=0")

def test_uncertain_set_never_claims_zero_or_success():
    out = audit.safe_summary(json.dumps({"status": "LAYOUT_BLOCKED_OR_UNCERTAIN_REVIEW_REQUIRED",
                                         "crm_writes": "UNVERIFIED", "detail": "secret"}))
    assert out.endswith("CRM_WRITES=UNVERIFIED")
    assert "secret" not in out

@pytest.mark.parametrize("mutation", [
    lambda x: x["repository"].update(full_name="evil/repo"),
    lambda x: x["issue"].update(number=83),
    lambda x: x["sender"].update(login="evil"),
    lambda x: x["comment"].update(body="SERVICEIT_MANAGER_LAYOUT_READONLY_V1"),
    lambda x: x["comment"]["user"].update(login="evil"),
    lambda x: x.update(action="edited"),
])
def test_exact_owner_apply_event_only(mutation):
    value = event()
    assert audit.allowed_event(value)
    mutation(value)
    assert not audit.allowed_event(value)

def test_apply_receipt_posts_only_fixed_status_once():
    calls = []
    def transport(request):
        assert request.method == "POST"
        assert str(request.url) == "https://api.github.com/repos/Ramzes2023/Service-IT-LeadGen/issues/74/comments"
        body = json.loads(request.content)
        assert set(body) == {"body"}
        assert "PRIVATE" not in body["body"]
        calls.append(body["body"])
        return httpx.Response(201, json={"body": body["body"],
                                         "user": {"login": "github-actions[bot]"}})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = audit.post_summary(event(), json.dumps({"status": "MANAGER_LAYOUT_APPLIED_EXACT",
                                                         "private": "PRIVATE"}),
                                    "synthetic-gh-secret-12345", client=client)
    assert result == {"status": "AUDIT_ACKED"}
    assert len(calls) == 1

def test_apply_refusal_does_not_retry():
    calls = []
    def transport(request):
        calls.append(request.method)
        return httpx.Response(403, json={"message": "PRIVATE"})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(audit.ApplyAuditBlocked):
            audit.post_summary(event(), "{}", "synthetic-gh-secret-12345", client=client)
    assert calls == ["POST"]

def test_workflow_keeps_apply_gated_and_separate_from_readonly_marker():
    workflow = Path(".github/workflows/serviceit-manager-layout-safe.yml").read_text()
    assert "SERVICEIT_MANAGER_LAYOUT_APPLY_V1" in workflow
    assert "SERVICEIT_MANAGER_LAYOUT_ADMIN_APPROVED" in workflow
    assert "SERVICEIT_MANAGER_LAYOUT_APPLY" in workflow
    assert "SERVICEIT_GH_LEDGER_KEY" in workflow
    assert "serviceit_manager_layout_operator --apply" in workflow
    assert "serviceit_manager_layout_apply_status --result" in workflow
