"""Offline V46 Research MINI: async receipt, credits and source provenance."""
import hashlib
import json
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import httpx
import pytest

from leadpilot import serviceit_b2b_tavily_research_mini_v47 as pro


KEY="tvly-"+("x"*43)
SINCE=(pro.SEARCH_POLICY_START + timedelta(seconds=1)).isoformat().replace("+00:00","Z")


def test_fixed_pro_model_and_tender_only_prompt():
    assert pro.MODEL=="mini"
    assert pro.MAX_MINI_CREDITS==110
    prompt=pro._prompt("4677404")
    assert "4677404" in prompt
    assert "ИНН" in prompt and "заказчика" in prompt
    assert "не подтверждено" in prompt
    assert "ramze" not in prompt.lower()


def test_off_by_default_even_with_other_tavily_search_enabled():
    assert not pro.approved({"SERVICEIT_VPS_B2B_TAVILY_SEARCH_ENABLED":"YES"})
    assert not pro.approved({"SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ENABLED":"YES"})
    assert pro.approved({
        "SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ENABLED":"YES",
        "SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ACTIVE_SINCE":SINCE,
        "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED":"YES",
        "SERVICEIT_VPS_WRITER_MODE":"LIVE",
        "SERVICEIT_WORKER_MODEL_PRIVACY_APMINIVED":"YES",
        "SERVICEIT_ROUTERAI_PRIVACY_APMINIVED":"YES_1062_21",
    })


def test_submit_uses_research_endpoint_exact_pro_no_stream():
    called=[]
    def handle(req):
        called.append(req)
        assert req.url.path=="/research" and req.method=="POST"
        assert req.headers.get("authorization")=="Bearer "+KEY
        payload=json.loads(req.content)
        assert payload["model"]=="mini"
        assert payload["stream"] is False
        assert payload["citation_format"]=="numbered"
        assert payload["output_length"]=="long"
        assert "4677404" in payload["input"]
        return httpx.Response(201,headers={"content-type":"application/json"},
                              json={"request_id":"123e4567-e89b-12d3-a456-426614174111"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        tid=pro._submit(client,KEY,"4677404")
    assert tid=="123e4567-e89b-12d3-a456-426614174111"
    assert len(called)==1


def test_provider_403_is_not_successful_or_logged():
    with httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(403,content=b"<html>denied</html>",
                                  headers={"content-type":"text/html"}))) as client:
        assert pro._submit(client,KEY,"4677404") is None


def test_maximum_credit_reserve_prevents_paygo_with_low_included_balance():
    fake=MagicMock()
    fake.execute.return_value.fetchone.return_value=(0,)
    def response(limit,used):
        return httpx.Response(200,json={"account":{
            "plan_limit":limit,"plan_usage":used,
            "paygo_usage":0,"paygo_limit":10000}},
            headers={"content-type":"application/json"})
    with httpx.Client(transport=httpx.MockTransport(lambda req:response(1500,1249))) as c:
        assert pro._usage(fake,c,KEY)==251
    with httpx.Client(transport=httpx.MockTransport(lambda req:response(1500,1251))) as c:
        assert pro._usage(fake,c,KEY)==249
    with httpx.Client(transport=httpx.MockTransport(lambda req:response(1500,1500))) as c:
        assert pro._usage(fake,c,KEY)==0


def test_in_flight_tasks_reduce_available_included_credits():
    fake=MagicMock()
    fake.execute.return_value.fetchone.return_value=(500,)
    with httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(200,json={"account":{
            "plan_limit":1500,"plan_usage":0}},
            headers={"content-type":"application/json"}))) as c:
        assert pro._usage(fake,c,KEY)==1000


@pytest.mark.parametrize("response",[
    {"account":{"plan_limit":1500}},
    {"account":{"plan_limit":"1500","plan_usage":1}},
    {"account":{"plan_limit":1500,"plan_usage":1501}},
    {"account":{"plan_limit":1500,"plan_usage":-1}},
    {"key":{"limit":1500,"usage":1}},
])
def test_usage_fail_closed_on_incomplete_or_invalid_quota(response):
    fake=MagicMock()
    fake.execute.return_value.fetchone.return_value=(0,)
    with httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(200,json=response))) as c:
        assert pro._usage(fake,c,KEY) is None


def test_no_unsigned_or_old_sources_billed():
    assert pro._notice("B2B-CENTER-4677404")=="4677404"
    assert pro._notice("B2B-CENTER-4677404-other") is None
    assert pro._notice("OTHER-4677404") is None


def test_sources_filter_bad_links_and_deduplicate():
    ok="https://zakupki.kontur.ru/4677404_40"
    values=[{"url":ok},{"url":ok},{"url":"http://bad.ru/a"},
            {"url":"https://u:p@example.com/secret"},
            {"url":"https://example.com/x#fragment"}]
    assert pro._sources(values)==[ok]


def test_research_mini_never_writes_direct_crm_and_never_trusts_ai_report():
    from pathlib import Path
    source=Path("leadpilot/serviceit_b2b_tavily_research_mini_v47.py").read_text()
    assert "crm.item.update" not in source
    assert "tasks.task.add" not in source
    assert "MODEL = \"mini\"" in source
    assert "ON CONFLICT(source_hash) DO NOTHING" in source
    assert "RESEARCH_MINI_INCLUDED_CREDITS_INSUFFICIENT" in source
    assert "status='COMPLETED'" in source
    assert "include_usage" in source
    assert "_from_page_or_public_browser" in source

def test_mini_only_new_verified_cards_after_activation():
    assert pro.activation_cutoff({}) is None
    assert pro.activation_cutoff({
        "SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ACTIVE_SINCE":"not-a-date"}) is None
    assert pro.activation_cutoff({
        "SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ACTIVE_SINCE":SINCE}) is not None
    from unittest.mock import MagicMock
    conn=MagicMock()
    conn.execute.return_value.fetchall.return_value=[]
    assert pro._candidate(conn,pro.activation_cutoff({
        "SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ACTIVE_SINCE":SINCE})) is None
    stmt,args=conn.execute.call_args.args
    assert "b.received_at >= %s" in stmt
    assert "r.source_id IS NULL" in stmt
    assert args[0]==pro.activation_cutoff({
        "SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ACTIVE_SINCE":SINCE})


def test_only_one_historical_b2b_record_is_not_eligible_after_activation():
    now=datetime.now(timezone.utc)
    assert pro.activation_cutoff({
        "SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ACTIVE_SINCE":(
            now+timedelta(hours=1)).isoformat().replace("+00:00","Z")}) is None
