import httpx
import pytest
from leadpilot import serviceit_vps_b2b_deep_research as deep
from leadpilot import serviceit_b2b_routerai_deep as ai
from leadpilot import serviceit_b2b_tender_documents as documents
from leadpilot import serviceit_b2b_dossier_consistency_v37 as consistency

URL = 'https://www.b2b-center.ru/market/licenses/tender-1234567/'
NOTICE = {'source':'B2B-Center', 'tender_id':'1234567', 'subject':'Продление лицензий Teams', 'source_url':URL}
TEXT = ('B2B-Center Запрос предложений №1234567 Продление лицензий Teams. '
        'Организатор АО «Учебник» Процедура проводится Для собственных нужд. '
        'Окончание приёма заявок 20.11.2026 13:59. '
        'Условия оплаты: отсрочка платежа 30 календарных дней. '
        'Условия поставки / оказания услуг электронная поставка. '
        'Адрес поставки / оказания услуг г. Москва, Тестовая улица, 10.')


def test_full_primary_preserves_link_and_resolves_self_buyer_inn(monkeypatch):
    monkeypatch.setattr('leadpilot.serviceit_public_evidence_research._robots', lambda *a:None)
    monkeypatch.setattr('leadpilot.serviceit_public_evidence_research._get', lambda *a:
        '<p>'+TEXT+'</p><a href="/firms/ao-uchebnik/123/">АО «Учебник»</a>')
    monkeypatch.setattr(deep, '_page', lambda *a: {'text':'Краткое наименование АО «Учебник» Полное наименование Акционерное общество «Учебник» ИНН 1234567894'})
    with httpx.Client() as client:
        result=deep.verified_original_b2b_details({'pages':[]},NOTICE,client)
        assert result['pages'][0]['profile_links']
        assert deep.public_buyer_candidate(result)=='АО «Учебник»'
        enriched=deep.followup_public_buyer_research(result,{'SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED':'YES'},client,'')
        assert deep.published_buyer_inn_evidence(enriched)['inn']=='1234567894'
        result['pages'][0]['excerpt']=TEXT.replace('Для собственных нужд','Для нужд третьих лиц')
        assert deep.public_buyer_candidate(result)==''


def test_successful_ai_cannot_drop_published_deadline_and_payment():
    found={'status':'PUBLIC_PAGES_INSPECTED','pages':[{'url':URL,'excerpt':TEXT}]}
    docs=ai.public_documents(found)
    report={'facts':[{'source_id':1,'url':URL,'quote':'Продление лицензий Teams','category':'SCOPE','finding':'Лицензии'}], 'provider':'RouterAI'}
    merged=ai.complete_source_coverage(report,docs)
    assert {'SCOPE','DEADLINE','PAYMENT','LOCATION'} <= {f['category'] for f in merged['facts']}
    assert len(merged['facts'])<=16
    assert all(f['quote'] in docs[f['source_id']-1]['text'] for f in merged['facts'])


def test_real_delivery_address_beats_electronic_delivery_mode():
    lines=[f'ПРОЧИТАННЫЙ ИСТОЧНИК / LOCATION: {q} [{URL}]' for q in (
        'Условия поставки / оказания услуг электронная поставка',
        'Адрес поставки / оказания услуг г. Москва, Тестовая улица, 10')]
    lines.append(f'ПРОЧИТАННЫЙ ИСТОЧНИК / DEADLINE: Актуально до 20.11.2026 13:59 [{URL}]')
    record=consistency._record_from_verified(NOTICE,{},lines)
    assert record['region']=='Москва'
    assert 'Тестовая' in record['delivery']
    assert record['deadline'].startswith('20.11.2026 13:59')


