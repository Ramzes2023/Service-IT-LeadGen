from leadpilot.serviceit_tender_manager_policy import summary,SCORE_FIELD

def test_service_fit_does_not_claim_participation_ready():
 s=summary({'acceptanceEndDate':'2026-11-16T13:00:00Z','price':0},{SCORE_FIELD:93})
 assert '93/100' in s and 'НЕ ПОДТВЕРЖДЕНА' in s and '16.11.2026 16:00 МСК' in s
 assert 'не раскрыт' in s

def test_unknown_zone_is_not_relabelled_as_moscow():
 assert 'не подтверждён' in summary({'acceptanceEndDate':'2026-11-16T13:00:00'}, {})

class Result:
 def __init__(self,rows):self.rows=rows
 def fetchall(self):return self.rows
 def fetchone(self):return self.rows[0] if self.rows else None

class Connection:
 def __init__(self):self.receipt=None
 def execute(self,sql,args=()):
  if sql.startswith('SELECT card_id'):return Result([(29,{'acceptanceEndDate':'2099-11-16T13:00:00Z'})])
  if sql.startswith('SELECT submission'):return Result([self.receipt] if self.receipt else [])
  if sql.startswith('INSERT INTO serviceit_tender_task'):self.receipt=(args[2],args[3],'UNCERTAIN')
  if sql.startswith('UPDATE serviceit_tender_task'):self.receipt=(*self.receipt[:2],'VERIFIED')
  return Result([])

def test_late_task_repaired_and_verified_without_duplicates(monkeypatch):
 import leadpilot.serviceit_tender_manager_policy as p
 conn=Connection();task={'id':'12','ufCrmTask':['T426_29'],'responsibleId':'605','status':'2','deadline':'2099-11-21T13:00:00+00:00'};writes=[]
 monkeypatch.setattr(p,'owner_type',lambda *a:'T426')
 def api(client,base,method,args):
  if method=='tasks.task.list':return {'tasks':[dict(task)]}
  if method=='tasks.task.get':return {'task':dict(task)}
  assert method=='tasks.task.update'
  writes.append(args);task['deadline']=args['fields']['DEADLINE'];return {}
 monkeypatch.setattr(p,'api',api)
 p.repair_deadlines(conn,None,'https://example.invalid')
 p.repair_deadlines(conn,None,'https://example.invalid')
 assert len(writes)==1 and conn.receipt[2]=='VERIFIED'
 assert p.submission_time(task['deadline'])<p.submission_time('2099-11-16T13:00:00Z')

def test_changed_assignment_is_not_overwritten(monkeypatch):
 import leadpilot.serviceit_tender_manager_policy as p
 conn=Connection();task={'id':'12','ufCrmTask':['T426_29'],'responsibleId':'605','status':'2','deadline':'2099-11-21T13:00:00+00:00'}
 monkeypatch.setattr(p,'owner_type',lambda *a:'T426')
 def api(client,base,method,args):
  if method=='tasks.task.list':return {'tasks':[dict(task)]}
  if method=='tasks.task.get':return {'task':{**task,'responsibleId':'999'}}
  raise AssertionError('must not overwrite changed task')
 monkeypatch.setattr(p,'api',api)
 p.repair_deadlines(conn,None,'https://example.invalid')
 assert conn.receipt is None
