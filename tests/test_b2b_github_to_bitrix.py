"""Synthetic-only no-network contract tests for automatic B2B issue->CRM HOLD."""
from copy import deepcopy
import json

import httpx
import pytest

from leadpilot import b2b_github_to_bitrix as bridge


def fixture_event(*, subject="Поддержка 1С:ЗУП", organizer="ООО «Тест»"):
    tender = "4661617"
    notice = {
        "schema": "serviceit-b2b-notice-v1", "source": "B2B-Center",
        "email_message_id": "1a19b7630301bafb", "tender_id": tender,
        "source_url": "https://www.b2b-center.ru/market/podderzhka-1s/tender-4661617/",
        "subject": subject, "organizer": organizer,
        "buyer_inn": None, "fns_msp": None, "region": None,
        "original_publication_verified": False, "contact_approved": False,
        "qualified_lead": False,
    }
    return {
        "action": "created", "repository": {"full_name": bridge.REPO},
        "issue": {"number": 66}, "sender": {"login": bridge.OWNER},
        "comment": {"user": {"login": bridge.OWNER},
                    "body": bridge.BEGIN + json.dumps(notice, ensure_ascii=False)},
    }


def change_notice(event, field, value):
    notice = json.loads(event["comment"]["body"][len(bridge.BEGIN):])
    notice[field] = value
    event["comment"]["body"] = bridge.BEGIN + json.dumps(notice, ensure_ascii=False)


def test_valid_tender_never_assumed_qualified_lead():
    report = bridge.run_once(fixture_event())
    assert report["status"] == "B2B_HOLD_VALIDATED_DRY_RUN"
    assert report["qualified_leads"] == report["crm_writes"] == 0
    assert report["services"] == ["1C"]


@pytest.mark.parametrize("change", [
    lambda e: e["repository"].update(full_name="attacker/repo"),
    lambda e: e["issue"].update(number=65),
    lambda e: e["sender"].update(login="attacker"),
    lambda e: e["comment"]["user"].update(login="attacker"),
    lambda e: e.update(action="edited"),
    lambda e: e["issue"].update(pull_request={"url": "https://example.invalid"}),
])
def test_no_untrusted_issue_comments(change):
    event = fixture_event()
    change(event)
    with pytest.raises(bridge.BridgeBlocked, match="UNTRUSTED_GITHUB_EVENT"):
        bridge.run_once(event)


@pytest.mark.parametrize("field,value", [
    ("buyer_inn", "1234567890"), ("fns_msp", "micro"), ("region", "77"),
    ("original_publication_verified", True), ("contact_approved", True),
    ("qualified_lead", True),
])
def test_notice_cannot_assert_fake_qualification(field, value):
    event = fixture_event()
    change_notice(event, field, value)
    with pytest.raises(bridge.BridgeBlocked, match="UNVERIFIED_VENDOR_EMAIL_CANNOT_BE_LEAD"):
        bridge.run_once(event)


def test_irrelevant_work_is_blocked():
    with pytest.raises(bridge.BridgeBlocked, match="NOT_A_SERVICEIT_PROCUREMENT"):
        bridge.run_once(fixture_event(subject="Возведение бетонного фундамента"))


def test_tracking_url_is_blocked():
    event = fixture_event()
    notice = json.loads(event["comment"]["body"][len(bridge.BEGIN):])
    change_notice(event, "source_url", notice["source_url"] + "?message_info=SECRET")
    with pytest.raises(bridge.BridgeBlocked, match="OFFICIAL_CANONICAL_URL_REQUIRED"):
        bridge.run_once(event)


def test_arbitrary_fields_not_allowed():
    event = fixture_event()
    change_notice(event, "phone", "+79123456789")
    with pytest.raises(bridge.BridgeBlocked, match="NOTICE_SCHEMA_INVALID"):
        bridge.run_once(event)


def test_crm_hold_has_strict_allowlist_and_unverified_identity():
    event = fixture_event()
    notice, services = bridge.notice_from_event(event)
    result = bridge.hold_payload(notice, services)
    assert result["entityTypeId"] == 1062 and result["useOriginalUfNames"] == "Y"
    fields = result["fields"]
    assert fields["categoryId"] == 21
    assert fields["stageId"] == "DT1062_21:NEW"
    assert fields[bridge.SIGNAL] == "B2B-CENTER-4661617"
    assert fields["UF_CRM_13_SVCIT_SIGNAL_KIND"] == "tender-opportunity"
    assert not any(k in fields for k in ("phone", "contactIds", "clientId", "assignedById", "opportunity"))


