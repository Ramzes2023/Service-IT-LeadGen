"""Read-only field, form and stage metadata for requested manager workspace."""
import os,json,re,httpx
from leadpilot.serviceit_vps_pg_queue import secret_file
from leadpilot.bitrix_metadata_audit import validate_webhook
base=validate_webhook(secret_file(dict(os.environ),'SERVICEIT_BITRIX_WEBHOOK'))
with httpx.Client(trust_env=False,timeout=25) as client:
 def call(method,args):
  r=client.post(base+'/'+method+'.json',json=args)
  data=r.json()
  if 'error' in data:
   print('WORKSPACE_API_ERROR='+method+':'+str(data['error']));return None
  return data.get('result')
 t=call('crm.type.getByEntityTypeId',{'entityTypeId':1062})
 print('WORKSPACE_TYPE='+json.dumps(t,ensure_ascii=False))
 layout=call('crm.item.details.configuration.get',{'entityTypeId':1062,'scope':'C','extras':{'categoryId':21}})
 print('WORKSPACE_LAYOUT='+json.dumps(layout,ensure_ascii=False))
 print('WORKSPACE_STAGES='+json.dumps(call('crm.status.list',{'filter':{'ENTITY_ID':'DYNAMIC_1062_STAGE_21'}}),ensure_ascii=False))
 fields=call('crm.item.fields',{'entityTypeId':1062,'useOriginalUfNames':'Y'})
 print('WORKSPACE_FIELDS='+json.dumps({k:v for k,v in (fields or {}).get('fields',{}).items() if re.search('POST|FIN_|PROFIT|INTEREST|COST|REVENUE|NEED|OBJECTION',k,re.I)},ensure_ascii=False))
 print('WORKSPACE_FIELD_PERMISSION='+str(call('userfieldconfig.list',{'moduleId':'crm','filter':{'entityId':'CRM_13','fieldName':'UF_CRM_13_SVCIT_AI_STATUS'}}) is not None))
