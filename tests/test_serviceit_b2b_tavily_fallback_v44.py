"""Offline V44 tests: Tavily URL hints only, one credit/source/month cap."""
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from leadpilot import serviceit_b2b_tavily_fallback_v44 as t


NOTICE = {
    "source":"B2B-Center", "tender_id":"4676478",
    "subject":"Серверное оборудование для дата-центра",
    "source_url":"https://www.b2b-center.ru/market/abc/tender-4676478/",
}
ENV = {
    "SERVICEIT_VPS_B2B_TAVILY_SEARCH_ENABLED":"YES",
    "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED":"YES",
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED":"YES",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED":"YES_1062_21",
}
URL = "https://zakupki.kontur.ru/4676478_4"


class PG:
    def __init__(self, existing=None, used=0):
        self.calls=[]
        self.existing=existing
        self.used=used
        self.inserted=None
        self.updated=None
    def transaction(self):
        return nullcontext()
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        result=MagicMock()
        if "pg_try_advisory_xact_lock" in sql:
            result.fetchone.return_value=(True,)
        elif "SELECT status,candidate_urls" in sql:
            result.fetchone.return_value=(self.updated or self.existing)
        elif "SELECT count(*)" in sql:
            result.fetchone.return_value=(self.used,)
        elif "INSERT INTO" in sql:
            self.inserted=args
            result.fetchone.return_value=(args[0],)
        else:
            if sql.startswith("UPDATE"):
                self.updated=(args[0],__import__("json").loads(args[1]))
        return result


def test_disabled_without_explicit_approval_or_secret(monkeypatch):
    conn=PG()
    client=MagicMock()
    assert t.discover_urls(conn,client,{},NOTICE)==[]
    assert conn.calls==[]
    monkeypatch.setattr(t,"secret_file",lambda *a,**k:"")
    assert t.discover_urls(conn,client,ENV,NOTICE)==[]
    assert conn.calls==[]


def test_no_personal_data_or_title_sent_to_tavily(monkeypatch):
    fake=MagicMock()
    fake.post.return_value=MagicMock(
        status_code=200,
        content=b'{"results":[{"url":"https://zakupki.kontur.ru/4676478_4","content":"ИНН 9999999999"}]}',
        headers={"content-type":"application/json"},
        json=lambda:{"results":[{"url":URL,"content":"НЕПРОВЕРЕННЫЙ ЗАКАЗЧИК: ООО Х"}]})
    result=t._search_once(fake,"tvly-"+"X"*25,NOTICE["tender_id"])
    assert result==("CANDIDATES",[URL])
    kwargs=fake.post.call_args.kwargs
    assert kwargs["json"]["query"] == '"4676478" B2B-Center тендер закупка'
    assert "Серверное оборудование" not in repr(kwargs)
    assert kwargs["json"]["search_depth"]=="basic"
    assert kwargs["json"]["include_answer"] is False
    assert kwargs["json"]["include_raw_content"] is False
    assert kwargs["json"]["max_results"]<=6
    assert kwargs["follow_redirects"] is False


@pytest.mark.parametrize("url", [
    "http://zakupki.kontur.ru/4676478_4",
    "https://zakupki.kontur.ru.evil.com/4676478_4",
    "https://user:password@zakupki.kontur.ru/4676478_4",
    "https://127.0.0.1/admin",
    "https://zakupki.kontur.ru/4676478_4?token=secret",
    "https://zakupki.kontur.ru/login",
    "https://b2b-center.ru/tender-4676478",
    "https://www.google.com/search?q=4676478",
])
def test_unsafe_search_hints_not_accepted(url):
    assert t.safe_candidate(url) is None


def test_duplicate_five_minute_runs_never_spend_second_credit(monkeypatch):
    monkeypatch.setattr(t,"secret_file",lambda *a,**k:"tvly-"+("X"*25))
    calls=[]
    monkeypatch.setattr(t,"_search_once",
                        lambda client,key,tid: calls.append(tid) or
                        ("CANDIDATES",[URL]))
    conn=PG()
    assert t.discover_urls(conn,object(),ENV,NOTICE)==[URL]
    assert t.discover_urls(conn,object(),ENV,NOTICE)==[URL]
    assert calls==["4676478"]
    assert len([sql for sql,args in conn.calls if "INSERT INTO" in sql])==1
    assert conn.updated==("CANDIDATES",[URL])


def test_monthly_cap_blocks_paid_call_even_for_new_card(monkeypatch):
    monkeypatch.setattr(t,"secret_file",lambda *a,**k:"tvly-"+("X"*25))
    monkeypatch.setattr(t,"_search_once",
                        lambda *a:pytest.fail("quota exceeded but paid call made"))
    conn=PG(used=t.MAX_MONTHLY_REQUESTS)
    assert t.discover_urls(conn,object(),ENV,NOTICE)==[]
    assert conn.inserted is None
    assert t.MAX_MONTHLY_REQUESTS < 1000


def test_rate_limit_is_fenced_as_spent_without_retry(monkeypatch):
    monkeypatch.setattr(t,"secret_file",lambda *a,**k:"tvly-"+("X"*25))
    calls=[]
    monkeypatch.setattr(t,"_search_once",
                        lambda client,key,tid: calls.append(tid) or
                        ("PROVIDER_LIMITED",[]))
    conn=PG()
    assert t.discover_urls(conn,object(),ENV,NOTICE)==[]
    assert t.discover_urls(conn,object(),ENV,NOTICE)==[]
    assert calls==["4676478"]
    assert conn.updated==("PROVIDER_LIMITED",[])


def test_tavily_candidates_never_enter_report_without_independent_get(monkeypatch):
    monkeypatch.setattr(t,"discover_urls",lambda *a,**k:[URL])
    import leadpilot.serviceit_b2b_public_mirrors as mirrors
    monkeypatch.setattr(mirrors,"_from_page_or_public_browser",
                        lambda *a,**k:None)
    assert t.verified_mirror_search(object(),object(),ENV,NOTICE) is None


def test_verified_pages_include_only_independent_evidence(monkeypatch):
    monkeypatch.setattr(t,"discover_urls",lambda *a,**k:[URL])
    import leadpilot.serviceit_b2b_public_mirrors as mirrors
    monkeypatch.setattr(mirrors,"_from_page_or_public_browser",
                        lambda *a,**k:{
                            "url":URL,
                            "excerpt":"4676478 B2B-Center, закупка серверного оборудования. Заказчик АО.",
                        })
    got=t.verified_mirror_search(object(),object(),ENV,NOTICE)
    assert got["status"]=="PUBLIC_PAGES_INSPECTED"
    assert got["pages"][0]["url"]==URL
    assert got["discovery"]=="TAVILY_URLS_WITH_INDEPENDENT_PUBLIC_READ"
