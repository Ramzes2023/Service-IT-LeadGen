"""Offline privacy, budget and exact-write tests for public-title GigaChat.

Only public tender title can reach the synthetic model; no network.
"""
from copy import deepcopy
import pytest

from leadpilot.serviceit_enrichment_store import Store
from leadpilot.serviceit_preliminary_gigachat import (
    PreliminaryAIBlocked, allowed_public_title, model_hypothesis_fields,
)
from leadpilot.serviceit_windows_autofill import (
    AI_FIELDS, FIELDS, PREFIX, preliminary_fields, preliminary_once,
)
from leadpilot.serviceit_bitrix_event_autofill_app import runtime_config, RuntimeBlocked, LIVE_FLAG


class CRM:
    def __init__(self, title="Поставка серверов и IT-аутсорсинг",
                 source="https://www.b2b-center.ru/market/a/tender-1234567/"):
        self.item = {name: None for name in FIELDS}
        self.item.update({
            "id": 1001, "entityTypeId": 1062, "categoryId": 21,
            "title": title,
            PREFIX+"SIGNAL_ID": "SOME-PUBLIC-TENDER-1234567",
            PREFIX+"SIGNAL_KIND": "tender-opportunity",
            PREFIX+"SIGNAL_URL": source,
            PREFIX+"SIGNAL_SOURCE": "Public procurement feed",
            PREFIX+"STOP_REASON": "",
            PREFIX+"COMPANY_NAME": "Заказчик только в CRM",
            PREFIX+"COMPANY_INN": "7701234567",
        })
        self.calls = []

    def __call__(self, method, params):
        self.calls.append(method)
        if method == "crm.item.get":
            return {"item": deepcopy(self.item)}
        if method == "crm.item.update":
            assert set(params["fields"]) == AI_FIELDS
            self.item.update(params["fields"])
            return {"item": deepcopy(self.item)}
        raise AssertionError(method)


@pytest.fixture
def schema(monkeypatch):
    import leadpilot.serviceit_windows_autofill as m
    monkeypatch.setattr(m, "schema_limits",
                        lambda _: {k: 12000 for k in FIELDS})


def approved_hosts():
    return frozenset({"b2b-center.ru", "zakupki.gov.ru"})


def valid_output():
    return {
        "origin": "AI_hypothesis",
        "provider": "gigachat",
        "model": "GigaChat-2",
        "quality_status": "complete",
        "service": "IT-аутсорсинг",
        "possible_need": "Возможно требуется сопровождение инфраструктуры.",
        "evidence_quote": "Поставка серверов",
        "confidence": 0.82,
    }


def test_only_approved_public_title_reaches_synthetic_gigachat(tmp_path, schema):
    crm = CRM()
    store = Store(tmp_path / "new-card-events.sqlite3")
    store.enqueue(1001)
    seen = []
    def model(text):
        seen.append(text)
        return valid_output()
    state = preliminary_once(
        store, crm, 1001, model=model, public_ai_hosts=approved_hosts())
    assert state == "HOLD_9_WRITTEN"
    assert seen == ["Поставка серверов и IT-аутсорсинг"]
    assert crm.item[PREFIX+"AI_STATUS"] == "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED"
    assert crm.item[PREFIX+"AI_QUOTE"] == "Поставка серверов"
    assert "GigaChat" in crm.item[PREFIX+"AI_FULL_REPORT"]
    assert "НЕ РАССЧИТАН" in crm.item[PREFIX+"AI_FULL_REPORT"]
    assert crm.item[PREFIX+"LEAD_SCORE"] is None
    assert crm.item[PREFIX+"AI_SCORE_BADGE"] is None
    assert store.metrics() == {"HOLD": 1}
    assert crm.calls.count("crm.item.update") == 1


def test_invalid_model_output_never_fabricates_quote_or_score(tmp_path, schema):
    crm = CRM()
    store = Store(tmp_path / "new-card-events.sqlite3")
    store.enqueue(1001)
    def model(text):
        return {**valid_output(), "evidence_quote": "Несуществующий бюджет утверждён"}
    state = preliminary_once(store, crm, 1001, model=model,
                             public_ai_hosts=approved_hosts())
    assert state == "HOLD_9_WRITTEN"
    assert crm.item[PREFIX+"AI_STATUS"] == "GIGACHAT_ATTEMPT_UNVERIFIED"
    assert crm.item[PREFIX+"LEAD_SCORE"] is None
    assert crm.calls.count("crm.item.update") == 1
    assert preliminary_once(store, crm, 1001, model=model,
                            public_ai_hosts=approved_hosts()) == "IDLE"
    assert crm.calls.count("crm.item.update") == 1


