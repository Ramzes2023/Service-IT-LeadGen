"""V37 worker safety: no CRM writes on preview/conflict and no starvation."""
from unittest.mock import MagicMock

import pytest

from leadpilot import serviceit_b2b_dossier_consistency_worker_v37 as worker
from leadpilot.serviceit_b2b_dossier_consistency_v37 import ReconcileBlocked
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX

NOTICE = {
    "source": "B2B-Center", "tender_id": "4675475",
    "subject": "Приобретение серверного оборудования для нужд МКБ",
    "source_url": "https://www.b2b-center.ru/market/servers/tender-4675475/",
}
ROW = (17, "B2B-CENTER-4675475", NOTICE, None, None)
ENV = {
    worker.ENABLED: "YES",
    "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED": "YES",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
}


def _setup(monkeypatch, *, preview=False, conflict=False):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = ([("serviceit_vps_b2b_dossier_consistency_v37_receipts",),
                                 (True,)]
                                if preview else [(True,)])
    monkeypatch.setattr(worker, "live_permitted", lambda e: True)
    monkeypatch.setattr(worker, "exact_environment", lambda e: e)
    monkeypatch.setattr(worker, "eligible", lambda conn, target_ids=None: ROW)
    monkeypatch.setattr(worker, "safe_notice",
                        lambda notice,card,source,item:
                        (NOTICE["source_url"], NOTICE["subject"]))
    monkeypatch.setattr(worker, "secret_file", lambda *args,**kwargs: "FAKE")
    monkeypatch.setattr(worker, "validate_webhook", lambda *args,**kwargs: True)
    monkeypatch.setattr(worker, "validate_identity", lambda *args,**kwargs: True)
    monkeypatch.setattr(worker, "_get",
                        lambda rest,card: {
                            "id":17,
                            **{x:"old verified dossier" for x in AI_FIELDS},
                        })
    monkeypatch.setattr(worker, "original_nine",
                        lambda first: {x:"old verified dossier" for x in AI_FIELDS})
    monkeypatch.setattr(worker, "verified_lines", lambda *args,**kwargs: [])
    monkeypatch.setattr(worker, "Rest", lambda *args,**kwargs: MagicMock())
    monkeypatch.setattr(worker, "active_card_write", lambda *args:False)
    if conflict:
        def fail(*args):
            raise ReconcileBlocked("EXISTING_COMPANY_CONFLICT_MANUAL_REVIEW")
        monkeypatch.setattr(worker, "prepare", fail)
    else:
        monkeypatch.setattr(worker, "prepare",
                            lambda *args: (None,{"status":"NO_ATTESTED_NEW_FACTS"}))
    return conn,cur


def test_preview_does_not_call_schema_migration_or_bitrix_update(monkeypatch):
    conn,cur=_setup(monkeypatch,preview=True)
    got=worker.run_once(ENV,object(),conn,target_ids={17},preview=True)
    assert got["status"]=="V37_READONLY_PREVIEW"
    assert got["crm_writes"]==0
    assert got["draft_ready"] is False
    conn.execute.assert_not_called()
    assert any("to_regclass" in c.args[0] for c in cur.execute.call_args_list)
    assert all("UPDATE" not in c.args[0] for c in cur.execute.call_args_list)


def test_preview_without_ledger_is_clean_readonly_block(monkeypatch):
    conn,cur=_setup(monkeypatch,preview=True)
    cur.fetchone.side_effect=[(None,)]
    got=worker.run_once(ENV,object(),conn,target_ids={17},preview=True)
    assert got["status"]=="V37_PREVIEW_LEDGER_NOT_READY"
    assert got["crm_writes"]==0
    conn.execute.assert_not_called()


def test_legal_company_conflict_persists_pg_receipt_and_moves_on(monkeypatch):
    conn,cur=_setup(monkeypatch,conflict=True)
    ledger=[]
    monkeypatch.setattr(worker,"reserve",lambda *args: ledger.append("reserve") or 1)
    monkeypatch.setattr(worker,"mark",
                        lambda conn,card,attempt,state,*args:
                        ledger.append(state))
    got=worker.run_once(ENV,object(),conn,target_ids={17},preview=False)
    assert got["status"]=="V37_PREPARE_CONFLICT_NO_WRITE"
    assert got["crm_writes"]==0
    assert ledger==["reserve","CONFLICT"]


def test_conflict_on_preview_never_records_receipt(monkeypatch):
    conn,cur=_setup(monkeypatch,preview=True,conflict=True)
    got=worker.run_once(ENV,object(),conn,target_ids={17},preview=True)
    assert got["status"]=="V37_PREVIEW_CONFLICT_NO_WRITE"
    conn.execute.assert_not_called()


def test_selector_excludes_persistent_conflicts_and_uncertain_writes():
    conn=MagicMock()
    cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value=None
    assert worker.eligible(conn,None) is None
    sql=cur.execute.call_args.args[0]
    assert "serviceit_vps_b2b_dossier_v11_receipts" in sql
    assert "last.status='VERIFIED'" in sql
    assert "NONSTANDARD_REPORT" in sql
    assert "CONFLICT" not in sql.split("AND (last.status IS NULL")[1]
    assert "last.status='UNCERTAIN'" in sql
    assert "15 minutes" in sql
    assert "WRITE_INTENT" in sql
