"""Synthetic E2E: producer Bitrix ID -> encrypted GitHub receipt -> exact CRM get."""
import base64
import json

import httpx
from cryptography.fernet import Fernet
import pytest

from leadpilot import b2b_github_to_bitrix as bridge
from leadpilot import serviceit_direct_id_handoff as direct


def event(card=41, number="4665948", status="NOT_RUN_SOURCE_UNVERIFIED"):
    return direct.receipt(card, "B2B-CENTER-" + number, ai_status=status)


class Github:
    def __init__(self, *, response=201):
        self.saved = None
        self.responses = []
        self.response = response
        self.requests = []

    def __call__(self, request):
        self.requests.append((request.method, str(request.url)))
        assert request.url.host == "api.github.com"
        assert request.url.path.startswith(
            "/repos/Ramzes2023/Service-IT-LeadGen/contents/.serviceit/handoffs/")
        assert "B2B-CENTER" not in str(request.url)
        assert request.url.params.get("ref", direct.BRANCH) == direct.BRANCH
        if request.method == "GET":
            if self.saved is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"type": "file", "encoding": "base64",
                                            "content": base64.b64encode(self.saved).decode()})
        assert request.method == "PUT"
        if self.response != 201:
            return httpx.Response(self.response, json={"message": "Denied"})
        body = json.loads(request.content)
        assert body["branch"] == direct.BRANCH
        assert "sha" not in body
        assert "B2B-CENTER" not in body["message"]
        self.saved = base64.b64decode(body["content"])
        return httpx.Response(201, json={"content": {"sha": "synthetic-sha"}})


def test_encrypted_exact_id_delivered_and_can_be_read_back():
    github = Github()
    key = Fernet.generate_key().decode()
    created = event()
    with httpx.Client(transport=httpx.MockTransport(github)) as c:
        assert direct.post_encrypted_handoff(c, "test-token-123456", key, created) == (
            "ENCRYPTED_ID_DELIVERED")
        assert github.saved is not None
        assert b"4665948" not in github.saved
        assert b"B2B-CENTER" not in github.saved
        assert b'"card_id":41' not in github.saved
        assert direct.receive_encrypted_handoff(
            c, "test-token-123456", key, "B2B-CENTER-4665948") == created
        assert direct.post_encrypted_handoff(c, "test-token-123456", key, created) == (
            "ALREADY_RECORDED_NO_RETRY")
    assert [x[0] for x in github.requests] == ["GET", "PUT", "GET", "GET"]


def test_new_handoff_not_committed_without_ack():
    github = Github(response=503)
    with httpx.Client(transport=httpx.MockTransport(github)) as c:
        with pytest.raises(direct.DirectHandoffBlocked, match="HANDOFF_PERSISTENCE_UNCONFIRMED"):
            direct.post_encrypted_handoff(c, "test-token-123456",
                                          Fernet.generate_key().decode(), event())
    assert github.saved is None
    assert len(github.requests) == 2


@pytest.mark.parametrize("id", [3, 7, 0, -1, True, "21", 2_000_000_001])
def test_excludes_historical_or_invalid_crm_ids(id):
    with pytest.raises(direct.DirectHandoffBlocked, match="HANDOFF_CARD_ID_INVALID"):
        event(card=id)


def test_wrong_source_and_key_fail_closed_without_record_reset():
    github = Github()
    key = Fernet.generate_key().decode()
    with httpx.Client(transport=httpx.MockTransport(github)) as c:
        direct.post_encrypted_handoff(c, "test-token-123456", key, event())
        with pytest.raises(direct.DirectHandoffBlocked):
            direct.receive_encrypted_handoff(c, "test-token-123456",
                                              key, "B2B-CENTER-4665949")
        with pytest.raises(direct.DirectHandoffBlocked):
            direct.receive_encrypted_handoff(c, "test-token-123456",
                                              Fernet.generate_key().decode(),
                                              "B2B-CENTER-4665948")
        assert github.saved is not None


def test_direct_target_exact_get_no_list_no_mutation():
    used = []
    def rest(method, params):
        used.append((method, params))
        assert method == "crm.item.get"
        assert params == {"entityTypeId": 1062, "id": 41,
                          "useOriginalUfNames": "Y"}
        return {"item": {
            "id": 41, "entityTypeId": 1062, "categoryId": 21,
            "UF_CRM_13_SVCIT_SIGNAL_ID": "B2B-CENTER-4665948",
            "UF_CRM_13_SVCIT_AI_STATUS": "NOT_RUN_SOURCE_UNVERIFIED",
        }}
    result = direct.verify_direct_target(rest, event())
    assert result == {
        "status": "DIRECT_ID_PRELIMINARY_READBACK_ONLY",
        "crm_reads": 1, "crm_writes": 0,
        "gigachat_calls": 0, "lead_score_calculated": False,
    }
    assert len(used) == 1


