"""Read-only independent acceptance of manager form, stages and financial outputs."""
import os,json,re
from decimal import Decimal,ROUND_HALF_UP
import httpx
from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.bitrix_metadata_audit import validate_webhook
P='UF_CRM_13_SVCIT_'
CODES='POST_NEED POST_INTEREST POST_OBJECTIONS POST_ACTION POST_DUE POST_NOTES POST_BADGE POST_SUMMARY FIN_REVENUE FIN_COSTS FIN_BASIS FIN_PROFIT FIN_DEVELOPER FIN_MANAGER FIN_COMPANY FIN_STATUS ALL_TOTALS'.split()
with db(dict(os.environ)) as conn,httpx.Client(trust_env=False,timeout=25) as client:
 base=validate_webhook(secret_file(dict(os.environ),'SERVICEIT_BITRIX_WEBHOOK'))
 def call(method,args):
  r=client.post(base+'/'+method+'.json',json=args,follow_redirects=False)
  if r.status_code!=200:raise ValueError('AUDIT_HTTP_ERROR')
  data=r.json()
  if 'error' in data:raise ValueError('AUDIT_API_ERROR')
  return data['result']
 fields=call('crm.item.fields',{'entityTypeId':1062,'useOriginalUfNames':'Y'})['fields']
 print('WORKSPACE_AUDIT_FIELDS='+str(sum(P+x in fields for x in CODES)),flush=True)
 assert all(P+x in fields for x in CODES)
 layout=call('crm.item.details.configuration.get',{'entityTypeId':1062,'scope':'C','extras':{'categoryId':21}})
 owned={s['name']:s for s in layout if s['name'].startswith('serviceit_workspace_')}
 assert set(owned)=={'serviceit_workspace_totals','serviceit_workspace_call','serviceit_workspace_finance'}
 assert layout[0]['name']=='serviceit_workspace_totals'
 displayed={e['name'] for s in owned.values() for e in s['elements']}
 assert displayed=={P+x for x in CODES}
 print('WORKSPACE_AUDIT_SECTIONS=3',flush=True)
 print('WORKSPACE_AUDIT_TOTALS_AT_TOP=YES',flush=True)
 stages=call('crm.status.list',{'filter':{'ENTITY_ID':'DYNAMIC_1062_STAGE_21'}})
 expected={'SVCIT_CALL_YES':'#2FC6A0','SVCIT_CALL_NO':'#FF5752','SVCIT_CALL_UNCLEAR':'#FFA900'}
 for code,color in expected.items():
  rows=[s for s in stages if s['STATUS_ID'].split(':')[-1]==code]
  assert len(rows)==1 and rows[0].get('COLOR','').upper()==color
 print('WORKSPACE_AUDIT_COLOR_STAGES=3',flush=True)
 saved=conn.execute("SELECT value FROM serviceit_manager_workspace_meta WHERE key='totals-current'").fetchone()
 assert saved
 text=saved[0]['text']
 rows=[];last=7
 for _ in range(100):
  batch=call('crm.item.list',{'entityTypeId':1062,'useOriginalUfNames':'Y','filter':{'categoryId':21,'>id':last},'order':{'id':'ASC'},'select':['*'],'start':0})['items']
  if not batch:break
  for item in batch:
   assert int(item['id'])>last and str(item['categoryId'])=='21'
   last=int(item['id']);rows.append(item)
  if len(batch)<50:break
 else:raise ValueError('AUDIT_PAGINATION_LIMIT')
 finance=notes=0
 for item in rows:
  assert item.get(P+'ALL_TOTALS')==text
  assert item.get(P+'POST_BADGE') and item.get(P+'FIN_STATUS')
  notes+=int(any(item.get(P+k) for k in ('POST_NEED','POST_INTEREST','POST_OBJECTIONS','POST_ACTION','POST_DUE','POST_NOTES')))
  r,c=item.get(P+'FIN_REVENUE'),item.get(P+'FIN_COSTS')
  if r not in (None,'') and c not in (None,''):
   profit=Decimal(str(r))-Decimal(str(c));positive=max(profit,Decimal(0))
   dev=(positive*Decimal('.15')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
   manager=min(positive-dev,(positive*Decimal('.10')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
   for code,value in [('PROFIT',profit),('DEVELOPER',dev),('MANAGER',manager),('COMPANY',profit-dev-manager)]:
    assert Decimal(str(item[P+'FIN_'+code]))==value
   finance+=1
  else:
   assert all(item.get(P+'FIN_'+code) in (None,'') for code in ('PROFIT','DEVELOPER','MANAGER','COMPANY'))
 print('WORKSPACE_AUDIT_CARDS='+str(len(rows)),flush=True)
 print('WORKSPACE_AUDIT_MANAGER_INPUTS='+str(notes),flush=True)
 print('WORKSPACE_AUDIT_FINANCE_INPUTS='+str(finance),flush=True)
 print('WORKSPACE_AUDIT_READBACK=VERIFIED',flush=True)
 print('WORKSPACE_AUDIT_CRM_WRITES=0',flush=True)
