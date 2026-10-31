"""Synthetic integration regression coverage; no external interfaces."""
from copy import deepcopy
from datetime import date
import json
import pytest

from leadpilot.cli import run
from leadpilot.crm_contract import MemoryCRM, preflight_contract
from leadpilot.integration_demo import demonstrate
from leadpilot.manager_briefs import build_evidence_briefs
from test_review_pipeline import candidate, NOW, SME
from test_public_1c_demand_gate import data


def test_deterministic_demo():
    report = demonstrate()
    assert report == demonstrate()
    assert report["simulated_leads"] == report["simulated_tasks"] == 1
    assert report["queued"]["status"] == "SIMULATION_QUEUED_RETRY"
    assert report["crm_ready"] is False
    assert report["briefs"]["briefs"][0]["signals"][0]["outsourcing_purchase_confirmed"] is False


@pytest.mark.parametrize("route", ["acceptance-demo", "evidence-briefs"])
def test_offline_cli_does_not_initialize_database(tmp_path, monkeypatch, capsys, route):
    fns, _ = data(tmp_path)
    args = ["leadpilot", route]
    if route == "evidence-briefs":
        args += ["--fns", str(fns)]
    monkeypatch.setattr("sys.argv", args)
    monkeypatch.setattr("leadpilot.cli.initialize", lambda _: pytest.fail("Unexpected DB write"))
    run()
    assert json.loads(capsys.readouterr().out)["crm_ready"] is False


@pytest.mark.parametrize("change", ["fns", "channel_date", "channel_inn", "missing_stop"])
def test_retry_revalidates_identity_age_and_stop_state(tmp_path, change):
    card = next(c for c in candidate(tmp_path)["companies"] if c["inn"] == SME)
    card = deepcopy(card)
    if change == "fns":
        card["fns_snapshot"] = "2026-08-01"
    elif change == "channel_date":
        card["channels"][0]["observed_on"] = "2026-09-01"
    elif change == "channel_inn":
        card["channels"][0]["inn"] = "7700000009"
    else:
        del card["channels"][0]["opt_out"]
    assert MemoryCRM().submit(card, manager_reviewed=True, today=NOW)["status"] == "SIMULATION_HOLD"


@pytest.mark.parametrize("authorization", [False, 1, "true", None])
def test_preflight_does_not_infer_permission(authorization):
    assert preflight_contract(authorized=authorization)["write_authorized"] is False


def test_size_only_shared_briefs_keep_holds(tmp_path):
    fns, _ = data(tmp_path)
    report = build_evidence_briefs(fns, today=NOW)
    assert all("DEMAND_UNVERIFIED" in c["holds"] for c in report["briefs"])
    assert report["counts"]["crm_ready"] == 0
