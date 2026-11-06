"""Offline safety checks for the free official-MSP, own-funnel final INTERNAL score."""
from copy import deepcopy
from datetime import datetime, timezone

import httpx
import pytest

import leadpilot.serviceit_leadgen_free_msp_score as score
from leadpilot.serviceit_enrichment_worker import AI_FIELDS, PREFIX
from leadpilot.serviceit_kanban_presentation import (
    SCORE_FIELD, BADGE_FIELD, VERSION_FIELD,
)

NOW = datetime(2026, 11, 6, 19, 0, tzinfo=timezone.utc)
INN = "7707083893"
ENV = {
    "GITHUB_REPOSITORY": score.REPOSITORY,
    "GITHUB_REF": "refs/heads/main",
    "SERVICEIT_LEADGEN_FREE_SCORE_ENABLED": score.GATE,
    "SERVICEIT_BITRIX_WEBHOOK":
        "https://serviseit.bitrix24.ru/rest/1615/TEST_TOKEN12345678",
    "SERVICEIT_BITRIX_POLL_START_UTC": "2026-11-06T00:00:00+00:00",
}


def item():
    obj = {
        "id": 123, "entityTypeId": 1062, "categoryId": 21,
        "createdTime": "2026-11-06T18:00:00+00:00",
        "stageId": "DT1062_21:NEW", "title": "Закупка 1С",
        PREFIX + "COMPANY_INN": INN,
        PREFIX + "SIGNAL_ID": "B2B-NEW-123",
        PREFIX + "SIGNAL_KIND": "tender-opportunity",
        PREFIX + "SIGNAL_SOURCE": "B2B-Center",
        PREFIX + "SIGNAL_URL": "https://example.org/procurement/123",
        PREFIX + "SIGNAL_DATE": "2026-11-05",
        PREFIX + "REGION": "Москва",
        SCORE_FIELD: None, VERSION_FIELD: "", BADGE_FIELD: None,
    }
    for key in AI_FIELDS:
        obj[key] = "Проверка человеком обязательна"
    obj[PREFIX + "AI_STATUS"] = "NOT_RUN_SOURCE_UNVERIFIED"
    return obj


class Fence:
    def __init__(self, *args):
        self.receipt = None
        self.messages = []
    def reserve(self, card):
        self.messages.append("reserved")
        return "RESERVED"
    def finalize(self, card, state):
        self.messages.append(state)
        return True


def wire(monkeypatch, row=None, *, own_rules=None, own_dup=False,
         corporate_result="GLOBAL_CRM_UNVERIFIED", msp="SMALL"):
    crm = deepcopy(row if row is not None else item())
    updates = []
    monkeypatch.setattr(score, "card_read", lambda *a, **kw: deepcopy(crm))
    monkeypatch.setattr(score, "read_rules", lambda: own_rules or [])
    monkeypatch.setattr(score, "after_activation", lambda *a: NOW.replace(hour=0))
    monkeypatch.setattr(score, "classify_preliminary",
                        lambda *a: {"type": "tender", "indicative_priority": 65})
    monkeypatch.setattr(score, "local_duplicate_status",
                        lambda *a: "HOLD_OWN_FUNNEL_DUPLICATE" if own_dup
                        else "NO_MATCH_IN_OWN_VISIBLE_FUNNEL")
    monkeypatch.setattr(score, "visible_corporate_duplicate_veto",
                        lambda *a: corporate_result)
    monkeypatch.setattr(score, "recent_receipt", lambda *a, **kw:
                        {"msp_category": msp, "published": "2026-10-10",
                         "fns_egrul_verified": False,
                         "formal_score_authorized": False})
    monkeypatch.setattr(score, "score_schema_ok", lambda *a: True)
    monkeypatch.setattr(score, "InternalScoreFence", Fence)
    def rest(client, base, method, params):
        if method == "crm.item.update":
            assert set(params["fields"]) == score.KEYS
            updates.append(deepcopy(params["fields"]))
            crm.update(params["fields"])
            return {}
        if method == "crm.item.get":
            return {"item": deepcopy(crm)}
        raise AssertionError(method)
    monkeypatch.setattr(score, "score_rest", rest)
    return crm, updates