@pytest.mark.parametrize("source,title", [
    ("https://b2b-center.ru.evil.test/market/a/tender-1234567/", "Поставка серверов"),
    ("http://www.b2b-center.ru/market/a/tender-1234567/", "Поставка серверов"),
    ("https://www.b2b-center.ru/market/a/tender-1234567/", "Свяжитесь с manager@example.ru"),
    ("https://www.b2b-center.ru/market/a/tender-1234567/", "Телефон +7 999 123 45 67"),
])
def test_unsafe_title_or_source_never_reaches_model(tmp_path, schema, source, title):
    crm = CRM(title=title, source=source)
    store = Store(tmp_path / "new-card-events.sqlite3")
    store.enqueue(1001)
    def model(_):
        pytest.fail("unsafe title/source must never go to GigaChat")
    assert preliminary_once(store, crm, 1001, model=model,
                            public_ai_hosts=approved_hosts()) == "HOLD_9_WRITTEN"
    assert crm.item[PREFIX+"AI_STATUS"] == "NOT_RUN_SOURCE_UNVERIFIED"


def test_absent_public_host_approval_blocks_model(tmp_path, schema):
    crm = CRM()
    store = Store(tmp_path / "new-card-events.sqlite3")
    store.enqueue(1001)
    def model(_):
        pytest.fail("model invoked without public source approval")
    preliminary_once(store, crm, 1001, model=model)
    assert crm.item[PREFIX+"AI_STATUS"] == "NOT_RUN_SOURCE_UNVERIFIED"


def test_only_nine_fields_written_and_model_confidence_never_becomes_lead_score():
    crm = CRM()
    fields = model_hypothesis_fields(
        preliminary_fields(crm.item), crm.item["title"], valid_output())
    assert set(fields) == AI_FIELDS
    assert all(isinstance(x, str) for x in fields.values())
    assert "0.82" not in fields[PREFIX+"AI_FULL_REPORT"]
    assert "Поставка серверов" in fields[PREFIX+"AI_QUOTE"]


def test_privacy_is_required_even_when_source_domain_is_approved(tmp_path):
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    env = {
        "SERVICEIT_EVENT_AUTOFILL_ENABLED": LIVE_FLAG,
        "SERVICEIT_BITRIX_APPLICATION_TOKEN": "SYNTHETIC-SERVICEIT-APP-TOKEN-ONLY-123456",
        "SERVICEIT_BITRIX_WEBHOOK": "https://serviseit.bitrix24.ru/rest/2/SYNTHETIC",
        "SERVICEIT_BITRIX_EVENT_QUEUE_DB": str(tmp_path / "new-card-events.sqlite3"),
        "SERVICEIT_APPROVAL_DIR": str(approvals),
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "NO",
        "SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED": "YES",
        "SERVICEIT_PRELIMINARY_PUBLIC_HOSTS": "b2b-center.ru",
    }
    with pytest.raises(RuntimeBlocked, match="PRELIMINARY_AI_PRIVACY_REQUIRED"):
        runtime_config(env, is_mount=lambda _: True)
    env.update({
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
        "AI_ENABLE": "1", "AI_PROVIDER": "gigachat",
        "GIGACHAT_CREDENTIALS": "SYNTHETIC-DO-NOT-USE",
        "GIGACHAT_SCOPE": "GIGACHAT_API_B2B",
    })
    config = runtime_config(env, is_mount=lambda _: True)
    assert config["model_enabled"] is True
    assert config["public_ai_hosts"] == frozenset({"b2b-center.ru"})
    env["SERVICEIT_PRELIMINARY_PUBLIC_HOSTS"] = "b2b-center.ru.evil.test,"
    with pytest.raises(RuntimeBlocked, match="PRELIMINARY_AI_PUBLIC_HOST_ALLOWLIST_REQUIRED"):
        runtime_config(env, is_mount=lambda _: True)


def test_gigachat_hypothesis_stays_unqualified_without_independent_gates():
    data = valid_output()
    data["confidence"] = 0.999
    fields = model_hypothesis_fields(preliminary_fields(CRM().item),
                                     "Поставка серверов и IT-аутсорсинг", data)
    assert fields[PREFIX+"AI_STATUS"].startswith("HYPOTHESIS_")
    assert "НЕ РАССЧИТАН" in fields[PREFIX+"AI_FULL_REPORT"]
    assert all("qualified" not in str(value).lower() for value in fields.values())
