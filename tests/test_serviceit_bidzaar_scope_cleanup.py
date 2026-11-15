import pytest
from leadpilot.serviceit_bidzaar_scope_cleanup import failed_stage

def test_rejection_uses_verified_failed_stage_not_success():
 rows=[{'STATUS_ID':'DT1062_21:SUCCESS','SEMANTICS':'S','NAME':'Успех','SORT':100},{'STATUS_ID':'DT1062_21:FAIL','EXTRA':{'SEMANTICS':'apology'},'NAME':'Нецелевой сигнал','SORT':110}]
 assert failed_stage(rows)=='DT1062_21:FAIL'
 with pytest.raises(ValueError,match='UNAVAILABLE'):failed_stage(rows[:1])

def test_non_it_card_is_closed_with_readback_and_no_deletion(monkeypatch):
 import leadpilot.serviceit_bidzaar_scope_cleanup as c
 import leadpilot.serviceit_stage34_signed_readonly_sidecar as sidecar
 class Result:
  def __init__(self,rows=()):self.rows=rows
  def fetchall(self):return self.rows
  def fetchone(self):return self.rows[0] if self.rows else None
 class Conn:
  def __init__(self):self.writes=[]
  def execute(self,sql,args=()):
   if sql.startswith('SELECT source_id'):return Result([('BIDZAAR-test',89,{'name':'Закупка питания, канц.тов. и иных хоз. товаров'}, {},'VERIFIED')])
   if sql.startswith('SELECT to_regclass'):return Result([(None,)])
   self.writes.append((sql,args));return Result()
 actual={'categoryId':21,c.PREFIX+'SIGNAL_ID':'BIDZAAR-test','stageId':'DT1062_21:NEW'};writes=[]
 monkeypatch.setattr(c,'Rest',lambda *a:lambda *a:{'item':dict(actual)})
 monkeypatch.setattr(sidecar,'owner_type',lambda *a:'T426')
 def request(client,base,method,args):
  if method=='crm.status.list':return [{'STATUS_ID':'DT1062_21:FAIL','SEMANTICS':'F','NAME':'Нецелевой','SORT':100}]
  assert method=='crm.item.update';writes.append(args);actual.update(args['fields']);return {}
 monkeypatch.setattr(c,'request',request)
 conn=Conn();c.run(conn,None,'')
 assert len(writes)==1 and actual['stageId']=='DT1062_21:FAIL' and actual[c.SCORE_FIELD]==0
 assert any("state='EXCLUDED'" in sql for sql,args in conn.writes)