@pytest.mark.parametrize("msp,expected", [
    ("MICRO", 65), ("SMALL", 69), ("MEDIUM", 73), (None, 65),
])
def test_official_free_msp_internal_score_readback(monkeypatch, msp, expected):
    crm, updates = wire(monkeypatch, msp=msp)
    with httpx.Client() as client:
        assert score.evaluate_one(client, ENV, 123, now=NOW) == (
            "INTERNAL_SCORE_EXACT_READBACK")
        assert score.evaluate_one(client, ENV, 123, now=NOW) == (
            "ALREADY_INTERNAL_SCORE_NO_REWRITE")
    assert len(updates) == 1 and updates[0][SCORE_FIELD] == expected
    assert crm[VERSION_FIELD] == score.VERSION
    assert "ВНУТРЕННИЙ ИТОГ" in crm[BADGE_FIELD]
    assert "Контакт: нет" in crm[BADGE_FIELD]
    assert "крупное" not in crm[BADGE_FIELD]
    assert "ЕГРЮЛ" not in crm[BADGE_FIELD]


def test_own_stop_blocks_without_any_msp_or_crm_write(monkeypatch):
    _, updates = wire(monkeypatch, own_rules=[
        {"kind": "card_id", "value": "123", "reason": "NOT_RELEVANT"}])
    monkeypatch.setattr(score, "recent_receipt", lambda *a, **kw:
                        pytest.fail("STOP prevents FNS access"))
    with httpx.Client() as client:
        assert score.evaluate_one(client, ENV, 123, now=NOW) == "HOLD_OWN_STOP"
    assert updates == []


def test_own_funnel_duplicate_always_blocks(monkeypatch):
    _, updates = wire(monkeypatch, own_dup=True)
    with httpx.Client() as client:
        assert score.evaluate_one(client, ENV, 123, now=NOW) == (
            "HOLD_OWN_FUNNEL_DUPLICATE")
    assert updates == []


def test_visible_corporate_match_blocks_but_missing_access_does_not(
        monkeypatch):
    _, updates = wire(monkeypatch, corporate_result="HOLD_VISIBLE_CORPORATE_MATCH")
    with httpx.Client() as client:
        assert score.evaluate_one(client, ENV, 123, now=NOW) == (
            "HOLD_VISIBLE_CORPORATE_MATCH")
    assert updates == []


def test_missing_official_msp_receipt_keeps_score_unfinalized(monkeypatch):
    _, updates = wire(monkeypatch)
    monkeypatch.setattr(score, "recent_receipt", lambda *a, **kw: None)
    with httpx.Client() as client:
        assert score.evaluate_one(client, ENV, 123, now=NOW) == (
            "WAIT_FOR_OFFICIAL_MSP_RECEIPT")
    assert updates == []


def test_manual_score_cannot_be_overwritten(monkeypatch):
    row = item()
    row[SCORE_FIELD], row[VERSION_FIELD], row[BADGE_FIELD] = (
        92, "MANUAL_BY_SALES", "92/100")
    _, updates = wire(monkeypatch, row=row)
    with httpx.Client() as client:
        assert score.evaluate_one(client, ENV, 123, now=NOW) == (
            "MANUAL_OR_OTHER_SCORE_PROTECTED")
    assert updates == []


def test_absent_in_msp_never_implies_large_or_registry_inactive():
    data = score.internal_payload(61, {
        "msp_category": None, "published": "2026-10-10",
        "fns_egrul_verified": False, "formal_score_authorized": False})
    assert "размер неизвестен" in data[BADGE_FIELD]
    assert data[SCORE_FIELD] == 61


def test_no_paid_apis_or_outreach_in_new_workflow():
    from pathlib import Path
    python_file = Path("leadpilot/serviceit_leadgen_free_msp_score.py").read_text("utf8")
    yml = Path(".github/workflows/serviceit-leadgen-free-msp-internal-score.yml").read_text("utf8")
    assert "crm.item.add" not in python_file
    assert "crm.contact.update" not in python_file
    assert "tasks.task.add" not in python_file
    assert "SERVICEIT_CORPORATE_STOP_INN" not in yml
    assert "SERVICEIT_STAGE4_CORPORATE_VERIFIER_ENABLED" not in yml
    assert "workflow_run:" in yml
    assert "SERVICEIT_FNS_MSP" not in yml or "FNS MSP" in yml
