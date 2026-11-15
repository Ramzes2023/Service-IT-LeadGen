import tempfile
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import automation as a

class AutomationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.old=a.DATA;a.DATA=Path(self.temp.name)
 def tearDown(self):a.DATA=self.old;self.temp.cleanup()
 def item(self):return {'id':39,'categoryId':21,'assignedById':1607,a.PREFIX+'FIN_REVENUE':'100000',a.PREFIX+'FIN_COSTS':'40000'}
 def test_refresh_rotates_and_persists_private_tokens(self):
  a.write('auth.json',{'access_token':'a'*32,'refresh_token':'b'*32,'expires':0})
  a.write('client.json',{'client_id':a.CODE,'client_secret':'secret'})
  response={'access_token':'c'*32,'refresh_token':'d'*32,'client_endpoint':a.PORTAL,'expires_in':3600}
  with patch.object(a.urllib.request,'build_opener') as build:
   build.return_value.open.return_value=io.BytesIO(json.dumps(response).encode())
   self.assertEqual(a.current_token(),'c'*32)
   request=build.return_value.open.call_args.args[0]
   self.assertNotIn('secret',request.full_url)
  self.assertEqual(a.read('auth.json')['refresh_token'],'d'*32)
 def test_http_error_exposes_only_safe_code(self):
  error=a.urllib.error.HTTPError(a.PORTAL,401,'auth',{},io.BytesIO(b'{"error":"expired_token","error_description":"SECRET"}'))
  with patch.object(a.urllib.request,'build_opener') as build:
   build.return_value.open.side_effect=error
   with self.assertRaisesRegex(a.Failure,'^expired_token$'):a.request(a.PORTAL,{})
 def test_money(self):
  self.assertEqual(a.split(a.money('100000'),a.money('40000')),[6000000,900000,600000,4500000])
  self.assertEqual(a.split(0,100),[-100,0,0,-100]);self.assertIsNone(a.split(None,0))
  for p in range(10000):
   profit,d,m,c=a.split(p,0);self.assertEqual(profit,d+m+c);self.assertGreaterEqual(c,0)
  for value in ['-1','NaN','1e4','1.234']:
   with self.assertRaises(a.Failure):a.money(value)
 def test_unchanged_does_not_write(self):
  calls=[]
  created=set()
  def api(token,method,**kwargs):
   calls.append(method)
   if method.endswith('.list'):return [{'ID':123,'COMPLETED':'N'}] if kwargs['filter']['ORIGIN_ID'] in created else []
   if method.endswith('.add'):created.add(kwargs['fields']['originId'])
   return 123
  cache={}
  with patch.object(a,'api',api):a.sync('token',self.item(),cache);a.sync('token',self.item(),cache)
  self.assertEqual(calls.count('crm.activity.configurable.add'),2)
 def test_timeout_add_not_retried_and_next_scan_lists(self):
  rows=[];count=0
  def api(token,method,**kwargs):
   nonlocal count
   if method.endswith('.list'):return rows
   if method.endswith('.add'):
    count+=1;rows.append({'ID':1});raise a.Failure('REMOTE_REQUEST_FAILED')
   if method.endswith('.get'):return {'fields':{'completed':False}}
   return True
  cache={}
  with patch.object(a,'api',api):
   with self.assertRaises(a.Failure):a.sync('token',self.item(),cache)
   a.sync('token',self.item(),cache)
  self.assertEqual(count,1)
 def test_completed_finance_reopened_even_with_cached_content(self):
  item=self.item();cache={};updates=[]
  def api(token,method,**kwargs):
   if method.endswith('.list'):return [{'ID':7,'COMPLETED':'Y'}]
   if method.endswith('.get'):return {'fields':{'completed':False}}
   updates.append(kwargs);return True
  with patch.object(a,'api',api):
   a.sync('token',item,cache);a.sync('token',item,cache)
  self.assertEqual(len(updates),4)
  self.assertTrue(all(x['fields']=={'completed':False} for x in updates))
 def test_nonadmin_cannot_enable_worker(self):
  with patch.object(a,'identity'),patch.object(a,'api',return_value=False):
   result=a.connect({'access_token':'a'*32,'refresh_token':'b'*32})
  self.assertTrue(result['administratorRequired']);self.assertFalse((a.DATA/'auth.json').exists())
 def test_wrong_application_rejected(self):
  with patch.object(a,'api',return_value={'CODE':'other'}):
   with self.assertRaises(a.Failure):a.identity('a'*32)
 def test_scope_rejected_before_write(self):
  item=self.item();item['categoryId']=99
  with patch.object(a,'api') as api:
   with self.assertRaises(a.Failure):a.sync('token',item,{})
   api.assert_not_called()
 def test_duplicate_block_stops_without_mutation(self):
  with patch.object(a,'api',return_value=[{'ID':1},{'ID':2}]) as api:
   with self.assertRaises(a.Failure):a.sync('token',self.item(),{})
   self.assertEqual(api.call_count,1)
 def test_atomic_private_storage(self):
  a.write('auth.json',{'secret':'private'});self.assertEqual((a.DATA/'auth.json').stat().st_mode&0o777,0o600)
 def test_finance_no_phone_logo(self):self.assertNotIn('logo',a.layout(self.item(),'finance')['body'])
 def test_ai_result_in_call_block(self):
  item=self.item();item[a.PREFIX+'POST_SUMMARY']='Проверено';self.assertIn('Проверено',str(a.layout(item,'call')))

if __name__=='__main__':unittest.main()
