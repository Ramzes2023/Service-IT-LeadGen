import unittest
from unittest.mock import patch
import finance_dashboard as f
import automation as a
class FinanceTests(unittest.TestCase):
 def item(self,**kw):
  return dict({'id':11,'categoryId':21,'stageId':'won','assignedById':605,f.P+'FIN_BASIS':'actual',f.P+'FIN_REVENUE':'100000',f.P+'FIN_COSTS':'40000'},**kw)
 def test_success_actual_only_and_recompute(self):
  eligible=self.item();plan=self.item(id=12,**{f.P+'FIN_BASIS':'plan'});active=self.item(id=13,stageId='review')
  result=f.aggregate([eligible,plan,active],{'won'},'actual')
  self.assertEqual(result['count'],1);self.assertEqual(result['totals']['company'],4500000)
  self.assertEqual(result['totals']['manager'],600000);self.assertEqual(result['totals']['developer'],900000)
  self.assertEqual(result['totals'],f.aggregate([eligible,plan,active],{'won'},'actual')['totals'])
  eligible[f.P+'FIN_REVENUE']='200000'
  self.assertEqual(f.aggregate([eligible],{'won'},'actual')['totals']['company'],12000000)
 def test_duplicates_missing_and_invalid(self):
  a=self.item(**{f.P+'SIGNAL_ID':'same'});b=self.item(id=12,**{f.P+'SIGNAL_ID':'same'})
  missing=self.item(id=13,**{f.P+'FIN_COSTS':''});invalid=self.item(id=14,**{f.P+'FIN_REVENUE':'-1'})
  result=f.aggregate([a,b,missing,invalid],{'won'},'actual')
  self.assertEqual(result['count'],1);self.assertEqual(result['excluded']['duplicate'],1)
  self.assertEqual(result['excluded']['missing'],1);self.assertEqual(result['excluded']['invalid'],1)
 def test_paid_is_not_deducted_twice_and_loss_is_company_only(self):
  item=self.item(**{f.P+'FIN_PAID_MANAGER':'3000',f.P+'FIN_PAID_DEVELOPER':'9000'})
  r=f.aggregate([item],{'won'},'actual');self.assertEqual(r['totals']['company'],4500000)
  self.assertEqual(r['totals']['paid_manager'],300000);self.assertFalse(r['rows'][0]['overpaid'])
  item[f.P+'FIN_REVENUE']='0';r=f.aggregate([item],{'won'},'actual')
  self.assertEqual(r['totals']['company'],-4000000);self.assertEqual(r['totals']['developer'],0);self.assertTrue(r['rows'][0]['overpaid'])
 def test_rounding_reconciles(self):
  for value in ['0','0.01','0.03','0.05','0.09','1.99']:
   r=f.aggregate([self.item(**{f.P+'FIN_REVENUE':value,f.P+'FIN_COSTS':'0'})],{'won'},'actual')['totals']
   self.assertEqual(r['profit'],r['company']+r['manager']+r['developer'])
 def test_binding_is_idempotent(self):
  existing=[{'placement':f.PLACEMENT,'handler':f.HANDLER}]
  with patch.object(a,'api',return_value=existing) as api:f.ensure_binding('token');self.assertEqual(api.call_count,1)
 def test_snapshot_uses_current_user_token(self):
  calls=[]
  def api(token,method,**kw):
   calls.append((token,method,kw));return {'items':[self.item()]}
  with patch.object(a,'identity') as identity,patch.object(a,'api',side_effect=api),patch.object(f,'config',return_value=({'won'},'actual')):
   result=f.snapshot('current-user');identity.assert_called_once_with('current-user')
  self.assertEqual(result['count'],1);self.assertTrue(all(x[0]=='current-user' for x in calls))
if __name__=='__main__':unittest.main()
