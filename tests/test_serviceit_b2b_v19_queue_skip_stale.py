"""Prevent stale independent native writer starving all V19 signed B2B cards."""
import ast
from pathlib import Path
from leadpilot.serviceit_b2b_v19_backfill import select_one

def test_v19_queue_selects_other_card_without_touching_unfinished_native_receipt():
    class Cursor:
        def __init__(self):self.text=None
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql,params=None):self.text=sql
        def fetchone(self):return (21,"B2B-CENTER-4673595",{})
    class Connection:
        def __init__(self):self.cur=Cursor()
        def cursor(self):return self.cur
    conn=Connection()
    row=select_one(conn)
    assert row[0]==21
    sql=conn.cur.text
    assert "NOT EXISTS" in sql
    assert "serviceit_vps_native_receipts n" in sql
    assert "n.card_id=b.card_id" in sql
    assert "n.status IN ('RESERVED','WRITE_INTENT')" in sql
    assert "ORDER BY b.received_at,b.source_id LIMIT 1" in sql

def test_one_module_vps_guarded_queue_fix_and_rollback():
    s=Path("ops/serviceit_vps_b2b_v19_queue_fix_activate.py").read_text("utf8")
    ast.parse(s)
    assert 'FILES = ("serviceit_b2b_v19_backfill.py",)' in s
    assert "AUDITED_OLD_SHAS" in s
    assert '"--network", "none"' in s
    assert '"--read-only"' in s
    assert '"--no-deps", "--no-build"' in s
    assert "pending_source_writes()" in s
    assert "V19_QUEUE_RELEASE_VPS_LIVE_ENABLED=YES" in s
    assert "V19_QUEUE_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in s
    assert "V19_QUEUE_OFFLINE_SMOKE_PASS" in s
    assert "crm.item.update" not in s
    wf=Path(".github/workflows/serviceit-vps-b2b-v19-queue-unblock-release.yml").read_text("utf8")
    assert "SERVICEIT_B2B_V19_QUEUE_SKIP_GUARDED_RELEASE_489" in wf
    assert "github.actor == 'Ramzes2023'" in wf
    assert "github.event.comment.user.login == 'Ramzes2023'" in wf
    assert "github.event.issue.number == 489" in wf
    assert "!github.event.issue.pull_request" in wf
    assert 'serviceit-vps-readonly' in wf
    assert 'V19_QUEUE_RELEASE_VPS_LIVE_ENABLED=YES' in wf
