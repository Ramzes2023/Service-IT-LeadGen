import hashlib
import hmac
import json
import time

import httpx
import pytest

from leadpilot import b2b_github_to_bitrix as bridge
from leadpilot import serviceit_b2b_appsscript_ingest as ingest

SECRET = "synthetic-serviceit-ingest-hmac-secret-0123456789"
TOKEN = "synthetic-github-token-not-valid"
WEBHOOK = "https://serviseit.bitrix24.ru/rest/2/SYNTHETICONLY123456"


def notice():
    return {
        "schema": "serviceit-b2b-notice-v1",
        "source": "B2B-Center",
        "email_message_id": "1a19b7630301bafb",
        "tender_id": "1234567",
        "source_url": "https://www.b2b-center.ru/market/pravovoe-soprovozhdenie/tender-1234567/",
        "subject": "Услуги технической поддержки IT-инфраструктуры",
        "organizer": "АО СО ЕЭС",
        "buyer_inn": None,
        "fns_msp": None,
        "region": None,
        "original_publication_verified": False,
        "contact_approved": False,
        "qualified_lead": False,
    }


def event(*, n=None, timestamp=None, secret=SECRET, mutate=None):
    body = {
        "version": 1,
        "mailbox": ingest.EXPECTED_MAILBOX,
        "email_message_id": "1a19b7630301bafb",
        "dispatch_id": "synthetic-dispatch-0123456789",
        "notice": notice() if n is None else n,
    }
    payload_json = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    ts = str(int(time.time()) if timestamp is None else timestamp)
    signature = "sha256=" + hmac.new(
        secret.encode(), (ts + "." + payload_json).encode(), hashlib.sha256
    ).hexdigest()
    result = {
        "repository": {"full_name": ingest.REPO},
        "action": ingest.EVENT_TYPE,
        "client_payload": {
            "timestamp": ts,
            "payload_json": payload_json,
            "signature": signature,
        },
    }
    if mutate:
        mutate(result)
    return result


def env(**extra):
    return {
        "SERVICEIT_B2B_INGEST_HMAC_KEY": SECRET,
        "SERVICEIT_BITRIX_WEBHOOK": WEBHOOK,
        "GITHUB_TOKEN": TOKEN,
        "GITHUB_EVENT_NAME": "repository_dispatch",
        "GITHUB_REPOSITORY": ingest.REPO,
        "GITHUB_REF": "refs/heads/main",
        **extra,
    }


def test_valid_signed_notice_reconstructs_strict_owner_notice():
    synthetic, dispatch_id = ingest.validate_dispatch(event(), SECRET)
    parsed, services = bridge.notice_from_event(synthetic)
    assert dispatch_id == "synthetic-dispatch-0123456789"
    assert parsed["tender_id"] == "1234567"
    assert services == ["IT_SUPPORT"]
    assert parsed["qualified_lead"] is False


@pytest.mark.parametrize("mutation", [
    lambda e: e.update(repository={"full_name": "attacker/repo"}),
    lambda e: e.update(action="other"),
    lambda e: e["client_payload"].update(signature="sha256=" + "0" * 64),
    lambda e: e["client_payload"].update(extra="not-allowed"),
])
def test_untrusted_or_modified_dispatch_is_rejected(mutation):
    value = event(mutate=mutation)
    with pytest.raises(ingest.IngestBlocked):
        ingest.validate_dispatch(value, SECRET)


def test_expired_dispatch_is_rejected():
    with pytest.raises(ingest.IngestBlocked, match="DISPATCH_EXPIRED"):
        ingest.validate_dispatch(event(timestamp=int(time.time()) - 600), SECRET)


def test_short_hmac_secret_is_rejected():
    with pytest.raises(ingest.IngestBlocked, match="HMAC_SECRET_MISSING"):
        ingest.validate_dispatch(event(), "short")


@pytest.mark.parametrize("field,value", [
    ("buyer_inn", "1234567890"),
    ("fns_msp", "micro"),
    ("region", "77"),
    ("original_publication_verified", True),
    ("contact_approved", True),
    ("qualified_lead", True),
])
def test_dispatch_cannot_assert_qualification(field, value):
    value_notice = notice()
    value_notice[field] = value
    synthetic, _ = ingest.validate_dispatch(event(n=value_notice), SECRET)
    with pytest.raises(bridge.BridgeBlocked, match="UNVERIFIED_VENDOR_EMAIL_CANNOT_BE_LEAD"):
        bridge.notice_from_event(synthetic)


def test_signed_notice_delegates_to_existing_guarded_writer(monkeypatch):
    seen = {}
    def fake_run_once(synthetic, *, client, github_token, webhook, live):
        parsed, services = bridge.notice_from_event(synthetic)
        seen.update({
            "notice": parsed,
            "services": services,
            "token": github_token,
            "webhook": webhook,
            "live": live,
            "client_type": isinstance(client, httpx.Client),
        })
        return {
            "status": "ONE_TENDER_HOLD_VERIFIED",
            "crm_writes": 1,
            "qualified_leads": 0,
            "gigachat_attempted": True,
            "ai_status": "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED",
        }
    monkeypatch.setattr(ingest, "run_once", fake_run_once)
    with httpx.Client() as client:
        result = ingest.execute(event(), env(), client=client)
    assert result["status"] == "SERVICEIT_B2B_INGEST_COMPLETE"
    assert result["crm_writes"] == 1
    assert result["qualified_leads"] == 0
    assert result["gigachat_attempted"] is True
    assert result["ai_status"] == "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED"
    assert seen["token"] == TOKEN and seen["webhook"] == WEBHOOK and seen["live"] is True


def test_writer_duplicate_is_reported_without_second_crm_write(monkeypatch):
    monkeypatch.setattr(ingest, "run_once", lambda *a, **k: {
        "status": "ALREADY_RESERVED_DO_NOT_RETRY", "crm_writes": 0,
        "qualified_leads": 0,
    })
    with httpx.Client() as client:
        result = ingest.execute(event(), env(), client=client)
    assert result["crm_writes"] == 0
    assert result["qualified_leads"] == 0


def test_unexpected_writer_status_fails_closed(monkeypatch):
    monkeypatch.setattr(ingest, "run_once", lambda *a, **k: {
        "status": "SOME_UNKNOWN_STATE", "crm_writes": "UNVERIFIED",
    })
    with httpx.Client() as client:
        with pytest.raises(ingest.IngestBlocked, match="B2B_WRITER_RESULT_UNEXPECTED"):
            ingest.execute(event(), env(), client=client)


def test_workflow_is_free_signed_and_does_not_use_crm_item_list_as_a_poller():
    from pathlib import Path
    workflow = Path(".github/workflows/serviceit-b2b-appsscript-ingest.yml").read_text()
    assert "repository_dispatch" in workflow
    assert "serviceit_b2b_notice" in workflow
    assert "SERVICEIT_B2B_INGEST_HMAC_KEY" in workflow
    assert "SERVICEIT_B2B_INGEST_ENABLED" in workflow
    assert "SERVICEIT_BITRIX_WEBHOOK" in workflow
    assert "GIGACHAT_CREDENTIALS" in workflow
    assert "crm.item.list" not in workflow