def test_late_download_and_direct_official_attachment_are_discovered(monkeypatch):
    monkeypatch.setattr(documents,'_robots',lambda *a:None)
    official='https://zakupki.gov.ru/223/purchase/public/download/download.html?id=12345'
    raw='<p>Тендер 1234567</p>'+''.join(f'<a href="/nav/{i}">Раздел</a>' for i in range(190))
    raw+='<a href="/files/download?id=123">Скачать документ</a>'
    raw+=f'<a href="{official}">Техническое задание</a>'
    raw+='<a href="https://evil.invalid/document.pdf">ТЗ</a>'
    monkeypatch.setattr(documents,'_get',lambda *a:raw)
    rows=documents.candidate_links(None,{'status':'PUBLIC_PAGES_INSPECTED','pages':[{'url':URL,'excerpt':TEXT}]},'1234567')
    assert len(rows)==2
    assert rows[0]['url']==official and rows[0]['official_eis']


def test_fresh_primary_updates_automatic_passport_with_source_proof(monkeypatch):
    from leadpilot import serviceit_b2b_dossier_consistency_worker_v37 as worker
    monkeypatch.setattr(deep,'verified_original_b2b_details', lambda *a:{'status':'PUBLIC_PAGES_INSPECTED','original_b2b_detail_independently_read':True,'pages':[{'url':URL,'excerpt':TEXT}]})
    monkeypatch.setattr(deep,'followup_public_buyer_research',lambda f,*a:f)
    monkeypatch.setattr(worker,'secret_file',lambda *a,**k:'')
    record,lines=worker.fresh_primary_evidence(NOTICE,{},None,{})
    assert record['buyer']=='АО «Учебник»'
    assert record['deadline'].startswith('20.11.2026 13:59')
    assert record['region']=='Москва'
    assert '30 календарных' in record['payment']
    assert 'inn' not in record
    assert all(consistency.PROVEN_QUOTE.fullmatch(line) for line in lines)


def test_combined_inn_kpp_label_on_independent_public_company_page(monkeypatch):
    monkeypatch.setattr(deep, '_page', lambda *a: {'text':'АО «Учебник» ОГРН 1234567890123 ИНН / КПП: 1234567894 / 770101001', 'url':'https://uch-company.ru/requisites/', 'emails':[], 'phones':[]})
    monkeypatch.setattr('leadpilot.serviceit_public_evidence_research._page', deep._page)
    evidence=deep._public_company_self_declaration(None,'АО «Учебник»',[{'url':'https://uch-company.ru/requisites/'}],'')
    assert evidence['inn']=='1234567894'
    assert evidence['fns_verified'] is False


def test_fetched_licence_title_and_plain_moscow_are_preserved(monkeypatch):
    from leadpilot.b2b_email_opportunity_bridge import classify_services
    actual=TEXT.replace('г. Москва','Россия, Москва').replace('отсрочка платежа 30 календарных дней', 'постоплата: указать да или нет')
    monkeypatch.setattr('leadpilot.serviceit_public_evidence_research._robots', lambda *a:None)
    monkeypatch.setattr('leadpilot.serviceit_public_evidence_research._get', lambda *a:'<p>'+actual+'</p>')
    found=deep.verified_original_b2b_details({'pages':[]},NOTICE,None)
    facts=ai.source_only_investigation(found)['facts']
    lines=[f'ПРОЧИТАННЫЙ ИСТОЧНИК / {f["category"]}: {f["quote"]} [{f["url"]}]' for f in facts]
    record=consistency._record_from_verified(NOTICE,{},lines)
    assert record['region']=='Москва'
    assert any(classify_services(x) for x in record['service_evidence'])
    assert 'постоплата' in record['payment']
    different={**NOTICE,'subject':'Несуществующее внедрение 1С'}
    assert not deep.verified_original_b2b_details({'pages':[]},different,None).get('pages')


