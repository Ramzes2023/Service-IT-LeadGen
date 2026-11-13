"""All-card public contact enrichment and durable document backlog processor."""
import json,os,re,traceback
import httpx
from . import serviceit_extended_documents as docs
from . import serviceit_company_contacts as contacts
from .serviceit_vps_pg_queue import db,secret_file
from .serviceit_vps_worker import live_permitted
from .serviceit_enrichment_worker import Rest,normalized
from .serviceit_bitrix_list_shape import strict_bitrix_items
from .serviceit_b2b_dossier_consistency_v37 import INN,COMPANY,REPORT,BRIEF,LIMITS
from .bitrix_1062_mapping import _is_valid_inn

DDL='''CREATE TABLE IF NOT EXISTS serviceit_company_contact_cache (
 inn TEXT PRIMARY KEY,name TEXT NOT NULL,result JSONB,next_attempt_at TIMESTAMPTZ DEFAULT now());
 CREATE TABLE IF NOT EXISTS serviceit_extended_evidence_receipts (
 card_id BIGINT PRIMARY KEY,expected JSONB NOT NULL,status TEXT NOT NULL,updated_at TIMESTAMPTZ DEFAULT now());'''
BEGIN='[SERVICEIT_PUBLIC_EVIDENCE_V1]';END='[/SERVICEIT_PUBLIC_EVIDENCE_V1]'


def owned(base,block):
    base=str(base or '')
    base=re.sub(re.escape(BEGIN)+r'.*?'+re.escape(END),'',base,flags=re.S).strip()
    return base+('\n\n'+BEGIN+'\n'+block+'\n'+END if block else '')


