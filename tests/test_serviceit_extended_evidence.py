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
            if sql.startswith('SELECT snapshot'):return Result([None])
            if sql.startswith('INSERT INTO serviceit_extended_card_inventory'):return Result([None])
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


def test_finished_cards_use_snapshot_without_repeated_crm_reads(monkeypatch):
    from leadpilot import serviceit_extended_evidence_worker as worker
    class Result:
        def __init__(self,rows):self.rows=rows
        def fetchone(self):return self.rows[0]
        def fetchall(self):return self.rows
    class Conn:
        def execute(self,sql,args=()):
            if sql.startswith('SELECT to_regclass'):return Result([(args[0],)])
            if sql.startswith('SELECT snapshot'):return Result([({'id':35,worker.INN:'7707083893'},)])
            return Result([(35,)])
    monkeypatch.setattr(worker,'get',lambda *args:pytest.fail('Completed cards must not be reread every five minutes'))
    assert worker.source_inventory(Conn(),None)==[{'id':35,worker.INN:'7707083893'}]


def test_rtf_extension_uses_existing_converter(monkeypatch):
    monkeypatch.setattr(docs,'legacy_doc',lambda data:'Договор поставки')
    assert docs.full_extract(b'{\\rtf1}', '.rtf')=='Договор поставки'


def test_empty_pdf_requests_ocr_but_text_pdf_does_not(monkeypatch):
    monkeypatch.setattr(docs,'extract_text',lambda data,kind:'')
    monkeypatch.setattr(docs,'recognize',lambda data,kind:'OCR: полный текст')
    assert docs.full_extract(b'pdf','.pdf').startswith('OCR:')
    monkeypatch.setattr(docs,'extract_text',lambda data,kind:'Исходный текст')
    assert docs.full_extract(b'pdf','.pdf')=='Исходный текст'


def test_ocr_command_has_no_shell_and_marks_uncertainty(monkeypatch):
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(docs.shutil,'which',lambda name:'/usr/bin/'+name)
    def run(args,**kwargs):
        calls.append((args,kwargs))
        return SimpleNamespace(returncode=0,stdout=b'Battery BRP-38-6000-1')
    monkeypatch.setattr(docs.subprocess,'run',run)
    text=docs.recognize(b'png','.png')
    assert text.startswith('OCR:') and 'Battery' in text
    assert calls[0][0][0]=='tesseract' and not calls[0][1].get('shell')
    assert calls[0][1]['timeout']==30


def test_ocr_empty_page_is_not_marked_read(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(docs.shutil,'which',lambda name:'/usr/bin/'+name)
    monkeypatch.setattr(docs.subprocess,'run',lambda *args,**kw:SimpleNamespace(returncode=0,stdout=b''))
    with pytest.raises(ValueError,match='OCR_PAGE_EMPTY'):docs.recognize(b'png','.png')


def test_group_legal_sections_do_not_mix_company_contacts():
    page={'url':'https://clinic.ru/patient/','text':'Полное наименование: ООО Альфа ИНН 7707083893 info@alpha.ru +7 (495) 123-45-67\nПолное наименование: ООО Бета ИНН 7707083894 info@beta.ru +7 (495) 111-22-33','emails':['footer@beta.ru'],'phones':[]}
    result=contacts.extract([page],'7707083893','ООО Альфа')
    assert {c['value'] for c in result}=={'info@alpha.ru','+7 (495) 123-45-67'}
    unrelated={'url':'https://clinic.ru/contact','text':'other@clinic.ru'}
    assert contacts.extract([page,unrelated],'7707083893','ООО Альфа')==result


def test_source_document_proof_is_local_not_marketplace_wide():
    doc={'url':'https://bidzaar.com/api/filestorage/files/download/abc','text':'ООО Альфа ИНН 7707083893 mail@alpha.ru','scoped':True}
    unrelated={'url':'https://bidzaar.com/contacts','text':'support@bidzaar.com'}
    result=contacts.extract([doc,unrelated],'7707083893','ООО Альфа')
    assert [c['value'] for c in result]==['mail@alpha.ru']


def test_www_contact_page_matches_legal_page_only_on_same_domain():
    pages=[{'url':'https://www.alpha.ru/legal','text':'ООО Альфа ИНН 7707083893'}, {'url':'https://alpha.ru/contact','text':'mail@alpha.ru'}, {'url':'https://fakealpha.ru/contact','text':'mail@fakealpha.ru'}]
    assert [c['value'] for c in contacts.extract(pages,'7707083893','ООО Альфа')]==['mail@alpha.ru']


def test_search_tries_second_provider_after_unusable_results(monkeypatch):
    calls=[]
    def search_urls(client,env,query,prefer_tavily=False):
        calls.append(prefer_tavily)
        return (['https://alpha.ru/contacts'],'TAVILY') if prefer_tavily else (['https://dead.ru/'],'TINYFISH')
    monkeypatch.setattr(contacts,'search_urls',search_urls)
    monkeypatch.setattr(contacts,'safe',lambda url:True)
    monkeypatch.setattr(contacts,'read',lambda client,url,robots: {'url':url,'text':'ООО Альфа ИНН 7707083893 mail@alpha.ru','links':[]} if 'alpha' in url else None)
    result=contacts.search(None,{},'7707083893','ООО Альфа')
    assert True in calls and result['status']=='VERIFIED'


def test_legal_section_does_not_extract_regulator_contacts():
    page={'url':'https://clinic.ru/patient/','text':'Полное наименование: ООО Альфа ИНН 7707083893 mail@alpha.ru\nРоспотребнадзор hotline@regulator.ru'}
    assert [c['value'] for c in contacts.extract([page],'7707083893','ООО Альфа')]==['mail@alpha.ru']


def test_reviewed_seed_is_fetched_and_still_requires_exact_identity(monkeypatch):
    seen=[]
    monkeypatch.setattr(contacts,'safe',lambda url:True)
    monkeypatch.setattr(contacts,'search_urls',lambda *args,**kwargs:([],'NONE'))
    def read(client,url,robots):
        seen.append(url)
        return {'url':url,'text':'Полное наименование: ООО Альфа ИНН 7707083893 wrong@alpha.ru','links':[]}
    monkeypatch.setattr(contacts,'read',read)
    result=contacts.search(None,{},'7729674716','Клиника К31')
    assert seen==['https://www.k31.ru/patient/'] and result['contacts']==[]
