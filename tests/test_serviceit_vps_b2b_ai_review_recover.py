"""No network/CRM: contract tests for two exact B2B native AI HOLD receipts."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone, timedelta

import pytest

from ops import serviceit_vps_b2b_ai_review_recover as repair
from leadpilot.serviceit_enrichment_worker import FIELDS, AI_FIELDS, PREFIX


def card():
    item = {k: None for k in FIELDS}
    item.update({
        "id": 88, "entityTypeId": 1062, "categoryId": 21,
        "createdTime": datetime.now(timezone.utc).isoformat(),
        "title": "📑 ТЕНДЕР · B2B-Center",
        PREFIX + "SIGNAL_ID": "B2B-CENTER-1234567",
        PREFIX + "SIGNAL_SOURCE": "B2B-Center email subscription",
        PREFIX + "SIGNAL_KIND": "tender-opportunity",
        PREFIX + "SIGNAL_URL":
            "https://www.b2b-center.ru/market/example/tender-1234567/",
        PREFIX + "EVIDENCE_STATUS": "HOLD: уведомление, не проверено",
        PREFIX + "STOP_REASON": "Не квалифицированный B2B-лид",
        PREFIX + "COMPANY_NAME": "ООО ПРИМЕР",
        PREFIX + "SUGGESTED_SERVICE": "1C",
    })
    return item


def test_candidate_honest_unqualified_nine_fields_and_no_rest_write():
    item = card()
    calls = []
    def rest(method, params):
        calls.append(method)
        assert method == "crm.item.get"
        return {"item": deepcopy(item)}
    before, fields = repair.candidate_fields(
        rest, 88, "B2B-CENTER-1234567",
        datetime.now(timezone.utc) - timedelta(days=2),
        {k: 12000 for k in AI_FIELDS})
    assert before["id"] == 88
    assert set(fields) == AI_FIELDS
    assert fields[PREFIX + "AI_STATUS"] == "NOT_RUN_SOURCE_UNVERIFIED"
    assert calls == ["crm.item.get"]


def test_existing_score_or_ai_is_not_eligible():
    item = card()
    item[PREFIX + "LEAD_SCORE"] = 90
    def rest(method, params):
        assert method == "crm.item.get"
        return {"item": deepcopy(item)}
    with pytest.raises(repair.RecoveryBlocked):
        repair.candidate_fields(
            rest, 88, "B2B-CENTER-1234567",
            datetime.now(timezone.utc) - timedelta(days=2),
            {k: 12000 for k in AI_FIELDS})


def test_recovery_checkpoint_must_precede_the_only_crm_update(monkeypatch):
    item = card()
    event = []
    def rest(method, params):
        event.append(method)
        if method == "crm.item.update":
            assert "checkpoint" in event
            assert params["id"] == 88
            assert set(params["fields"]) == AI_FIELDS
            item.update(params["fields"])
            return {"item": deepcopy(item)}
        assert method == "crm.item.get"
        return {"item": deepcopy(item)}
    monkeypatch.setattr(repair, "checkpoint",
                        lambda conn, card, fields:
                            (event.append("checkpoint") or b"0"*16, b"1"*32))
    monkeypatch.setattr(repair, "acknowledge",
                        lambda conn, card, salt, digest: event.append("acknowledge"))
    repair.recover_one(
        object(), rest, 88, "B2B-CENTER-1234567",
        datetime.now(timezone.utc) - timedelta(days=2),
        {k: 12000 for k in AI_FIELDS})
    assert event == [
        "crm.item.get", "crm.item.get", "checkpoint",
        "crm.item.update", "crm.item.get", "acknowledge"]