def get(rest,card):
    item=normalized(rest('crm.item.get',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
    if card in (3,7) or card<8 or str(item.get('categoryId'))!='21':raise ValueError('CARD_SCOPE_INVALID')
    return item


def inventory(rest):
    items=[];last=0
    for _ in range(100):
        page=strict_bitrix_items(rest('crm.item.list',{'entityTypeId':1062,'useOriginalUfNames':'Y','filter':{'categoryId':21,'>id':last},'order':{'id':'ASC'},'select':['id']}))
        if not page:return items
        ids=[int(i['id']) for i in page]
        if ids!=sorted(set(ids)) or min(ids)<=last:raise ValueError('CARD_PAGINATION_INVALID')
        for card in ids:
            if card>=8:items.append(get(rest,card))
        last=max(ids)
    raise ValueError('CARD_INVENTORY_INCOMPLETE')


def patch(conn,rest,client,card,before,fields):
    if not fields or all(str(before.get(k,''))==str(v) for k,v in fields.items()):return False
    if not set(fields)<= {REPORT,BRIEF,LIMITS}:raise ValueError('EXTENSION_FIELDS_INVALID')
    # Share the existing Bidzaar writer's lock only around CRM reconciliation.
    if not conn.execute('SELECT pg_try_advisory_lock(1062,604)').fetchone()[0]:return False
    try:
        actual=get(rest,card)
        if any(actual.get(k)!=before.get(k) for k in (REPORT,BRIEF,LIMITS,INN)):return False
        receipt=conn.execute('SELECT expected,status FROM serviceit_extended_evidence_receipts WHERE card_id=%s',(card,)).fetchone()
        if receipt and receipt[1]=='UNCERTAIN':
            if all(str(actual.get(k,''))==str(v) for k,v in receipt[0].items()):
                conn.execute("UPDATE serviceit_extended_evidence_receipts SET status='VERIFIED' WHERE card_id=%s",(card,))
            else:return False
        bid=conn.execute('SELECT expected,state FROM serviceit_bidzaar_inbox WHERE card_id=%s',(card,)).fetchone()
        if bid:
            expected,state=bid
            if state!='VERIFIED' or any(str(actual.get(k))!=str(v) for k,v in expected.items() if k not in {'title','stageId','assignedById'}):return False
        with conn.transaction():
            conn.execute("INSERT INTO serviceit_extended_evidence_receipts(card_id,expected,status) VALUES(%s,%s::jsonb,'UNCERTAIN') ON CONFLICT(card_id) DO UPDATE SET expected=excluded.expected,status='UNCERTAIN',updated_at=now()",(card,json.dumps(fields,ensure_ascii=False)))
            if bid:
                expected.update(fields)
                conn.execute("UPDATE serviceit_bidzaar_inbox SET expected=%s::jsonb,state='UPDATE_UNCERTAIN' WHERE card_id=%s",(json.dumps(expected,ensure_ascii=False),card))
        response=client.post(rest.base+'/crm.item.update.json',json={'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y','fields':fields},timeout=20,follow_redirects=False)
        response.raise_for_status()
        if len(response.content)>256000 or 'error' in response.json():raise ValueError('EXTENSION_UPDATE_UNCERTAIN')
        after=get(rest,card)
        if any(str(after.get(k,''))!=str(v) for k,v in fields.items()):raise ValueError('EXTENSION_READBACK_MISMATCH')
        with conn.transaction():
            conn.execute("UPDATE serviceit_extended_evidence_receipts SET status='VERIFIED',updated_at=now() WHERE card_id=%s",(card,))
            if bid:conn.execute("UPDATE serviceit_bidzaar_inbox SET state='VERIFIED' WHERE card_id=%s",(card,))
        return True
    finally:conn.execute('SELECT pg_advisory_unlock(1062,604)')


def run():
    env=dict(os.environ)
    if not live_permitted(env):raise ValueError('VPS_LIVE_REQUIRED')
    with db(env) as conn,httpx.Client(trust_env=False,timeout=20,follow_redirects=False) as client:
        if not conn.execute('SELECT pg_try_advisory_lock(1062,706)').fetchone()[0]:return
        conn.execute(DDL);conn.execute(docs.DDL)
        rest=Rest(secret_file(env,'SERVICEIT_BITRIX_WEBHOOK'),client)
        items=inventory(rest)
        print('EXT_CARDS_SCANNED='+str(len(items)),flush=True)
        for item in items:
            card=int(item['id']);inn=str(item.get(INN) or '')
            if _is_valid_inn(inn):
                conn.execute('INSERT INTO serviceit_company_contact_cache(inn,name) VALUES(%s,%s) ON CONFLICT DO NOTHING',(inn,str(item.get(COMPANY) or '')[:250]))
        for card,notice in conn.execute("SELECT card_id,notice FROM serviceit_bidzaar_inbox WHERE state='VERIFIED'").fetchall():
            docs.register(conn,card,notice.get('files',[]))
        print('EXT_DOCUMENT_CHUNKS='+str(docs.process(conn,client,env,batch=6)),flush=True)
        row=conn.execute('SELECT inn,name FROM serviceit_company_contact_cache WHERE next_attempt_at<=now() ORDER BY next_attempt_at,inn LIMIT 1').fetchone()
        if row:
            inn,name=row
            # Reserve before external requests, preventing a crash from burning calls every five minutes.
            conn.execute("UPDATE serviceit_company_contact_cache SET next_attempt_at=now()+interval '1 day' WHERE inn=%s",(inn,))
            result=contacts.search(client,env,inn,name)
            conn.execute("UPDATE serviceit_company_contact_cache SET result=%s::jsonb,next_attempt_at=now()+CASE WHEN %s THEN interval '1 day' ELSE interval '7 days' END WHERE inn=%s",(json.dumps(result,ensure_ascii=False),result['status']=='SEARCH_UNAVAILABLE',inn))
            print('EXT_CONTACT_SEARCH='+result['status'],flush=True)
        updated=0
        for item in items:
            card=int(item['id']);inn=str(item.get(INN) or '')
            row=conn.execute('SELECT result FROM serviceit_company_contact_cache WHERE inn=%s',(inn,)).fetchone()
            contact=contacts.render(row[0],inn) if row and row[0] else ''
            document,read,pending,total=docs.render(conn,card)
            block='\n\n'.join(s for s in (contact,document) if s)
            if not block:continue
            fields={REPORT:owned(item.get(REPORT),block)}
            if contact:fields[BRIEF]=owned(item.get(BRIEF),contact)
            if total:
                limit=owned(item.get(LIMITS),f'Полная очередь вложений: {total}; прочитано: {read}; в очереди: {pending}; ошибок: {total-read-pending}.')
                # Reconcile obsolete six-file counters without claiming failed files read.
                limit=re.sub(r'Прочитано документов: \d+',f'Прочитано документов: {read}',limit)
                limit=re.sub(r'Не обработано: \d+',f'Не обработано: {total-read}',limit)
                fields[LIMITS]=limit
            updated+=patch(conn,rest,client,card,item,fields)
        print('EXT_CARDS_UPDATED='+str(updated),flush=True)
        for status,count in conn.execute('SELECT status,count(*) FROM serviceit_extended_documents GROUP BY status').fetchall():print('EXT_DOCUMENTS_'+status+'='+str(count),flush=True)
        for status,count in conn.execute("SELECT coalesce(result->>'status','PENDING'),count(*) FROM serviceit_company_contact_cache GROUP BY 1").fetchall():print('EXT_COMPANIES_'+status+'='+str(count),flush=True)

if __name__=='__main__':
    try:run()
    except Exception as exc:
        print('EXT_ERROR='+type(exc).__name__.upper(),flush=True)
        print('EXT_ERROR_LINE='+str(traceback.extract_tb(exc.__traceback__)[-1].lineno),flush=True)
        if re.fullmatch(r'[A-Z][A-Z0-9_]{3,100}',str(exc)):print('EXT_REASON='+str(exc),flush=True)
        raise SystemExit(2)
