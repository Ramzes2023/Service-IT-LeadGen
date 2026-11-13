from unittest.mock import MagicMock
import httpx
import pytest
from leadpilot import serviceit_bidzaar_worker as w

ROW={'name':'Поставка серверов','description':'Оплата после поставки в течение 30 дней.',
     'link':'https://bidzaar.com/process/light/00000000-0000-0000-0000-000000000001',
     'inn':'7729058330','legalName':'АО МЕДИЦИНА','status':'proposal',
     'acceptanceEndDate':'2099-01-01T00:00:00Z','deliveryAddresses':['Москва, Тверская, 1'],
     'price':0,'currency':'RUB','files':[]}

def test_zero_budget_is_not_invented_and_quotes_must_match(monkeypatch):
    monkeypatch.setattr(w,'_model_research',lambda *a:[{'facts':[
        {'quote':'Оплата после поставки в течение 30 дней.','url':ROW['link'],'category':'PAYMENT'},
        {'quote':'Бюджет составляет 100 миллионов рублей','url':ROW['link'],'category':'BUDGET'}]}])
    fields=w.dossier(None,{},ROW,[17])
    assert 'Не раскрыт' in fields[w.REPORT]
    assert '100 миллионов' not in fields[w.REPORT]
    assert 'Оплата после поставки' in fields[w.REPORT]
    assert 'Карточка №17' in fields[w.BRIEF]
    assert w.PREFIX+'REVIEW_DECISION' not in fields


def test_document_download_does_not_follow_redirect_or_cross_host():
    called=[]
    def handler(request):
        called.append(str(request.url));return httpx.Response(302,headers={'Location':'http://127.0.0.1/secrets'})
    row={**ROW,'files':[{'name':'ТЗ.pdf','url':'https://bidzaar.com/api/filestorage/files/download/00000000-0000-0000-0000-000000000001'},
                       {'name':'ТЗ.pdf','url':'https://evil.test/file.pdf'}]}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        docs,failed=w.documents(client,row)
    assert not docs and failed==2 and len(called)==1


def test_post_timeout_leaves_durable_uncertain_intent(monkeypatch):
    conn=MagicMock();conn.execute.return_value.fetchall.return_value=[('BIDZAAR-X',ROW)]
    conn.execute.return_value.fetchone.return_value=('BIDZAAR-X',)
    rest=MagicMock();rest.base='https://example.invalid/rest/test'
    monkeypatch.setattr(w,'Rest',lambda *a:rest)
    monkeypatch.setattr(w,'secret_file',lambda *a:'unused')
    monkeypatch.setattr(w,'crm_matches',lambda *a:[])
    monkeypatch.setattr(w,'dossier',lambda *a:{w.REPORT:'Verified source'})
    def post(*a):
        assert any("POST_UNCERTAIN" in c.args[0] for c in conn.execute.call_args_list)
        raise httpx.ReadTimeout('timeout')
    monkeypatch.setattr(w,'bitrix_call',post)
    result=w.deliver(conn,None,{})
    assert result['uncertain']==1 and result['verified']==0
    assert not any("state='VERIFIED'" in c.args[0] for c in conn.execute.call_args_list)


def test_persistence_keeps_written_cards_out_of_create_queue():
    conn=MagicMock()
    w.persist(conn,[{**ROW,'lastModified':'2026-11-13T00:00:00Z'}],'2026-11-13T00:00:00Z')
    sql=conn.execute.call_args_list[0].args[0]
    assert "IN ('PENDING','FILTERED')" in sql
    assert 'excluded.modified>serviceit_bidzaar_inbox.modified' in sql
    assert conn.transaction.called
