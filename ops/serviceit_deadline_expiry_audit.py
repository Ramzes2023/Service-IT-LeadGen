"""Host-side read-only acceptance, no credentials in output."""
from pathlib import Path
import subprocess,re
root=Path.home()/'serviceit/manager-workspace'
code='import sys,types\n'
for name in ('serviceit_postcall_assistant','serviceit_manager_workspace','serviceit_deadline_expiry'):
 code+=f'm=types.ModuleType("leadpilot.{name}");m.__package__="leadpilot"\nsys.modules[m.__name__]=m\nexec('+repr((root/(name+'.py')).read_text())+',m.__dict__)\n'
code+='''
from leadpilot.serviceit_deadline_expiry import *
from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.bitrix_metadata_audit import validate_webhook
with db(dict(os.environ)) as conn,httpx.Client(trust_env=False) as client:
 rest=Rest(validate_webhook(secret_file(dict(os.environ),'SERVICEIT_BITRIX_WEBHOOK')),client)
 rows=stages(rest)
 assert sum(s['STATUS_ID']==STAGE for s in rows)==1
 assert next(s for s in rows if s['STATUS_ID']==STAGE)['COLOR'].upper()=='#FF5752'
 terminal={s['STATUS_ID'] for s in rows if s.get('SEMANTICS') in ('S','F')}
 notices={int(c):n for c,n in conn.execute('SELECT card_id,notice FROM serviceit_bidzaar_inbox WHERE card_id IS NOT NULL ORDER BY modified ASC').fetchall()}
 items=list_items(rest);late=unknown=mismatch=0;now=datetime.now(timezone.utc)
 for item in items:
  due=deadline(item,notices.get(int(item['id'])))
  if due is None:unknown+=1;continue
  if due<=now and item['stageId'] not in terminal:
   late+=1
   if item['stageId']!=STAGE or not item['title'].startswith(PREFIX):mismatch+=1
 print('EXPIRY_AUDIT_CARDS='+str(len(items)))
 print('EXPIRY_AUDIT_LATE='+str(late))
 print('EXPIRY_AUDIT_UNKNOWN='+str(unknown))
 print('EXPIRY_AUDIT_MISMATCH='+str(mismatch))
 assert mismatch==0
 print('EXPIRY_AUDIT=VERIFIED')
'''
r=subprocess.run(['docker','exec','-i','serviceit-leadgen-vps-coordinator-1','python','-B','-'],input=code,text=True,capture_output=True,timeout=180)
for line in r.stdout.splitlines():
 if re.fullmatch('EXPIRY_[A-Z_]+=[A-Z0-9_]+',line):print(line)
if r.returncode:raise SystemExit('EXPIRY_AUDIT_FAILED')
