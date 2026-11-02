"""One-shot HOLD write is opt-in, source-limited, fenced and mock-tested."""
from email.message import EmailMessage

import httpx
import pytest

from leadpilot.bitrix_1062_mapping import ALL_FIELD_CODES
from leadpilot.b2b_email_opportunity_bridge import build_bitrix_preview
from leadpilot.b2b_bitrix_readiness import ReadOnlyBitrix
from leadpilot.b2b_bitrix_hold_delivery import (
    APPROVAL, HoldWriteBlocked, deliver_one_hold, validated_hold,
)

HOOK = "https://serviseit.bitrix24.ru/rest/123/TESTreadOnlyToken123"


def sample():
    m = EmailMessage()
    m["From"] = "marketplace@b2b-center.ru"
    m["To"] = "fixtures@example.invalid"
    m["Subject"] = "Приглашение к участию в актуальных закупках"
    m["Date"] = "Mon, 02 Nov 2026 13:00:00 +0300"
    m.set_content(
        '<div>Организатор закупки: АО «Тестовый заказчик»'
        '<p>Предмет закупки: Сопровождение 1С:Бухгалтерия</p>'
        '<p>Актуально до: 16.11.2026 09:00</p>'
        '<a href="https://www.b2b-center.ru/market/example-1c/tender-4661617/?token=hidden">'
        'Посмотреть процедуру №4661617</a></div>', subtype="html")
    return build_bitrix_preview([m.as_bytes()])


def mock_transport(seen, *, remote_duplicate=False, add_failure=False, missing_stage=False):
    def request(req):
        method = req.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        import json
        data = json.loads(req.content)
        seen.append((method, data))
        if method == "crm.type.getByEntityTypeId":
            value = {"type": {"id": 13, "entityTypeId": 1062,
                      "isAutomationEnabled": False, "isBizProcEnabled": False}}
        elif method == "crm.category.list":
            value = {"categories": [{"id": 21}]}
        elif method == "crm.item.fields":
            value = {"fields": {k: {"type": "string", "isReadOnly": False,
                      "isImmutable": False} for k in ALL_FIELD_CODES}}
        elif method == "crm.status.list":
            value = [] if missing_stage else [
                {"ENTITY_ID": "DYNAMIC_1062_STAGE_21",
                 "STATUS_ID": "DT1062_21:NEW"}]
        elif method == "crm.item.list":
            assert data["filter"] == {"categoryId": 21, "UF_CRM_13_SVCIT_SIGNAL_ID": "B2B-CENTER-4661617"}
            value = {"items": ([{"id": 777, "entityTypeId": 1062,
                     "categoryId": 21, "UF_CRM_13_SVCIT_SIGNAL_ID": "B2B-CENTER-4661617"}]
                    if remote_duplicate else []), "total": (1 if remote_duplicate else 0)}
        elif method == "crm.item.add":
            if add_failure:
                return httpx.Response(503, json={"error": "TEMPORARY"})
            assert data["fields"]["UF_CRM_13_SVCIT_SIGNAL_KIND"] == "tender-opportunity"
            assert data["fields"]["UF_CRM_13_SVCIT_STOP_REASON"] == "Не является квалифицированным B2B-лидом"
            assert data["fields"]["UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"].startswith("B2B-HOLD-")
            value = {"item": {"id": 812}}
        elif method == "crm.item.get":
            submitted = next(d["fields"] for m, d in seen if m == "crm.item.add")
            value = {"item": {**submitted, "id": 812, "entityTypeId": 1062}}
        else:
            raise AssertionError("UNEXPECTED_API_METHOD")
        return httpx.Response(200, json={"result": value})
    return httpx.MockTransport(request)


def run(preview, tmp_path, *, approve=APPROVAL, original=True, **opts):
    events = []
    with httpx.Client(transport=mock_transport(events, **opts)) as client:
        result = deliver_one_hold(
            preview, tender_id="4661617", rest=ReadOnlyBitrix(HOOK, client=client),
            approval=approve, original_verified=original, fence_dir=tmp_path)
    return result, events


def test_one_bounded_hold_only_is_written_after_independent_checks(tmp_path):
    result, events = run(sample(), tmp_path)
    assert result["status"] == "ONE_TENDER_HOLD_WRITTEN_AND_READBACK_VERIFIED"
    assert result["qualified_real_leads"] == 0
    assert [m for m, p in events].count("crm.item.add") == 1
    assert [m for m, p in events].count("crm.item.get") == 1
    assert all(m not in ("crm.contact.list", "tasks.task.add", "crm.item.update")
               for m, p in events)
    assert list(tmp_path.glob("B2B-HOLD-*.json"))


def test_replay_blocked_by_durable_fence_even_with_new_client(tmp_path):
    run(sample(), tmp_path)
    with pytest.raises(HoldWriteBlocked, match="ALREADY_CLAIMED"):
        run(sample(), tmp_path)


def test_exact_existing_tender_skipped_before_post(tmp_path):
    result, events = run(sample(), tmp_path, remote_duplicate=True)
    assert result["status"] == "EXISTING_TENDER_REVIEW_NO_POST"
    assert all(m != "crm.item.add" for m, p in events)


def test_manual_approval_must_precede_metadata_and_source_checks(tmp_path):
    for approval, original in (("no", True), (APPROVAL, False)):
        with pytest.raises(HoldWriteBlocked, match="APPROVAL_MISSING"):
            run(sample(), tmp_path, approve=approval, original=original)
    assert not list(tmp_path.iterdir())


def test_bad_stage_blocks_without_any_crm_mutations(tmp_path):
    with pytest.raises(Exception, match="SAFE_NEW_STAGE_NOT_VERIFIED"):
        run(sample(), tmp_path, missing_stage=True)
    assert not list(tmp_path.iterdir())


def test_ambiguous_post_is_irreversible_local_fence(tmp_path):
    with pytest.raises(HoldWriteBlocked, match="UNCERTAIN_DO_NOT_RETRY"):
        run(sample(), tmp_path, add_failure=True)
    claims = list(tmp_path.glob("B2B-HOLD-*.json"))
    assert len(claims) == 1
    import json
    assert json.loads(claims[0].read_text())["state"] == "POST_UNCERTAIN"
    with pytest.raises(HoldWriteBlocked, match="ALREADY_CLAIMED"):
        run(sample(), tmp_path)


def test_reject_fake_qualification_and_contacts_before_any_network():
    preview = sample()
    preview["qualified_real_leads"] = 1
    with pytest.raises(HoldWriteBlocked, match="NOT_A_HOLD_PREVIEW"):
        validated_hold(preview, tender_id="4661617")
    preview = sample()
    preview["cards_not_sent"][0]["fields_not_sent"]["UF_CRM_13_SVCIT_COMPANY_NAME"] = "send to person@example.com"
    with pytest.raises(HoldWriteBlocked, match="BUYER_OR_CONTACT_NOT_VERIFIED"):
        validated_hold(preview, tender_id="4661617")


def test_no_wrong_tender_or_arbitrary_fields():
    preview = sample()
    with pytest.raises(HoldWriteBlocked, match="SOURCE_NOT_UNIQUE"):
        validated_hold(preview, tender_id="1234567")
    preview["cards_not_sent"][0]["fields_not_sent"]["phone"] = "0123456789"
    with pytest.raises(HoldWriteBlocked, match="REVIEW_FIELDS_NOT_CANONICAL"):
        validated_hold(preview, tender_id="4661617")
