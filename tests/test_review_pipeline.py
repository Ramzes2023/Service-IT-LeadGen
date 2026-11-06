"""Synthetic integration acceptance. Never uses private exports or network."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pytest
from leadpilot.review_pipeline import assemble, public_url, save
from leadpilot.crm_contract import MemoryCRM, preflight_contract
from test_public_1c_demand_gate import data, SME, ANOTHER
from test_sme_vacancy_audit import setup

NOW = date(2026, 10, 31)


@pytest.mark.parametrize("approval", [1, "true", "false"])
def test_simulation_requires_explicit_boolean_review(tmp_path, approval):
    c = next(c for c in candidate(tmp_path)["companies"] if c["inn"] == SME)
    assert MemoryCRM().submit(c, manager_reviewed=approval)["status"] == "SIMULATION_HOLD"


def test_persistent_stop_cancels_retry_and_deal(tmp_path):
    c = next(c for c in candidate(tmp_path)["companies"] if c["inn"] == SME)
    crm = MemoryCRM()
    crm.submit(c, manager_reviewed=True, today=NOW)
    crm.available = False
    crm.submit(c, manager_reviewed=True, today=NOW)
    crm.stop(SME)
    crm.available = True
    assert crm.retry(manager_reviewed=True, today=NOW) == []
    assert crm.submit(c, manager_reviewed=True, today=NOW)["status"] == "SIMULATION_HOLD"
    with pytest.raises(ValueError):
        crm.confirm_interest(SME, confirmed=True)


def test_repeat_updates_score_without_duplicate_task(tmp_path):
    c = next(c for c in candidate(tmp_path)["companies"] if c["inn"] == SME)
    crm = MemoryCRM()
    crm.submit(c, manager_reviewed=True, today=NOW)
    c["score"] = 85
    crm.submit(c, manager_reviewed=True, today=NOW)
    assert crm.leads[SME]["score"] == 85
    assert len(crm.history) == 1


@pytest.mark.parametrize("domain", ["example.org/path", "example.org?key=x", "bad..org", "-bad.org"])
def test_reject_malformed_corporate_domain(tmp_path, domain):
    fns, req = data(tmp_path)
    with pytest.raises(ValueError):
        assemble(fns, requests=req, contacts=channels(tmp_path, corporate_domain=domain), today=NOW)


def channels(tmp_path, inn=SME, **changes):
    row = dict(inn=inn, channel="email", value="info@example.org",
               source_url="https://www.example.org/contacts", corporate_domain="example.org",
               observed_on="2026-10-31", review_state="PENDING", purpose="corporate_review", opt_out=False)
    row.update(changes)
    p = tmp_path / "contacts.json"
    p.write_text(json.dumps({"mode": "PUBLIC_CORPORATE_CHANNELS", "items": [row]}), encoding="utf8")
    return p


def candidate(tmp_path):
    fns, req = data(tmp_path)
    return assemble(fns, requests=req, contacts=channels(tmp_path), today=NOW)


def test_size_only_is_not_demand_or_contact(tmp_path):
    fns, _ = data(tmp_path)
    r = assemble(fns, today=NOW)
    assert r["counts"] == dict(seen=2, fns_size_verified=2, with_current_evidence=0,
                               ready_for_review=0, qualified_leads=0, crm_ready=0, unmatched_requests=0)
    assert r["optional_inputs"]["requests"] == "NOT_SUPPLIED"
    assert all(c["score"] == 20 and c["status"] == "candidate_target" for c in r["companies"])
    assert r["sales"] is None


def test_manager_card_provenance_and_score(tmp_path):
    r = candidate(tmp_path)
    assert r == candidate(tmp_path)
    c = next(c for c in r["companies"] if c["inn"] == SME)
    assert c["status"] == "manager_review_candidate"
    assert c["score"] == 75 == sum(c["score_weights"].values())
    assert c["signals"][0]["event_on"] == "2026-10-30"
    assert c["signals"][0]["provenance"] == "SUPPLIED_SOURCE_NOT_LIVE_VERIFIED"
    assert c["ai_status"] == "AI_UNAVAILABLE" and c["ai_confidence"] is None
    assert not c["crm_ready"] and not c["outbound_authorized"] and c["draft_call_notes"] is None


def test_vacancy_adapter_and_two_sources(tmp_path):
    fns, folders = setup(tmp_path)
    _, requests = data(tmp_path)
    # data() replaces the FNS fixture with a compatible synthetic cohort.
    r = assemble(fns, requests=requests, folders=folders, offsets=[0, 1], today=NOW)
    c = next(c for c in r["companies"] if c["inn"] == SME)
    assert len(c["signals"]) == 2
    assert c["score_weights"]["independent_source_domains"] == 10
    assert all(not s["outsourcing_purchase_confirmed"] for s in c["signals"])


@pytest.mark.parametrize("changes", [dict(review_state="APPROVED"), dict(crm_ready=True),
    dict(value="ivan@example.org"), dict(source_url="https://other.org/contact"),
    dict(observed_on="2026-01-01"), dict(opt_out="false"), dict(channel="telegram")])
def test_contact_gate(tmp_path, changes):
    fns, req = data(tmp_path)
    with pytest.raises(ValueError):
        assemble(fns, requests=req, contacts=channels(tmp_path, **changes), today=NOW)


@pytest.mark.parametrize("url", ["http://example.org/a", "https://u:p@example.org/a",
    "https://127.0.0.1/a", "https://example.org\\evil/a", "https://example.org/a\n",
    "https://example.org:444/a", "https://example.org/a#secret"])
def test_url_security(url):
    with pytest.raises(ValueError):
        public_url(url)


def test_stop_list_and_bid_channel_are_distinct(tmp_path):
    fns, req = data(tmp_path)
    stopped = assemble(fns, requests=req, contacts=channels(tmp_path, opt_out=True), today=NOW)
    assert stopped["counts"]["ready_for_review"] == 0
    url = "https://b2b-center.ru/search/number/test/"
    r = assemble(fns, requests=req, contacts=channels(tmp_path, channel="bid_url", value=url,
                  source_url=url, purpose="published_bid"), today=NOW)
    assert r["counts"]["ready_for_review"] == 1
    assert not next(c for c in r["companies"] if c["inn"] == SME)["channels"][0]["outbound_authorized"]


def test_expired_evidence_and_missing_contacts(tmp_path):
    fns, req = data(tmp_path)
    obj = json.loads(req.read_text(encoding="utf8"))
    obj["items"][0]["deadline_on"] = "2026-10-30"
    req.write_text(json.dumps(obj), encoding="utf8")
    r = assemble(fns, requests=req, today=NOW)
    assert r["counts"]["with_current_evidence"] == 0
    assert r["counts"]["ready_for_review"] == 0


def test_same_phone_domain_different_inns_never_merge(tmp_path):
    fns, req = data(tmp_path)
    cp = channels(tmp_path, channel="phone", value="+74950000000")
    obj = json.loads(cp.read_text())
    obj["items"].append({**obj["items"][0], "inn": ANOTHER})
    cp.write_text(json.dumps(obj))
    r = assemble(fns, requests=req, contacts=cp, today=NOW)
    assert len(r["companies"]) == 2
    assert all("SHARED_CHANNEL_OR_DOMAIN_REVIEW" in c["holds"] for c in r["companies"])
    assert r["counts"]["ready_for_review"] == 0


def test_simulated_retry_idempotency_new_signal_and_deal(tmp_path):
    c = next(c for c in candidate(tmp_path)["companies"] if c["inn"] == SME)
    crm = MemoryCRM()
    assert crm.submit(c)["status"] == "SIMULATION_HOLD"
    crm.available = False
    assert crm.submit(c, manager_reviewed=True, today=NOW)["status"] == "SIMULATION_QUEUED_RETRY"
    crm.available = True
    assert crm.retry(manager_reviewed=True, today=NOW)[0]["status"] == "SIMULATED_LEAD_LINKED"
    crm.submit(c, manager_reviewed=True, today=NOW)
    assert len(crm.leads) == len(crm.history) == 1
    c2 = deepcopy(c)
    c2["signals"][0]["id"] = "second-signal"
    crm.submit(c2, manager_reviewed=True, today=NOW)
    assert len(crm.leads) == 1 and len(crm.history) == 2
    assert len(crm.leads[SME]["tasks"]) == 2
    with pytest.raises(ValueError):
        crm.confirm_interest(SME)
    assert crm.confirm_interest(SME, confirmed=True)["entity"] == "Deal"
    assert preflight_contract()["status"] == "CRM_BLOCKED"


def test_simulator_rejects_forged_or_stopped_card(tmp_path):
    c = next(c for c in candidate(tmp_path)["companies"] if c["inn"] == SME)
    for key in ("crm_ready", "outbound_authorized"):
        forged = {**c, key: True}
        assert MemoryCRM().submit(forged, manager_reviewed=True)["status"] == "SIMULATION_HOLD"
    c["channels"][0]["opt_out"] = True
    assert MemoryCRM().submit(c, manager_reviewed=True)["status"] == "SIMULATION_HOLD"


def test_retry_rechecks_expiry(tmp_path):
    c = next(c for c in candidate(tmp_path)["companies"] if c["inn"] == SME)
    crm = MemoryCRM()
    crm.available = False
    crm.submit(c, manager_reviewed=True, today=NOW)
    crm.available = True
    assert crm.retry(manager_reviewed=True, today=date(2026, 12, 1))[0]["status"] == "SIMULATION_HOLD"
    assert not crm.leads


def test_output_restricted_and_csv_escaped(tmp_path):
    r = candidate(tmp_path)
    with pytest.raises(ValueError):
        save(r, Path("tests") / "forbidden.json", Path("tests") / "forbidden.csv")
    from leadpilot.sme_vacancy_audit import _excel_safe
    assert _excel_safe("\t=malicious").startswith("'")
    # The production export contract is anchored at the repository's exports/.
    # Keep the test portable: pytest's tmp_path may be outside the repository
    # (GitHub Actions, Linux, and Windows default temp directories).
    from tempfile import TemporaryDirectory
    from leadpilot import review_pipeline
    export_root = Path(review_pipeline.__file__).resolve().parents[1] / "exports"
    export_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="review-pipeline-test-", dir=export_root) as target:
        directory = Path(target)
        result_json, result_csv = directory / "queue.json", directory / "queue.csv"
        save(r, result_json, result_csv)
        assert json.loads(result_json.read_text(encoding="utf8"))["counts"]["qualified_leads"] == 0
        with pytest.raises(ValueError):
            save(r, result_json, result_csv)


def test_manual_fact_is_hypothesis_not_purchase(tmp_path):
    fns, _ = data(tmp_path)
    p = tmp_path / "facts.json"
    p.write_text(json.dumps({"mode": "PUBLIC_CORPORATE_FACTS", "items": [dict(
        inn=SME, kind="retail_opening", source_url="https://example.org/news/opening",
        event_on="2026-10-30", observed_on="2026-10-31", excerpt="Synthetic announcement of a new retail store", review_state="PENDING")]}))
    c = next(c for c in assemble(fns, facts=p, today=NOW)["companies"] if c["inn"] == SME)
    assert c["service_hypotheses"] == ["cash/retail hardware"]
    assert not c["signals"][0]["outsourcing_purchase_confirmed"]
