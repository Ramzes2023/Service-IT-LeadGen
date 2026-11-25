from decimal import Decimal
from unittest.mock import MagicMock
import pytest
from leadpilot import serviceit_manager_workspace as w


def test_profit_from_net_revenue_less_costs():
 a=w.calculate('100 000,00','60000')
 assert a==dict(revenue=Decimal('100000.00'),costs=Decimal('60000.00'),profit=Decimal('40000.00'),developer=Decimal('6000.00'),manager=Decimal('4000.00'),company=Decimal('30000.00'))


@pytest.mark.parametrize('r,c',[('0','0'),('100','200'),('0.01','0'),('0.03','0'),('999999999999.99','0')])
def test_conservation_and_no_commission_on_losses(r,c):
 a=w.calculate(r,c)
 assert a['developer']+a['manager']+a['company']==a['profit']
 if a['profit']<=0:assert a['developer']==a['manager']==0


@pytest.mark.parametrize('value',['-5','NaN','Infinity','1e5','1.123',True,'99999999999999'])
def test_invalid_amounts_are_not_guessed(value):
 with pytest.raises(ValueError):w.money(value)


def test_missing_is_not_zero_and_removes_stale_calculation():
 assert w.calculate('',100) is None
 fields,amounts,_=w.finance_fields({w.P+'FIN_REVENUE':'1000'}, {})
 assert amounts is None and fields[w.P+'FIN_PROFIT'] is None
 assert w.calculate('1000',0)['profit']==1000


def test_plan_and_actual_do_not_mix_and_duplicate_does_not_double_count():
 items=[{'id':8,w.P+'SIGNAL_ID':'a',w.P+'FIN_REVENUE':'100',w.P+'FIN_COSTS':'20'},
 {'id':9,w.P+'SIGNAL_ID':'b',w.P+'FIN_REVENUE':'200',w.P+'FIN_COSTS':'100',w.P+'FIN_BASIS':'22'},
 {'id':10,w.P+'SIGNAL_ID':'a',w.P+'FIN_REVENUE':'100',w.P+'FIN_COSTS':'20'}, {'id':11}]
 text=w.totals(items,{'22':'ACTUAL'})
 assert 'ФАКТ: карточек с расчётом 1' in text and 'ПЛАН: карточек с расчётом 1' in text
 assert 'прибыль 100.00 ₽' in text and 'прибыль 80.00 ₽' in text
 assert 'Исключено повторов сигнала: 1' in text and 'Не заполнены суммы: 1' in text
 assert w.totals(items,{'22':'ACTUAL'})==text


def test_interest_remains_explicit_and_titles_idempotent():
 title='★ ПРИОРИТЕТ · BIDZAAR · Сопровождение 1С'
 marked=w.title_with_interest(title,'YES')
 assert w.title_with_interest(marked,'YES')==marked
 refused=w.title_with_interest(marked,'NO')
 assert refused.startswith('[❌ ОТКАЗ] ★') and '✅' not in refused
 assert w.title_with_interest(refused,'')==title
 assert w.decode(None,{'1':'YES'})==''
 assert w.call_fields({},'', '')[w.P+'POST_BADGE']=='Результат разговора ещё не заполнен'


def test_layout_keeps_existing_sections_and_fields_and_is_idempotent():
 original=[{'name':'ai','title':'AI и приоритет','type':'section','elements':[{'name':'OLD_FIELD','optionFlags':'0'}]}, {'name':'custom','title':'Мой раздел','elements':[{'name':'CUSTOM','optionFlags':'1'}]}]
 after=w.layout_plan(original)
 assert after==original
 assert after==w.layout_plan(after)
 rejected={'name':'serviceit_workspace_call','elements':[{'name':w.P+'POST_NEED'},{'name':'FOREIGN'}]}
 assert w.layout_plan([rejected,*original])==[{'name':'serviceit_workspace_call','elements':[{'name':'FOREIGN'}]},*original]
 assert w.layout_plan([{'name':'serviceit_workspace_finance','elements':[{'name':w.P+'FIN_REVENUE'}]},*original])==original


def test_concurrent_manager_change_blocks_update():
 before={'id':8,'categoryId':21,w.P+'POST_NEED':'old'}
 def rest(method,args):
  assert method=='crm.item.get'
  return {'item':{**before,w.P+'POST_NEED':'new'}}
 with pytest.raises(ValueError,match='MANAGER_CONCURRENT_EDIT'):
  w.safe_patch(rest,8,before,{w.P+'FIN_PROFIT':'100'})


def test_noop_patch_does_not_write():
 rest=MagicMock()
 assert not w.safe_patch(rest,8,{w.P+'FIN_PROFIT':100.0},{w.P+'FIN_PROFIT':'100.00'})
 rest.assert_not_called()


def test_inputs_are_never_writable_by_worker():
 client=MagicMock()
 with pytest.raises(ValueError,match='UPDATE_FIELDS_OUT_OF_SCOPE'):
  w.Rest('https://unused.invalid',client).raw('crm.item.update',{'entityTypeId':1062,'id':8,'fields':{w.P+'POST_INTEREST':'YES'}})
 client.post.assert_not_called()


def test_custom_aliases_normalize():
 assert w.normalize({'ufCrm13SvcitPostNeed':'test'})[w.P+'POST_NEED']=='test'


def test_missing_permission_is_reported_without_secret_or_retry():
 import httpx
 client=MagicMock()
 client.post.return_value=httpx.Response(403,json={'error':'insufficient_scope','error_description':'private details'})
 with pytest.raises(ValueError,match='^WORKSPACE_API_INSUFFICIENT_SCOPE$'):
  w.Rest('https://unused.invalid',client).raw('userfieldconfig.list',{'moduleId':'crm'})
 assert client.post.call_count==1
