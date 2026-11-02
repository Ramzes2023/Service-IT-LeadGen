"""Mock-only controlled remediation of a previous silent CRM write."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from leadpilot import serviceit_kanban_badge_reconciliation as fix
from leadpilot.serviceit_kanban_score_badge import CODE,score_badge
from leadpilot.bitrix_1062_mapping import FIELD_MAP
from leadpilot.bitrix_1062_ai_cockpit import CockpitBlocked


class Portal:
    def __init__(self):
        self.camel = "ufCrm13SvcitAiScoreBadge"
        self.v = ""
        self.item = {
            "id":3,"entityTypeId":1062,"categoryId":21,
            "title":"DEMO ServiceIT AI — DEMO-ITREQ-001",
            "stageId":"DT1062_21:NEW",
            FIELD_MAP["lead_score"]:90,
            FIELD_MAP["score_version"]:fix.VERSION,
            "UF_CRM_13_SVCIT_AI_FULL_REPORT":"SYNTHETIC REPORT " * 15,
            "UF_CRM_13_SVCIT_STOP_REASON":"",
        }
        self.calls=[]
        self.ignore = False
        self.timeout = False
        self.concurrent=False

    def __call__(self,m,p):
        self.calls.append((m,deepcopy(p)))
        if m=="crm.item.get":
            item=deepcopy(self.item)
            key=CODE if p["useOriginalUfNames"]=="Y" else self.camel
            item[key]=self.v
            return {"item":item}
        if m=="crm.item.fields":
            assert p["useOriginalUfNames"]=="N"
            return {"fields":{self.camel:{"type":"string","isReadOnly":False,
                                        "isImmutable":False,"isMultiple":False}}}
        if m=="crm.item.update":
            assert p == {
                "entityTypeId":1062,"id":3,"useOriginalUfNames":"N",
                "fields":{self.camel:score_badge(90)}
            }
            if self.timeout: raise CockpitBlocked("MOCK_WRITE_TIMEOUT")
            if not self.ignore: self.v = p["fields"][self.camel]
            return {"item":{"id":3}}
        raise AssertionError("Forbidden REST method "+m)


def ledger(tmp_path, field="VERIFIED", item="UNCERTAIN"):
    with sqlite3.connect(tmp_path/"cockpit-report-v1.sqlite3") as con:
        con.execute("CREATE TABLE cockpit_ops (key TEXT PRIMARY KEY, state TEXT NOT NULL,digest TEXT NOT NULL)")
        con.executemany("INSERT INTO cockpit_ops VALUES (?,?,?)",[
            (fix.OLD_FIELD_KEY,field,"mock"),(fix.OLD_ITEM_KEY,item,"mock")])


def states(tmp_path):
    with sqlite3.connect(tmp_path/"cockpit-report-v1.sqlite3") as db:
        return dict(db.execute("SELECT key,state FROM cockpit_ops").fetchall())


def test_readonly_only_uses_get_and_fields(tmp_path):
    ledger(tmp_path)
    portal=Portal()
    plan=fix.snapshot(portal,tmp_path)
    assert plan["field_key"]==portal.camel
    assert plan["expected"]==score_badge(90)
    assert all(m in {"crm.item.get","crm.item.fields"} for m,_ in portal.calls)
    assert states(tmp_path)[fix.OLD_ITEM_KEY]=="UNCERTAIN"


def test_distinct_explicit_reconciliation_one_post_old_uncertain_preserved(tmp_path):
    ledger(tmp_path)
    portal=Portal()
    old=deepcopy(portal.item)
    result=fix.apply_reconciliation(portal,tmp_path)
    assert result["status"]=="BADGE_RECONCILIATION_VERIFIED"
    assert result["kanban_display_configured"] is False
    assert portal.v==score_badge(90)
    assert portal.item==old
    assert len([c for c in portal.calls if c[0]=="crm.item.update"])==1
    state=states(tmp_path)
    assert state[fix.OLD_FIELD_KEY]=="VERIFIED"
    assert state[fix.OLD_ITEM_KEY]=="UNCERTAIN"
    assert state[fix.REPAIR_KEY]=="VERIFIED"
    with pytest.raises(CockpitBlocked):
        fix.apply_reconciliation(portal,tmp_path)
    assert len([c for c in portal.calls if c[0]=="crm.item.update"])==1


@pytest.mark.parametrize("field,item",[
    ("UNCERTAIN","UNCERTAIN"),("VERIFIED","VERIFIED"),
    ("NOT_RESERVED","UNCERTAIN"),("VERIFIED","NOT_RESERVED"),
])
def test_prior_ledger_not_expected_no_write(tmp_path,field,item):
    ledger(tmp_path,field,item)
    portal=Portal()
    with pytest.raises(CockpitBlocked,match="REPAIR_ORIGINAL_STATE_NOT_EXPECTED"):
        fix.apply_reconciliation(portal,tmp_path)
    assert not any(m=="crm.item.update" for m,p in portal.calls)


def test_missing_original_ledger_is_never_reinitialized(tmp_path):
    p=Portal()
    with pytest.raises(CockpitBlocked,match="REPAIR_ORIGINAL_LEDGER_NOT_FOUND"):
        fix.apply_reconciliation(p,tmp_path)
    assert not (tmp_path/"cockpit-report-v1.sqlite3").exists()
    assert not p.calls


def test_nonempty_value_blocks(tmp_path):
    ledger(tmp_path)
    p=Portal()
    p.v="someone edited"
    with pytest.raises(CockpitBlocked,match="REPAIR_FIELD_ALREADY_NONEMPTY"):
        fix.apply_reconciliation(p,tmp_path)
    assert not any(m=="crm.item.update" for m,params in p.calls)


def test_old_ignored_write_followed_by_this_timeout_stays_uncertain(tmp_path):
    ledger(tmp_path)
    p=Portal()
    p.timeout=True
    with pytest.raises(CockpitBlocked,match="MOCK_WRITE_TIMEOUT"):
        fix.apply_reconciliation(p,tmp_path)
    state=states(tmp_path)
    assert state[fix.OLD_ITEM_KEY]=="UNCERTAIN"
    assert state[fix.REPAIR_KEY]=="UNCERTAIN"
    assert len([m for m,params in p.calls if m=="crm.item.update"])==1
    p.timeout=False
    with pytest.raises(CockpitBlocked):
        fix.apply_reconciliation(p,tmp_path)
    assert len([m for m,params in p.calls if m=="crm.item.update"])==1


def test_new_write_silently_ignored_does_not_mark_verified(tmp_path):
    ledger(tmp_path)
    p=Portal()
    p.ignore=True
    with pytest.raises(CockpitBlocked,match="REPAIR_READBACK_UNCERTAIN_NO_RETRY"):
        fix.apply_reconciliation(p,tmp_path)
    assert states(tmp_path)[fix.REPAIR_KEY]=="UNCERTAIN"
    assert p.v==""


def test_score_or_name_change_blocks(tmp_path):
    ledger(tmp_path)
    p=Portal();p.item[FIELD_MAP["lead_score"]]=85
    with pytest.raises(CockpitBlocked,match="REPAIR_SCORE_CHANGED"):
        fix.snapshot(p,tmp_path)
    assert not any(m=="crm.item.update" for m,params in p.calls)


def test_unwritable_field_meta_blocks(tmp_path):
    ledger(tmp_path)
    p=Portal()
    old=p.__call__
    def call(m,payload):
        result=old(m,payload)
        if m=="crm.item.fields":
            result["fields"][p.camel]["isImmutable"]=True
        return result
    with pytest.raises(CockpitBlocked,match="REPAIR_FIELD_NOT_WRITABLE"):
        fix.snapshot(call,tmp_path)


def test_no_live_without_explicit_opt_in(monkeypatch,tmp_path,capsys):
    monkeypatch.setenv("SERVICEIT_AI_COCKPIT_WEBHOOK","SECRET_MUST_NOT_LEAK")
    assert fix.main(["--inspect-live","--apply-reconciliation",
                     "--webhook-from-env","--state-dir",str(tmp_path)])==2
    printed=capsys.readouterr().out
    assert "SECRET_MUST_NOT_LEAK" not in printed
    assert json.loads(printed)["status"]=="BADGE_RECONCILIATION_BLOCKED_OR_UNCERTAIN"


def test_offline_default_does_not_touch_ledger(monkeypatch,tmp_path,capsys):
    monkeypatch.setattr(fix.httpx,"Client",
                        lambda: (_ for _ in ()).throw(AssertionError("No network")))
    assert fix.main(["--state-dir",str(tmp_path)])==0
    assert json.loads(capsys.readouterr().out)["status"]=="BADGE_RECONCILIATION_OFFLINE_READY"
    assert not list(tmp_path.iterdir())


def test_source_no_calls_to_any_other_write_method():
    s=Path(fix.__file__).read_text(encoding="utf-8")
    assert 'rest("crm.item.update",payload)' in s
    for forbidden in ('rest("userfieldconfig.add"', 'rest("crm.item.add"',
                      'rest("crm.item.delete"', 'rest("tasks.task.add"',
                      'rest("crm.item.details.configuration.set"', 'GIGACHAT_CREDENTIALS'):
        assert forbidden not in s
