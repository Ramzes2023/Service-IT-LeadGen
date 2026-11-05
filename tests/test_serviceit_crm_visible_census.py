"""Visible CRM census: strict pagination and never-global-clear semantics."""
from unittest.mock import Mock
import pytest

from leadpilot.serviceit_crm_visible_census import (
    CensusBlocked, inspect_domain, rest, run, LIMIT, MAX_PAGES,
)


def test_reject_unsupported_domain_before_network():
    client = Mock()
    with pytest.raises(CensusBlocked):
        rest(client, "https://example.invalid", "secrets", 0)
    client.post.assert_not_called()


def test_reject_unbounded_pagination_before_network():
    client = Mock()
    with pytest.raises(CensusBlocked):
        rest(client, "https://example.invalid", "companies", LIMIT * MAX_PAGES)
    client.post.assert_not_called()


def test_exhausts_all_visible_pages_and_keeps_global_false(monkeypatch):
    from leadpilot import serviceit_crm_visible_census as module
    calls = []
    def fake(_client, _base, kind, start):
        calls.append(start)
        if start == 0:
            return list(range(1, 51)), 50, 52
        return [51, 52], None, 52
    monkeypatch.setattr(module, "rest", fake)
    result = inspect_domain(None, "", "companies")
    assert calls == [0, 50]
    assert result["visible_pages_complete"] is True
    assert result["global_visibility_proven"] is False


def test_repeating_page_is_blocked(monkeypatch):
    from leadpilot import serviceit_crm_visible_census as module
    monkeypatch.setattr(module, "rest", lambda _c, _b, _kind, start:
        ([1] if start else list(range(1,51)),
         None if start else 50,
         None))
    with pytest.raises(CensusBlocked):
        inspect_domain(None, "", "contacts")


def test_total_changed_is_blocked(monkeypatch):
    from leadpilot import serviceit_crm_visible_census as module
    monkeypatch.setattr(module, "rest", lambda _c, _b, _kind, start:
        (([51] if start else list(range(1, 51))),
         (None if start else 50),
         (70 if start else 60)))
    with pytest.raises(CensusBlocked):
        inspect_domain(None, "", "companies")


def test_pagination_limit_never_claims_complete(monkeypatch):
    from leadpilot import serviceit_crm_visible_census as module
    monkeypatch.setattr(module, "rest", lambda _c, _b, _kind, start:
        (list(range(start + 1, start + 51)), start + 50, None))
    report = inspect_domain(None, "", "deals")
    assert report["visible_pages_complete"] is False
    assert report["global_visibility_proven"] is False


def test_global_visibility_never_proven_even_all_domains_accessible(monkeypatch):
    from leadpilot import serviceit_crm_visible_census as module
    monkeypatch.setattr(module, "validate_webhook",
                        lambda _: "https://serviseit.bitrix24.ru/rest/1234/redacted")
    monkeypatch.setattr(module, "inspect_domain", lambda *_:
        {"read_access": "AVAILABLE", "visible_pages_complete": True,
         "visible_sample": "NONEMPTY", "scope": "VISIBLE_TECHNICAL_USER_ONLY",
         "global_visibility_proven": False})
    env = {
        "GITHUB_REPOSITORY": "Ramzes2023/Service-IT-LeadGen",
        "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "schedule",
        "SERVICEIT_BITRIX_WEBHOOK": "not used",
    }
    report = run(None, env)
    assert report["status"] == "COMPLETE_VISIBLE_SCOPE_ONLY"
    assert report["global_corporate_crm_visibility_proven"] is False
    assert report["formal_score_authorized"] is False
    assert report["crm_writes"] == 0
