"""Showcase checks: real offline scenarios, synthetic only."""
from pathlib import Path

from leadpilot.architecture_demo import simulate, render


def test_architecture_demo_is_repeatable_and_separate_from_real_sources():
    a=simulate()
    b=simulate()
    for result in (a,b):
        assert result["mode"] == "SYNTHETIC_ARCHITECTURE_DEMO_ONLY"
        assert result["source_connections_made"] == 0
        assert result["external_calls"] == 0
        assert result["crm_writes"] == "NO"
        assert result["messages_sent"] == 0
        assert result["company_count"] == 2
        assert result["signal_count"] == 3
        assert result["first_created"] is True
        assert result["repeat_new_company"] is False
        assert result["repeat_new_signal"] is False
        assert result["second_signal_created"] is True
        assert result["failure"]["simulated_retry"] == 2
        assert result["recovered"]["simulated_completed"] == 2
        assert result["duplicate_replay"]["simulated_completed"] == 0
        assert result["existing_client_hold"] == "HOLD"
        assert result["stop_list_hold"] == "HOLD"
        assert result["workflow_audit_events"] > 5
        assert result["real_bitrix24"] == "NOT_CONNECTED"
        assert result["actual_contracts_revenue_mrr"] == "NOT_VERIFIED"
    assert a["company_count"] == b["company_count"]


def test_showcase_html_has_explicit_simulation_warning():
    html=render(simulate())
    assert "<!doctype html>" in html
    assert "ДЕМОНСТРАЦИЯ" in html
    assert "Битрикс24" in html
    assert "Никаких реальных сообщений" in html
    assert "Не создаётся дубль" in html
