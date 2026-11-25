"""Recomputed successful/actual totals in integer kopecks; never executes payouts."""
import time
P='UF_CRM_13_SVCIT_'
HANDLER='https://leadgen.serviseit.ru/apps/serviceit-workspace/dashboard'
PLACEMENT='CRM_DYNAMIC_1062_LIST_TOOLBAR'

def aggregate(items,success,actual):
 from automation import money,split,Failure
 keys=('revenue','costs','profit','developer','manager','company','paid_manager','paid_developer')
 totals=dict.fromkeys(keys,0);rows=[];seen=set();excluded={'not_success':0,'not_actual':0,'missing':0,'invalid':0,'duplicate':0}
 managers={}
 for item in sorted(items,key=lambda x:int(x['id'])):
  if item.get('stageId') not in success:excluded['not_success']+=1;continue
  if str(item.get(P+'FIN_BASIS'))!=str(actual):excluded['not_actual']+=1;continue
  try:
   r,c=money(item.get(P+'FIN_REVENUE')),money(item.get(P+'FIN_COSTS'))
   if r is None or c is None:excluded['missing']+=1;continue
   paid_m=money(item.get(P+'FIN_PAID_MANAGER')) or 0;paid_d=money(item.get(P+'FIN_PAID_DEVELOPER')) or 0
  except Failure:excluded['invalid']+=1;continue
  signal=str(item.get(P+'SIGNAL_ID') or 'CARD:'+str(item['id']))
  if signal in seen:excluded['duplicate']+=1;continue
  seen.add(signal)
  profit,developer,manager,company=split(r,c)
  data=dict(zip(keys,(r,c,profit,developer,manager,company,paid_m,paid_d)))
  for k in keys:totals[k]+=data[k]
  owner=str(item.get('assignedById') or '0')
  bucket=managers.setdefault(owner,{'id':owner,'count':0,'allocated':0,'paid':0})
  bucket['count']+=1;bucket['allocated']+=manager;bucket['paid']+=paid_m
  rows.append(dict(id=int(item['id']),title=str(item.get('title') or ''),manager_id=owner,overpaid=paid_m>manager or paid_d>developer,**data))
 return {'totals':totals,'rows':rows,'count':len(rows),'scanned':len(items),'excluded':excluded,'managers':list(managers.values()),'updated_at':int(time.time())}

def config(token):
 from automation import api,Failure
 stages=api(token,'crm.status.list',filter={'ENTITY_ID':'DYNAMIC_1062_STAGE_21'})
 success={s['STATUS_ID'] for s in stages if s.get('SEMANTICS')=='S'}
 fields=api(token,'crm.item.fields',entityTypeId=1062,useOriginalUfNames='Y')['fields']
 values=fields[P+'FIN_BASIS'].get('items',[])
 actual=next((x.get('ID',x.get('id')) for x in values if x.get('VALUE',x.get('value'))=='Факт — получено и расходы учтены'),None)
 if actual is None or not success:raise Failure('FINANCE_CONFIGURATION_MISSING')
 return success,actual

def snapshot(token):
 from automation import identity,api,Failure
 identity(token);success,actual=config(token)
 items=[];last=7
 for _ in range(1000):
  rows=api(token,'crm.item.list',entityTypeId=1062,useOriginalUfNames='Y',filter={'categoryId':21,'>id':last},order={'id':'ASC'},select=['*'],start=0)['items']
  for item in rows:
   if int(item['id'])<=last or int(item['categoryId'])!=21:raise Failure('FINANCE_SCOPE_ERROR')
   last=int(item['id']);items.append(item)
  if len(rows)<50:break
 else:raise Failure('FINANCE_SCAN_LIMIT')
 result=aggregate(items,success,actual)
 result['scope']='Данные карточек, доступных вам в Битрикс24'
 return result

def ensure_binding(token):
 from automation import api,Failure
 def exists(rows):return any(r.get('placement',r.get('PLACEMENT'))==PLACEMENT and r.get('handler',r.get('HANDLER'))==HANDLER for r in rows)
 if exists(api(token,'placement.get')):return
 api(token,'placement.bind',PLACEMENT=PLACEMENT,HANDLER=HANDLER,TITLE='Финансы — общая прибыль',DESCRIPTION='Итоги успешно завершённых карточек и распределение прибыли')
 if not exists(api(token,'placement.get')):raise Failure('FINANCE_MENU_READBACK_FAILED')
