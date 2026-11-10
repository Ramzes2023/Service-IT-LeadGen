"""Offline tests: never replay original AI update or create a second B2B card."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from copy import deepcopy
import json

import httpx
import pytest

from ops import serviceit_vps_b2b_missing_ai_field_repair as repair
from leadpilot.serviceit_enrichment_worker import AI_FIELDS, FIELDS, PREFIX
from leadpilot.serviceit_windows_autofill import preliminary_fields


def current_partially_written_card():
    item = {key: None for key in FIELDS}
    item.update({
        "id": 88, "entityTypeId": 1062, "categoryId": 21,
        "createdTime": datetime.now(timezone.utc).isoformat(),
        "title": "📑 ТЕНДЕР · B2B-Center",
        PREFIX+"SIGNAL_ID": "B2B-CENTER-1234567",
        PREFIX+"SIGNAL_KIND": "tender-opportunity",
        PREFIX+"SIGNAL_SOURCE": "B2B-Center email subscription",
        PREFIX+"SIGNAL_URL": "https://www.b2b-center.ru/market/a/tender-1234567/",
        PREFIX+"STOP_REASON": "Не проверен покупатель",
        PREFIX+"EVIDENCE_STATUS": "HOLD: awaiting verification",
        PREFIX+"SUGGESTED_SERVICE": "1С",
    })
    exact = preliminary_fields(item)
    item.update({key: exact[key] for key in repair.ALREADY_PRESENT})
    return item, exact


def test_repair_preflight_requires_six_original_values_and_empty_three():
    item, expected = current_partially_written_card()
    def rest(method, params):
        assert method == "crm.item.get"
        return {"item": deepcopy(item)}
    source="B2B-CENTER-1234567"
    start=datetime.now(timezone.utc)-timedelta(days=2)
    before, values=repair.verify_candidate(
        rest,88,source,"summary",start,{k:12000 for k in AI_FIELDS})
    assert before["id"]==88
    assert values==expected
    item[PREFIX+"AI_QUOTE"]="changed by manager"
    with pytest.raises(repair.Blocked,match="ORIGINAL_SIX_FIELDS_CHANGED"):
        repair.verify_candidate(
            rest,88,source,"summary",start,{k:12000 for k in AI_FIELDS})


def test_repair_rejects_existing_target_or_mismatched_source():
    item, _=current_partially_written_card()
    def rest(method, params):
        return {"item": deepcopy(item)}
    start=datetime.now(timezone.utc)-timedelta(days=2)
    item[PREFIX+"AI_SUMMARY"]="already edited"
    with pytest.raises(repair.Blocked,match="TARGET_FIELD_NOT_EMPTY"):
        repair.verify_candidate(rest,88,"B2B-CENTER-1234567","summary",
                                start,{k:12000 for k in AI_FIELDS})
    with pytest.raises(repair.Blocked,match="B2B_IDENTITY_CHANGED"):
        repair.verify_candidate(rest,88,"B2B-CENTER-7654321","summary",
                                start,{k:12000 for k in AI_FIELDS})


def test_single_field_http_call_exact_scope_and_no_fanout():
    seen=[]
    def transport(request):
        assert request.url.path.endswith("/crm.item.update.json")
        payload=json.loads(request.content)
        seen.append(payload)
        return httpx.Response(200,json={"result":{"item":{"id":88}}})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        code=repair.bitrix_single_field(
            client,"https://test.invalid/rest/3/abcdef",88,
            PREFIX+"AI_SUMMARY","Strictly unverified HOLD")
        assert code=="RESPONSE_RECEIVED"
        with pytest.raises(repair.Blocked,match="SINGLE_FIELD_SCOPE_UNTRUSTED"):
            repair.bitrix_single_field(
                client,"https://test.invalid/rest/3/abcdef",88,
                PREFIX+"LEAD_SCORE","90")
    assert len(seen)==1
    assert seen[0]["entityTypeId"]==1062 and seen[0]["id"]==88
    assert seen[0]["fields"]=={PREFIX+"AI_SUMMARY":"Strictly unverified HOLD"}


def test_reminder_never_accidentally_reserves_used_field():
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,sql,params): self.params=params
        def fetchall(self): return [("AI_REPAIR_SUMMARY_V1","WRITE_INTENT")]
    class Connection:
        def cursor(self): return Cursor()
    with pytest.raises(repair.Blocked,match="THIS_OR_LATER_FIELD_ALREADY_FENCED"):
        repair.previous_complete(Connection(),88,"summary")
    with pytest.raises(repair.Blocked,match="PREVIOUS_MISSING_FIELD_NOT_VERIFIED"):
        repair.previous_complete(Connection(),88,"manager_brief")
