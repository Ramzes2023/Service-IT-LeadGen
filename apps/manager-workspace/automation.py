"""Portal-pinned background reconciliation. Credentials never leave server storage."""
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from decimal import Decimal, ROUND_HALF_UP

PORTAL='https://serviseit.bitrix24.ru/rest/'
CODE='local.6af97755661a80.97347802'
PREFIX='UF_CRM_13_SVCIT_'
DATA=Path(os.environ.get('WORKSPACE_DATA','/data'))
LOCK=threading.RLock()
WAKE=threading.Event()
class Failure(Exception):pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise Failure('REDIRECT_REFUSED')

def request(url,data):
 req=urllib.request.Request(url,json.dumps(data).encode(),{'Content-Type':'application/json'})
 try:
  with urllib.request.build_opener(NoRedirect).open(req,timeout=25) as r:result=json.load(r)
 except urllib.error.HTTPError as e:
  try:result=json.loads(e.read(65536))
  except Exception:raise Failure('REMOTE_REQUEST_FAILED') from None
 except Exception:raise Failure('REMOTE_REQUEST_FAILED') from None
 if not isinstance(result,dict):raise Failure('INVALID_RESPONSE')
 if result.get('error'):raise Failure(re.sub('[^A-Za-z0-9_]','',str(result['error']))[:60])
 return result

def api(token,method,**kwargs):
 for attempt in range(3):
  time.sleep(.52 if attempt==0 else attempt*2)
  try:return request(PORTAL+method,dict(kwargs,auth=token)).get('result')
  except Failure as error:
   error.method=method
   if attempt==2 or not method.endswith(('.list','.get')) or str(error) not in ('REMOTE_REQUEST_FAILED','QUERY_LIMIT_EXCEEDED','INTERNAL_SERVER_ERROR'):raise

def read(name,default=None):
 try:return json.loads((DATA/name).read_text())
 except FileNotFoundError:return default

def write(name,value):
 DATA.mkdir(mode=0o700,parents=True,exist_ok=True)
 tmp=DATA/(name+'.tmp')
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
 os.replace(tmp,DATA/name)

def valid_token(token):
 if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9._-]{16,1024}',token):raise Failure('INVALID_AUTH')
 return token

def identity(token):
 info=api(valid_token(token),'app.info')
 if not isinstance(info,dict) or info.get('CODE')!=CODE:raise Failure('WRONG_APPLICATION')
 return info

def connect(auth):
 """Only a verified portal administrator can authorize unattended processing."""
 token=valid_token(auth.get('access_token'))
 identity(token)
 if api(token,'user.admin') is not True: return {'connected':bool(read('auth.json')),'administratorRequired':not bool(read('auth.json'))}
 refresh=valid_token(auth.get('refresh_token'))
 with LOCK:
  # Keep the existing refresh-token chain; SDK tokens belong to another session.
  if not read('auth.json') or read('status.json',{}).get('status') in ('TOKEN_REFRESH_REJECTED','expired_token','invalid_token'):
   write('auth.json',{'access_token':token,'refresh_token':refresh,'expires':time.time()+1800})
  WAKE.set()
 return {'connected':True}

def current_token():
 auth=read('auth.json')
 if not auth:raise Failure('WAITING_FOR_ADMIN_OPEN')
 if float(auth.get('expires',0))>time.time()+120 and read('status.json',{}).get('status') not in ('expired_token','invalid_token'):return auth['access_token']
 config=read('client.json')
 if not config or config.get('client_id')!=CODE:raise Failure('CLIENT_CONFIGURATION_MISSING')
 # Use POST body: secrets never appear in URL, access logs or exceptions.
 data=urllib.parse.urlencode({'grant_type':'refresh_token','client_id':CODE,'client_secret':config['client_secret'],'refresh_token':auth['refresh_token']}).encode()
 req=urllib.request.Request('https://oauth.bitrix24.tech/oauth/token/',data,{'Content-Type':'application/x-www-form-urlencoded'})
 try:
  with urllib.request.build_opener(NoRedirect).open(req,timeout=25) as r:result=json.load(r)
 except Exception:raise Failure('TOKEN_REFRESH_FAILED') from None
 if result.get('error'):raise Failure('TOKEN_REFRESH_REJECTED')
 if result.get('client_endpoint','').rstrip('/')!=PORTAL.rstrip('/'):raise Failure('WRONG_TOKEN_ENDPOINT')
 for k in ['access_token','refresh_token']:valid_token(result.get(k))
 # Persist rotated refresh token before any further network calls.
 write('auth.json',{'access_token':result['access_token'],'refresh_token':result['refresh_token'],'expires':time.time()+min(int(result.get('expires_in',3600)),3600)})
 return result['access_token']

def money(value):
 if value is None or str(value).strip()=='':return None
 s=str(value).strip().replace(',','.')
 if not re.fullmatch(r'\d{1,10}(\.\d{1,2})?',s):raise Failure('INVALID_MONEY')
 return int(Decimal(s)*100)

