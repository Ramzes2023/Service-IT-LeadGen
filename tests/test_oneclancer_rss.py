"""Public task RSS, strict non-CRM qualification and real CLI acceptance."""
from datetime import date
from html import escape
import csv
import json
import sys

import httpx
import pytest
from leadpilot import oneclancer_rss as c

TODAY = date(2026, 10, 31)


def xml(*, extras="", title="Помочь с настройкой 1С:Бухгалтерия",
        link="https://1clancer.ru/task/79561",
        desc="Нужно настроить обмен XML с 1С и обновить отчеты",
        date_text="Fri, 30 Oct 2026 14:20:00 +0300"):
    return ("<rss version='2.0'><channel><title>Задания</title>"
            "<item><title>"+escape(title)+"</title><link>"+escape(link)+"</link>"
            "<description>"+escape(desc)+"</description><pubDate>"+escape(date_text)+"</pubDate></item>"
            +extras+"</channel></rss>").encode()


def test_task_is_research_only_without_inn_or_contact():
    r=c.parse(xml(), today=TODAY)
    assert r['counts']['items_seen']==1
    assert r['counts']['research_task_signals']==1
    row=r['items'][0]
    assert row['task_id']=='79561'
    assert row['source_url']=='https://1clancer.ru/task/79561'
    assert row['buyer_inn'] is None and row['buyer_region']=='UNVERIFIED'
    assert row['buyer_company_contact'] is None
    assert row['qualified_lead'] is False and row['crm_ready'] is False
    assert r['counts']['qualified_b2b_leads']==0 and r['crm_writes']=='NO'


def test_public_contact_redacted_from_description():
    r=c.parse(xml(desc='Напишите на test.person@example.org либо +7 (999) 999-99-99, нужна доработка 1С'), today=TODAY)
    desc=r['items'][0]['description_excerpt_public_redacted']
    assert 'test.person' not in desc and '999-99-99' not in desc
    assert '[EMAIL_REDACTED]' in desc and '[PHONE_REDACTED]' in desc


def test_vacancy_and_untrusted_hosts_ignored():
    extra=("<item><title>Вакансия 1С</title><link>https://1clancer.ru/job/80000</link><pubDate>Fri, 30 Oct 2026 10:00:00 +0300</pubDate></item>"
           "<item><title>1С</title><link>https://evil.example/task/99999</link><pubDate>Fri, 30 Oct 2026 10:00:00 +0300</pubDate></item>")
    r=c.parse(xml(extras=extra), today=TODAY)
    assert r['counts']['items_seen']==3
    assert r['counts']['non_task_or_untrusted_entry']==2
    assert len(r['items'])==1


def test_publisher_alias_and_duplicate_task_dedupe():
    extra="<item><title>Повтор задачи</title><link>https://1lancer.ru/task/79561/</link><pubDate>Fri, 30 Oct 2026 14:20:00 +0300</pubDate></item>"
    r=c.parse(xml(extras=extra),today=TODAY)
    assert r['counts']['duplicate_task_entries']==1
    assert len(r['items'])==1


def test_old_task_and_unknown_date_not_marked_live():
    r=c.parse(xml(date_text='Fri, 25 Sep 2026 14:20:00 +0300'),today=TODAY)
    assert r['counts']['not_recent']==1 and r['items']==[]
    r=c.parse(xml(date_text='NO-DATE'), today=TODAY)
    assert r['counts']['publication_date_missing']==1 and r['items']==[]


def test_empty_feed_has_numeric_zero():
    doc=c.parse(b'<rss version="2.0"><channel><title>empty</title></channel></rss>',today=TODAY)
    assert doc['counts']=={'items_seen':0,'research_task_signals':0,'qualified_b2b_leads':0,'crm_ready':0}


def test_xml_entities_cannot_do_xxe():
    with pytest.raises(ValueError,match='XML_INVALID'):
        c.parse(b'<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss><channel><item>&xxe;</item></channel></rss>', today=TODAY)


def test_httpx_one_get_tls_transport_mock():
    calls=[]
    def handle(req):
        calls.append((req.method,str(req.url)))
        return httpx.Response(200,headers={'content-type':'application/rss+xml; charset=UTF-8'},content=xml())
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        r=c.fetch(client=client)
    assert len(calls)==1 and calls[0]==('GET',c.RSS_URL)
    assert r['counts']['research_task_signals']>=0


def test_httpx_redirect_and_non_xml_fail_closed():
    for code,content_type in [(302,'application/rss+xml'),(200,'text/html')]:
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(code,headers={'content-type':content_type},content=b'hello'))) as client:
            with pytest.raises(RuntimeError):
                c.fetch(client=client)


def test_export_utf8_excel_and_no_overwrite(tmp_path,monkeypatch):
    result=c.parse(xml(title='=HYPERLINK(123)'),today=TODAY)
    monkeypatch.chdir(tmp_path)
    jsonfile,csvfile=c.save(result,'exports/out.json','exports/out.csv')
    assert json.loads(jsonfile.read_text(encoding='utf-8'))['crm_writes']=='NO'
    with csvfile.open(encoding='utf-8-sig',newline='') as f:
        first=next(csv.DictReader(f))
    assert first['title'].startswith("'=")
    with pytest.raises(ValueError):
        c.save(result,'exports/out.json','exports/new.csv')
    with pytest.raises(ValueError):
        c.save(result,'../notallowed.json','exports/new.csv')


def test_real_cli_from_saved_rss(tmp_path,monkeypatch,capsys):
    raw=tmp_path/'saved.xml'
    raw.write_bytes(xml())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys,'argv',['oneclancer_rss','--rss-file',str(raw),'--json','exports/live.json','--csv','exports/live.csv'])
    c.main()
    out=capsys.readouterr().out
    assert 'CLANCER_RSS=OFFICIAL_TASK_FEED_PARSED' in out
    assert 'CLANCER_B2B_LEADS=0' in out
    assert (tmp_path/'exports/live.csv').is_file()


@pytest.mark.parametrize('link',[
    'https://other.example/task/1000',
    'http://other.example/task/1000',
    'https://1clancer.ru/vacancy/1000',
    'https://1clancer.ru/task/1000?secret=1',
    'https://user@1clancer.ru/task/1000',
    'https://1clancer.ru/task/1000#secret',
])
def test_unsafe_task_url(link):
    with pytest.raises(ValueError):
        c._task_url(link)
