"""EIS / Mikhail Bitrix readiness: no fake-ready buyers or CRM writes."""
import json
from pathlib import Path

import pytest
from leadpilot import eis_bitrix_readiness as r


def source(tmp_path, monkeypatch):
    run = tmp_path / "run-20261031"
    run.mkdir()
    triage = {
        "mode": "EIS_108_COMMERCIALLY_SAFE_TRIAGE_ONLY",
        "source": "eis-zakupki-gov-ru-official-rss",
        "crm_writes": "NO", "outbound_messages": "NO",
        "counts": {"unique_notices": 108, "public_sector_text_hints": 108,
                   "qualified_b2b_leads": 0, "crm_ready": 0},
    }
    (run / "eis_triage.json").write_text(json.dumps(triage), encoding="utf-8")
    monkeypatch.setattr(r, "verify_eis", lambda _: {
        "latest_run": str(run), "official_notices_latest": 108,
        "new_since_previous": 0, "read_only_checks": "PASS",
    })
    return run


def test_realistic_108_public_tenders_have_zero_ready_bitrix_leads(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    data = r.check(tmp_path)
    assert data["eis_transport_and_sqlite"] == "PASS"
    assert data["eis_notices"] == 108
    assert data["eis_public_sector_text_hints"] == 108
    assert data["marketing_qualified_leads"] == 0
    assert data["crm_write_enabled"] is False
    assert data["crm_writes"] == "NO"
    assert data["bitrix24"]["isolated_test_smartprocess_read_confirmed"] is False
    assert len(data["open_acceptance_gates"]) >= 8


def test_untrusted_buyer_audit_never_becomes_approved(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    path=tmp_path/"buyer.json"
    path.write_text(json.dumps({"mode":"EIS_BUYER_FNS_REVIEW_ONLY",
                                "crm_writes":"NO","outbound_messages":"NO",
                                "counts":{"qualified_leads":1,"crm_ready":1},
                                "items":[]}),encoding="utf-8")
    with pytest.raises(ValueError, match="UNTRUSTED_BUYER_AUDIT"):
        r.check(tmp_path, buyer_audit=path)


def test_missing_evidence_does_not_assert_manager_or_portal_ready(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    data=r.check(tmp_path)
    assert data["manager_queue"]["status"] == "NOT_PROVIDED"
    assert data["buyer_audit"]["status"] == "NOT_PROVIDED"
    assert data["bitrix24"]["status"] == "NOT_TESTED_WITH_COMPANY_PORTAL"


def test_portal_readonly_report_cannot_grant_write_authority(tmp_path, monkeypatch):
    source(tmp_path, monkeypatch)
    path=tmp_path/"portal.json"
    mock={"mode":"EIS_BITRIX_READ_ONLY", "status":"READ_OK_WRITE_AND_DUPLICATE_GATES_CLOSED",
          "crm_writes":"NO","write_authorized":False,"crm_ready":False}
    path.write_text(json.dumps(mock),encoding="utf-8")
    data=r.check(tmp_path, crm_preflight=path)
    assert data["bitrix24"]["isolated_test_smartprocess_read_confirmed"] is True
    assert data["crm_write_enabled"] is False and data["crm_ready"] == 0
    mock["write_authorized"]=True
    path.write_text(json.dumps(mock),encoding="utf-8")
    with pytest.raises(ValueError,match="UNTRUSTED_PORTAL_PREFLIGHT"):
        r.check(tmp_path,crm_preflight=path)


def test_cli_uses_ascii_json_and_never_prints_secrets(tmp_path,monkeypatch,capsys):
    import sys
    source(tmp_path,monkeypatch)
    monkeypatch.setattr(sys,"argv",["eis_bitrix_readiness","--state-dir",str(tmp_path)])
    r.main()
    output=capsys.readouterr().out
    assert "EIS_BITRIX_READINESS=" in output
    assert "EIS_BITRIX24_CONNECTOR=NOT_AUTHORIZED" in output
    assert "CRM_WRITES=NO" in output


def test_no_bitrix_or_http_calls_in_module(tmp_path, monkeypatch):
    source(tmp_path,monkeypatch)
    import socket
    monkeypatch.setattr(socket.socket,"connect",lambda *a,**kw:pytest.fail("NO NETWORK"))
    assert r.check(tmp_path)["marketing_qualified_leads"] == 0