def test_direct_target_refuses_another_source_or_wrong_type():
    for wrong in (
        {"entityTypeId": 1062, "categoryId": 21,
         "UF_CRM_13_SVCIT_SIGNAL_ID": "B2B-CENTER-0000000"},
        {"entityTypeId": 1063, "categoryId": 21,
         "UF_CRM_13_SVCIT_SIGNAL_ID": "B2B-CENTER-4665948"},
        {"entityTypeId": 1062, "categoryId": 22,
         "UF_CRM_13_SVCIT_SIGNAL_ID": "B2B-CENTER-4665948"},
    ):
        card = {"id": 41, "UF_CRM_13_SVCIT_AI_STATUS": "NOT_RUN_SOURCE_UNVERIFIED", **wrong}
        with pytest.raises(direct.DirectHandoffBlocked):
            direct.verify_direct_target(lambda *_: {"item": card}, event())


def test_producer_without_opt_in_does_not_transmit_receipt(monkeypatch):
    # Existing source writer's test fixture provides a mocked Bitrix endpoint
    # to ensure no GitHub encrypted receipt is attempted by default.
    from tests.test_b2b_github_to_bitrix import Endpoints, simulate
    monkeypatch.delenv("SERVICEIT_DIRECT_ID_HANDOFF_ENABLED", raising=False)
    target = Endpoints()
    r = simulate(monkeypatch, target)
    assert r["status"] == "ONE_TENDER_HOLD_VERIFIED"
    assert r["handoff_state"] == "OFF"
    assert target.calls == ["GH.GET", "crm.item.list", "GH.POST",
                            "crm.item.add", "crm.item.get"]


def test_producer_handoffs_exact_add_id_no_second_crm_scan(monkeypatch):
    from tests.test_b2b_github_to_bitrix import Endpoints, simulate
    target = Endpoints()
    calls = []
    def capture(client, token, secret, receipt):
        calls.append((token, secret, receipt))
        return "ENCRYPTED_ID_DELIVERED"
    monkeypatch.setenv("SERVICEIT_DIRECT_ID_HANDOFF_ENABLED", "YES")
    monkeypatch.setenv("SERVICEIT_GH_LEDGER_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(direct, "post_encrypted_handoff", capture)
    r = simulate(monkeypatch, target)
    assert r["status"] == "ONE_TENDER_HOLD_VERIFIED"
    assert r["handoff_state"] == "ENCRYPTED_ID_DELIVERED"
    assert calls[0][2]["card_id"] == 24
    assert calls[0][2]["source_id"] == "B2B-CENTER-4661617"
    assert r["qualified_leads"] == 0
    assert target.calls == ["GH.GET", "crm.item.list", "GH.POST",
                            "crm.item.add", "crm.item.get"]


def test_after_write_handoff_failure_never_recreates_card(monkeypatch):
    from tests.test_b2b_github_to_bitrix import Endpoints, simulate
    target = Endpoints()
    monkeypatch.setenv("SERVICEIT_DIRECT_ID_HANDOFF_ENABLED", "YES")
    monkeypatch.setenv("SERVICEIT_GH_LEDGER_KEY", "bad")
    r = simulate(monkeypatch, target)
    assert r["status"] == "ONE_TENDER_HOLD_VERIFIED"
    assert r["handoff_state"] == "UNCONFIRMED_DO_NOT_RETRY"
    assert target.calls.count("crm.item.add") == 1
    assert target.calls.count("crm.item.get") == 1


def test_b2b_direct_actions_all_fanout_is_off_by_default():
    from pathlib import Path
    source = Path(".github/workflows/serviceit-b2b-direct-id-new-notice.yml").read_text()
    assert "issue_comment:" in source
    assert "github.event.issue.number == 66" in source
    assert "github.actor == 'Ramzes2023'" in source
    assert "startsWith(github.event.comment.body, 'SERVICEIT_B2B_NOTICE_V1')" in source
    assert "vars.SERVICEIT_B2B_DIRECT_AUTO_ENABLED == 'YES'" in source
    assert "serviceit-b2b-bitrix-issue-66" in source
    assert "cancel-in-progress: false" in source
    assert "SERVICEIT_DIRECT_ID_HANDOFF_ENABLED" in source
    assert "SERVICEIT_GH_LEDGER_KEY" in source
    assert "GIGACHAT_CREDENTIALS" in source
    assert "vars.SERVICEIT_B2B_GIGACHAT_PRIVACY_APPROVED" in source
    assert "AI_PROVIDER: gigachat" in source
    assert 'python -m leadpilot.b2b_github_to_bitrix --event' in source
    assert "SERVICEIT_FREE_CLOUD_ENABLED" not in source
    # The opt-in source workflow does not invoke the broken list poller.
    assert "serviceit_github_free_worker" not in source


def test_source_reservation_precedes_optional_gigachat_and_crm_add():
    import inspect
    code = inspect.getsource(bridge.run_once)
    assert code.index("ledger.reserve(source_id)") < code.index(
        'os.environ.get("SERVICEIT_B2B_GIGACHAT_AUTO_ENABLED")')
    assert code.index("ledger.reserve(source_id)") < code.index(
        '"crm.item.add"')
    assert code.index('"crm.item.add"') < code.index(
        '"crm.item.get"')
    assert code.index('"crm.item.get"') < code.index(
        'post_encrypted_handoff')
