"""No-network regressions for read-only owner Issue #74 status feedback."""
import json
from pathlib import Path

import httpx
import pytest

from leadpilot import serviceit_manager_layout_status_comment as audit


def event():
    return {
        "action": "created",
        "repository": {"full_name": audit.REPOSITORY},
        "issue": {"number": audit.ISSUE},
        "sender": {"login": "Ramzes2023"},
        "comment": {"user": {"login": "Ramzes2023"},
                    "body": audit.TRIGGER},
    }


def test_status_only_removes_layout_hashes_and_possible_customer_text():
    secret = "CONFIDENTIAL_PRIVATE_CUSTOMER_DATA"
    data = {
        "status": "PREVIEW_ONLY",
        "added_fields": 14,
        "before_hash": "1234567890" + secret,
        "after_hash": "private",
        "customer": secret,
    }
    out = audit.safe_summary(json.dumps(data))
    assert out == "SERVICEIT_MANAGER_LAYOUT_AUDIT_V1 STATUS=PREVIEW_ONLY ADDED=14 CRM_WRITES=0"
    assert secret not in out
    assert audit.safe_summary('{"status":"OVERRIDE_AUDIT_AND_REVEAL_CRM","added_fields":200}') == (
        "SERVICEIT_MANAGER_LAYOUT_AUDIT_V1 STATUS=BLOCKED_REVIEW ADDED=UNKNOWN CRM_WRITES=0")
    assert audit.safe_summary("not json") == (
        "SERVICEIT_MANAGER_LAYOUT_AUDIT_V1 STATUS=BLOCKED_REVIEW ADDED=UNKNOWN CRM_WRITES=0")


@pytest.mark.parametrize("mutation", [
    lambda x: x["repository"].update(full_name="attacker/repo"),
    lambda x: x["issue"].update(number=83),
    lambda x: x["sender"].update(login="attacker"),
    lambda x: x["comment"].update(body="SERVICEIT_MANAGER_LAYOUT_APPLY"),
    lambda x: x["comment"]["user"].update(login="attacker"),
    lambda x: x.update(action="edited"),
    lambda x: x["issue"].update(pull_request={"id": 1}),
])
def test_exact_owner_trigger_only(mutation):
    valid = event()
    assert audit.allowed_event(valid)
    mutation(valid)
    assert not audit.allowed_event(valid)


def test_real_mock_gh_post_contains_only_fixed_codes_and_one_issue_comment():
    calls = []
    def transport(request):
        assert request.method == "POST"
        assert str(request.url) == (
            "https://api.github.com/repos/Ramzes2023/Service-IT-LeadGen/issues/74/comments")
        assert request.headers["authorization"] == "Bearer synthetic-gh-secret-12345"
        obj = json.loads(request.content)
        assert set(obj) == {"body"}
        assert "private_field" not in obj["body"]
        assert obj["body"].endswith("CRM_WRITES=0")
        calls.append(obj["body"])
        return httpx.Response(201, json={
            "body": obj["body"], "user": {"login": "github-actions[bot]"}
        })
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = audit.post_summary(event(), json.dumps({
            "status": "PREVIEW_ONLY", "added_fields": 15,
            "private_field": "NEVER_LOG",
        }), "synthetic-gh-secret-12345", client=client)
    assert result == {"status": "AUDIT_ACKED", "crm_writes": 0}
    assert len(calls) == 1


def test_github_refusal_does_not_claim_success_or_retry():
    requests = []
    def transport(request):
        requests.append(request.method)
        return httpx.Response(403, json={"message": "PRIVATE_DETAILS"})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(audit.AuditBlocked, match="GITHUB_AUDIT_WRITE_UNVERIFIED"):
            audit.post_summary(event(), "{}", "synthetic-gh-secret-12345", client=client)
    assert requests == ["POST"]


def test_readonly_feedback_workflow_never_exposes_apply_for_issue_marker():
    source = Path(".github/workflows/serviceit-manager-layout-safe.yml").read_text("utf-8")
    assert "issue_comment:" in source
    assert "issues: write" in source
    assert "github.event.issue.number == 74" in source
    assert "github.actor == 'Ramzes2023'" in source
    assert "SERVICEIT_MANAGER_LAYOUT_READONLY_V1" in source
    assert "serviceit_manager_layout_status_comment" in source
    assert "github.event_name == 'issue_comment'" in source
    assert "github.event_name == 'workflow_dispatch' && inputs.apply" in source
    assert "Require read-only preview success" in source
    assert "serviceit-layout-preview.json" in source
    assert "GITHUB_TOKEN" in source
