"""Exact synthetic unit contract for the irreversible single-HOLD pilot."""
import json

import httpx
import pytest

from leadpilot import serviceit_b2b_single_hold_pilot as pilot
from leadpilot.b2b_email_opportunity_bridge import classify_services


def owner_trigger():
    return {
        "action": "created", "repository": {"full_name": pilot.REPO},
        "issue": {"number": 83}, "sender": {"login": pilot.OWNER},
        "comment": {"user": {"login": pilot.OWNER}, "body": pilot.MARKER},
    }


def public_source_comment(*, source_id="4665948", from_user="Ramzes2023"):
    notice = {
        "schema": "serviceit-b2b-notice-v1",
        "source": "B2B-Center",
        "email_message_id": "1a19d05bf05de30e",
        "tender_id": source_id,
        "source_url": (
            "https://www.b2b-center.ru/market/test-uslugi/tender-" + source_id + "/"
        ),
        "subject": "Техническое обслуживание базы данных",
        "organizer": "АО СО ЕЭС",
        "buyer_inn": None, "fns_msp": None, "region": None,
        "original_publication_verified": False,
        "contact_approved": False,
        "qualified_lead": False,
    }
    return {
        "id": pilot.SOURCE_COMMENT,
        "issue_url": pilot.SOURCE_ISSUE_API,
        "user": {"login": from_user},
        "body": pilot.BEGIN + json.dumps(notice, ensure_ascii=False),
    }


def test_owner_must_post_exact_one_off_marker():
    assert pilot.is_owner_pilot_event(owner_trigger())
    for changes in [
        ("issue", "number", 66),
        ("sender", "login", "attacker"),
        ("comment", "body", "SERVICEIT_B2B_NOTICE_V1"),
        ("action", None, "edited"),
    ]:
        event = owner_trigger()
        scope, key, value = changes
        if key is None:
            event[scope] = value
        else:
            event[scope][key] = value
        assert not pilot.is_owner_pilot_event(event)


def test_source_fetch_fixed_issue_and_owner(monkeypatch):
    called = []
    def transport(request):
        called.append((request.method, str(request.url)))
        assert request.method == "GET"
        assert str(request.url) == pilot.SOURCE_API
        return httpx.Response(200, json=public_source_comment())
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        event = pilot.load_existing_review(client, "github-test-token-notreal")
    assert event["comment"]["body"].startswith(pilot.BEGIN)
    assert called == [("GET", pilot.SOURCE_API)]
    assert event["issue"]["number"] == 66


@pytest.mark.parametrize("changed", ["wrong_owner", "wrong_tender"])
def test_untrusted_or_wrong_review_rejected(changed):
    comment = public_source_comment(
        from_user="attacker" if changed == "wrong_owner" else "Ramzes2023",
        source_id="4666206" if changed == "wrong_tender" else "4665948",
    )
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=comment))) as client:
        with pytest.raises(pilot.OneCardPilotBlocked):
            pilot.load_existing_review(client, "github-test-token-notreal")


def test_owner_pilot_calls_existing_writer_once_and_masks_card_id(monkeypatch):
    called = []
    monkeypatch.setattr(pilot, "load_existing_review",
                        lambda client, token: {"original": "existing owner comment"})
    def one_call(event, *, client, github_token, webhook, live):
        assert event == {"original": "existing owner comment"}
        assert live is True
        assert webhook == "synthetic corporate hook"
        called.append(1)
        return {
            "status": "ONE_TENDER_HOLD_VERIFIED", "card_id": 567,
            "tender_id": "4665948", "crm_writes": 1,
        }
    monkeypatch.setattr(pilot, "run_once", one_call)
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: (_ for _ in ()).throw(AssertionError("network denied")))) as c:
        result = pilot.run(owner_trigger(), client=c,
                           webhook="synthetic corporate hook",
                           github_token="github-test-token")
    assert result == {
        "status": "ONE_HOLD_VERIFIED_EXACT_READBACK",
        "tender_id": "4665948", "crm_writes": 1,
        "qualified_real_leads": 0, "gigachat_calls": 0,
    }
    assert called == [1]
    assert "card_id" not in result


def test_missing_hook_never_calls_network():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: (_ for _ in ()).throw(AssertionError("network denied")))) as c:
        result = pilot.run(owner_trigger(), client=c,
                           webhook="", github_token="fake")
    assert result == {"status": "CORPORATE_WEBHOOK_MISSING", "crm_writes": 0}

def test_narrow_database_support_service_fit_no_generic_service():
    assert "IT_SUPPORT" in classify_services(
        "Право заключения договора оказания услуг по техническому обслуживанию базы данных")
    assert "IT_SUPPORT" in classify_services("Сервисная поддержка СУБД")
    assert not classify_services("Техническое обслуживание складских стеллажей")
    assert not classify_services("Техническое обслуживание базы отдыха")
