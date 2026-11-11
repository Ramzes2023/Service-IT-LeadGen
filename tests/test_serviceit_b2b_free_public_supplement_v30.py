"""V30: proved thin-page supplemental evidence, no duplicate/unapproved sources.

Offline: no Bitrix, VPS, HTTP, paid model or secrets.
"""
from unittest.mock import Mock

import pytest

from leadpilot import serviceit_vps_b2b_deep_research as research
from leadpilot import serviceit_b2b_free_public_fallback as free
from leadpilot import serviceit_b2b_public_mirrors as mirrors


TENDER_ID = "4673595"
B2B_URL = "https://www.b2b-center.ru/market/it-services/tender-4673595/"
TENDEM_URL = "https://tendem.ru/tenders/16548471"
NOTICE = {
    "source": "B2B-Center",
    "tender_id": TENDER_ID,
    "subject": "Оказание услуг по предоставлению ИТ-специалистов",
    "source_url": B2B_URL,
}
ENV = {
    free.ENABLE: "YES",
    free.T_EN: "YES",
    free.B_EN: "NO",
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
}
PRIMARY = {
    "status": "PUBLIC_PAGES_INSPECTED",
    "pages": [{
        "url": B2B_URL,
        "excerpt": "Тендер № 4673595 Оказание услуг по предоставлению ИТ-специалистов",
    }],
    "model_excerpt": "Оказание услуг по предоставлению ИТ-специалистов",
}
FACT = {
    "buyer": {
        "value": 'ООО «ГЛОБУС-ИТ»',
        "source_url": TENDEM_URL,
        "verification": "PUBLIC_PAGE_BUYER_CLAIM",
    },
}
FREE = {
    "status": "PUBLIC_PAGES_INSPECTED",
    "pages": [{
        "url": TENDEM_URL,
        "excerpt": "Тендер № 4673595. Оказание услуг по предоставлению ИТ-специалистов. Заказчик ООО ГЛОБУС-ИТ",
    }],
    "structured_facts": [FACT],
}


def test_thin_primary_is_supplemented_with_independently_verified_public_page(monkeypatch):
    calls = []
    monkeypatch.setattr(
        free, "read_free_public_mirror",
        lambda notice, env, client, **kw: (calls.append(kw) or FREE),
    )
    got = research._supplement_thin_public_evidence(
        PRIMARY, NOTICE, ENV, Mock(), index_hints=[])
    assert calls == [{"index_hints": []}]
    assert got["pages"] == PRIMARY["pages"] + FREE["pages"]
    assert got["structured_facts"] == [FACT]
    assert got["free_public_supplemented"] is True
    assert PRIMARY == {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{
            "url": B2B_URL,
            "excerpt": "Тендер № 4673595 Оказание услуг по предоставлению ИТ-специалистов",
        }],
        "model_excerpt": "Оказание услуг по предоставлению ИТ-специалистов",
    }


@pytest.mark.parametrize("free_page", [
    {"url": TENDEM_URL, "excerpt": "Тендер № 4670000 другой предмет"},
    {"url": "https://evil.invalid/tenders/4673595",
     "excerpt": "Тендер № 4673595 поддельный"},
    {"url": TENDEM_URL, "excerpt": "Тендер № 46735955 похожий номер"},
    {"url": TENDEM_URL, "excerpt": ""},
])
def test_wrong_id_or_host_never_becomes_verified_addendum(monkeypatch, free_page):
    monkeypatch.setattr(
        free, "read_free_public_mirror",
        lambda *a, **k: {**FREE, "pages": [free_page]},
    )
    got = research._supplement_thin_public_evidence(PRIMARY, NOTICE, ENV, Mock())
    assert got is PRIMARY
    assert got["pages"] == PRIMARY["pages"]
    assert "free_public_supplemented" not in got


def test_duplicate_url_not_added(monkeypatch):
    duplicate = {**PRIMARY, "pages": [
        {"url": TENDEM_URL, "excerpt": "Тендер 4673595 Заказчик"},
    ]}
    monkeypatch.setattr(
        free, "read_free_public_mirror",
        lambda *a, **k: pytest.fail("duplicate source fetched"),
    )
    assert research._supplement_thin_public_evidence(
        duplicate, NOTICE, ENV, Mock()) is duplicate


@pytest.mark.parametrize("changes", [
    {free.ENABLE: "NO"},
    {free.T_EN: "NO"},
])
def test_supplement_has_no_side_effect_when_disabled(monkeypatch, changes):
    monkeypatch.setattr(
        free, "read_free_public_mirror",
        lambda *a, **k: pytest.fail("disabled provider called"),
    )
    assert research._supplement_thin_public_evidence(
        PRIMARY, NOTICE, {**ENV, **changes}, Mock()) is PRIMARY


def test_sufficient_primary_conditions_skip_duplicate_provider(monkeypatch):
    rich = {**PRIMARY, "pages": [{
        "url": B2B_URL,
        "excerpt": "Тендер 4673595. Заказчик: ООО «А». "
                   "Срок подачи заявок 16.11.2026. Документы для заявки. "
                   "Условия оплаты после поставки. Место доставки Москва.",
    }]}
    monkeypatch.setattr(
        free, "read_free_public_mirror",
        lambda *a, **k: pytest.fail("rich primary re-fetched"),
    )
    assert research._supplement_thin_public_evidence(
        rich, NOTICE, ENV, Mock()) is rich


def test_native_mirror_branch_uses_approved_supplement_once(monkeypatch):
    monkeypatch.setattr(
        research, "host_allowlist",
        lambda env: frozenset({"b2b-center.ru"}),
    )
    monkeypatch.setattr(research, "secret_file", lambda *a, **k: "")
    monkeypatch.setattr(
        research, "research_public",
        lambda *a, **k: {"status": "PUBLIC_SOURCE_NOT_READABLE", "pages": []},
    )
    monkeypatch.setattr(research, "brave_notice_page", lambda *a, **k: None)
    monkeypatch.setattr(mirrors, "brave_mirror_search", lambda *a, **k: PRIMARY)
    calls = []
    monkeypatch.setattr(
        free, "read_free_public_mirror",
        lambda *a, **k: (calls.append(True) or FREE),
    )
    monkeypatch.setattr(
        research, "followup_public_buyer_research",
        lambda findings, *a, **k: findings,
    )
    got = research.source_findings({}, NOTICE, ENV, Mock())
    assert got["free_public_supplemented"] is True
    assert len(got["pages"]) == 2
    assert calls == [True]
