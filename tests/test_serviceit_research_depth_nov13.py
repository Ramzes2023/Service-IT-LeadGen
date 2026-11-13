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
