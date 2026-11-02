"""Synthetic-only, no live CRM/Gmail/AI requests for single-card preflight."""
from copy import deepcopy
import httpx
from leadpilot import serviceit_b2b_single_card_preflight as pilot


def fixture():
    return {
        "action": "created",
        "repository": {"full_name": pilot.REPO},
        "issue": {"number": 83},
        "sender": {"login": "Ramzes2023"},
        "comment": {"body": pilot.MARKER, "user": {"login": "Ramzes2023"}},
    }


def test_strict_owner_event_gate():
    assert pilot.check_event(fixture())
    for path, value in [
        (("issue", "number"), 66),
        (("sender", "login"), "another-user"),
        (("comment", "body"), "SERVICEIT_B2B_NOTICE_V1"),
        (("repository", "full_name"), "anyone/other"),
        (("action",), "edited"),
    ]:
        evt = deepcopy(fixture())
        head = evt
        for k in path[:-1]:
            head = head[k]
        head[path[-1]] = value
        assert not pilot.check_event(evt)


def test_missing_secret_or_token_never_makes_requests():
    def fail(req):
        raise AssertionError("no HTTP allowed")
    with httpx.Client(transport=httpx.MockTransport(fail)) as c:
        assert pilot.run(fixture(), hook="", gh_token="", client=c)["status"] == "WEBHOOK_NOT_CONFIGURED"
        assert pilot.run(fixture(), hook="placeholder", gh_token="", client=c)["status"] == "GITHUB_LEDGER_NOT_AVAILABLE"


def test_readonly_source_verification(monkeypatch):
    assert len(pilot.CANDIDATES) == 5
    calls = []
    class FakeReadOnlyBitrix:
        def __init__(self, hook, *, client):
            assert hook == "test corporate webhook"
            self.base = "https://example.invalid/rest/2/REDACTED"
    class FakeLedger:
        def __init__(self, client, token):
            assert token == "scoped-github-token"
        def markers(self):
            return ["B2B-CENTER-4661617"]
    monkeypatch.setattr(pilot, "ReadOnlyBitrix", FakeReadOnlyBitrix)
    monkeypatch.setattr(pilot, "preflight", lambda rest: calls.append("metadata"))
    def exact(client, base, source_id):
        calls.append(source_id)
        return [{"id": 42}] if source_id == "B2B-CENTER-4664763" else []
    monkeypatch.setattr(pilot, "exact_duplicate", exact)
    monkeypatch.setattr(pilot, "GitHubLedger", FakeLedger)
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: (_ for _ in ()).throw(AssertionError("no HTTP in synthetic test")))) as client:
        result = pilot.run(fixture(), hook="test corporate webhook",
                           gh_token="scoped-github-token", client=client)
    assert result == {
        "status": "READONLY_PREFLIGHT_PASS",
        "examined_source_ids": 5,
        "existing_crm_count": 1,
        "locked_source_count": 1,
        "unlocked_not_in_crm_count": 3,
        "crm_writes": 0,
        "gigachat_calls": 0,
    }
    assert calls == ["metadata"] + ["B2B-CENTER-" + tid for tid in pilot.CANDIDATES]


def test_crm_errors_fail_closed(monkeypatch):
    class FakeReadOnly:
        def __init__(self, hook, *, client):
            self.base = "https://example.invalid/rest"
    monkeypatch.setattr(pilot, "ReadOnlyBitrix", FakeReadOnly)
    monkeypatch.setattr(pilot, "preflight", lambda rest: 1)
    def denied(client, base, signal):
        raise RuntimeError("private webhook must never appear")
    monkeypatch.setattr(pilot, "exact_duplicate", denied)
    with httpx.Client() as c:
        result = pilot.run(fixture(), hook="fake", gh_token="github-test", client=c)
    assert result == {"status": "CRM_OR_LEDGER_READONLY_BLOCKED", "crm_writes": 0}
