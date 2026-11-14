"""Promote researched source cards to human review, never to confirmed interest."""
import re
from .bitrix_1062_mapping import _is_valid_inn
from .serviceit_b2b_dossier_consistency_v37 import INN,COMPANY,REPORT,BRIEF

DDL='''CREATE TABLE IF NOT EXISTS serviceit_stage_handoff (
 card_id BIGINT PRIMARY KEY,previous_stage TEXT NOT NULL,target_stage TEXT NOT NULL,
 status TEXT NOT NULL,updated_at TIMESTAMPTZ DEFAULT now());'''


def stages(client,base):
    response=client.post(base+'/crm.status.list.json',json={'filter':{'ENTITY_ID':'DYNAMIC_1062_STAGE_21'}},timeout=20,follow_redirects=False)
    response.raise_for_status();data=response.json()
    if 'error' in data or not isinstance(data.get('result'),list):raise ValueError('STAGE_METADATA_UNVERIFIED')
    result={}
    for row in data['result']:
        name=str(row.get('NAME','')).strip().casefold();sid=str(row.get('STATUS_ID',''))
        if not re.fullmatch(r'DT1062_21:[A-Za-z0-9_]+',sid):continue
        if name=='новый сигнал':result['new']=sid
        elif name=='проверка компании и инн':result['company']=sid
        elif name.startswith('проверка потребности'):result['research']=sid
        elif name=='проверка менеджером':result['manager']=sid
    if set(result)!={'new','company','research','manager'} or len(set(result.values()))!=4:raise ValueError('STAGE_NAMES_UNVERIFIED')
    return result


def target(item,contact_status,documents_complete,mapping):
    current=item.get('stageId')
    if current not in {mapping['new'],mapping['company'],mapping['research']}:return None
    if not _is_valid_inn(str(item.get(INN) or '')) or not str(item.get(COMPANY) or '').strip():
        return mapping['company'] if current==mapping['new'] else None
    ready=(documents_complete and contact_status in ('VERIFIED','NOT_FOUND') and
           len(str(item.get(REPORT) or ''))>=200 and len(str(item.get(BRIEF) or ''))>=100)
    return mapping['manager'] if ready else mapping['research']


def advance(conn,rest,client,items,get):
    conn.execute(DDL);mapping=stages(client,rest.base);moved=0
    for cached in items:
        card=int(cached['id'])
        receipt=conn.execute('SELECT target_stage,status FROM serviceit_stage_handoff WHERE card_id=%s',(card,)).fetchone()
        if receipt and receipt==(mapping['manager'],'VERIFIED'):continue
        if not conn.execute('SELECT pg_try_advisory_lock(1062,604)').fetchone()[0]:continue
        locked=False
        try:
            locked=conn.execute('SELECT pg_try_advisory_lock(6754819001)').fetchone()[0]
            if not locked:continue
            actual=get(rest,card)
            if receipt and receipt[1]=='UNCERTAIN':
                if actual.get('stageId')==receipt[0]:conn.execute("UPDATE serviceit_stage_handoff SET status='VERIFIED',updated_at=now() WHERE card_id=%s",(card,))
                continue
            inn=str(actual.get(INN) or '')
            contact=conn.execute("SELECT result->>'status' FROM serviceit_company_contact_cache WHERE inn=%s",(inn,)).fetchone()
            complete=conn.execute("SELECT count(*) FILTER (WHERE status!='READ')=0 FROM serviceit_extended_documents WHERE card_id=%s",(card,)).fetchone()[0]
            dest=target(actual,contact[0] if contact else None,complete,mapping)
            if not dest or dest==actual.get('stageId'):continue
            conn.execute("INSERT INTO serviceit_stage_handoff(card_id,previous_stage,target_stage,status) VALUES(%s,%s,%s,'UNCERTAIN') ON CONFLICT(card_id) DO UPDATE SET previous_stage=excluded.previous_stage,target_stage=excluded.target_stage,status='UNCERTAIN',updated_at=now()",(card,actual['stageId'],dest))
            r=client.post(rest.base+'/crm.item.update.json',json={'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y','fields':{'stageId':dest}},timeout=20,follow_redirects=False)
            r.raise_for_status()
            if 'error' in r.json():raise ValueError('STAGE_WRITE_UNCERTAIN')
            if get(rest,card).get('stageId')!=dest:raise ValueError('STAGE_READBACK_MISMATCH')
            conn.execute("UPDATE serviceit_stage_handoff SET status='VERIFIED',updated_at=now() WHERE card_id=%s",(card,));moved+=1
        finally:
            if locked:conn.execute('SELECT pg_advisory_unlock(6754819001)')
            conn.execute('SELECT pg_advisory_unlock(1062,604)')
    print('EXT_STAGE_MOVED='+str(moved),flush=True)
