"""Manager handoff gates must keep the same INNs, legal hold and zero CRM writes."""
import json
from datetime import date
from pathlib import Path

import pytest
from leadpilot.fns_manager_gate import reconcile_for_manager, save_report

NOW = date(2026, 10, 31)
INNS = ["7724850252", "7702319337", "7709969870", "9701112870"]
SOURCE = "https://rmsp.nalog.ru/search.html?mode=inn-list"
STATUS = ["SMALL", "MEDIUM", "NOT_SME", "NOT_FOUND"]


def example(tmp_path, *, with_legal=True):
    m = tmp_path / "manager.json"
    f = tmp_path / "fns.json"
    manager_rows = [
        {"inn": inn, "company": f"Учебная компания {i}", "score": 90-i,
         "region": "Москва", "status": "HOLD_FOR_REVIEW",
         "crm_ready": False, "outbound_authorized": False,
         **({"public_research_action": "LEGAL_REVIEW"} if with_legal and i == 0 else {})}
        for i, inn in enumerate(INNS)]
    results = [{"inn": inn, "name": f"Company {i}", "fns_status": STATUS[i],
                "source": SOURCE, "snapshot": NOW.isoformat(),
                "crm_ready": False, "outbound_authorized": False}
               for i, inn in enumerate(INNS)]
    manager = {"mode": "MANAGER_READINESS_OFFLINE",
               "crm_writes": "NO", "outbound_messages": "NO", "crm_ready": 0,
               "counts": {"candidates": len(manager_rows)}, "companies": manager_rows}
    fns = {"mode": "FNS_OFFICIAL_GUI_RECONCILIATION",
           "snapshot": NOW.isoformat(), "crm_writes": "NO", "outreach": "NOT_AUTHORIZED",
           "counts": {"total": len(results), "found": 3, "not_found": 1,
                      "small": 1, "micro": 0, "medium": 1, "not_sme": 1,
                      "crm_ready": 0}, "items": results}
    m.write_text(json.dumps(manager, ensure_ascii=False), encoding="utf8")
    f.write_text(json.dumps(fns, ensure_ascii=False), encoding="utf8")
    return m, f


def test_four_distinct_decisions_and_legal_hold(tmp_path):
    m, f = example(tmp_path)
    before = (m.read_bytes(), f.read_bytes())
    report = reconcile_for_manager(m, f, today=NOW)
    assert report["counts"]["total"] == 4
    assert report["counts"]["legal_hold"] == 1
    assert report["counts"]["small"] == 1
    assert report["counts"]["medium"] == 1
    assert report["counts"]["not_sme"] == 1
    assert report["counts"]["not_found"] == 1
    assert report["counts"]["crm_ready"] == 0
    by_inn = {x["inn"]: x for x in report["companies"]}
    assert by_inn["7724850252"]["decision"] == "LEGAL_HOLD"
    assert by_inn["7702319337"]["decision"] == "OUTSIDE_SMALL_BUSINESS_PILOT"
    assert by_inn["7709969870"]["decision"] == "EXCLUDED_FROM_SME_REGISTER_REVIEW"
    assert by_inn["9701112870"]["decision"] == "NOT_FOUND_SIZE_UNKNOWN"
    assert all(x["crm_ready"] is False and not x["outbound_authorized"] for x in report["companies"])
    assert report["crm_writes"] == "NO"
    assert (m.read_bytes(), f.read_bytes()) == before


def test_small_without_legal_hint_still_not_crm_ready(tmp_path):
    m, f = example(tmp_path, with_legal=False)
    result = reconcile_for_manager(m, f, today=NOW)
    match = next(x for x in result["companies"] if x["inn"] == "7724850252")
    assert match["decision"] == "SIZE_MATCH_ONLY"
    assert match["size_check_alone_is_sufficient"] is False


def test_inn_mismatch_rejected(tmp_path):
    m, f = example(tmp_path)
    obj = json.loads(f.read_text("utf8"))
    obj["items"][0]["inn"] = "5003079216"
    f.write_text(json.dumps(obj), encoding="utf8")
    with pytest.raises(ValueError, match="differ"):
        reconcile_for_manager(m, f, today=NOW)


def test_duplicate_inn_rejected(tmp_path):
    m, f = example(tmp_path)
    obj = json.loads(f.read_text("utf8"))
    obj["items"][1]["inn"] = obj["items"][0]["inn"]
    f.write_text(json.dumps(obj), encoding="utf8")
    with pytest.raises(ValueError, match="duplicate"):
        reconcile_for_manager(m, f, today=NOW)


def test_tampered_count_rejected(tmp_path):
    m, f = example(tmp_path)
    obj = json.loads(f.read_text("utf8"))
    obj["counts"]["medium"] = 2
    f.write_text(json.dumps(obj), encoding="utf8")
    with pytest.raises(ValueError, match="counts"):
        reconcile_for_manager(m, f, today=NOW)


def test_wrong_source_rejected(tmp_path):
    m, f = example(tmp_path)
    obj = json.loads(f.read_text("utf8"))
    obj["items"][0]["source"] = "https://example.org/"
    f.write_text(json.dumps(obj), encoding="utf8")
    with pytest.raises(ValueError, match="source"):
        reconcile_for_manager(m, f, today=NOW)


def test_upstream_crm_approval_rejected(tmp_path):
    m, f = example(tmp_path)
    obj = json.loads(m.read_text("utf8"))
    obj["companies"][0]["crm_ready"] = True
    m.write_text(json.dumps(obj), encoding="utf8")
    with pytest.raises(ValueError, match="approved"):
        reconcile_for_manager(m, f, today=NOW)


def test_stale_fns_requires_recheck(tmp_path):
    m, f = example(tmp_path)
    result = reconcile_for_manager(m, f, today=date(2027, 1, 5))
    assert result["fns_snapshot_stale"] is True
    by_inn = {x["inn"]: x for x in result["companies"]}
    assert by_inn["7724850252"]["decision"] == "LEGAL_HOLD"
    assert by_inn["7702319337"]["decision"] == "STALE_FNS_RECHECK"


def test_save_report_only_in_exports_and_no_overwrite(tmp_path, monkeypatch):
    m, f = example(tmp_path)
    monkeypatch.chdir(tmp_path)
    report = reconcile_for_manager(m, f, today=NOW)
    with pytest.raises(ValueError, match="exports"):
        save_report(report, "data/gate.json")
    path = save_report(report, "exports/fns_manager.json")
    assert Path(path).exists()
    with pytest.raises(FileExistsError):
        save_report(report, "exports/fns_manager.json")
