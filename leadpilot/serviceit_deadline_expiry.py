"""Submission-deadline routing, scoped to 1062/21. No task deadlines or inferred dates."""
from datetime import datetime, timezone, timedelta
import json, os, re
import httpx
from .serviceit_manager_workspace import Rest, stages, list_items, item_get, safe_patch, require
STAGE='DT1062_21:SVCIT_EXPIRED'
LABEL='⛔ ПРОСРОЧЕНО — подача закрыта'
PREFIX='⛔ ПРОСРОЧЕНО · '
DDL='''CREATE TABLE IF NOT EXISTS serviceit_deadline_expiry_state (
 card_id bigint PRIMARY KEY, previous_stage text NOT NULL, deadline timestamptz NOT NULL,
 status text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());'''

def parse_date(value):
 value=str(value or '').strip()
 try:
  result=datetime.fromisoformat(value.replace('Z','+00:00'))
  if result.tzinfo is not None:return result.astimezone(timezone.utc)
 except ValueError:pass
 match=re.fullmatch(r'(\d{2}\.\d{2}\.\d{4}) (\d{2}:\d{2})(?::(\d{2}))? МСК',value)
 if match:
  try:return datetime.strptime(match[1]+' '+match[2],'%d.%m.%Y %H:%M').replace(tzinfo=timezone(timedelta(hours=3))).astimezone(timezone.utc)
  except ValueError:pass
 return None

def deadline(item, notice=None):
 if notice is not None:
  # A known source notice overrides stale prose, including missing/withdrawn dates.
  return parse_date(notice.get('acceptanceEndDate'))
 found=[]
 for key,value in item.items():
  if key.endswith(('AI_FULL_REPORT','AI_MANAGER_BRIEF')) and isinstance(value,str):
   for line in value.splitlines():
    match=re.fullmatch(r'\s*Подача до:\s*(.*?)\s*',line)
    if match:
     parsed=parse_date(match[1])
     if parsed is None:return None
     found.append(parsed)
 return found[0] if found and len(set(found))==1 else None

def transition(item, due, now, receipt, valid, terminal):
 stage=item.get('stageId');title=str(item.get('title') or '')
 if due is None or stage in terminal:return {}
 if due<=now:
  return {'stageId':STAGE,'title':title if title.startswith(PREFIX) else PREFIX+title}
 if stage==STAGE and receipt and receipt[0] in valid-terminal-{STAGE}:
  return {'stageId':receipt[0],'title':title.removeprefix(PREFIX)}
 return {}

def run(conn,rest):
 conn.execute(DDL)
 rows=stages(rest)
 if not any(s['STATUS_ID']==STAGE for s in rows):
  active=[int(s['SORT']) for s in rows if s.get('SEMANTICS') not in ('S','F')]
  sort=max(active)+1
  require(all(sort<int(s['SORT']) for s in rows if s.get('SEMANTICS') in ('S','F')),'EXPIRY_STAGE_SORT_CONFLICT')
  rest('crm.status.add',{'fields':{'ENTITY_ID':'DYNAMIC_1062_STAGE_21','STATUS_ID':'SVCIT_EXPIRED','NAME':LABEL,'COLOR':'#FF5752','SORT':sort,'SEMANTICS':''}})
  rows=stages(rest)
 require(any(s['STATUS_ID']==STAGE and s['NAME']==LABEL and s['COLOR'].upper()=='#FF5752' for s in rows),'EXPIRY_STAGE_READBACK')
 valid={s['STATUS_ID'] for s in rows};terminal={s['STATUS_ID'] for s in rows if s.get('SEMANTICS') in ('S','F')}
 notices={}
 if conn.execute("SELECT to_regclass('serviceit_bidzaar_inbox')").fetchone()[0]:
  for card,notice in conn.execute('SELECT card_id,notice FROM serviceit_bidzaar_inbox WHERE card_id IS NOT NULL ORDER BY modified ASC').fetchall():notices[int(card)]=notice
 count={'CARDS':0,'EXPIRED':0,'RESTORED':0,'UNKNOWN_DATE':0,'UNCHANGED':0,'BLOCKED':0}
 now=datetime.now(timezone.utc)
 for item in list_items(rest):
  count['CARDS']+=1;card=int(item['id'])
  try:
   due=deadline(item,notices.get(card))
   if due is None:count['UNKNOWN_DATE']+=1;continue
   receipt=conn.execute('SELECT previous_stage,status FROM serviceit_deadline_expiry_state WHERE card_id=%s',(card,)).fetchone()
   patch=transition(item,due,now,receipt,valid,terminal)
   if not patch or all(item.get(k)==v for k,v in patch.items()):count['UNCHANGED']+=1;continue
   expiring=patch['stageId']==STAGE
   if expiring and item['stageId']!=STAGE:
    conn.execute("INSERT INTO serviceit_deadline_expiry_state(card_id,previous_stage,deadline,status) VALUES(%s,%s,%s,'INTENT') ON CONFLICT(card_id) DO UPDATE SET previous_stage=excluded.previous_stage,deadline=excluded.deadline,status='INTENT',updated_at=now()",(card,item['stageId'],due))
   # Guard both manager input and source report changes before writing.
   fresh=item_get(rest,card)
   require(deadline(fresh,notices.get(card))==due,'EXPIRY_DATE_CHANGED')
   safe_patch(rest,card,item,patch)
   conn.execute('UPDATE serviceit_deadline_expiry_state SET status=%s,deadline=%s,updated_at=now() WHERE card_id=%s',('EXPIRED' if expiring else 'RESTORED',due,card))
   count['EXPIRED' if expiring else 'RESTORED']+=1
  except (ValueError,httpx.HTTPError):
   count['BLOCKED']+=1;print('EXPIRY_BLOCKED_CARD='+str(card),flush=True)
 for key,value in count.items():print('EXPIRY_'+key+'='+str(value),flush=True)
 require(count['BLOCKED']==0,'EXPIRY_REVIEW_REQUIRED')
 print('EXPIRY_STATUS=VERIFIED',flush=True)

def main():
 from .serviceit_vps_pg_queue import db,secret_file
 from .bitrix_metadata_audit import validate_webhook
 env=dict(os.environ)
 with db(env) as conn,httpx.Client(trust_env=False) as client:
  # Same lock as the manager writer: no stage/title race between our workers.
  if not conn.execute('SELECT pg_try_advisory_lock(1062,615)').fetchone()[0]:
   print('EXPIRY_STATUS=BUSY');return
  try:
   base=env.get('SERVICEIT_WORKSPACE_SETUP_WEBHOOK') or secret_file(env,'SERVICEIT_BITRIX_WEBHOOK')
   run(conn,Rest(validate_webhook(base),client))
  finally:conn.execute('SELECT pg_advisory_unlock(1062,615)')
if __name__=='__main__':
 try:main()
 except Exception as exc:
  print('EXPIRY_ERROR='+str(exc) if re.fullmatch('[A-Z0-9_]+',str(exc)) else 'EXPIRY_ERROR='+type(exc).__name__.upper(),flush=True)
  raise SystemExit(2)
