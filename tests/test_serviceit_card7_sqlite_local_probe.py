"""Temporary SQLite sibling test: never touch real one-shot ledger or CRM."""
from pathlib import Path
import json
import sqlite3

from leadpilot import serviceit_card7_sqlite_local_probe as p


def test_local_probe_creates_missing_parent_then_removes_only_temporary_sqlite(tmp_path):
    ledger=tmp_path/"ServiceIT"/"Existing-B2B-Hold-AI"/"backfill-v1.sqlite3"
    assert not ledger.parent.exists()
    result=p.probe_sqlite_storage(ledger)
    assert result["status"]=="LOCAL_SQLITE_PROBE_PASS"
    assert result["parent_existed_before"] is False
    assert result["parent_exists_after"] is True
    assert result["sqlite_transaction_readback_verified"] is True
    assert result["temporary_probe_cleaned"] is True
    assert result["real_ledger_exists_after"] is False
    assert result["crm_writes"]==0
    assert result["historical_post_retried"] is False
    assert list(ledger.parent.iterdir())==[]


def test_existing_real_reservation_is_preserved_without_any_test_file(tmp_path):
    ledger=tmp_path/"backfill-v1.sqlite3"
    with sqlite3.connect(ledger) as db:
        db.execute("CREATE TABLE backfill_ops (op_key TEXT PRIMARY KEY,state TEXT,digest TEXT)")
        db.execute("INSERT INTO backfill_ops VALUES ('REAL-DO-NOT-ALTER','UNCERTAIN','X')")
    snapshot=ledger.read_bytes()
    before=list(tmp_path.iterdir())
    result=p.probe_sqlite_storage(ledger)
    assert result["status"]=="LOCAL_SQLITE_PROBE_NOT_RUN_EXISTING_LEDGER"
    assert ledger.read_bytes()==snapshot
    assert before==list(tmp_path.iterdir())


def test_sqlite_open_failure_does_not_modify_original_ledger(tmp_path,monkeypatch):
    ledger=tmp_path/"ServiceIT"/"Existing-B2B-Hold-AI"/"backfill-v1.sqlite3"
    def unavailable(*args,**kwargs):
        raise sqlite3.OperationalError("private path must not appear in logs")
    monkeypatch.setattr(p.sqlite3,"connect",unavailable)
    result=p.probe_sqlite_storage(ledger)
    assert result["status"]=="LOCAL_SQLITE_PROBE_BLOCKED"
    assert result["stopped_at"]=="SQLITE_OPEN"
    assert result["temporary_probe_cleaned"] is True
    assert not ledger.exists()
    assert list(ledger.parent.iterdir())==[]
    assert "private path" not in json.dumps(result)


def test_unwritable_parent_reports_mkdir_not_unexpected_crm_error(tmp_path,monkeypatch):
    ledger=tmp_path/"ServiceIT"/"Existing-B2B-Hold-AI"/"backfill-v1.sqlite3"
    original=Path.mkdir
    def deny(self,*args,**kwargs):
        if self==ledger.parent:
            raise PermissionError("LOCAL_PRIVATE_SECRET_NOT_FOR_OUTPUT")
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,"mkdir",deny)
    result=p.probe_sqlite_storage(ledger)
    assert result["status"]=="LOCAL_SQLITE_PROBE_BLOCKED"
    assert result["stopped_at"]=="MKDIR"
    assert result["crm_writes"]==0
    assert "LOCAL_PRIVATE" not in json.dumps(result)


def test_fixed_localappdata_path_and_no_webhook(monkeypatch,tmp_path,capsys):
    monkeypatch.setenv("LOCALAPPDATA",str(tmp_path))
    assert p.main(["--local-only"])==0
    result=json.loads(capsys.readouterr().out)
    assert result["status"]=="LOCAL_SQLITE_PROBE_PASS"
    assert result["real_ledger_exists_after"] is False
    assert (tmp_path/"ServiceIT"/"Existing-B2B-Hold-AI").is_dir()
    assert p.main([])==0
    assert json.loads(capsys.readouterr().out)["status"]=="LOCAL_SQLITE_PROBE_OFFLINE"


def test_existing_symlink_is_rejected_without_write(tmp_path):
    link=tmp_path/"link"
    import pytest
    try:
        link.symlink_to(tmp_path,target_is_directory=True)
    except OSError as exc:
        if getattr(exc,'winerror',None)==1314:
            pytest.skip('Windows account lacks symlink privilege; exercised on Linux CI')
        raise
    with pytest.raises(p.ProbeBlocked,match="UNEXPECTED_SYMLINK_DIRECTORY"):
        p.probe_sqlite_storage(link/"backfill-v1.sqlite3")


def test_no_api_or_real_reservation_in_probe():
    src=Path(p.__file__).read_text(encoding="utf-8")
    assert 'crm.item.update' not in src
    assert '_reserve_once(' not in src
    assert 'event.bind' not in src
    assert 'requests.post' not in src
    assert 'httpx.Client(' not in src
    assert 'GIGACHAT_CREDENTIALS' not in src
    assert "backfill_ops VALUES" not in src
