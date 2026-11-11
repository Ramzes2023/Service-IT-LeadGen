"""Offline optional-free-source integration proof. No internet/Bitrix/AI/PG."""
from unittest.mock import Mock
import pytest

from leadpilot import serviceit_b2b_free_public_fallback as free
from leadpilot import serviceit_vps_b2b_deep_research as research
from leadpilot import serviceit_b2b_public_mirrors as mirrors

T = "4673595"
TITLE = "Оказание услуг по предоставлению ИТ-специалистов"
TENDER = "https://tendem.ru/tenders/16548471"
B2B = ("https://www.b2b-center.ru/market/okazanie-uslug-po-"
       "predostavleniyu-it-spetsialistov/tender-4673595/")
NOTICE = {
    "source": "B2B-Center", "tender_id": T, "subject": TITLE,
    "source_url": B2B,
}
ENV = {
    free.ENABLE: "YES",
    free.T_EN: "YES",
    free.B_EN: "NO",
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
}
EVIDENCE = {
    "status": "REMOTE_PUBLIC_EVIDENCE_VALIDATED",
    "pages": [{"url": TENDER, "excerpt": "Оказание услуг по предоставлению ИТ-специалистов. Заказчик ООО ГЛОБУС-ИТ. Номер 4673595"}],
    "model_excerpt": "Оказание услуг по предоставлению ИТ-специалистов 4673595",
    "checked_at": "2026-11-11T16:00:00Z",
    "structured_facts": [{"buyer": {"value": "ООО ГЛОБУС-ИТ", "source_url": TENDER}}],
}


def test_disabled_by_default_never_reads_secret_or_calls_http(monkeypatch):
    monkeypatch.setattr(free, "secret_file", lambda *a, **k: pytest.fail("secret touched"))
    assert free.read_free_public_mirror(NOTICE, {}, Mock()) is None
    assert free.read_free_public_mirror(NOTICE, dict(ENV, **{free.ENABLE: "NO"}), Mock()) is None
    assert free.read_free_public_mirror(NOTICE, dict(ENV, **{free.T_EN: "NO"}), Mock()) is None
    assert free.read_free_public_mirror(NOTICE, dict(ENV, **{
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "NO"}), Mock()) is None


def test_missing_key_never_calls_provider(monkeypatch):
    monkeypatch.setattr(free, "secret_file", lambda *a, **k: "")
    monkeypatch.setattr(free, "discover_free_public_urls",
                        lambda *a, **k: pytest.fail("network without key"))
    assert free.read_free_public_mirror(NOTICE, ENV, Mock()) is None


def test_free_tendem_only_no_unlicensed_b2b_send(monkeypatch):
    monkeypatch.setattr(free, "secret_file", lambda *a, **k: "Z"*30)
    requests = []
    monkeypatch.setattr(free, "discover_free_public_urls",
                        lambda *a, **k: [B2B, TENDER])
    def fetch(client, **kw):
        requests.append(kw)
        return EVIDENCE
    monkeypatch.setattr(free, "fetch_remote_public_evidence", fetch)
    result = free.read_free_public_mirror(NOTICE, ENV, Mock())
    assert len(requests) == 1
    assert requests[0]["urls"] == [TENDER]
    assert result["status"] == "PUBLIC_PAGES_INSPECTED"
    assert result["structured_facts"][0]["buyer"]["source_url"] == TENDER
    assert result["free_source_requests_max"] == {"search": 1, "fetch_urls": 1}
    assert result["contacts"] == []


def test_b2b_origin_requires_separate_explicit_license(monkeypatch):
    monkeypatch.setattr(free, "secret_file", lambda *a, **k: "Z"*30)
    monkeypatch.setattr(free, "discover_free_public_urls",
                        lambda *a, **k: [B2B])
    urls = []
    monkeypatch.setattr(free, "fetch_remote_public_evidence",
                        lambda client, **kw: (urls.extend(kw["urls"]) or EVIDENCE))
    assert free.read_free_public_mirror(NOTICE, ENV, Mock()) is None
    assert urls == []
    result = free.read_free_public_mirror(
        NOTICE, dict(ENV, **{free.B_EN: "YES"}), Mock())
    assert result is not None
    assert urls == [B2B]


def test_free_rejection_or_block_never_becomes_evidence(monkeypatch):
    monkeypatch.setattr(free, "secret_file", lambda *a, **k: "Z"*30)
    monkeypatch.setattr(free, "discover_free_public_urls",
                        lambda *a, **k: [TENDER])
    monkeypatch.setattr(free, "fetch_remote_public_evidence",
                        lambda *a, **k: None)
    assert free.read_free_public_mirror(NOTICE, ENV, Mock()) is None
    monkeypatch.setattr(free, "fetch_remote_public_evidence",
                        lambda *a, **k: {"status": "INDEX_ONLY_UNVERIFIED", "pages": []})
    assert free.read_free_public_mirror(NOTICE, ENV, Mock()) is None


