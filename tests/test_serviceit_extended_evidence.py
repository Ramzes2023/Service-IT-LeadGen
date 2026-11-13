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
    assert conn.updates[-1][0]=='EXTRACTED' and conn.updates[-1][1]==48000


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


def test_excel_reads_fourth_sheet_and_full_long_cell():
    from io import BytesIO
    import zipfile
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('xl/workbook.xml','<workbook/>')
        for n in range(1,6):
            archive.writestr(f'xl/worksheets/sheet{n}.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c t="inlineStr"><is><t>'+str(n)+'x'*220+'</t></is></c></row></sheetData></worksheet>')
    text=docs.full_extract(stream.getvalue(),'.xlsx')
    assert '5'+'x'*220 in text and 'sheet4.xml' in text


def test_inventory_handles_nested_records_and_empty_last_page(monkeypatch):
    from leadpilot import serviceit_extended_evidence_worker as worker
    calls=[]
    def rest(method,args):
        calls.append(args)
        return {'items':[[{'id':11,'categoryId':21},{'id':29,'categoryId':21}]]} if args['filter']['>id']==0 else {'items':[[]]}
    monkeypatch.setattr(worker,'get',lambda rest,card:{'id':card})
    assert worker.inventory(rest)==[{'id':11},{'id':29}]
    assert calls[0]['select']==['id','categoryId'] and calls[1]['filter']['>id']==29


def test_identity_requires_own_legal_page_name_and_unique_inn():
    page={'url':'https://acme.ru/contacts','text':'ООО Альфа ИНН 7707083893'}
    assert contacts.identity(page,'7707083893','ООО Альфа')
    assert not contacts.identity(page,'7707083893','ООО Бета')
    assert not contacts.identity({**page,'url':'https://news.ru/blog/client-story'},'7707083893','ООО Альфа')
    assert not contacts.identity({**page,'text':page['text']+' ИНН 7707083894'},'7707083893','ООО Альфа')


def test_source_inventory_reads_all_three_receipts_and_verifies_ids(monkeypatch):
    from leadpilot import serviceit_extended_evidence_worker as worker
    class Result:
        def __init__(self,rows):self.rows=rows
        def fetchone(self):return self.rows[0]
        def fetchall(self):return self.rows
    class Conn:
        def execute(self,sql,args=()):
            if sql.startswith('SELECT to_regclass'):return Result([(args[0],)])
            if 'serviceit_bidzaar_inbox' in sql:return Result([(33,),(35,)])
            if 'serviceit_vps_b2b_inbox' in sql:return Result([(11,),(29,)])
            return Result([(3,),(7,),(11,),(37,)])
    seen=[]
    monkeypatch.setattr(worker,'get',lambda rest,card:seen.append(card) or {'id':card})
    assert [i['id'] for i in worker.source_inventory(Conn(),None)]==[11,29,33,35,37]
    assert seen==[11,29,33,35,37]


def test_contact_reader_follows_public_canonical_redirect_and_checks_robots(monkeypatch):
    monkeypatch.setattr(contacts,'safe',lambda url:url.startswith('https://acme.ru/'))
    def handler(request):
        if request.url.path=='/robots.txt':return httpx.Response(200,text='User-agent: *\nDisallow: /private')
        if request.url.path=='/contacts':return httpx.Response(301,headers={'Location':'/contacts/'})
        if request.url.path=='/contacts/':return httpx.Response(200,headers={'Content-Type':'text/html'},text='<p>ИНН 7707083893</p><p>info@acme.ru</p>')
        raise AssertionError('Must not read prohibited page')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cache={};page=contacts.read(client,'https://acme.ru/contacts',cache)
        assert page['url']=='https://acme.ru/contacts/' and 'info@acme.ru' in page['text']
        assert contacts.read(client,'https://acme.ru/private',cache) is None


def test_contact_redirect_does_not_enter_private_network(monkeypatch):
    monkeypatch.setattr(contacts,'safe',lambda url:url.startswith('https://acme.ru/'))
    def handler(request):
        assert request.url.host=='acme.ru'
        if request.url.path=='/robots.txt':return httpx.Response(404)
        return httpx.Response(302,headers={'Location':'http://127.0.0.1/secret'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert contacts.read(client,'https://acme.ru/contacts',{}) is None