def test_free_search_never_returns_snippet_inn_as_evidence(monkeypatch):
    monkeypatch.setattr(deep,'secret_file',lambda *a,**k:'x'*32)
    def handler(request):
        return httpx.Response(200,json={'results':[
            {'url':'https://registry-example.ru/company/123','snippet':'АО «Учебник» ИНН 1234567894'},
            {'url':'http://127.0.0.1/private','snippet':'АО «Учебник» ИНН 1234567894'}]})
    with httpx.Client(transport=httpx.MockTransport(handler),trust_env=False) as client:
        rows=deep._free_requisites_candidates(client,'АО «Учебник»',{
            'SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED':'YES',
            'SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED':'YES'})
    assert rows==[{'url':'https://registry-example.ru/company/123'}]


def test_long_official_page_and_registry_contact_separation(monkeypatch):
    from leadpilot import serviceit_public_evidence_research as public
    source='https://cbr.ru/registries/rcb/ecb/orginfo/?id=123456'
    page={'text':'Раздел меню '*700,'url':source,'emails':[],'phones':[]}
    monkeypatch.setattr(public,'_page',lambda *a:dict(page,text=page['text'][:7000]))
    monkeypatch.setattr(public,'_get',lambda *a:'<p>'+page['text']+'АО «Учебник» ИНН 1234567894</p>')
    monkeypatch.setattr(public,'_contacts',lambda *a:[{'type':'phone','value':'+74950000000'}])
    evidence=deep._public_company_self_declaration(None,'АО «Учебник»',[{'url':source}],'')
    assert evidence['inn']=='1234567894'
    assert evidence['contacts']==[]
    assert evidence['provenance']=='PUBLIC_REGISTRY_REQUISITES'


def test_delivery_label_embedded_in_previous_value_does_not_hide_real_address():
    lines=[f'ПРОЧИТАННЫЙ ИСТОЧНИК / LOCATION: {q} [{URL}]' for q in (
        'Условия поставки / оказания услуг За счет поставщика Адрес поставки / оказания услуг Россия',
        'Адрес поставки / оказания услуг Россия, Москва, Тестовая улица, 10')]
    assert consistency._record_from_verified(NOTICE,{},lines)['region']=='Москва'


@pytest.mark.parametrize('status,size,success', [(200,180000,True),(403,100,False),(200,1100000,False)])
def test_large_legal_page_reader_preserves_access_and_size_limits(monkeypatch,status,size,success):
    from leadpilot import serviceit_public_evidence_research as public
    def unavailable(*args):
        raise public.ResearchBlocked('RESEARCH_PAGE_UNAVAILABLE')
    monkeypatch.setattr(public,'_page',unavailable)
    monkeypatch.setattr(public,'_public_dns',lambda host:True)
    html='<style>'+('x'*size)+'</style><p>АО «Учебник» ИНН 1234567894</p>'
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(status,text=html,headers={'content-type':'text/html; charset=utf-8'})),trust_env=False) as client:
        if success:
            page=deep._company_requisites_page(client,'https://company-example.ru/',frozenset({'company-example.ru'}))
            assert page['text']=='АО «Учебник» ИНН 1234567894'
        else:
            with pytest.raises(public.ResearchBlocked):
                deep._company_requisites_page(client,'https://company-example.ru/',frozenset({'company-example.ru'}))


