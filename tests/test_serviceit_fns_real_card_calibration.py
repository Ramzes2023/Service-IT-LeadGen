"""Only FNS/AUX positive-control fix: verified 1062/21 Bitrix GET, never ID3."""
from datetime import datetime,timezone
from unittest.mock import Mock
import inspect
import pytest

from leadpilot.serviceit_vps_pg_queue import VpsBlocked
from leadpilot import serviceit_vps_native_aux as aux
from leadpilot import serviceit_vps_native_msp as msp
from leadpilot import serviceit_stage4_fns_dedupe_watch as stage

STAMP = "2026-11-10T10:00:00+00:00"

class ReadCursor:
    def __init__(self,row): self.row=row
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self,sql,params=None):
        assert sql.startswith("SELECT card_id FROM serviceit_vps_b2b_inbox ")
        assert "state='VERIFIED'" in sql
        assert "LIMIT 1" in sql
        assert params is None
    def fetchone(self): return self.row

class Conn:
    def __init__(self,row): self.row=row
    def cursor(self): return ReadCursor(self.row)

def item(card=104,category=21):
    return {"id":card,"entityTypeId":1062,"categoryId":category,
            "createdTime":STAMP}

def test_actual_verified_crm_positive_control_never_reads_historical_demo(monkeypatch):
    seen=[]
    def read_item(client,base,card):
        seen.append(card)
        assert card == 104 and card not in (3,7)
        return item()
    monkeypatch.setattr(stage,"read_item",read_item)
    assert aux.calibrate_verified_live_card(object(),"https://example.invalid/rest/",Conn((104,)))
    assert seen==[104]

@pytest.mark.parametrize("row",[None,(3,),(7,),("104",),(-1,)])
def test_no_trusted_live_control_fails_closed_without_network(monkeypatch,row):
    monkeypatch.setattr(stage,"read_item",
                        lambda *a: pytest.fail("MUST_NOT_READ_UNTRUSTED_ID"))
    with pytest.raises(VpsBlocked,match="NO_VERIFIED_POSITIVE_CONTROL"):
        aux.calibrate_verified_live_card(object(),"unused",Conn(row))

def test_mismatching_live_item_is_not_accepted(monkeypatch):
    # Exact Bitrix reader rejects different item or category.
    from leadpilot.serviceit_enrichment_worker import validate_identity
    def wrong(client,base,card):
        data=item(category=22)
        validate_identity(data,card)
        return data
    monkeypatch.setattr(stage,"read_item",wrong)
    with pytest.raises(Exception,match="IDENTITY_CATEGORY_CONFLICT"):
        aux.calibrate_verified_live_card(object(),"unused",Conn((104,)))

def test_native_aux_passes_connection_and_fns_collector_uses_same_control(monkeypatch):
    assert "eligible_cards(client, env, base, now, connection)" in inspect.getsource(aux.run_once)
    assert "calibrate_msp_verified_card(client, base, connection)" in inspect.getsource(
        msp.run_once)
    assert "calibrate_known_card(read_only_list)" not in inspect.getsource(msp.run_once)

def test_no_existing_qualifying_card_cannot_fake_discovery(monkeypatch):
    def fail_reader(*args): pytest.fail("NO_BYPASS")
    monkeypatch.setattr(stage,"read_item",fail_reader)
    with pytest.raises(VpsBlocked):
        aux.calibrate_verified_live_card(object(),"unused",Conn(None))

def test_msp_has_independent_control_for_different_image(monkeypatch):
    seen=[]
    def valid(client,base,card):
        seen.append(card)
        return item(card)
    monkeypatch.setattr(msp,"read_item",valid)
    assert msp.calibrate_msp_verified_card(object(),"unused",Conn((104,)))
    assert seen==[104]
    with pytest.raises(VpsBlocked,match="NO_VERIFIED_POSITIVE_CONTROL"):
        msp.calibrate_msp_verified_card(object(),"unused",Conn(None))