def split(revenue,cost):
 if revenue is None or cost is None:return None
 p=revenue-cost
 d=int((Decimal(max(p,0))*Decimal('.15')).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
 m=min(max(p,0)-d,int((Decimal(max(p,0))*Decimal('.10')).quantize(Decimal('1'),rounding=ROUND_HALF_UP)))
 return [p,d,m,p-d-m]

def rub(value):
 if value is None:return '—'
 return f'{value/100:,.2f}'.replace(',',' ').replace('.',',')+' ₽'

def cell(title,value):return {'type':'withTitle','properties':{'title':title,'block':{'type':'text','properties':{'value':str(value or 'Не заполнено'),'bold':True,'color':'base_90'}}}}

def layout(item,kind):
 v=lambda k:item.get(PREFIX+k)
 finance=kind=='finance'
 if finance:
  try:r,c=money(v('FIN_REVENUE')),money(v('FIN_COSTS'));x=split(r,c)
  except Failure:r=c=x=None
  values=[r,c]+(x if x else [None]*4)
  titles=['Цена / выручка','Расходы','Прибыль','Разработчику · 15%','Менеджеру · 10%','Компании · 75% / убыток']
  blocks={'amount_'+str(i):cell(t,rub(value)) for i,(t,value) in enumerate(zip(titles,values))}
 else:
  blocks={k:cell(t,v(f)) for k,t,f in [('status','Результат','POST_BADGE'),('need','Потребность','POST_NEED'),('objections','Возражения','POST_OBJECTIONS'),('next','Следующий шаг','POST_ACTION'),('due','Срок','POST_DUE'),('analysis','Анализ ИИ','POST_SUMMARY')]}
 body={'blocks':blocks,'logo':{'code':'notification' if finance else 'call-incoming'}}
 return {'icon':{'code':'document' if finance else 'call-completed'},'header':{'title':'Сервис ИТ · Финансы и прибыль' if finance else 'Сервис ИТ · Итог разговора'},'body':body,'footer':{'buttons':{'edit':{'title':'Заполнить финансы' if finance else 'Заполнить итог','type':'primary','action':{'type':'openRestApp','actionParams':{'cardId':str(item['id']),'section':kind}}}}}}

def scope(item):
 if not item or int(item.get('categoryId',-1))!=21 or int(item.get('id',0))<8:raise Failure('OUTSIDE_WORKSPACE')
 return item

def sync(token,item,cache):
 scope(item)
 for kind in ['call','finance']:
  key=f"{item['id']}:{kind}"
  desired=layout(item,kind)
  digest=hashlib.sha256(json.dumps(desired,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
  previous=cache.get(key,{})
  # These are persistent manager panels: keep them in planned activities.
  # Check completion even when their content has not changed.
  origin='serviceit-workspace:'+key
  rows=api(token,'crm.activity.list',filter={'OWNER_TYPE_ID':1062,'OWNER_ID':item['id'],'ORIGINATOR_ID':'serviceit_workspace_app_v1','ORIGIN_ID':origin},select=['ID','COMPLETED'])
  if len(rows)>1:raise Failure('DUPLICATE_BLOCKS')
  if rows:
   active=rows[0].get('COMPLETED') in ('N',False,0)
   if active and previous.get('hash')==digest and time.time()-previous.get('checked',0)<86400:continue
   api(token,'crm.activity.configurable.update',id=int(rows[0]['ID']),fields={'completed':False},layout=desired)
   checked=api(token,'crm.activity.configurable.get',id=int(rows[0]['ID']))
   if checked.get('activity',checked).get('fields',{}).get('completed') is not False:raise Failure('BLOCK_ACTIVE_READBACK_FAILED')
  else:
   # No automatic retry of a timed-out add. Next cycle lists first.
   api(token,'crm.activity.configurable.add',ownerTypeId=1062,ownerId=int(item['id']),fields={'typeId':'CONFIGURABLE','completed':False,'responsibleId':int(item['assignedById']),'originatorId':'serviceit_workspace_app_v1','originId':origin},layout=desired)
  cache[key]={'hash':digest,'checked':time.time()}
  write('blocks.json',cache)

def sync_card(token,card_id):
 identity(token)
 # Verify current user's access; never substitute administrator rights here.
 item=scope(api(token,'crm.item.get',entityTypeId=1062,id=int(card_id),useOriginalUfNames='Y')['item'])
 if not LOCK.acquire(timeout=2):return {'queued':True}
 try:sync(token,item,read('blocks.json',{}));return {'synced':True}
 finally:LOCK.release()

def cycle():
 with LOCK:
  token=current_token();cache=read('blocks.json',{});last=7;count=0;failed={}
  for page in range(1000):
   rows=api(token,'crm.item.list',entityTypeId=1062,useOriginalUfNames='Y',filter={'categoryId':21,'>id':last},order={'id':'ASC'},select=['id','categoryId','assignedById','title']+[PREFIX+k for k in ['POST_BADGE','POST_NEED','POST_OBJECTIONS','POST_ACTION','POST_DUE','POST_SUMMARY','FIN_REVENUE','FIN_COSTS']],start=0)['items']
   if not rows:break
   for item in rows:
    if int(item['id'])<=last:raise Failure('INVALID_PAGINATION')
    try:sync(token,item,cache)
    except Failure as error:
     if str(error) in ('expired_token','invalid_token'):raise
     failed[str(item['id'])]=str(error)
    last=int(item['id']);count+=1
   if len(rows)<50:break
  else:raise Failure('SCAN_LIMIT')
  write('status.json',{'status':'PARTIAL' if failed else 'OK','cards':count,'failed_cards':failed,'completed_at':int(time.time())})

def worker():
 while True:
  started=time.monotonic()
  try:cycle()
  except Failure as e:write('status.json',{'status':str(e),'method':getattr(e,'method',''),'checked_at':int(time.time())})
  except Exception:write('status.json',{'status':'INTERNAL_ERROR','checked_at':int(time.time())})
  WAKE.wait(max(5,300-(time.monotonic()-started)));WAKE.clear()
