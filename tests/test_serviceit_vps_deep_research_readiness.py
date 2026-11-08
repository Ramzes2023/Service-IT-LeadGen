"""Offline protection for missing public host approvals and research status."""
from pathlib import Path

import pytest
from leadpilot.serviceit_vps_deep_research_readiness import status, valid_hosts


def test_empty_host_lists_do_not_cause_unapproved_ai_or_browser():
    observed = status({
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED":"YES",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED":"YES_1062_21",
    })
    assert observed["approved_source_host_count"] == 0
    assert observed["public_source_title_model_eligible"] is False
    assert observed["crm_writes"] == observed["model_calls"] == 0
    assert observed["external_requests"] == 0
    assert observed["deep_public_search"] == "SOURCE_HOSTS_MISSING_NO_AI_OUTBOUND"


def test_valid_explicit_public_hosts_only():
    assert valid_hosts("hh.ru, b2b-center.ru,hh.ru") == ["hh.ru", "b2b-center.ru"]
    data = status({
        "SERVICEIT_PRELIMINARY_PUBLIC_HOSTS":"hh.ru,b2b-center.ru",
        "SERVICEIT_PUBLIC_RESEARCH_COMPANY_HOSTS":"company.ru",
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED":"YES",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED":"YES_1062_21",
    })
    assert data["approved_source_host_count"] == 2
    assert data["approved_independent_host_count"] == 1
    assert data["public_source_title_model_eligible"] is True


@pytest.mark.parametrize("host", [
    "localhost", "127.0.0.1", "internal.local", "x.invalid",
    "http://bad.com", "evil.com/path", "evil.com:80", "bad.net:username",
])
def test_invalid_host_rejected(host):
    with pytest.raises(ValueError):
        valid_hosts(host)


def test_research_worker_never_turns_empty_domain_list_into_write_failure():
    source = Path("leadpilot/serviceit_bitrix_dispatch_worker.py").read_text("utf8")
    assert "if names:" in source
    assert "require(len(names) <= 80 and all(" in source
    assert 'and bool(env.get("SERVICEIT_PRELIMINARY_PUBLIC_HOSTS", "").strip())' in source
    assert "PUBLIC_RESEARCH_HOSTS_INVALID" in source
    assert "SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED" in source
    # No silent bypass of the model privacy proof.
    assert "MODEL_PRIVACY_CONSENT_REQUIRED" in source
