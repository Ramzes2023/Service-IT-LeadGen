import httpx,pytest
from leadpilot import serviceit_extended_documents as docs
from leadpilot import serviceit_company_contacts as contacts
from leadpilot.serviceit_extended_evidence_worker import owned,BEGIN,END


def test_contacts_require_labelled_inn_not_name_or_number():
    page={'url':'https://acme.ru/contacts','text':'Acme 7707083893 +7 (495) 123-45-67 info@acme.ru'}
    assert contacts.extract([page],'7707083893')==[]
    page['text']='ИНН: 7707083893\n'+page['text']
    assert {c['value'] for c in contacts.extract([page],'7707083893')}=={'+7 (495) 123-45-67','info@acme.ru'}
    assert not contacts.extract([page],'7707083894')


def test_no_cross_domain_or_multi_company_contacts():
    proof={'url':'https://acme.ru/legal','text':'ИНН 7707083893'}
    other={'url':'https://other.ru/contact','text':'info@other.ru'}
    group={'url':'https://acme.ru/group','text':'ИНН 7707083893 ИНН 7707083894 info@other.ru'}
    assert contacts.extract([proof,other,group],'7707083893')==[]


def test_bank_numbers_not_phones():
    page={'url':'https://acme.ru/legal','text':'ИНН 7707083893 Счёт 40702810900000012345'}
    assert contacts.extract([page],'7707083893')==[]


def test_register_all_files_not_first_six():
    class Conn:
        calls=[]
        def execute(self,*args):self.calls.append(args)
    conn=Conn();docs.register(conn,35,[{'url':str(i),'name':str(i)+'.doc'} for i in range(13)])
    assert len(conn.calls)==13


def test_download_legacy_converter_and_safe_redirect(monkeypatch):
    url='https://bidzaar.com/api/filestorage/files/download/d0160ece-9258-4201-8a96-135e560e04d5'
    monkeypatch.setattr(docs,'legacy_doc',lambda raw:'текст '*10000)
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'doc'))) as client:
        assert len(docs.download(client,url,'old.doc')['text'])>24000
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'http://127.0.0.1/secret'}))) as client:
        with pytest.raises(ValueError,match='REDIRECT'):docs.download(client,url,'old.doc')


def test_converter_rejects_fake_doc():
    with pytest.raises(ValueError,match='SIGNATURE'):docs.legacy_doc(b'<html>login</html>')


def test_owned_block_replacement_preserves_manager_text():
    before='Условия клиента\n'+BEGIN+'\nold\n'+END+'\nРешение менеджера'
    result=owned(before,'new')
    assert 'Условия клиента' in result and 'Решение менеджера' in result
    assert result.count(BEGIN)==1 and 'old' not in result
    assert owned(result,'new')==result


def test_chunk_queue_resumes_after_24000_without_redownload(monkeypatch):
    class Result:
        def __init__(self,rows):self.rows=rows
        def fetchall(self):return self.rows
    class Conn:
        updates=[]
        def execute(self,sql,args=()):
            if sql.startswith('SELECT card_id,url,name'):return Result([])
            if sql.startswith('SELECT card_id,url,text_content'):return Result([(35,'https://source', 'a'*48001,24000)])
            self.updates.append(args);return Result([])
    conn=Conn();seen=[]
    monkeypatch.setattr(docs,'_model_research',lambda client,env,inputs:seen.extend(inputs) or [])
    docs.process(conn,None,{})
    assert len(seen[0]['text'])==24000
    assert conn.updates[0][0]=='EXTRACTED' and conn.updates[0][1]==48000


def test_crm_update_preserves_fields_and_requires_readback(monkeypatch):
    from contextlib import nullcontext
    from leadpilot import serviceit_extended_evidence_worker as worker
    class Result:
        def __init__(self,row=None):self.row=row
        def fetchone(self):return self.row
    class Conn:
        calls=[]
        def transaction(self):return nullcontext()
        def execute(self,sql,args=()):
            self.calls.append((sql,args))
            return Result((True,)) if 'pg_try_advisory' in sql else Result()
    before={worker.REPORT:'old',worker.BRIEF:'brief',worker.INN:'7707083893','categoryId':21}
    snapshots=iter([before,{**before,worker.REPORT:'new'}]);sent=[]
    monkeypatch.setattr(worker,'get',lambda rest,card:next(snapshots))
    class Rest:base='https://crm.invalid/rest/test'
    def handler(request):
        import json
        payload=json.loads(request.content);sent.append(payload)
        assert payload['fields']=={worker.REPORT:'new'}
        return httpx.Response(200,json={'result':{'item':{}}})
    conn=Conn()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert worker.patch(conn,Rest(),client,35,before,{worker.REPORT:'new'})
    assert len(sent)==1 and any("status='VERIFIED'" in sql for sql,args in conn.calls)
