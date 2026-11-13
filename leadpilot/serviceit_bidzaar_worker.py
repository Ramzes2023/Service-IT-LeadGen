"""Durable Bidzaar intake and guarded CRM delivery, separate from B2B receipts."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import urlsplit, urljoin, unquote
import httpx
from .serviceit_bidzaar_source import collect, decision, identity, UUID
from .serviceit_vps_pg_queue import db, secret_file
from .serviceit_vps_worker import exact_environment, live_permitted
from .serviceit_enrichment_worker import Rest, normalized
from .b2b_github_to_bitrix import bitrix_call
from .serviceit_bitrix_list_shape import strict_bitrix_items
from .serviceit_b2b_dossier_consistency_v37 import PREFIX, INN, BRIEF, REPORT, SUMMARY, LIMITS
from .serviceit_kanban_presentation import SCORE_FIELD, VERSION_FIELD, BADGE_FIELD
from .serviceit_b2b_tender_documents import extract_text
from .serviceit_vps_b2b_document_worker import _model_research

DDL = '''
CREATE TABLE IF NOT EXISTS serviceit_bidzaar_cursor (
 singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton), cursor TEXT NOT NULL);
INSERT INTO serviceit_bidzaar_cursor VALUES(TRUE,'2026-11-11T00:00:00Z') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS serviceit_bidzaar_inbox (
 source_id TEXT PRIMARY KEY, modified TIMESTAMPTZ NOT NULL, notice JSONB NOT NULL,
 state TEXT NOT NULL DEFAULT 'PENDING', card_id BIGINT, expected JSONB,
 reason TEXT NOT NULL DEFAULT '', updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
ALTER TABLE serviceit_bidzaar_inbox ADD COLUMN IF NOT EXISTS document_attempts INTEGER NOT NULL DEFAULT 0;
'''


def persist(conn, rows, cursor):
    with conn.transaction():
        for row in rows:
            verdict,_=decision(row)
            state='PENDING' if verdict=='ELIGIBLE' else 'FILTERED'
            conn.execute('''INSERT INTO serviceit_bidzaar_inbox(source_id,modified,notice,state,reason)
              VALUES(%s,%s,%s::jsonb,%s,%s) ON CONFLICT(source_id) DO UPDATE SET
              modified=excluded.modified,notice=excluded.notice,
              state=CASE WHEN serviceit_bidzaar_inbox.state IN ('PENDING','FILTERED')
                THEN excluded.state ELSE serviceit_bidzaar_inbox.state END,
              reason=excluded.reason,
              updated_at=now() WHERE excluded.modified>serviceit_bidzaar_inbox.modified''',
              (identity(row),row['lastModified'],json.dumps(row,ensure_ascii=False),state,verdict))
        conn.execute('UPDATE serviceit_bidzaar_cursor SET cursor=%s WHERE singleton', (cursor,))


def documents(client, row):
    docs=[]
    failures=0
    files=row.get('files') or []
    if not isinstance(files,list):
        return [],1
    for file in files[:6]:
        try:
            url=file['url'];p=urlsplit(url)
            if (p.scheme!='https' or p.netloc!='bidzaar.com' or p.query or p.fragment
                or not re.fullmatch('/api/filestorage/files/download/'+UUID,p.path)):
                raise ValueError('DOCUMENT_URL_INVALID')
            kind=PurePosixPath(file['name']).suffix.lower()
            if kind not in {'.pdf','.docx','.xlsx','.txt','.zip'}:
                raise ValueError('DOCUMENT_FORMAT_UNSUPPORTED')
            data=bytearray()
            current=url
            for redirect in range(3):
                with client.stream('GET',current,timeout=25,follow_redirects=False) as response:
                    if response.status_code in (301,302,303,307,308):
                        target=urljoin(current,response.headers.get('location',''))
                        dest=urlsplit(target)
                        suffix=dest.path.removeprefix(p.path+'/')
                        if (redirect==2 or dest.scheme!='https' or dest.netloc!='bidzaar.com'
                            or dest.query or dest.fragment or not dest.path.startswith(p.path+'/')
                            or not suffix or '/' in unquote(suffix) or '\\' in unquote(suffix)
                            or unquote(suffix) in ('.','..')):
                            raise ValueError('DOCUMENT_REDIRECT_REJECTED')
                        current=target
                        continue
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data)>4000000:raise ValueError('DOCUMENT_TOO_LARGE')
                    break
            text=extract_text(bytes(data),kind)
            if not text.strip():raise ValueError('DOCUMENT_EMPTY_TEXT')
            docs.append({'url':url,'text':text[:24000],'kind':kind,'bytes':len(data),
                         'sha256':hashlib.sha256(data).hexdigest()})
        except (ValueError,KeyError,TypeError,httpx.HTTPError):
            failures+=1
    return docs, failures+max(0,len(files)-6)


def service_priority(row, peers):
    """Business fit from procurement subject, independent of document availability."""
    from .serviceit_b2b_moscow_geo import classify_location_lines
    title = row.get('name', '')
    groups = set(decision(row)[1])
    service = bool(re.search(r'сопровожд|поддержк|обслуживан|аутсорс|аутстафф|администрир|обновлен|исправлен', title, re.I))
    development = bool(re.search(r'внедрен|разработ|создани|создать|доработ|автоматизац|интеграц', title, re.I))
    if ('1C' in groups and service) or ('IT_SUPPORT' in groups and service) or 'IT_STAFFING' in groups:
        base, exact, reason = 90, True, 'сопровождение 1С / IT-поддержка / аутсорсинг специалистов'
    elif '1C' in groups and development:
        base, exact, reason = 85, True, 'разработка или внедрение на платформе 1С'
    elif groups.intersection({'CRM', 'KKT_EDO'}) and (service or development):
        base, exact, reason = 85, True, 'услуги по CRM, кассам или ЭДО'
    elif '1C' in groups:
        base, exact, reason = 75, False, 'направление 1С; состав услуг требует уточнения'
    else:
        base, exact, reason = 60, False, 'смежная IT-закупка; профильные услуги не подтверждены названием'
    geo = classify_location_lines(['Адрес поставки: '+a for a in row.get('deliveryAddresses', [])])
    geo_points = 5 if geo == 'MOSCOW' else 0
    repeat_points = 3 if peers else 0
    score = min(98 if exact else 83 if '1C' in groups else 78, base + geo_points + repeat_points)
    explanation = (f'{reason}: {base}; Москва / МО: +{geo_points}; связанные сигналы: +{repeat_points}. '
                   'Доступность документов, бюджета и контактов не снижает соответствие услугам.')
    status = decision(row)[0]
    if status != 'ELIGIBLE':
        score = min(score, 39 if status in ('EXPIRED', 'NOT_OPEN', 'OUT_OF_SCOPE') else 59)
        exact = False
        explanation += ' Требует проверки: '+status+'.'
    badge = ('★ ТОЧНОЕ ПОПАДАНИЕ | ' if exact else '⚠ ') + str(score)+'/100 — '+('высокий приоритет' if score >= 80 else 'средний приоритет' if score >= 60 else 'требует проверки')
    return score, badge, explanation, exact


def priority_title(row, peers=()):
    return ('★ ПРИОРИТЕТ · ' if service_priority(row, peers)[3] else '')+'BIDZAAR · '+row['name'][:180]


def dossier(client, env, row, peers, *, cached=None):
    docs,missing=documents(client,row) if cached is None else ([],0)
    # API description is a separate attested source, not a downloaded file.
    description='\n'.join([row.get('name',''),row.get('description','')])[:24000]
    api_doc={'url':row['link'],'text':description}
    evidence=[api_doc,*docs]
    reports=_model_research(client,env,docs or [api_doc]) if cached is None else []
    if docs:
        reports+=_model_research(client,env,[api_doc],use_model=False)
    quotes=[]
    evidence_by_url={d['url']:d['text'] for d in evidence}
    for report in reports:
        for fact in report.get('facts',[]):
            q=fact.get('quote');url=fact.get('url')
            if (isinstance(q,str) and len(q)>=12 and url in evidence_by_url
                and ' '.join(q.split()) in ' '.join(evidence_by_url[url].split())):
                line=f"{fact.get('category','Факт')}: {q[:600]}\nИсточник: {url}"
                if line not in quotes:quotes.append(line)
    groups=decision(row)[1]
    budget=row.get('price')
    budget_text=(str(budget)+' '+str(row.get('currency',''))
                 if isinstance(budget,(int,float)) and not isinstance(budget,bool) and budget>0
                 else 'Не раскрыт; ноль в API не считается бюджетом')
    score,badge,priority_reason,_=service_priority(row,peers)
    passport=[row['name'], 'Заказчик: '+str(row.get('legalName') or row.get('publisherName')),
              'ИНН: '+row['inn'], 'Место исполнения: '+'; '.join(row['deliveryAddresses']),
              'Подача до: '+row['acceptanceEndDate'], 'Бюджет: '+budget_text,
              'Направления: '+', '.join(groups),'Источник: '+row['link']]
    limitations=f'Прочитано документов: {len(docs)}. Не обработано: {missing}. ФНС не проверена. '
    limitations+='Ненайденные условия не считаются отсутствующими; сверить актуальную редакцию перед участием.'
    related=[p if isinstance(p,str) else 'Карточка №'+str(p) for p in peers]
    report='\n'.join(passport+['','ДОКАЗАТЕЛЬСТВА ИЗ ИЗВЕЩЕНИЯ И ДОКУМЕНТОВ',*quotes[:12],
         '', 'ПРОЧИТАННЫЕ ФАЙЛЫ',*[d['url']+' · SHA256 '+d['sha256'][:12] for d in docs],
         '', 'СВЯЗАННЫЕ СИГНАЛЫ ПО ИНН',*related, '',limitations])
    fields={INN:row['inn'],PREFIX+'COMPANY_NAME':str(row.get('legalName') or row.get('publisherName'))[:250],
            BRIEF:'СЕРВИС ИТ | BIDZAAR\n'+'\n'.join(passport+related)[:3500],REPORT:report[:12000],
            SUMMARY:row['name'][:1000],LIMITS:limitations,SCORE_FIELD:score,
            PREFIX+'AI_STATUS':('RouterAI: цитаты проверены' if any(r.get('provider')=='RouterAI' for r in reports) else 'Разбор первоисточника; RouterAI не подтверждён'),
            PREFIX+'AI_QUOTE':('\n'.join(quotes[:2])[:3500] or 'Подтверждённые цитаты не извлечены'),
            PREFIX+'AI_HYPOTHESIS':'Соответствие направлениям: '+', '.join(groups)+'. Требования к участнику необходимо сверить с документами.',
            PREFIX+'AI_RECOMMENDATION':'Рассмотреть участие после проверки обязательных условий; автоматическое одобрение не выполнялось.',
            VERSION_FIELD:'svc-bidzaar-service-fit-v4',BADGE_FIELD:badge,
            PREFIX+'AI_NEXT_ACTION':'Проверить требования и подготовить решение об участии по приведённым источникам.'}
    if cached is not None:
        fields={k:v for k,v in cached.items() if k in fields}
        report=str(cached.get(REPORT,'')).split('\n\nПЛАН РАБОТЫ МЕНЕДЖЕРА')[0]
        prior=str(cached.get(BRIEF,'')).partition('Контакты с контекстом документа (принадлежность проверить): ')[2].split('\nПочему сейчас:',1)[0]
        for line in prior.splitlines():
            context,separator,source=line.rpartition(' | Источник: ')
            if separator and source.startswith('https://bidzaar.com/'):
                evidence.append({'url':source,'text':context})
        fields.update({SCORE_FIELD:score,VERSION_FIELD:'svc-bidzaar-service-fit-v4',
            BADGE_FIELD:badge})
    offers={'1C':'сопровождение и развитие 1С','IT_STAFFING':'предоставление IT-специалистов',
            'IT_SUPPORT':'поддержка IT-инфраструктуры','CRM':'внедрение и сопровождение CRM',
            'KKT_EDO':'кассовые решения и ЭДО','SERVERS':'поставка и настройка IT-оборудования',
            'CLOUD':'облачная инфраструктура','LICENSES':'поставка и продление лицензий'}
    offer='; '.join(offers.get(g,g) for g in groups)
    contacts=[]
    for doc in evidence:
        for line in doc['text'].splitlines():
            if (re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}',line)
                or re.search(r'(?<!\d)(?:\+7|8)[ (.-]*\d{3}[ ).-]*\d{3}[ -]*\d{2}[ -]*\d{2}(?!\d)',line)):
                entry=line.strip()[:350]+' | Источник: '+doc['url']
                if entry not in contacts:contacts.append(entry)
    contact_text='\n'.join(contacts[:4]) or 'Именной контакт и телефон в прочитанных материалах не найдены.'
    pitch=('Здравствуйте! По закупке «'+row['name'][:180]+'» хотим уточнить требования. '
           'Кто отвечает за техническую часть? Какие требования обязательны, '
           'допустимы ли альтернативы и каков порядок уточнений до подачи заявки?')
    manager=['ГОТОВАЯ КАРТОЧКА ДЛЯ МЕНЕДЖЕРА',*passport,
             badge, 'Почему такой приоритет: '+priority_reason,
             'Отрасль компании: не подтверждена первоисточником; предмет закупки не равен отрасли.',
             'Что предложить (гипотеза): '+offer,
             'Кому обращаться: опубликованному контактному лицу закупки; роль для уточнения — закупки / IT.',
             'Контакты с контекстом документа (принадлежность проверить): '+contact_text,
             'Почему сейчас: открыт приём предложений до '+row['acceptanceEndDate']+'.',
             'Короткий сценарий разговора: '+pitch,
             'Связанные сигналы по тому же ИНН: '+str(len(peers))+'. '+
             ('Повторная потребность: +3 к приоритету; это не подтверждение интереса клиента.' if peers else 'Дополнительных сигналов в CRM не найдено.'),
             *related[:4],
             'После разговора: зафиксировать потребность, интерес (да / нет / неясно), возражения и согласованный следующий шаг со сроком.']
    fields[BRIEF]='\n'.join(manager)[:7000]
    fields[REPORT]=(report+'\n\nПЛАН РАБОТЫ МЕНЕДЖЕРА\n'+'\n'.join(manager[len(passport)+1:]))[:16000]
    fields[PREFIX+'AI_HYPOTHESIS']='Предложение для обсуждения: '+offer+'. Соответствие обязательным требованиям сверить с ТЗ.'
    fields[PREFIX+'AI_NEXT_ACTION']='Изучить приведённые требования, выбрать опубликованный контакт и подготовить уточнения по сценарию. После разговора сохранить итог и согласованный срок следующего действия.'
    if cached is not None:
        # Priority-only migrations must retain independently verified evidence.
        marker='[SERVICEIT_PUBLIC_EVIDENCE_V1]'
        pattern=r'\[SERVICEIT_PUBLIC_EVIDENCE_V1\].*?\[/SERVICEIT_PUBLIC_EVIDENCE_V1\]'
        for key in (REPORT,BRIEF,LIMITS):
            blocks=re.findall(pattern,str(cached.get(key,'')),re.S)
            if blocks:fields[key]=str(fields.get(key,'')).split(marker)[0].rstrip()+'\n\n'+'\n'.join(blocks)
    return {k:(''.join(ch if ord(ch)<=0xffff else '•' for ch in v) if isinstance(v,str) else v) for k,v in fields.items()}


def crm_matches(rest, filters):
    ids=[];start=0
    for _ in range(10):
        result=rest('crm.item.list',{'entityTypeId':1062,'useOriginalUfNames':'Y',
                    'filter':{'categoryId':21,**filters},'start':start})
        for item in strict_bitrix_items(result):
            card=int(item['id'])
            actual=normalized(rest('crm.item.get',{'entityTypeId':1062,'id':card,
                                                  'useOriginalUfNames':'Y'})['item'])
            if str(actual.get('categoryId'))=='21' and all(actual.get(k)==v for k,v in filters.items()):
                ids.append(card)
        nxt=result.get('next')
        if nxt is None:return sorted(set(ids))
        if type(nxt) is not int or nxt<=start:raise ValueError('CRM_PAGINATION_INVALID')
        start=nxt
    raise ValueError('CRM_MATCHES_INCOMPLETE')


def related_signals(rest, peers, inn):
    details=[]
    for card in peers[:12]:
        item=normalized(rest('crm.item.get',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
        if str(item.get('categoryId'))!='21' or item.get(INN)!=inn:continue
        details.append('Карточка №'+str(card)+': '+str(item.get(SUMMARY) or item.get('title') or 'Без названия')[:250]+
                       '. Источник: '+str(item.get(PREFIX+'SIGNAL_SOURCE') or 'не указан')+
                       '. Дата создания: '+str(item.get('createdTime') or 'не указана')+
                       '. Стадия: '+str(item.get('stageId') or 'не указана')+
                       '. Сигнал: '+str(item.get(PREFIX+'SIGNAL_URL') or 'ссылка отсутствует')+
                       '. Связь подтверждена совпадением ИНН '+inn+'.')
    return details


def deliver(conn,client,env):
    rest=Rest(secret_file(env,'SERVICEIT_BITRIX_WEBHOOK'),client)
    result={'verified':0,'filtered':0,'uncertain':0}
    rows=conn.execute("SELECT source_id,notice FROM serviceit_bidzaar_inbox WHERE state='PENDING' ORDER BY modified LIMIT 30").fetchall()
    for source,row in rows:
        status,_=decision(row)
        if status!='ELIGIBLE':
            conn.execute("UPDATE serviceit_bidzaar_inbox SET state='FILTERED',reason=%s WHERE source_id=%s",(status,source))
            result['filtered']+=1;continue
        if result['verified']>=2:break
        duplicate=crm_matches(rest,{PREFIX+'SIGNAL_ID':source})
        if duplicate:
            conn.execute("UPDATE serviceit_bidzaar_inbox SET state='EXISTING',card_id=%s WHERE source_id=%s",(duplicate[0],source));continue
        peers=crm_matches(rest,{INN:row['inn']})
        fields=dossier(client,env,row,related_signals(rest,peers,row['inn']))
        fields.update({'title':priority_title(row,peers),'categoryId':21,'stageId':'DT1062_21:NEW','assignedById':605,
            PREFIX+'SIGNAL_ID':source,PREFIX+'SIGNAL_KIND':'tender-opportunity',
            PREFIX+'SIGNAL_SOURCE':'Bidzaar API',PREFIX+'SIGNAL_URL':row['link'],
            PREFIX+'SUGGESTED_SERVICE':', '.join(decision(row)[1]),
            PREFIX+'IDEMPOTENCY_KEY':hashlib.sha256(source.encode()).hexdigest()[:32],
            PREFIX+'EVIDENCE_STATUS':'Извещение Bidzaar API; источник и место исполнения проверены'})
        # Durable intent precedes POST. Never blindly repeat an uncertain create.
        changed=conn.execute("UPDATE serviceit_bidzaar_inbox SET state='POST_UNCERTAIN',expected=%s::jsonb WHERE source_id=%s AND state='PENDING' RETURNING source_id",(json.dumps(fields,ensure_ascii=False),source)).fetchone()
        if not changed:continue
        try:
            response=bitrix_call(client,rest.base,'crm.item.add',{'entityTypeId':1062,'useOriginalUfNames':'Y','fields':fields})['result']
            card=response['item']['id']
            if type(card)is not int or card<8:raise ValueError('CRM_ID_INVALID')
            actual=normalized(rest('crm.item.get',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
            if not all(str(actual.get(k))==str(v) for k,v in fields.items() if k!='title'):
                raise ValueError('CRM_READBACK_MISMATCH')
            conn.execute("UPDATE serviceit_bidzaar_inbox SET state='VERIFIED',card_id=%s WHERE source_id=%s",(card,source))
            result['verified']+=1
        except Exception:
            result['uncertain']+=1
    return result



def saved_fields_match(actual,expected):
    """Saved dossier integrity; stage/owner/title belong to CRM lifecycle."""
    return all(str(actual.get(k))==str(v) for k,v in expected.items()
               if k not in {'title','stageId','assignedById'})


def reconcile_uncertain(conn,client,env):
    rest=Rest(secret_file(env,'SERVICEIT_BITRIX_WEBHOOK'),client)
    for source,expected in conn.execute("SELECT source_id,expected FROM serviceit_bidzaar_inbox WHERE state IN ('POST_UNCERTAIN','UPDATE_UNCERTAIN') LIMIT 10").fetchall():
        matches=crm_matches(rest,{PREFIX+'SIGNAL_ID':source})
        if len(matches)!=1:continue
        card=matches[0]
        actual=normalized(rest('crm.item.get',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
        if saved_fields_match(actual,expected):
            conn.execute("UPDATE serviceit_bidzaar_inbox SET state='VERIFIED',card_id=%s WHERE source_id=%s AND state IN ('POST_UNCERTAIN','UPDATE_UNCERTAIN')",(card,source))



def repair_documents(conn,client,env,*,presentation_only=False):
    if not presentation_only and env.get('SERVICEIT_BIDZAAR_EXTENDED_DOCUMENTS_OWNER')=='YES':
        return 'EXTENDED_DOCUMENT_QUEUE_ACTIVE'
    row=conn.execute("""SELECT source_id,card_id,notice,expected FROM serviceit_bidzaar_inbox
        WHERE state='VERIFIED' AND (
          ((expected->>%s) IS DISTINCT FROM 'svc-bidzaar-service-fit-v4')
          OR (NOT %s AND document_attempts<2 AND ((expected->>%s) ~ 'Не обработано: [1-9]'
               OR (expected->>%s) NOT LIKE 'RouterAI:%%')))
        ORDER BY document_attempts,card_id LIMIT 1""",(VERSION_FIELD,presentation_only,LIMITS,PREFIX+'AI_STATUS')).fetchone()
    if not row:return 'NO_DOCUMENT_RETRY'
    source,card,notice,expected=row
    rest=Rest(secret_file(env,'SERVICEIT_BITRIX_WEBHOOK'),client)
    params={'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'}
    actual=normalized(rest('crm.item.get',params)['item'])
    if not saved_fields_match(actual,expected):
        return 'DOCUMENT_RETRY_CONFLICT'
    if expected.get(VERSION_FIELD)=='svc-bidzaar-service-fit-v4':
        conn.execute('UPDATE serviceit_bidzaar_inbox SET document_attempts=document_attempts+1 WHERE source_id=%s',(source,))
    peers=[peer for peer in crm_matches(rest,{INN:notice['inn']}) if peer!=card]
    patch=dossier(client,env,notice,related_signals(rest,peers,notice['inn']),
                  cached=expected if expected.get(VERSION_FIELD)!='svc-bidzaar-service-fit-v4' else None)
    def count(fields):
        m=re.search(r'Прочитано документов: (\d+)',fields.get(LIMITS,''))
        return int(m[1]) if m else 0
    new_model=patch.get(PREFIX+'AI_STATUS','').startswith('RouterAI:') and not expected.get(PREFIX+'AI_STATUS','').startswith('RouterAI:')
    if count(patch)<=count(expected) and not new_model and expected.get(VERSION_FIELD)=='svc-bidzaar-service-fit-v4':return 'DOCUMENT_RETRY_NO_IMPROVEMENT'
    # Recheck after network/model work, before any update of managed fields.
    actual=normalized(rest('crm.item.get',params)['item'])
    if not saved_fields_match(actual,expected):
        return 'DOCUMENT_RETRY_CONFLICT'
    # Preserve a title edited by the manager; decorate only our own title.
    if actual.get('title') == expected.get('title') and 'title' in expected:
        patch['title']=priority_title(notice,peers)
    combined={**expected,**patch}
    reserved=conn.execute("UPDATE serviceit_bidzaar_inbox SET state='UPDATE_UNCERTAIN',expected=%s::jsonb WHERE source_id=%s AND state='VERIFIED' RETURNING source_id",(json.dumps(combined,ensure_ascii=False),source)).fetchone()
    if not reserved:return 'DOCUMENT_RETRY_BUSY'
    response=client.post(rest.base+'/crm.item.update.json',json={**params,'fields':patch},timeout=20,follow_redirects=False)
    response.raise_for_status()
    if 'error' in response.json():return 'DOCUMENT_UPDATE_UNCERTAIN'
    actual=normalized(rest('crm.item.get',params)['item'])
    if not saved_fields_match(actual,combined) or ('title' in patch and actual.get('title')!=patch['title']):
        return 'DOCUMENT_UPDATE_UNCERTAIN'
    conn.execute("UPDATE serviceit_bidzaar_inbox SET state='VERIFIED' WHERE source_id=%s AND state='UPDATE_UNCERTAIN'",(source,))
    return 'DOCUMENT_UPDATE_VERIFIED'


def manager_tasks(conn,client,env):
    from .serviceit_stage3_all_new_task_automation import process_card
    from .serviceit_vps_native_fence import PostgresNativeFence
    runtime={**env,'SERVICEIT_BITRIX_WEBHOOK':secret_file(env,'SERVICEIT_BITRIX_WEBHOOK'),
             'SERVICEIT_STAGE3_TASK_WRITE_ENABLED':'YES_1062_21_INTERNAL_REVIEW',
             'SERVICEIT_VPS_TASK_ASSIGNEE_ID':'605',
             'SERVICEIT_VPS_TASK_ASSIGNEE_APPROVED':'YES_1062_21_INTERNAL_MANAGER'}
    rows=conn.execute("""SELECT b.card_id,b.expected FROM serviceit_bidzaar_inbox b
        LEFT JOIN serviceit_vps_native_receipts r ON r.card_id=b.card_id AND r.namespace='TASK_V1'
        WHERE b.state='VERIFIED' AND r.card_id IS NULL
          AND b.expected->>%s='svc-bidzaar-service-fit-v4' ORDER BY b.card_id LIMIT 8""",(VERSION_FIELD,)).fetchall()
    rest=Rest(runtime['SERVICEIT_BITRIX_WEBHOOK'],client)
    verified=0
    for card,expected in rows:
        actual=normalized(rest('crm.item.get',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
        if not saved_fields_match(actual,expected):continue
        try:
            status=process_card(client,runtime,card,native_fence=PostgresNativeFence(conn,'TASK_V1'))
            verified+=int(status=='INTERNAL_REVIEW_TASK_READBACK_VERIFIED')
            print('BIDZAAR_MANAGER_TASK='+status,flush=True)
        except Exception as exc:
            print('BIDZAAR_MANAGER_TASK_ERROR='+type(exc).__name__.upper(),flush=True)
            reason=str(exc)
            if re.fullmatch(r'[A-Z][A-Z0-9_]{3,100}',reason):print('BIDZAAR_MANAGER_TASK_REASON='+reason,flush=True)
    return verified


def main():
    env=dict(os.environ)
    endpoint=env.get('SERVICEIT_BIDZAAR_FEED_URL','')
    if not endpoint:
        print('BIDZAAR_STATUS=FEED_CONFIGURATION_REQUIRED');return
    if not live_permitted(env):raise ValueError('VPS_LIVE_REQUIRED')
    with db(env) as conn,httpx.Client(trust_env=False,follow_redirects=False,timeout=30) as client:
        # One poll/delivery process, including when a scheduler overlaps.
        if not conn.execute('SELECT pg_try_advisory_lock(1062,604)').fetchone()[0]:
            print('BIDZAAR_STATUS=BUSY');return
        try:
            conn.execute(DDL)
            cursor=conn.execute('SELECT cursor FROM serviceit_bidzaar_cursor WHERE singleton').fetchone()[0]
            fetched=collect(client,endpoint,cursor,lambda rows,c:persist(conn,rows,c),max_pages=5)
            print('BIDZAAR_FETCH='+fetched['status'])
            print('BIDZAAR_ROWS='+str(fetched['rows']))
            if env.get('SERVICEIT_BIDZAAR_WRITE_ENABLED')=='YES':
                reconcile_uncertain(conn,client,env)
                for _ in range(12):
                    upgraded=repair_documents(conn,client,exact_environment(env),presentation_only=True)
                    print('BIDZAAR_PRESENTATION='+upgraded,flush=True)
                    if upgraded!='DOCUMENT_UPDATE_VERIFIED':break
                print('BIDZAAR_DOCUMENT_REPAIR='+repair_documents(conn,client,exact_environment(env)),flush=True)
                result=deliver(conn,client,exact_environment(env))
                for key,value in result.items():print('BIDZAAR_'+key.upper()+'='+str(value))
                print('BIDZAAR_TASKS_VERIFIED='+str(manager_tasks(conn,client,env)),flush=True)
                try:
                    from .serviceit_postcall_assistant import run as postcall
                    print('BIDZAAR_POSTCALL='+postcall(conn,client,env),flush=True)
                except Exception as exc:
                    print('BIDZAAR_POSTCALL_ERROR='+type(exc).__name__.upper(),flush=True)
        finally:conn.execute('SELECT pg_advisory_unlock(1062,604)')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('BIDZAAR_ERROR='+type(exc).__name__.upper())
        import traceback
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        print('BIDZAAR_ERROR_LINE='+str(frame.lineno))
        reason=str(exc)
        if re.fullmatch(r'[A-Z][A-Z0-9_]{3,100}',reason):
            print('BIDZAAR_ERROR_REASON='+reason)
        raise SystemExit(2)
