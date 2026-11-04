"""Contract tests: a CRM read scope is not global CRM clearance."""
import json

import httpx
import pytest

import leadpilot.serviceit_stage4_crm_coverage_audit as m

WEBHOOK = "https://serviseit.bitrix24.ru/rest/1615/synthetic_webhook_123"
BASE = "https://serviseit.bitrix24.ru/rest/1615/synthetic_webhook_123"


def fake(*, companies=None, contacts=None, deny_contacts=False,
         admin=False, next_page=None, bad_company=False):
    def handler(req):
        method = req.url.path.rsplit("/",1)[-1]
        args = json.loads(req.content)
        if method == "user.admin.json":
            assert args == {}
            return httpx.Response(200, json={"result":admin})
        if method in ("crm.company.list.json", "crm.contact.list.json"):
            assert args == {"filter": {}, "select": ["ID"], "start": 0}
            if method.startswith("crm.contact.") and deny_contacts:
                return httpx.Response(403,json={"error":"ACCESS_DENIED"})
            rows = companies if method.startswith("crm.company.") else contacts
            rows = [{"ID":"1"}] if rows is None else rows
            if method.startswith("crm.company.") and bad_company:
                rows = [{"TITLE": "private name"}]
            data = {"result":rows}
            if method.startswith("crm.company.") and next_page is not None:
                data["next"]=next_page
            return httpx.Response(200,json=data)
        if method == "crm.requisite.fields.json":
            fields = {k:{"type":"string"} for k in m.requisite_preflight.__globals__["FIELDS"]}
            return httpx.Response(200, json={"result":fields})
        if method == "crm.requisite.list.json":
            assert args["filter"] == {"RQ_INN":"7700000009"}
            return httpx.Response(200, json={"result":[],"total":0})
        raise AssertionError(method)
    return httpx.MockTransport(handler)


def test_full_read_methods_still_do_not_prove_global_coverage():
    with httpx.Client(transport=fake()) as client:
        result = m.audit(client, WEBHOOK)
    assert result["status"]=="SERVICEIT_CRM_READ_COVERAGE_AUDIT"
    assert result["companies"]["list_read_access"]=="AVAILABLE"
    assert result["contacts"]["list_read_access"]=="AVAILABLE"
    assert result["exact_requisite_filter_readable"] is True
    assert result["webhook_is_portal_admin"] is False
    assert result["full_corporate_crm_coverage_verified"] is False
    assert result["global_dedup_approved"] is False
    assert result["crm_writes"]==0
    assert "private name" not in json.dumps(result)


def test_restricted_contact_scope_never_deems_all_records_accessible():
    with httpx.Client(transport=fake(deny_contacts=True)) as client:
        result=m.audit(client,WEBHOOK)
    assert result["companies"]["list_read_access"]=="AVAILABLE"
    assert result["contacts"]["list_read_access"]=="UNAVAILABLE_OR_UNVERIFIED"
    assert result["full_corporate_crm_coverage_verified"] is False


def test_paginated_scope_only_partial():
    with httpx.Client(transport=fake(next_page=50)) as client:
        result=m.audit(client,WEBHOOK)
    assert result["companies"]["list_scope"]=="PAGINATED_VISIBLE_ONLY"
    assert result["global_dedup_approved"] is False


def test_bad_response_not_counted_as_scope():
    with httpx.Client(transport=fake(bad_company=True)) as client:
        result=m.audit(client, WEBHOOK)
    assert result["companies"]["list_read_access"]=="UNAVAILABLE_OR_UNVERIFIED"


def test_no_customer_identity_or_writes_embedded_in_log():
    from pathlib import Path
    body=Path("leadpilot/serviceit_stage4_crm_coverage_audit.py").read_text("utf-8")
    for word in ("crm.company.update.json","crm.contact.update.json",
                 "crm.item.update.json","tasks.task.add.json"):
        assert word not in body
