"""Every future B2B-Center HOLD has meaningful nine-field PRELIMINARY analysis.

No false GigaChat completion, Lead Score, buyer identity or contact approval.
"""
from copy import deepcopy

import pytest

from leadpilot.serviceit_b2b_hold_ai_fields import (
    AI_FIELDS, HoldAnalysisRejected, hold_analysis_fields,
)
from leadpilot.b2b_github_to_bitrix import (
    notice_from_event, hold_payload,
)
from test_b2b_github_to_bitrix import fixture_event


def test_all_nine_existing_fields_present_on_new_b2b_hold():
    notice,services=notice_from_event(fixture_event())
    fields=hold_analysis_fields(notice,services)
    assert set(fields)==AI_FIELDS
    assert len(fields)==9
    assert fields["UF_CRM_13_SVCIT_AI_STATUS"]=="NOT_RUN_SOURCE_UNVERIFIED"
    assert fields["UF_CRM_13_SVCIT_AI_SUMMARY"].startswith("📑 ТЕНДЕР")
    assert fields["UF_CRM_13_SVCIT_AI_MANAGER_BRIEF"].startswith("📑 ТЕНДЕР")
    assert "дедлайн" in fields["UF_CRM_13_SVCIT_AI_MANAGER_BRIEF"]
    assert "подрядчику" not in fields["UF_CRM_13_SVCIT_AI_NEXT_ACTION"]

    assert "AI-провайдер: NOT_RUN" in fields["UF_CRM_13_SVCIT_AI_FULL_REPORT"]
    assert "НЕ РАССЧИТАН" in fields["UF_CRM_13_SVCIT_AI_FULL_REPORT"]
    assert "контакт" in fields["UF_CRM_13_SVCIT_AI_LIMITATIONS"]
    assert "Поддержка 1С:ЗУП" in fields["UF_CRM_13_SVCIT_AI_QUOTE"]
    assert all(0 < len(v) < 12000 for v in fields.values())
    payload=hold_payload(notice,services)
    assert set(fields).issubset(payload["fields"])
    assert payload["fields"]["UF_CRM_13_SVCIT_STOP_REASON"]=="Не квалифицированный B2B-лид"
    assert "UF_CRM_13_SVCIT_LEAD_SCORE" not in payload["fields"]
    assert "UF_CRM_13_SVCIT_AI_SCORE_BADGE" not in payload["fields"]
    assert payload["fields"]["stageId"]=="DT1062_21:NEW"
    # CRM title is visible before opening a Kanban card even when the shared
    # AI_SUMMARY placement in the Kanban UI still needs corporate acceptance.
    assert payload["fields"]["title"].startswith("📑 ТЕНДЕР")


@pytest.mark.parametrize("key,value",[
    ("qualified_lead",True),("contact_approved",True),
    ("original_publication_verified",True),("buyer_inn","7707083893"),
    ("subject","<script>alert(1)</script>"),
    ("subject","Попытка\nвзломать карточку"),
])
def test_reject_unsafe_or_falsely_verified_input(key,value):
    notice,services=notice_from_event(fixture_event())
    notice[key]=value
    with pytest.raises(HoldAnalysisRejected):
        hold_analysis_fields(notice,services)


def test_source_service_group_is_not_fabricated():
    notice,services=notice_from_event(fixture_event())
    with pytest.raises(HoldAnalysisRejected,match="SERVICES_INVALID"):
        hold_analysis_fields(notice,["НЕСУЩЕСТВУЮЩИЙ СЕРВИС"])
    with pytest.raises(HoldAnalysisRejected):
        hold_analysis_fields(notice,[])


def test_does_not_call_model_or_touch_any_real_crm():
    from pathlib import Path
    import leadpilot.serviceit_b2b_hold_ai_fields as impl
    src=Path(impl.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "classify_cockpit_signal(", "gigachat.invoke(", "httpx.Client(",
        "crm.item.update", "crm.item.add", "rest(", "send_email("
    ):
        assert forbidden not in src