@pytest.mark.parametrize("bad", [
    {"tender_id": "9999"},
    {"tender_id": "4673595\nother"},
    {"source": "Anonymous"},
    {"subject": ""},
])
def test_no_vendor_calls_for_invalid_notice(monkeypatch, bad):
    monkeypatch.setattr(free, "secret_file", lambda *a, **k: pytest.fail("invalid read"))
    assert free.read_free_public_mirror({**NOTICE, **bad}, ENV, Mock()) is None


def test_native_reader_uses_free_source_only_after_all_primary_mirrors_fail(monkeypatch):
    monkeypatch.setattr(research, "host_allowlist",
                        lambda env: frozenset({"b2b-center.ru"}))
    monkeypatch.setattr(research, "secret_file", lambda *a, **k: "")
    monkeypatch.setattr(research, "research_public",
                        lambda *a, **k: {"status": "PUBLIC_SOURCE_NOT_READABLE", "pages": []})
    monkeypatch.setattr(research, "brave_notice_page", lambda *a, **k: None)
    monkeypatch.setattr(mirrors, "brave_mirror_search", lambda *a, **k: None)
    receipts = []
    monkeypatch.setattr(free, "read_free_public_mirror",
                        lambda *a, **k: (receipts.append(True) or
                                        {**EVIDENCE, "status": "PUBLIC_PAGES_INSPECTED"}))
    monkeypatch.setattr(research, "followup_public_buyer_research",
                        lambda finding, *a, **k: finding)
    got = research.source_findings({}, NOTICE, ENV, Mock())
    assert got["status"] == "PUBLIC_PAGES_INSPECTED"
    assert got["pages"][0]["url"] == TENDER
    assert receipts == [True]


def test_native_reader_keeps_success_when_independent_free_search_is_empty(monkeypatch):
    monkeypatch.setattr(research, "host_allowlist",
                        lambda env: frozenset({"b2b-center.ru"}))
    monkeypatch.setattr(research, "secret_file", lambda *a, **k: "")
    monkeypatch.setattr(research, "research_public",
                        lambda *a, **k: {"status": "PUBLIC_SOURCE_NOT_READABLE", "pages": []})
    monkeypatch.setattr(research, "brave_notice_page", lambda *a, **k: None)
    monkeypatch.setattr(mirrors, "brave_mirror_search", lambda *a, **k: {
        "status": "PUBLIC_PAGES_INSPECTED", "pages": [
            {"url": TENDER, "excerpt": "Оказание услуг по предоставлению ИТ-специалистов 4673595"}]})
    calls = []
    monkeypatch.setattr(free, "read_free_public_mirror",
                        lambda *a, **k: (calls.append(True) or None))
    monkeypatch.setattr(research, "followup_public_buyer_research",
                        lambda finding, *a, **k: finding)
    got = research.source_findings({}, NOTICE, ENV, Mock())
    assert got["pages"][0]["url"] == TENDER
    assert calls == [True]
    assert got["tinyfish_independent_stage"] == "NO_VERIFIED_PUBLIC_PAGE_RETRYABLE"



def test_existing_brave_index_hint_avoids_free_search(monkeypatch):
    monkeypatch.setattr(free, "secret_file", lambda *a, **k: "Z"*30)
    monkeypatch.setattr(free, "discover_free_public_urls",
                        lambda *a, **k: pytest.fail("repeated search"))
    urls = []
    monkeypatch.setattr(free, "fetch_remote_public_evidence",
                        lambda client, **kw: (urls.extend(kw["urls"]) or EVIDENCE))
    result = free.read_free_public_mirror(
        NOTICE, ENV, Mock(), index_hints=[
            {"evidence_level": "INDEX_ONLY_UNVERIFIED", "url": TENDER},
            {"evidence_level": "INDEX_ONLY_UNVERIFIED", "url": B2B},
        ])
    assert urls == [TENDER]
    assert result["status"] == "PUBLIC_PAGES_INSPECTED"


def test_untrusted_hint_is_rejected_and_falls_back_to_free_discovery(monkeypatch):
    monkeypatch.setattr(free, "secret_file", lambda *a, **k: "Z"*30)
    calls = []
    monkeypatch.setattr(free, "discover_free_public_urls",
                        lambda *a, **k: (calls.append("search") or []))
    assert free.read_free_public_mirror(
        NOTICE, ENV, Mock(), index_hints=[
            {"evidence_level": "INDEX_ONLY_UNVERIFIED", "url": "https://evil.invalid/4673595"},
            {"evidence_level": "INDEX_ONLY_UNVERIFIED", "url": B2B},
        ]) is None
    assert calls == ["search"]
