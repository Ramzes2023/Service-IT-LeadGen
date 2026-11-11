"""Offline safety tests of V41 strictly scoped partial Bitrix dossier writer."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from leadpilot.serviceit_b2b_dossier_consistency_worker_v37 import (
    ALLOWED_PATCH, V37Blocked, _digest, scoped_subset_update,
    uncertain_no_effect_verified, verify_patch_schema,
)
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_enrichment_worker import Rest
from leadpilot.serviceit_kanban_presentation import SCORE_FIELD


class FakeHTTP:
    def __init__(self, *, result=None, error=None):
        self.calls = []
        self.result = result or {"item":{"id":17}}
        self.error = error

    def post(self,url,**kwargs):
        self.calls.append((url,kwargs))
        payload=(
            {"error":"INVALID_PARAMETER","error_description":"synthetic"}
            if self.error else {"result":self.result})
        return SimpleNamespace(
            status_code=200,
            content=json.dumps(payload).encode("utf-8"),
            json=lambda:payload,
        )


def _rest(*, error=None):
    client=FakeHTTP(error=error)
    rest=Rest.__new__(Rest)
    rest.base="https://serviseit.bitrix24.ru/rest/999/SYNTHETIC_TEST_ONLY"
    rest.client=client
    return rest,client


def test_general_rest_still_rejects_partial_field_updates():
    rest,client=_rest()
    with pytest.raises(ValueError,match="REST_UPDATE_BLOCKED"):
        rest("crm.item.update",{
            "entityTypeId":1062,"id":17,"useOriginalUfNames":"Y",
            "fields":{PREFIX+"COMPANY_INN":"7734202860"}})
    assert client.calls==[]


def test_scoped_subsets_are_allowed_after_durable_write_intent():
    rest,client=_rest()
    data={
        PREFIX+"COMPANY_INN":"7734202860",
        PREFIX+"REGION":"Москва",
        PREFIX+"AI_SUMMARY":"Закупка №4675475, заказчик подтверждён",
    }
    assert set(data)<=ALLOWED_PATCH
    assert scoped_subset_update(rest,17,data)=={"item":{"id":17}}
    assert len(client.calls)==1
    url,args=client.calls[0]
    assert url.endswith("/crm.item.update.json")
    assert args["follow_redirects"] is False
    assert args["json"]=={
        "entityTypeId":1062,"id":17,"useOriginalUfNames":"Y",
        "fields":data}


@pytest.mark.parametrize("patch",[
    {SCORE_FIELD:"100"},
    {"NONEXISTENT":"TENDER"},
    {PREFIX+"COMPANY_INN":""},
    {PREFIX+"AI_FULL_REPORT":"a"*12001},
    {PREFIX+"REGION":"a"*4097},
])
def test_unapproved_or_overlimit_patch_is_denied_without_http(patch):
    rest,client=_rest()
    with pytest.raises(V37Blocked,match="V41_SCOPED_UPDATE_FIELDS_INVALID"):
        scoped_subset_update(rest,17,patch)
    assert client.calls==[]


def test_bitrix_rest_error_is_not_a_success_and_does_not_retry():
    rest,client=_rest(error=True)
    with pytest.raises(V37Blocked,match="V41_BITRIX_UPDATE_REJECTED"):
        scoped_subset_update(rest,17,{PREFIX+"COMPANY_INN":"7734202860"})
    assert len(client.calls)==1


def test_schema_requires_actual_editable_string_fields():
    patch={PREFIX+"COMPANY_INN":"7734202860",
           PREFIX+"AI_FULL_REPORT":"Согласованное досье"}
    good=lambda method,p: {"fields":{
        k:{"type":"string","isReadOnly":False,"isMultiple":False,
           "isImmutable":False} for k in patch}}
    assert verify_patch_schema(good,patch) is None
    unavailable=lambda method,p: {"fields":{
        PREFIX+"COMPANY_INN":{"type":"string","isReadOnly":True}}}
    with pytest.raises(V37Blocked,match="V41_SCHEMA_FIELD_NOT_WRITABLE"):
        verify_patch_schema(unavailable,patch)


def test_uncertain_confirmed_no_effect_allows_only_versioned_new_attempt():
    card=17
    salt=b"0"*16
    old={PREFIX+"AI_SUMMARY":"original summary",
         PREFIX+"COMPANY_INN":None}
    desired={PREFIX+"AI_SUMMARY":"confirmed summary",
             PREFIX+"COMPANY_INN":"7734202860"}
    proof=_digest(desired,salt)
    conn=MagicMock()
    conn.execute.return_value.fetchone.return_value=(old,desired,salt,proof)
    assert uncertain_no_effect_verified(conn,card,"a"*64,dict(old))
    assert not uncertain_no_effect_verified(
        conn,card,"a"*64,{PREFIX+"AI_SUMMARY":"confirmed summary",
                          PREFIX+"COMPANY_INN":None})
    assert not uncertain_no_effect_verified(
        conn,card,"a"*64,{PREFIX+"AI_SUMMARY":"unrelated manager edit",
                          PREFIX+"COMPANY_INN":None})


def test_uncertain_receipt_with_invalid_digest_stays_blocked():
    old={PREFIX+"AI_SUMMARY":"old"}
    desired={PREFIX+"AI_SUMMARY":"new"}
    conn=MagicMock()
    conn.execute.return_value.fetchone.return_value=(
        old,desired,b"0"*16,b"1"*32)
    assert not uncertain_no_effect_verified(
        conn,11,"a"*64,dict(old))
