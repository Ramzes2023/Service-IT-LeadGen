"""Offline root-cause coverage: Bitrix discarded 3 fields containing emoji."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json

import httpx
import pytest

from leadpilot.serviceit_enrichment_worker import AI_FIELDS, FIELDS, PREFIX
from leadpilot.serviceit_windows_autofill import preliminary_fields
from ops import serviceit_vps_b2b_bmp_recover_two as bmp


def partial_card():
    item={k:None for k in FIELDS}
    item.update({
        "id":88, "entityTypeId":1062, "categoryId":21,
        "createdTime":datetime.now(timezone.utc).isoformat(),
        "title":"📑 ТЕНДЕР · B2B-Center",
        PREFIX+"SIGNAL_ID":"B2B-CENTER-1234567",
        PREFIX+"SIGNAL_KIND":"tender-opportunity",
        PREFIX+"SIGNAL_SOURCE":"B2B-Center email subscription",
        PREFIX+"SIGNAL_URL":"https://www.b2b-center.ru/market/a/tender-1234567/",
        PREFIX+"EVIDENCE_STATUS":"HOLD: unverified original procurement",
        PREFIX+"STOP_REASON":"Не подтверждено",
        PREFIX+"SUGGESTED_SERVICE":"1C",
    })
    values=preliminary_fields(item)
    return item,values


def test_preliminary_bmp_fix_retains_full_report_and_all_nine_fields():
    item,fields=partial_card()
    assert set(fields)==AI_FIELDS
    assert all(isinstance(value,str) and value for value in fields.values())
    assert all(all(ord(char)<=0xFFFF for char in value)
               for value in fields.values())
    assert len(fields[PREFIX+"AI_FULL_REPORT"])>1600
    assert len(fields[PREFIX+"AI_MANAGER_BRIEF"])>350
    assert "HOLD" in fields[PREFIX+"AI_FULL_REPORT"]
    assert "НЕ Lead Score" in fields[PREFIX+"AI_SUMMARY"]


def test_first_card_only_missing_three_emojifree_fields():
    item,fields=partial_card()
    item.update({k:fields[k] for k in bmp.SIX})
    calls=[]
    def rest(method,params):
        calls.append(method)
        assert method=="crm.item.get"
        return {"item":deepcopy(item)}
    start=datetime.now(timezone.utc)-timedelta(days=2)
    before,generated,patch=bmp.check_card(
        rest,88,"B2B-CENTER-1234567",0,start,
        {k:12000 for k in AI_FIELDS})
    assert before["id"]==88
    assert generated==fields
    assert set(patch)==bmp.MISSING
    assert all(ord(c)<=0xFFFF for value in patch.values() for c in value)
    assert calls==["crm.item.get"]


def test_second_card_exact_nine_fields_and_unverified_no_score():
    item,fields=partial_card()
    def rest(method,params):
        assert method=="crm.item.get"
        return {"item":deepcopy(item)}
    _,generated,patch=bmp.check_card(
        rest,88,"B2B-CENTER-1234567",1,
        datetime.now(timezone.utc)-timedelta(days=2),
        {k:12000 for k in AI_FIELDS})
    assert set(patch)==AI_FIELDS
    assert patch==generated==fields
    item[PREFIX+"LEAD_SCORE"]=90
    with pytest.raises(bmp.Blocked,match="SCORE_OR_QUALIFICATION_EXISTS"):
        bmp.check_card(rest,88,"B2B-CENTER-1234567",1,
          datetime.now(timezone.utc)-timedelta(days=2),
          {k:12000 for k in AI_FIELDS})


def test_bitrix_bmp_update_only_allowlisted_fields_and_valid_id():
    _,generated=partial_card()
    patch={k:generated[k] for k in bmp.MISSING}
    observed=[]
    def handle(request):
        payload=json.loads(request.content)
        observed.append(payload)
        return httpx.Response(200,json={"result":{"item":{"id":88}}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        status=bmp.write_exact_bmp(client,"https://test.invalid/rest/1/key",88,patch)
        assert status=="ACK_RECEIVED"
        unsafe=dict(patch)
        unsafe[PREFIX+"AI_SUMMARY"]="📑 Not supported"
        with pytest.raises(bmp.Blocked,match="UNSAFE_BMP_UPDATE_SCOPE"):
            bmp.write_exact_bmp(client,"https://test.invalid/rest/1/key",88,unsafe)
    assert len(observed)==1
    assert observed[0]["entityTypeId"]==1062
    assert observed[0]["id"]==88
    assert set(observed[0]["fields"])==bmp.MISSING
    assert "crm.item.add" not in str(observed)


def test_missing_field_or_source_change_blocks_without_writes():
    item,fields=partial_card()
    item.update({k:fields[k] for k in bmp.SIX})
    item[PREFIX+"AI_SUMMARY"]="Manager edited this field"
    def rest(method,params): return {"item":deepcopy(item)}
    with pytest.raises(bmp.Blocked,match="FIRST_CARD_NO_LONGER_EXACT_SIX_OF_NINE"):
        bmp.check_card(rest,88,"B2B-CENTER-1234567",0,
                       datetime.now(timezone.utc)-timedelta(days=2),
                       {k:12000 for k in AI_FIELDS})
    item[PREFIX+"AI_SUMMARY"]=None
    with pytest.raises(bmp.Blocked,match="SOURCE_ID_CHANGED"):
        bmp.check_card(rest,88,"B2B-CENTER-7654321",0,
                       datetime.now(timezone.utc)-timedelta(days=2),
                       {k:12000 for k in AI_FIELDS})