class Endpoints:
    def __init__(self, *, marker=False, existing=False, post_error=False, bad_readback=False):
        self.calls = []
        self.reserved = []
        self.marker = marker
        self.existing = existing
        self.post_error = post_error
        self.bad_readback = bad_readback
        self.add_fields = None

    def __call__(self, request: httpx.Request):
        address = str(request.url)
        if "api.github.com" in address:
            self.calls.append("GH." + request.method)
            if request.method == "GET":
                items = [{"user": {"login": "github-actions[bot]"},
                          "body": bridge.LOCK_PREFIX + "B2B-CENTER-4661617"}] if self.marker else []
                return httpx.Response(200, json=items)
            text = json.loads(request.content)["body"]
            self.reserved.append(text)
            return httpx.Response(201, json={
                "id": 1, "user": {"login": "github-actions[bot]"}, "body": text})
        method = address.split("/")[-1].split(".json")[0]
        self.calls.append(method)
        body = json.loads(request.content)
        if method == "crm.item.list":
            assert "select" not in body
            # Live portal 2026-11-03 gave a flat record with a valid ID only
            # when the exact-source listing OMITTED select entirely.
            records = ([{"id": 23, "categoryId": 21,
                         bridge.SIGNAL: "B2B-CENTER-4661617"}]
                       if self.existing else [])
            return httpx.Response(200, json={"result": {"items": records},
                                             "total": len(records)})
        if method == "crm.item.add":
            assert len(self.reserved) == 1, "PRE-POST BOT RESERVATION IS MANDATORY"
            if self.post_error:
                return httpx.Response(503, text="server error")
            self.add_fields = deepcopy(body["fields"])
            return httpx.Response(200, json={"result": {"item": {"id": 24}}})
        if method == "crm.item.get":
            if self.existing and body.get("id") == 23:
                return httpx.Response(200, json={"result": {"item": {
                    "id": 23, "entityTypeId": 1062, "categoryId": 21,
                    bridge.SIGNAL: "B2B-CENTER-4661617"}}})
            assert self.add_fields
            row = {**self.add_fields, "id": 24, "entityTypeId": 1062}
            if self.bad_readback:
                row[bridge.SIGNAL] = "wrong source id"
            return httpx.Response(200, json={"result": {"item": row}})
        pytest.fail("UNEXPECTED_CRM_CALL " + method)


def simulate(monkeypatch, endpoints):
    # Already independently tested strict schema preflight; mock 4 GETs here.
    monkeypatch.setattr(bridge, "preflight", lambda rest: {"status": "PASS"})
    with httpx.Client(transport=httpx.MockTransport(endpoints),
                      trust_env=False) as client:
        return bridge.run_once(
            fixture_event(), client=client,
            github_token="github-test-token-example",
            webhook="https://serviseit.bitrix24.ru/rest/2/SYNTHETICONLY123456",
            live=True)


def test_one_real_contract_write_after_reservation_and_exact_readback(monkeypatch):
    target = Endpoints()
    result = simulate(monkeypatch, target)
    assert result["status"] == "ONE_TENDER_HOLD_VERIFIED"
    assert result["qualified_leads"] == 0 and result["crm_writes"] == 1
    assert target.calls == ["GH.GET", "crm.item.list", "GH.POST",
                            "crm.item.add", "crm.item.get"]


def test_duplicate_in_crm_prevents_write(monkeypatch):
    target = Endpoints(existing=True)
    assert simulate(monkeypatch, target)["status"] == "ALREADY_EXISTS_IN_CRM"
    assert target.calls == ["GH.GET", "crm.item.list", "crm.item.get"]


def test_prior_reserved_marker_blocks_any_second_post(monkeypatch):
    target = Endpoints(marker=True)
    assert simulate(monkeypatch, target)["status"] == "ALREADY_RESERVED_DO_NOT_RETRY"
    assert target.calls == ["GH.GET"]


def test_ambiguous_response_keeps_one_durable_lock(monkeypatch):
    target = Endpoints(post_error=True)
    with pytest.raises(bridge.BridgeBlocked, match="CRM_TRANSPORT_UNCERTAIN"):
        simulate(monkeypatch, target)
    assert target.calls == ["GH.GET", "crm.item.list", "GH.POST", "crm.item.add"]
    assert len(target.reserved) == 1


def test_readback_failure_never_reposts(monkeypatch):
    target = Endpoints(bad_readback=True)
    with pytest.raises(bridge.BridgeBlocked, match="CRM_READBACK_UNCERTAIN_NO_RETRY"):
        simulate(monkeypatch, target)
    assert target.calls.count("crm.item.add") == 1


def test_cli_offline_never_reads_env_webhook_or_network(monkeypatch, tmp_path, capsys):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(fixture_event()), encoding="utf-8")
    monkeypatch.setenv("SERVICEIT_BITRIX_WEBHOOK", "SECRET_SENSITIVE_DO_NOT_PRINT")
    monkeypatch.setenv("GITHUB_TOKEN", "SECRET_SENSITIVE_DO_NOT_PRINT")
    assert bridge.main(["--event", str(event_path)]) == 0
    assert "SECRET_SENSITIVE" not in capsys.readouterr().out
    event_path.write_text("{}", encoding="utf-8")
    assert bridge.main(["--event", str(event_path)]) == 2
    assert "SECRET_SENSITIVE" not in capsys.readouterr().out


def test_staffing_tender_4673595_generates_unverified_hold_only():
    event = fixture_event(subject="Оказание услуг по предоставлению ИТ-специалистов",
                          organizer='ООО "ГЛОБУС-ИТ"')
    change_notice(event, "tender_id", "4673595")
    change_notice(event, "source_url",
                  "https://www.b2b-center.ru/market/"
                  "okazanie-uslug-po-predostavleniiu-it-spetsialistov/tender-4673595/")
    notice, categories = bridge.notice_from_event(event)
    assert categories == ["IT_STAFFING"]
    item = bridge.hold_payload(notice, categories)
    assert item["entityTypeId"] == 1062
    assert item["fields"]["categoryId"] == 21
    assert item["fields"][bridge.SIGNAL] == "B2B-CENTER-4673595"
    assert item["fields"]["UF_CRM_13_SVCIT_SUGGESTED_SERVICE"] == "IT_STAFFING"
    assert notice["qualified_lead"] is False
    assert notice["contact_approved"] is False
    assert notice["original_publication_verified"] is False

