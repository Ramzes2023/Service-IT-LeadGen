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
from .serviceit_kanban_presentation import SCORE_FIELD, VERSION_FIELD, BADGE_FIELD, score_label
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


def dossier(client, env, row, peers):
    docs,missing=documents(client,row)
    # API description is a separate attested source, not a downloaded file.
    description='\n'.join([row.get('name',''),row.get('description','')])[:24000]
    evidence=[{'url':row['link'],'text':description},*docs][:6]
    reports=_model_research(client,env,evidence)
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
    score=60+10*bool(docs)+8*bool(quotes)+12*bool(peers)
    passport=[row['name'], 'Заказчик: '+str(row.get('legalName') or row.get('publisherName')),
              'ИНН: '+row['inn'], 'Место исполнения: '+'; '.join(row['deliveryAddresses']),
              'Подача до: '+row['acceptanceEndDate'], 'Бюджет: '+budget_text,
              'Направления: '+', '.join(groups),'Источник: '+row['link']]
    limitations=f'Прочитано документов: {len(docs)}. Не обработано: {missing}. ФНС не проверена. '
    limitations+='Ненайденные условия не считаются отсутствующими; сверить актуальную редакцию перед участием.'
    related=['Карточка №'+str(p) for p in peers]
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
            VERSION_FIELD:'svc-bidzaar-evidence-v1',BADGE_FIELD:score_label(score),
            PREFIX+'AI_NEXT_ACTION':'Проверить требования и подготовить решение об участии по приведённым источникам.'}
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
        fields=dossier(client,env,row,peers)
        fields.update({'title':'BIDZAAR · '+row['name'][:180],'categoryId':21,'stageId':'DT1062_21:NEW',
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



def reconcile_uncertain(conn,client,env):
    rest=Rest(secret_file(env,'SERVICEIT_BITRIX_WEBHOOK'),client)
    for source,expected in conn.execute("SELECT source_id,expected FROM serviceit_bidzaar_inbox WHERE state IN ('POST_UNCERTAIN','UPDATE_UNCERTAIN') LIMIT 10").fetchall():
        matches=crm_matches(rest,{PREFIX+'SIGNAL_ID':source})
        if len(matches)!=1:continue
        card=matches[0]
        actual=normalized(rest('crm.item.get',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
        if all(str(actual.get(k))==str(v) for k,v in expected.items() if k!='title'):
            conn.execute("UPDATE serviceit_bidzaar_inbox SET state='VERIFIED',card_id=%s WHERE source_id=%s AND state IN ('POST_UNCERTAIN','UPDATE_UNCERTAIN')",(card,source))



def repair_documents(conn,client,env):
    row=conn.execute("""SELECT source_id,card_id,notice,expected FROM serviceit_bidzaar_inbox
        WHERE state='VERIFIED' AND document_attempts<2
          AND ((expected->>%s) ~ 'Не обработано: [1-9]'
               OR (expected->>%s) NOT LIKE 'RouterAI:%%')
        ORDER BY document_attempts,card_id LIMIT 1""",(LIMITS,PREFIX+'AI_STATUS')).fetchone()
    if not row:return 'NO_DOCUMENT_RETRY'
    source,card,notice,expected=row
    rest=Rest(secret_file(env,'SERVICEIT_BITRIX_WEBHOOK'),client)
    params={'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'}
    actual=normalized(rest('crm.item.get',params)['item'])
    if not all(str(actual.get(k))==str(v) for k,v in expected.items() if k!='title'):
        return 'DOCUMENT_RETRY_CONFLICT'
    conn.execute('UPDATE serviceit_bidzaar_inbox SET document_attempts=document_attempts+1 WHERE source_id=%s',(source,))
    peers=[peer for peer in crm_matches(rest,{INN:notice['inn']}) if peer!=card]
    patch=dossier(client,env,notice,peers)
    def count(fields):
        m=re.search(r'Прочитано документов: (\d+)',fields.get(LIMITS,''))
        return int(m[1]) if m else 0
    new_model=patch.get(PREFIX+'AI_STATUS','').startswith('RouterAI:') and not expected.get(PREFIX+'AI_STATUS','').startswith('RouterAI:')
    if count(patch)<=count(expected) and not new_model:return 'DOCUMENT_RETRY_NO_IMPROVEMENT'
    # Recheck after network/model work, before any update of managed fields.
    actual=normalized(rest('crm.item.get',params)['item'])
    if not all(str(actual.get(k))==str(v) for k,v in expected.items() if k!='title'):
        return 'DOCUMENT_RETRY_CONFLICT'
    combined={**expected,**patch}
    reserved=conn.execute("UPDATE serviceit_bidzaar_inbox SET state='UPDATE_UNCERTAIN',expected=%s::jsonb WHERE source_id=%s AND state='VERIFIED' RETURNING source_id",(json.dumps(combined,ensure_ascii=False),source)).fetchone()
    if not reserved:return 'DOCUMENT_RETRY_BUSY'
    response=client.post(rest.base+'/crm.item.update.json',json={**params,'fields':patch},timeout=20,follow_redirects=False)
    response.raise_for_status()
    if 'error' in response.json():return 'DOCUMENT_UPDATE_UNCERTAIN'
    actual=normalized(rest('crm.item.get',params)['item'])
    if not all(str(actual.get(k))==str(v) for k,v in combined.items() if k!='title'):
        return 'DOCUMENT_UPDATE_UNCERTAIN'
    conn.execute("UPDATE serviceit_bidzaar_inbox SET state='VERIFIED' WHERE source_id=%s AND state='UPDATE_UNCERTAIN'",(source,))
    return 'DOCUMENT_UPDATE_VERIFIED'


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
                print('BIDZAAR_DOCUMENT_REPAIR='+repair_documents(conn,client,exact_environment(env)),flush=True)
                result=deliver(conn,client,exact_environment(env))
                for key,value in result.items():print('BIDZAAR_'+key.upper()+'='+str(value))
        finally:conn.execute('SELECT pg_advisory_unlock(1062,604)')

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('BIDZAAR_ERROR='+type(exc).__name__.upper())
        raise SystemExit(2)
