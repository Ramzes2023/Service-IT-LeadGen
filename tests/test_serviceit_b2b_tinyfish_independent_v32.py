"""V32: mandatory, policy-gated TinyFish public research is independent of Brave.

Offline tests only: no Bitrix, paid model, VPS, API key or network operations.
"""
from unittest.mock import Mock

import pytest

from leadpilot import serviceit_vps_b2b_deep_research as research
from leadpilot import serviceit_b2b_free_public_fallback as free
from leadpilot import serviceit_b2b_public_mirrors as mirrors

ID = "4673971"
SUBJECT = "Поставка серверного оборудования для компании"
PRIMARY_URL = "https://www.b2b-center.ru/market/servers/tender-4673971/"
TEND_URL = "https://tendem.ru/tenders/16890012"
NOTICE = {
    "source": "B2B-Center",
    "tender_id": ID,
    "subject": SUBJECT,
    "source_url": PRIMARY_URL,
}
ENV = {
    free.ENABLE: "YES",
    free.T_EN: "YES",
    free.B_EN: "NO",
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
}
REMOTE = {
    "status": "PUBLIC_PAGES_INSPECTED",
    "pages": [{"url": TEND_URL,
               "excerpt": "Тендер № 4673971. Поставка серверного оборудования. "
                          "Заказчик ПАО «Пример». Срок подачи до 17.11.2026."}],
    "structured_facts": [{"deadline": {"value": "17.11.2026",
                                      "source_url": TEND_URL}}],
    "model_excerpt": "Тендер № 4673971. Поставка серверного оборудования.",
}


def _arrange(monkeypatch, *, primary, remote, policy=None):
    calls = []
    monkeypatch.setattr(research, "host_allowlist",
                        lambda env: frozenset({"b2b-center.ru"}))
    monkeypatch.setattr(research, "secret_file", lambda *a, **k: "")
    monkeypatch.setattr(research, "research_public", lambda *a, **k: primary)
    monkeypatch.setattr(research, "brave_notice_page", lambda *a, **k: None)
    monkeypatch.setattr(mirrors, "brave_mirror_search", lambda *a, **k: None)
    monkeypatch.setattr(research, "followup_public_buyer_research",
                        lambda findings, *a, **k: findings)
    def provider(notice, env, client, *, index_hints=None):
        calls.append((notice["tender_id"], index_hints))
        return remote
    monkeypatch.setattr(free, "read_free_public_mirror", provider)
    env = {**ENV, **(policy or {})}
    return env, calls


def test_tinyfish_still_runs_once_after_rich_brave_evidence(monkeypatch):
    rich = {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": PRIMARY_URL,
                   "excerpt": "Тендер 4673971. Заказчик ПАО Пример. "
                              "Срок подачи 17 ноября. Документы для заявки. "
                              "Оплата после поставки. Место доставки Москва."}],
    }
    env, calls = _arrange(monkeypatch, primary=rich, remote=REMOTE)
    output = research.source_findings({}, NOTICE, env, Mock())
    assert calls == [(ID, [])]
    assert output["tinyfish_independent_stage"] == "VERIFIED"
    assert output["pages"] == rich["pages"] + REMOTE["pages"]
    assert output["structured_facts"] == REMOTE["structured_facts"]
    assert rich["pages"] == [{
        "url": PRIMARY_URL,
        "excerpt": rich["pages"][0]["excerpt"],
    }]


def test_tinyfish_can_rescue_missing_brave_page(monkeypatch):
    env, calls = _arrange(
        monkeypatch,
        primary={"status": "PUBLIC_TENDER_NOT_FOUND", "pages": []},
        remote=REMOTE)
    output = research.source_findings({}, NOTICE, env, Mock())
    assert calls == [(ID, [])]
    assert output["status"] == "PUBLIC_PAGES_INSPECTED"
    assert output["pages"] == REMOTE["pages"]
    assert output["tinyfish_independent_stage"] == "VERIFIED"


def test_failed_provider_does_not_erase_good_source_and_marks_retry(monkeypatch):
    primary = {"status": "PUBLIC_PAGES_INSPECTED",
               "pages": [{"url": PRIMARY_URL,
                          "excerpt": "Тендер 4673971, серверы и документы."}]}
    env, calls = _arrange(monkeypatch, primary=primary, remote=None)
    output = research.source_findings({}, NOTICE, env, Mock())
    assert calls == [(ID, [])]
    assert output["pages"] == primary["pages"]
    assert output["tinyfish_independent_stage"] == (
        "NO_VERIFIED_PUBLIC_PAGE_RETRYABLE")


@pytest.mark.parametrize("remote", [
    {"status": "PUBLIC_PAGES_INSPECTED", "pages": [{
        "url": "https://evil.example.invalid/tenders/4673971",
        "excerpt": "Тендер 4673971: подделка и неверная сумма"}]},
    {"status": "PUBLIC_PAGES_INSPECTED", "pages": [{
        "url": TEND_URL, "excerpt": "Тендер 46739719 чужой номер"}]},
    {"status": "INDEX_ONLY_UNVERIFIED", "pages": REMOTE["pages"]},
])
def test_unapproved_or_wrong_id_never_becomes_evidence(monkeypatch, remote):
    env, calls = _arrange(
        monkeypatch, primary={"status": "PUBLIC_TENDER_NOT_FOUND",
                              "pages": []}, remote=remote)
    result = research.source_findings({}, NOTICE, env, Mock())
    assert calls == [(ID, [])]
    assert result["pages"] == []
    assert result["tinyfish_independent_stage"] == (
        "NO_VERIFIED_PUBLIC_PAGE_RETRYABLE")


def test_tinyfish_deduplicates_and_preserves_three_page_cap(monkeypatch):
    base_pages = [{"url": PRIMARY_URL, "excerpt": "Тендер 4673971"}]
    env, calls = _arrange(
        monkeypatch, primary={"status": "PUBLIC_PAGES_INSPECTED",
                              "pages": base_pages + [
                                  {"url": "https://public.example.com/a",
                                   "excerpt": "Другие условия"},
                                  {"url": "https://public.example.com/b",
                                   "excerpt": "Дополнения"}]}, remote=REMOTE)
    result = research.source_findings({}, NOTICE, env, Mock())
    assert calls == [(ID, [])]
    assert len(result["pages"]) == research.MAX_PAGES
    assert result["tinyfish_verified_page_limit"] is True
    assert result["tinyfish_independent_stage"] == "VERIFIED"


@pytest.mark.parametrize("changes", [
    {free.ENABLE: "NO"},
    {free.T_EN: "NO"},
])
def test_policy_disabled_provider_not_called(monkeypatch, changes):
    env, calls = _arrange(
        monkeypatch,
        primary={"status": "PUBLIC_TENDER_NOT_FOUND", "pages": []},
        remote=REMOTE, policy=changes)
    result = research.source_findings({}, NOTICE, env, Mock())
    assert calls == []
    assert result["tinyfish_independent_stage"] == "DISABLED_BY_SOURCE_POLICY"
    assert result["pages"] == []
