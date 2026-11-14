"""Manager-entered call outcomes and deterministic RUB profit workspace, 1062/21.

Own fields only. Existing dossiers, manager inputs, stage definitions and source
receipts remain owned by their original writers. No customer messages or payouts.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
import os
import re
import time
import httpx

P='UF_CRM_13_SVCIT_'
SCOPE={'entityTypeId':1062,'scope':'C','extras':{'categoryId':21}}
INTEREST={'YES':'Да — интерес подтверждён','NO':'Нет — отказ','UNKNOWN':'Неясно — нужно уточнить'}
BASIS={'PLAN':'План — ожидаемые суммы','ACTUAL':'Факт — получено и расходы учтены'}
SPECS={
 'POST_NEED':('Потребность клиента','string'),
 'POST_INTEREST':('Интерес клиента — выбор менеджера','enumeration'),
 'POST_OBJECTIONS':('Возражения / причина отказа','string'),
 'POST_ACTION':('Согласованный следующий шаг','string'),
 'POST_DUE':('Срок следующего действия','datetime'),
 'POST_NOTES':('Заметки по разговору','string'),
 'POST_BADGE':('Статус после разговора (авто)','string'),
 'POST_SUMMARY':('Резюме и рекомендации ИИ (авто)','string'),
 'FIN_REVENUE':('Цена / выручка без НДС, ₽','double'),
 'FIN_COSTS':('Все прямые расходы без НДС, ₽','double'),
 'FIN_BASIS':('Тип расчёта: план или факт','enumeration'),
 'FIN_PROFIT':('Прибыль после расходов, ₽ (авто)','double'),
 'FIN_DEVELOPER':('Разработчику 15%, ₽ (авто)','double'),
 'FIN_MANAGER':('Менеджеру продаж 10%, ₽ (авто)','double'),
 'FIN_COMPANY':('Компании 75% / убыток, ₽ (авто)','double'),
 'FIN_STATUS':('Пояснение расчёта (авто)','string'),
 'ALL_TOTALS':('Общий результат всех карточек, ₽ (авто)','string'),
}
INPUTS=tuple(P+x for x in ('POST_NEED','POST_INTEREST','POST_OBJECTIONS','POST_ACTION','POST_DUE','POST_NOTES','FIN_REVENUE','FIN_COSTS','FIN_BASIS'))
CALL_INPUTS=INPUTS[:6]
OUTPUTS={P+x for x in SPECS}-set(INPUTS)
NUMBERS={P+x for x,(_,kind) in SPECS.items() if kind=='double'}
STAGE_ENTITY='DYNAMIC_1062_STAGE_21'
STAGES={
 'YES':('SVCIT_CALL_YES','Интерес подтверждён','#2FC6A0'),
 'NO':('SVCIT_CALL_NO','Нет интереса — отказ','#FF5752'),
 'UNKNOWN':('SVCIT_CALL_UNCLEAR','Неясно — уточнить','#FFA900'),
}
DDL='''CREATE TABLE IF NOT EXISTS serviceit_manager_workspace_state (
 card_id BIGINT PRIMARY KEY, call_hash TEXT NOT NULL DEFAULT '',
 interest TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS serviceit_manager_workspace_meta (
 key TEXT PRIMARY KEY, value JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());'''


def require(ok,reason):
 if not ok:raise ValueError(reason)


def digest(value):
 return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def normalize(item):
 out=dict(item)
 for key in [*INPUTS,*OUTPUTS,P+'SIGNAL_ID']:
  alias='ufCrm'+''.join(w.capitalize() for w in key[7:].split('_'))
  if key not in out and alias in out:out[key]=out[alias]
 return out


def money(value):
 if value is None or value=='':return None
 require(not isinstance(value,bool),'MONEY_INVALID')
 text=str(value).strip().replace('\u00a0','').replace(' ','').replace(',','.')
 require(bool(re.fullmatch(r'\d{1,12}(?:\.\d{1,2})?',text)),'MONEY_INVALID')
 result=Decimal(text)
 require(result.is_finite() and 0<=result<=Decimal('999999999999.99'),'MONEY_INVALID')
 return result.quantize(Decimal('.01'))


def calculate(revenue,cost):
 r,c=money(revenue),money(cost)
 if r is None or c is None:return None
 profit=r-c
 positive=max(profit,Decimal('0.00'))
 dev=(positive*Decimal('.15')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
 manager=min(positive-dev,(positive*Decimal('.10')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
 return {'revenue':r,'costs':c,'profit':profit,'developer':dev,'manager':manager,'company':profit-dev-manager}


def decode(value,options):
 if value in (None,'',False,0,'0'):return ''
 mapped=options.get(str(value))
 require(mapped is not None,'ENUM_VALUE_UNKNOWN')
 return mapped


def finance_fields(item,enum):
 blank={P+'FIN_'+k:None for k in ('PROFIT','DEVELOPER','MANAGER','COMPANY')}
 try:
  basis=decode(item.get(P+'FIN_BASIS'),enum) or 'PLAN'
  amounts=calculate(item.get(P+'FIN_REVENUE'),item.get(P+'FIN_COSTS'))
 except ValueError:
  return {**blank,P+'FIN_STATUS':'Проверьте суммы: рубли, неотрицательные числа, не более двух знаков после запятой.'},None,''
 if amounts is None:
  return {**blank,P+'FIN_STATUS':'Заполните цену и расходы. Если расходов нет, укажите 0. Пустое поле не считается нулём.'},None,basis
 fields={P+'FIN_'+k.upper():str(amounts[k]) for k in ('profit','developer','manager','company')}
 label='ФАКТ по данным менеджера' if basis=='ACTUAL' else 'ПЛАН'
 fields[P+'FIN_STATUS']=label+': прибыль = выручка без НДС − прямые расходы. Распределение положительной прибыли: 15% / 10% / остаток 75%. Округление до копеек.'
 if amounts['profit']<0:fields[P+'FIN_STATUS']+=' Убыток: разработчику и менеджеру 0; отрицательный остаток у компании.'
 return fields,amounts,basis


def title_with_interest(title,interest):
 clean=re.sub(r'^(?:\[(?:✅ ИНТЕРЕС|❌ ОТКАЗ|⚠ НЕЯСНО)\]\s*)+','',title)
 prefix={'YES':'[✅ ИНТЕРЕС] ','NO':'[❌ ОТКАЗ] ','UNKNOWN':'[⚠ НЕЯСНО] '}.get(interest,'')
 return (prefix+clean)[:255]


def call_fields(item,interest,summary):
 has_notes=any(item.get(k) for k in CALL_INPUTS)
 badge={'YES':'✅ Интерес подтверждён менеджером','NO':'❌ Нет интереса — отказ','UNKNOWN':'⚠ Неясно — требуется уточнение'}.get(interest,'Результат разговора ещё не заполнен' if not has_notes else 'Выберите интерес: да / нет / неясно')
 return {P+'POST_BADGE':badge,P+'POST_SUMMARY':summary}


def totals(items,basis_enum):
 sums={b:{k:Decimal('0.00') for k in ('revenue','costs','profit','developer','manager','company')} for b in BASIS}
 counts={b:0 for b in BASIS};seen=set();missing=invalid=duplicate=0
 for item in sorted(items,key=lambda x:int(x['id'])):
  key=item.get(P+'SIGNAL_ID') or 'CARD:'+str(item['id'])
  if key in seen:duplicate+=1;continue
  seen.add(key)
  fields,a,basis=finance_fields(item,basis_enum)
  if a is None:
   if 'Проверьте' in fields[P+'FIN_STATUS']:invalid+=1
   else:missing+=1
   continue
  counts[basis]+=1
  for k,v in a.items():sums[basis][k]+=v
 lines=['СВОДКА ПО КАРТОЧКАМ «СЕРВИС ИТ» — RUB, без НДС', 'Факт и план учитываются отдельно; суммы факта введены менеджером.']
 for b,label in [('ACTUAL','ФАКТ'),('PLAN','ПЛАН')]:
  a=sums[b]
  lines.extend([f'{label}: карточек с расчётом {counts[b]}',f"Выручка {a['revenue']:.2f} ₽ | расходы {a['costs']:.2f} ₽ | прибыль {a['profit']:.2f} ₽",f"Разработчику {a['developer']:.2f} ₽ | менеджерам {a['manager']:.2f} ₽ | компании {a['company']:.2f} ₽"])
 lines.append(f'Не заполнены суммы: {missing}. Требуют исправления: {invalid}. Исключено повторов сигнала: {duplicate}.')
 lines.append('При повторе сигнала учитывается одна карточка с меньшим номером. Изменения пересчитываются каждые 5 минут. Расчёт не выполняет выплаты.')
 return '\n'.join(lines)


def layout_plan(before):
 """Withdraw the rejected left-side presentation without deleting stored data."""
 require(isinstance(before,list) and bool(before),'COMMON_LAYOUT_MISSING')
 owned={'serviceit_workspace_totals','serviceit_workspace_call','serviceit_workspace_finance'}
 fields={P+x for x in SPECS}
 result=[]
 for section in deepcopy(before):
  require(isinstance(section,dict) and isinstance(section.get('elements'),list),'LAYOUT_INVALID')
  if section.get('name') in owned:
   section['elements']=[e for e in section['elements'] if e.get('name') not in fields]
   if not section['elements']:continue
  result.append(section)
 require(bool(result),'COMMON_LAYOUT_MISSING')
 return result


class Rest:
 def __init__(self,base,client):self.base,self.client=base,client
 def raw(self,method,args):
  allowed={'crm.type.getByEntityTypeId','crm.item.fields','crm.item.list','crm.item.get','crm.item.update','crm.item.details.configuration.get','crm.item.details.configuration.set','userfieldconfig.list','userfieldconfig.get','userfieldconfig.add','crm.status.list','crm.status.add'}
  require(method in allowed,'METHOD_OUT_OF_SCOPE')
  if method.startswith('crm.item.'):
   require(args.get('entityTypeId')==1062,'ITEM_TYPE_OUT_OF_SCOPE')
  if method=='crm.item.update':
   require(int(args['id'])>=8 and set(args['fields'])<=OUTPUTS|{'title','stageId'},'UPDATE_FIELDS_OUT_OF_SCOPE')
  r=self.client.post(self.base+'/'+method+'.json',json=args,timeout=25,follow_redirects=False)
  require(len(r.content)<2000000,'WORKSPACE_HTTP_ERROR')
  data=r.json()
  if 'error' in data:
   code=str(data['error']);raise ValueError('WORKSPACE_API_'+re.sub('[^A-Z0-9_]','_',code.upper())[:90])
  require(r.status_code==200,'WORKSPACE_HTTP_ERROR')
  require('result' in data,'WORKSPACE_RESULT_MISSING')
  time.sleep(.12)
  return data
 def __call__(self,method,args):return self.raw(method,args)['result']


def meta_get(conn,key):
 row=conn.execute('SELECT value FROM serviceit_manager_workspace_meta WHERE key=%s',(key,)).fetchone()
 return row[0] if row else None


def meta_put(conn,key,value):
 conn.execute('INSERT INTO serviceit_manager_workspace_meta(key,value) VALUES(%s,%s::jsonb) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=now()', (key,json.dumps(value,ensure_ascii=False)))


def field_catalog(rest):
 fields=[];start=0
 for _ in range(20):
  data=rest.raw('userfieldconfig.list',{'moduleId':'crm','filter':{'entityId':'CRM_13'},'select':['*'],'start':start})
  result=data['result'];fields.extend(result['fields'])
  nxt=data.get('next',result.get('next'))
  if nxt is None:return {f['fieldName']:f for f in fields}
  require(isinstance(nxt,int) and nxt>start,'FIELD_PAGINATION_INVALID');start=nxt
 raise ValueError('FIELD_PAGINATION_LIMIT')


def validate_field(field,code):
 require(field.get('entityId')=='CRM_13' and field.get('fieldName')==P+code and field.get('userTypeId')==SPECS[code][1] and field.get('multiple') in (False,'N'),'FIELD_CONFLICT_'+code)


def enum_maps(rest,catalog):
 maps={}
 for code,options in [('POST_INTEREST',INTEREST),('FIN_BASIS',BASIS)]:
  field=rest('userfieldconfig.get',{'moduleId':'crm','id':catalog[P+code]['id']})['field']
  values=field.get('enum',[])
  maps[code]={str(e['id']):e.get('xmlId') for e in values}
  require(set(maps[code].values())==set(options) and len(maps[code])==len(options),'ENUM_SCHEMA_CONFLICT')
 return maps


def stages(rest):
 rows=rest('crm.status.list',{'filter':{'ENTITY_ID':STAGE_ENTITY}})
 require(isinstance(rows,list) and rows and all(r['ENTITY_ID']==STAGE_ENTITY for r in rows),'STAGE_SCHEMA_INVALID')
 return rows


def provision(conn,rest):
 typ=rest('crm.type.getByEntityTypeId',{'entityTypeId':1062})['type']
 require(str(typ['id'])=='13','TYPE_MISMATCH')
 catalog=field_catalog(rest)
 for code,(label,kind) in SPECS.items():
  key=P+code
  if key not in catalog:
   require(meta_get(conn,'field-intent:'+key) is None,'FIELD_ADD_UNCERTAIN_'+code)
   field={'entityId':'CRM_13','fieldName':key,'userTypeId':kind,'multiple':'N','mandatory':'N','editFormLabel':{'ru':label},'showFilter':'Y' if code in ('POST_INTEREST','FIN_BASIS') else 'N'}
   if kind=='string':field['settings']={'ROWS':8 if code=='ALL_TOTALS' else 4 if code in ('POST_SUMMARY','POST_NOTES') else 2,'SIZE':70,'MAX_LENGTH':12000}
   if kind=='double':field['settings']={'PRECISION':2,'SIZE':24}
   if kind=='enumeration':
    options=INTEREST if code=='POST_INTEREST' else BASIS
    field['settings']={'DISPLAY':'LIST','LIST_HEIGHT':1,'SHOW_NO_VALUE':'Y','CAPTION_NO_VALUE':'Не выбрано'}
    field['enum']=[{'xmlId':k,'value':v,'def':'N','sort':(i+1)*100} for i,(k,v) in enumerate(options.items())]
   meta_put(conn,'field-intent:'+key,field)
   rest('userfieldconfig.add',{'moduleId':'crm','field':field})
   catalog=field_catalog(rest)
  validate_field(catalog[key],code)
 maps=enum_maps(rest,catalog)
 before=rest('crm.item.details.configuration.get',SCOPE)
 desired=layout_plan(before)
 if desired!=before:
  meta_put(conn,'layout-backup:'+digest(before),before)
  require(rest('crm.item.details.configuration.get',SCOPE)==before,'LAYOUT_CONCURRENT_EDIT')
  rest('crm.item.details.configuration.set',{**SCOPE,'data':desired})
  require(rest('crm.item.details.configuration.get',SCOPE)==desired,'LAYOUT_READBACK_MISMATCH')
 rows=stages(rest)
 for interest,(code,label,color) in STAGES.items():
  existing=[s for s in rows if s['STATUS_ID'].split(':')[-1]==code]
  if not existing:
   require(meta_get(conn,'stage-intent:'+code) is None,'STAGE_ADD_UNCERTAIN')
   active=[int(s['SORT']) for s in rows if not s.get('SEMANTICS')]
   terminal_sorts=[int(s['SORT']) for s in rows if s.get('SEMANTICS') in ('S','F')]
   sort=max(active)+1
   require(not terminal_sorts or sort<min(terminal_sorts),'NO_STAGE_SORT_GAP')
   meta_put(conn,'stage-intent:'+code,rows)
   rest('crm.status.add',{'fields':{'ENTITY_ID':STAGE_ENTITY,'STATUS_ID':code,'NAME':label,'SORT':sort,'COLOR':color,'SEMANTICS':''}})
   rows=stages(rest);existing=[s for s in rows if s['STATUS_ID'].split(':')[-1]==code]
  require(len(existing)==1 and existing[0]['NAME']==label and existing[0].get('COLOR','').upper()==color.upper(),'STAGE_CONFLICT')
 meta_put(conn,'enum-maps',maps)
 print('WORKSPACE_FIELDS_VERIFIED='+str(len(SPECS)),flush=True)
 print('WORKSPACE_LAYOUT_VERIFIED=YES',flush=True)
 print('WORKSPACE_STAGES_VERIFIED=3',flush=True)
 return maps


def list_items(rest):
 result=[];last=0
 for _ in range(100):
  data=rest('crm.item.list',{'entityTypeId':1062,'useOriginalUfNames':'Y','filter':{'categoryId':21,'>id':max(last,7)},'order':{'id':'ASC'},'select':['*'],'start':0})
  rows=data.get('items')
  require(isinstance(rows,list) and all(isinstance(r,dict) for r in rows),'ITEM_LIST_SHAPE')
  if not rows:return result
  for row in rows:
   require(str(row.get('categoryId'))=='21' and int(row['id'])>last,'ITEM_LIST_SCOPE')
   last=int(row['id']);result.append(normalize(row))
  if len(rows)<50:return result
 raise ValueError('ITEM_LIST_LIMIT')


def item_get(rest,card):
 item=normalize(rest('crm.item.get',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
 require(str(item.get('categoryId'))=='21' and int(item['id'])==card,'CARD_SCOPE_CHANGED')
 return item


def equal(key,actual,desired):
 if key in NUMBERS:
  if actual in (None,'') and desired is None:return True
  try:return Decimal(str(actual))==Decimal(str(desired))
  except (InvalidOperation,ValueError):return False
 return (actual or '')==(desired or '')


def safe_patch(rest,card,before,patch):
 patch={k:v for k,v in patch.items() if not equal(k,before.get(k),v)}
 if not patch:return False
 fresh=item_get(rest,card)
 require(all(fresh.get(k)==before.get(k) for k in [*INPUTS,'title','stageId',*patch]),'MANAGER_CONCURRENT_EDIT')
 rest('crm.item.update',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y','fields':patch})
 actual=item_get(rest,card)
 require(all(equal(k,actual.get(k),v) for k,v in patch.items()),'WORKSPACE_READBACK_MISMATCH')
 return True


def build_summary(client,env,item,interest):
 notes='\n'.join(label+': '+str(item.get(P+code) or 'не указано') for code,label in [('POST_NEED','Потребность'),('POST_OBJECTIONS','Возражения'),('POST_ACTION','Согласованный шаг'),('POST_DUE','Срок'),('POST_NOTES','Запись')])
 if not any(item.get(k) for k in CALL_INPUTS):return ''
 summary='Интерес по выбору менеджера: '+INTEREST.get(interest,'не выбран')+'.\n'+notes
 if len(str(item.get(P+'POST_NOTES') or '').strip())>=12:
  from .serviceit_postcall_assistant import draft
  result=draft(client,env,notes[:10000])
  summary+='\nРезюме ИИ: '+result['summary']+'\nПредложение ИИ (не согласованный с клиентом шаг): '+result['next_action']
 return summary[:11500]


def run(conn,rest,client,env):
 catalog=rest('crm.item.fields',{'entityTypeId':1062,'useOriginalUfNames':'Y'})['fields']
 require(all(P+code in catalog for code in SPECS),'WORKSPACE_FIELDS_MISSING')
 maps=meta_get(conn,'enum-maps')
 require(isinstance(maps,dict) and set(maps)=={'POST_INTEREST','FIN_BASIS'},'WORKSPACE_SETUP_REQUIRED')
 stage_rows=stages(rest)
 targets={k:next(s['STATUS_ID'] for s in stage_rows if s['STATUS_ID'].split(':')[-1]==v[0]) for k,v in STAGES.items()}
 terminal={s['STATUS_ID'] for s in stage_rows if s.get('SEMANTICS') in ('S','F')}
 items=list_items(rest);changed=blocked=0
 for listed in items:
  card=int(listed['id'])
  try:
   item=item_get(rest,card)
   interest=decode(item.get(P+'POST_INTEREST'),maps['POST_INTEREST'])
   h=digest({k:item.get(k) for k in CALL_INPUTS})
   state=conn.execute('SELECT call_hash,interest,summary FROM serviceit_manager_workspace_state WHERE card_id=%s',(card,)).fetchone()
   changed_call=not state or state[0]!=h
   summary=build_summary(client,env,item,interest) if changed_call else state[2]
   patch=call_fields(item,interest,summary)
   patch.update(finance_fields(item,maps['FIN_BASIS'])[0])
   if changed_call and (interest or state and state[1]):
    patch['title']=title_with_interest(str(item.get('title','')),interest)
    if interest and (not state or state[1]!=interest) and item.get('stageId') not in terminal:
     patch['stageId']=targets[interest]
   changed+=int(safe_patch(rest,card,item,patch))
   conn.execute('INSERT INTO serviceit_manager_workspace_state(card_id,call_hash,interest,summary) VALUES(%s,%s,%s,%s) ON CONFLICT(card_id) DO UPDATE SET call_hash=excluded.call_hash,interest=excluded.interest,summary=excluded.summary,updated_at=now()', (card,h,interest,summary))
  except (ValueError,httpx.HTTPError) as exc:
   blocked+=1;print('WORKSPACE_CARD_BLOCKED='+str(card),flush=True)
   print('WORKSPACE_REASON='+str(exc) if re.fullmatch('[A-Z0-9_]+',str(exc)) else 'WORKSPACE_REASON=TRANSPORT_OR_CONFLICT',flush=True)
 # Re-read all current manager inputs. Summary is a cycle snapshot, never an accumulating counter.
 items=list_items(rest);summary=totals(items,maps['FIN_BASIS'])
 meta_put(conn,'totals-current',{'text':summary,'cards':len(items),'at':datetime.now(timezone.utc).isoformat()})
 for item in items:
  try:changed+=int(safe_patch(rest,int(item['id']),item,{P+'ALL_TOTALS':summary}))
  except (ValueError,httpx.HTTPError):blocked+=1
 print('WORKSPACE_CARDS='+str(len(items)),flush=True)
 print('WORKSPACE_UPDATED='+str(changed),flush=True)
 print('WORKSPACE_BLOCKED='+str(blocked),flush=True)
 require(blocked==0,'WORKSPACE_REVIEW_REQUIRED')


def main():
 from .serviceit_vps_pg_queue import db,secret_file
 from .bitrix_metadata_audit import validate_webhook
 env=dict(os.environ)
 with db(env) as conn,httpx.Client(trust_env=False) as client:
  if not conn.execute('SELECT pg_try_advisory_lock(1062,615)').fetchone()[0]:
   print('WORKSPACE_STATUS=BUSY');return
  try:
   conn.execute(DDL)
   rest=Rest(validate_webhook(secret_file(env,'SERVICEIT_BITRIX_WEBHOOK')),client)
   if env.get('SERVICEIT_WORKSPACE_SETUP')=='YES' or meta_get(conn,'setup-complete') is None:
    setup_base=validate_webhook(env.get('SERVICEIT_WORKSPACE_SETUP_WEBHOOK') or rest.base)
    require(setup_base.startswith('https://serviseit.bitrix24.ru/rest/'),'SETUP_PORTAL_INVALID')
    try:
     provision(conn,Rest(setup_base,client))
    except ValueError as exc:
     if str(exc)=='WORKSPACE_API_INSUFFICIENT_SCOPE':
      print('WORKSPACE_STATUS=WAITING_USERFIELDCONFIG_SCOPE',flush=True)
      return
     raise
    meta_put(conn,'setup-complete',{'version':1})
   run(conn,rest,client,env)
  finally:conn.execute('SELECT pg_advisory_unlock(1062,615)')

if __name__=='__main__':
 try:main()
 except Exception as exc:
  reason=str(exc)
  print('WORKSPACE_ERROR='+reason if re.fullmatch('[A-Z0-9_]+',reason) else 'WORKSPACE_ERROR='+type(exc).__name__.upper(),flush=True)
  import traceback
  print('WORKSPACE_ERROR_LINE='+str(traceback.extract_tb(exc.__traceback__)[-1].lineno),flush=True)
  raise SystemExit(2)
