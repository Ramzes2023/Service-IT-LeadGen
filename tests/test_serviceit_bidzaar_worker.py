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


def test_same_file_named_redirect_is_downloaded(monkeypatch):
    url='https://bidzaar.com/api/filestorage/files/download/00000000-0000-0000-0000-000000000001'
    seen=[]
    def handler(request):
        seen.append(str(request.url))
        if len(seen)==1:return httpx.Response(302,headers={'Location':'./00000000-0000-0000-0000-000000000001/requirements.txt'})
        return httpx.Response(200,content='Требования к участнику и порядок оплаты'.encode())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        docs,failed=w.documents(client,{**ROW,'files':[{'name':'requirements.txt','url':url}]})
    assert failed==0 and len(docs)==1 and len(seen)==2
    assert docs[0]['url']==url


def test_different_document_redirect_is_rejected():
    url='https://bidzaar.com/api/filestorage/files/download/00000000-0000-0000-0000-000000000001'
    def handler(request):
        return httpx.Response(302,headers={'Location':'./00000000-0000-0000-0000-000000000002/requirements.txt'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        docs,failed=w.documents(client,{**ROW,'files':[{'name':'requirements.txt','url':url}]})
    assert not docs and failed==1


def test_document_repair_never_overwrites_manager_changes(monkeypatch):
    conn=MagicMock();conn.execute.return_value.fetchone.return_value=('BIDZAAR-X',33,ROW,{w.BRIEF:'old'})
    monkeypatch.setattr(w,'secret_file',lambda *a:'unused')
    monkeypatch.setattr(w,'Rest',lambda *a:lambda *a:{'item':{w.BRIEF:'manager edit'}})
    monkeypatch.setattr(w,'dossier',lambda *a:pytest.fail('must not invoke model after conflict'))
    assert w.repair_documents(conn,None,{})=='DOCUMENT_RETRY_CONFLICT'
    assert len(conn.execute.call_args_list)==1


def test_sixth_document_is_not_displaced_by_api_description(monkeypatch):
    docs=[{'url':ROW['link']+'/file'+str(i),'text':'Условия оплаты и сроки поставки','sha256':'a'*64} for i in range(6)]
    monkeypatch.setattr(w,'documents',lambda *a:(docs,0))
    model_inputs=[]
    def research(client,env,evidence,*,use_model=True):
        if use_model:model_inputs.extend(d['url'] for d in evidence)
        return []
    monkeypatch.setattr(w,'_model_research',research)
    w.dossier(None,{},ROW,[])
    assert model_inputs==[d['url'] for d in docs]


def test_manager_card_contains_sourced_contacts_and_bmp_priority(monkeypatch):
    monkeypatch.setattr(w,'_model_research',lambda *a,**k:[])
    row={**ROW,'description':'Контакт закупки: Иван, +7 (495) 123-45-67, tender@example.ru'}
    fields=w.dossier(None,{},row,[])
    assert 'tender@example.ru' in fields[w.BRIEF]
    assert ROW['link'] in fields[w.BRIEF]
    assert 'Короткий сценарий разговора' in fields[w.BRIEF]
    assert 'Отрасль компании: не подтверждена' in fields[w.BRIEF]
    assert fields[w.BADGE_FIELD].startswith('⚠')
    assert all(ord(c)<=65535 for c in fields[w.BADGE_FIELD])


def test_related_signal_requires_same_inn_and_includes_substance():
    def rest(method,args):
        return {'item':{'categoryId':21,w.INN:ROW['inn'] if args['id']==17 else '7700000000',
                        w.SUMMARY:'Внедрение 1С',w.PREFIX+'SIGNAL_SOURCE':'B2B',
                        w.PREFIX+'SIGNAL_URL':'https://example.test/tender',
                        'createdTime':'2026-11-12','stageId':'NEW'}}
    related=w.related_signals(rest,[17,19],ROW['inn'])
    assert len(related)==1
    assert 'Внедрение 1С' in related[0] and '2026-11-12' in related[0]
    assert ROW['inn'] in related[0]


def test_presentation_upgrade_preserves_document_evidence_without_model(monkeypatch):
    monkeypatch.setattr(w,'documents',lambda *a:pytest.fail('no redownload'))
    monkeypatch.setattr(w,'_model_research',lambda *a,**kw:pytest.fail('no paid repeat'))
    old={w.REPORT:'Условия оплаты. Источник: https://example.test/spec',w.LIMITS:'Прочитано документов: 5. Не обработано: 0.',w.PREFIX+'AI_STATUS':'RouterAI: цитаты проверены',w.PREFIX+'AI_QUOTE':'Точная цитата',w.PREFIX+'REVIEW_DECISION':'manager-owned'}
    fields=w.dossier(None,{},ROW,[],cached=old)
    assert fields[w.LIMITS]==old[w.LIMITS]
    assert fields[w.PREFIX+'AI_QUOTE']=='Точная цитата'
    assert 'Условия оплаты' in fields[w.REPORT]
    assert w.PREFIX+'REVIEW_DECISION' not in fields


def test_normal_crm_progress_does_not_invalidate_dossier_or_hide_content_changes():
    expected={'stageId':'DT1062_21:NEW','assignedById':605,'categoryId':21,w.SUMMARY:'Поставка серверов'}
    moved={**expected,'stageId':'DT1062_21:PREPARATION','assignedById':606}
    assert w.saved_fields_match(moved,expected)
    assert not w.saved_fields_match({**moved,w.SUMMARY:'Правка менеджера'},expected)
    assert not w.saved_fields_match({**moved,'categoryId':22},expected)


def test_bank_account_is_not_presented_as_a_phone_contact(monkeypatch):
    monkeypatch.setattr(w,'_model_research',lambda *a,**k:[])
    fields=w.dossier(None,{},dict(ROW,description='Расчётный счёт 40817810099910004312'),[])
    assert 'Именной контакт и телефон в прочитанных материалах не найдены' in fields[w.BRIEF]


def test_cached_contacts_keep_source_but_drop_account_number(monkeypatch):
    monkeypatch.setattr(w,'documents',lambda *a:pytest.fail('no download'))
    cached={w.REPORT:'Исходные доказательства',w.LIMITS:'Прочитано документов: 1. Не обработано: 0.',
      w.BRIEF:'Контакты с контекстом документа (принадлежность проверить): Счёт 40817810099910004312 | Источник: '+ROW['link']+'\nКонтакт Иван: +7 (495) 123-45-67 | Источник: '+ROW['link']+'\nПочему сейчас: срок подачи'}
    fields=w.dossier(None,{},ROW,[],cached=cached)
    assert '40817810099910004312' not in fields[w.BRIEF]
    assert 'Контакт Иван: +7 (495) 123-45-67 | Источник: '+ROW['link'] in fields[w.BRIEF]


@pytest.mark.parametrize('title,score', [
    ('Сопровождение 1С Бухгалтерия',95),
    ('IT-аутсорсинг рабочих мест и серверов',95),
    ('Техническая поддержка информационных систем',95),
    ('Предоставление IT-специалистов',95),
    ('Создание системы Антифрод на платформе 1С:Предприятие',90),
    ('Внедрение Битрикс24 CRM',90),
])
def test_core_service_priority_without_documents_or_budget(title,score):
    result=w.service_priority(dict(ROW,name=title),[])
    assert result[0]==score and result[3]
    assert result[1].startswith('★ ТОЧНОЕ ПОПАДАНИЕ')
    assert w.priority_title(dict(ROW,name=title)).startswith('★ ПРИОРИТЕТ')
    assert all(ord(c)<=65535 for c in result[1])


def test_hardware_cannot_outrank_service_with_documents_and_repeat_signals(monkeypatch):
    monkeypatch.setattr(w,'_model_research',lambda *a,**k:[])
    hardware=w.dossier(None,{},ROW,[17,19],cached={w.LIMITS:'Прочитано документов: 6.',w.REPORT:'Источник: verified'})
    service=w.dossier(None,{},dict(ROW,name='Сопровождение 1С'),[])
    assert hardware[w.SCORE_FIELD] < service[w.SCORE_FIELD]
    assert not hardware[w.BADGE_FIELD].startswith('★')
    assert service[w.SCORE_FIELD]==95


def test_old_service_card_rescored_without_paid_research(monkeypatch):
    monkeypatch.setattr(w,'_model_research',lambda *a,**k:pytest.fail('no repeat research'))
    cached={w.LIMITS:'Прочитано документов: 3.',w.REPORT:'Точная цитата из документа',w.SCORE_FIELD:78}
    fields=w.dossier(None,{},dict(ROW,name='Сопровождение 1С'),[],cached=cached)
    assert fields[w.SCORE_FIELD]==95
    assert fields[w.LIMITS]==cached[w.LIMITS]
    assert 'Точная цитата' in fields[w.REPORT]
    assert 'Почему такой приоритет:' in fields[w.BRIEF]


@pytest.mark.parametrize('changes',[
    {'acceptanceEndDate':'2020-01-01T00:00:00Z'},
    {'status':'closed'},
    {'deliveryAddresses':['Казань']},
])
def test_exact_fit_does_not_override_closed_or_out_of_region(changes):
    result=w.service_priority({**ROW,'name':'Сопровождение 1С',**changes},[])
    assert result[0]<60 and not result[3]


def test_license_purchase_is_not_service_exact_fit():
    assert not w.service_priority(dict(ROW,name='Поставка лицензий 1С'),[])[3]


def test_live_legacy_retry_yields_to_extended_document_queue():
    conn=MagicMock()
    assert w.repair_documents(conn,None,{'SERVICEIT_BIDZAAR_EXTENDED_DOCUMENTS_OWNER':'YES'})=='EXTENDED_DOCUMENT_QUEUE_ACTIVE'
    conn.execute.assert_not_called()


def test_cached_priority_migration_retains_extended_contact_blocks(monkeypatch):
    monkeypatch.setattr(w,'_model_research',lambda *args:[])
    fields=w.dossier(None,{},ROW,[])
    block='[SERVICEIT_PUBLIC_EVIDENCE_V1]\nПубличные контакты: info@company.ru\n[/SERVICEIT_PUBLIC_EVIDENCE_V1]'
    for key in (w.REPORT,w.BRIEF,w.LIMITS):fields[key]+='\n\n'+block
    monkeypatch.setattr(w,'_model_research',lambda *args:pytest.fail('Cached priority must not repeat paid research'))
    actual=w.dossier(None,{},ROW,[],cached=fields)
    for key in (w.REPORT,w.BRIEF,w.LIMITS):
        assert block in actual[key] and actual[key].count('[SERVICEIT_PUBLIC_EVIDENCE_V1]')==1