@pytest.mark.parametrize('prior_scan',[False,True])
def test_document_discovery_gets_search_scope_only_on_initial_scan(monkeypatch,prior_scan):
    from contextlib import contextmanager
    from unittest.mock import MagicMock
    from leadpilot import serviceit_vps_b2b_document_worker as worker
    from leadpilot.serviceit_vps_worker import SearchBudget
    conn=MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value=(True,)
    conn.execute.return_value.fetchone.return_value=(1,) if prior_scan else None
    monkeypatch.setattr(worker,'live_permitted',lambda env:True)
    monkeypatch.setattr(worker,'exact_environment',lambda env:env)
    monkeypatch.setattr(worker,'selected',lambda *a,**k:(31,'B2B-CENTER-1234567',NOTICE))
    monkeypatch.setattr(worker,'secret_file',lambda *a,**k:'fake')
    monkeypatch.setattr(worker,'Rest',lambda *a:lambda *a,**k:{'item':{field:'ready' for field in worker.AI_FIELDS}})
    monkeypatch.setattr(worker,'normalized',lambda item:item)
    monkeypatch.setattr(worker,'validate_identity',lambda *a:None)
    monkeypatch.setattr(worker,'safe_notice',lambda *a:(URL,NOTICE['subject']))
    monkeypatch.setattr(worker,'inspect_public_documents',lambda *a,**k:{'documents':[],'discovered':0})
    monkeypatch.setattr(worker,'save_no_documents',lambda *a:None)
    monkeypatch.setattr('leadpilot.serviceit_b2b_eis_mirrors.supplement_eis_mirrors',lambda found,*a,**k:found)
    budget=SearchBudget(); opened=[]
    @contextmanager
    def scope(card,source):
        opened.append(card)
        with budget.for_card(True):
            budget.remaining=3
            yield
    def discover(*a):
        request=httpx.Request('GET','https://api.search.tinyfish.ai/')
        if not prior_scan:
            for i in range(3): budget.check(request)
        with pytest.raises(httpx.RequestError): budget.check(request)
        return {'status':'PUBLIC_RESEARCH_INCOMPLETE','pages':[]}
    monkeypatch.setattr(worker,'source_findings',discover)
    env={worker.ENABLED:'YES','SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED':'YES',
         'SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED':'YES',
         'SERVICEIT_ROUTERAI_PRIVACY_APPROVED':'YES_1062_21'}
    got=worker.run_once(env,None,conn,search_scope=scope)
    assert got['crm_writes']==0 and 'RETRY_12H' in got['status']
    assert bool(opened) is (not prior_scan)
    assert budget.sent == (0 if prior_scan else 3)


def test_requisites_follow_same_site_legal_link_before_other_candidates(monkeypatch):
    from leadpilot import serviceit_public_evidence_research as public
    seen=[]
    def page(client,url,hosts):
        seen.append(url)
        body='АО «Учебник» ИНН 1234567894' if url.endswith('/agreement/') else 'АО «Учебник» Описание компании'
        return {'url':url,'text':body,'emails':[],'phones':[], 'links':[
            ('https://other.invalid/legal/','Юридическая информация'),
            ('/agreement/','Пользовательское соглашение')]}
    monkeypatch.setattr(public,'_page',page)
    found=deep._public_company_self_declaration(None,'АО «Учебник»',[{'url':'https://company-example.ru/'}],'')
    assert found['inn']=='1234567894'
    assert seen==['https://company-example.ru/','https://company-example.ru/agreement/']


@pytest.mark.parametrize('target,allowed',[
    ('/market/updated-public-title/tender-1234567/',True),
    ('/market/other/tender-9999999/',False),('/login/',False),
    ('https://other.invalid/market/title/tender-1234567/',False),
    ('/market/title/tender-1234567/?token=private',False)])
def test_primary_canonical_redirect_is_same_host_and_tender_only(monkeypatch,target,allowed):
    from leadpilot import serviceit_public_evidence_research as public
    monkeypatch.setattr(public,'_robots',lambda *a:None)
    monkeypatch.setattr(public,'_public_dns',lambda host:True)
    calls=[]
    def response(request):
        calls.append(str(request.url))
        return (httpx.Response(307,headers={'location':target}) if len(calls)==1 else
                httpx.Response(200,text='<p>'+TEXT+'</p>',headers={'content-type':'text/html'}))
    with httpx.Client(transport=httpx.MockTransport(response),trust_env=False) as client:
        found=deep.verified_original_b2b_details({'pages':[]},NOTICE,client)
        assert bool(found.get('original_b2b_detail_independently_read')) is allowed
        assert len(calls)==(2 if allowed else 1)
        assert not client.event_hooks['response']
        if allowed:
            assert found['pages'][0]['url']=='https://www.b2b-center.ru'+target
