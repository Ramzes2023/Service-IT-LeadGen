"""Standalone stdlib acceptance as well as pytest-compatible tests."""
from copy import deepcopy
from contextlib import closing
from datetime import date
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from leadpilot.bitrix_handoff_dryrun import build_offline_handoff, synthetic_fixture
from leadpilot.bitrix_1062_mapping import build_synthetic_1062_plan, SyntheticPlanRejected
from leadpilot.bitrix_1062_manager_bridge import build_synthetic_1062_manager_plan
from leadpilot.bitrix_1062_reconcile import inspect_remote_synthetic_key
from leadpilot.bitrix_1062_outbox import (
    initialize, enqueue_synthetic_plan, claim_next, overview, confirm_remote_readback,
    fence_claimed_payload,
)
from leadpilot.bitrix_manager_demo import persist_decisions, reviewed_plan, run_demo, LocalMockREST

NOW = date(2026, 11, 1)
ROOT = Path(__file__).resolve().parents[1] / 'docs'


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='demo-test-', dir=ROOT))
        self.addCleanup(shutil.rmtree, self.folder)
        self.report = build_offline_handoff(synthetic_fixture(NOW), today=NOW)
        self.plan = build_synthetic_1062_plan(self.report, today=NOW)
        self.db = self.folder / 'test.sqlite3'
        self.reviews = [dict(idempotency_key=op['idempotency_key'],
                            event_id=f'DEMO-REVIEW-TEST{i:04}', decision='relevant',
                            reviewer_role='manager', reviewer_label='DEMO-MANAGER-1',
                            reason='Синтетический проект для проверки', reviewed_on=NOW.isoformat())
                        for i, op in enumerate(self.plan['requests_not_sent'])]

    def test_no_network_and_utf8_artifacts(self):
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK_FORBIDDEN')), \
                patch.object(socket, 'create_connection', side_effect=AssertionError('NETWORK_FORBIDDEN')):
            result = run_demo(self.folder / 'output', today=NOW)
        self.assertFalse(result['real_crm_confirmation'])
        self.assertEqual(result['local_outbox']['states']['VERIFIED'], 2)
        self.assertEqual(result['mock_calls'].count('MOCK crm.item.add'), 2)
        self.assertEqual(result['duplicate_replay']['duplicate_skipped'], 2)
        self.assertEqual(result['report']['held_count'], 1)
        text = (self.folder / 'output/index.html').read_text(encoding='utf-8')
        self.assertIn('Михаил', text)
        self.assertNotIn('<script', text)
        self.assertNotIn('�', text)
        serialized = json.dumps(result)
        for marker in ('/rest/', 'access_token', 'api_key', 'password', '@'):
            self.assertNotIn(marker, serialized)

    def test_replay_conflict_and_transaction_rollback(self):
        persist_decisions(self.db, self.report, self.reviews[:1], today=NOW)
        with self.assertRaises(SyntheticPlanRejected):
            persist_decisions(self.db, self.report, list(reversed(self.reviews)), today=NOW)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM demo_decisions').fetchone()[0], 1)
        with self.assertRaises(SyntheticPlanRejected):
            reviewed_plan(self.db, self.report, today=NOW)

    def test_stale_snapshot(self):
        persist_decisions(self.db, self.report, self.reviews, today=NOW)
        changed = deepcopy(self.report)
        changed['operations'][0]['logical_fields']['lead_score'] = 75
        with self.assertRaisesRegex(SyntheticPlanRejected, 'STALE'):
            reviewed_plan(self.db, changed, today=NOW)

    def test_tampered_persisted_decision_rejected(self):
        persist_decisions(self.db, self.report, self.reviews, today=NOW)
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE demo_decisions SET review_json=replace(review_json, 'relevant', 'irrelevant')")
            con.commit()
        with self.assertRaisesRegex(SyntheticPlanRejected, 'INTEGRITY'):
            reviewed_plan(self.db, self.report, today=NOW)

    def test_expired_review_and_missing_db_fail_closed(self):
        from datetime import timedelta
        reviews = [dict(r, reviewed_on=(NOW - timedelta(days=31)).isoformat()) for r in self.reviews]
        with self.assertRaisesRegex(SyntheticPlanRejected, 'DATE_OUT_OF_RANGE'):
            persist_decisions(self.db, self.report, reviews, today=NOW)
        missing = self.folder / 'missing.sqlite3'
        with self.assertRaisesRegex(SyntheticPlanRejected, 'NOT_READY'):
            reviewed_plan(missing, self.report, today=NOW)
        self.assertFalse(missing.exists())

    def test_outbox_rejects_unvalidated_fields_and_tampered_storage(self):
        initialize(self.db)
        for field, value in [('UF_CRM_13_SVCIT_SIGNAL_URL', 'https://example.com/customer'),
                             ('UF_CRM_13_SVCIT_COMPANY_NAME', 'DEMO 79991234567'),
                             ('UF_CRM_13_SVCIT_MANAGER_NEXT_ACTION', 'Contact person@example.com'),
                             ('UF_CRM_13_SVCIT_REVIEW_DECISION', 'APPROVED_REAL')]:
            plan = deepcopy(self.plan)
            plan['requests_not_sent'][0]['params']['fields'][field] = value
            with self.subTest(field=field), self.assertRaises(SyntheticPlanRejected):
                enqueue_synthetic_plan(self.db, plan)
        self.assertEqual(overview(self.db)['states']['QUEUED'], 0)
        enqueue_synthetic_plan(self.db, self.plan)
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE bitrix_1062_outbox SET payload_json=replace(payload_json, 'example.invalid', 'example.com')")
            con.commit()
        with self.assertRaisesRegex(SyntheticPlanRejected, 'INTEGRITY'):
            claim_next(self.db)
        self.assertEqual(overview(self.db)['states']['CLAIMED'], 0)

    def test_output_outside_docs_rejected_without_creation(self):
        target = ROOT.parent / 'forbidden-demo-output'
        with self.assertRaises(SyntheticPlanRejected):
            run_demo(target, today=NOW)
        self.assertFalse(target.exists())

    def test_policy_holds(self):
        for changes in ({'existing_customer': True}, {'manual_review': True}, {'stops': ['all']},
                        {'inn': '7700000000'}, {'lead_score': True},
                        {'company_name': 'DEMO 79991234567'},
                        {'signal_url': 'https://example.invalid/serviceit-demo/79991234567'},
                        {'signal_url': 'https://example.invalid/serviceit-demo/%4079991234567'},
                        {'signal_url': 'https://example.com/private'}):
            card = dict(synthetic_fixture(NOW)[0], **changes)
            with self.subTest(changes=changes):
                self.assertEqual(build_offline_handoff([card], today=NOW)['held_count'], 1)

    def test_unsafe_review_contact(self):
        for reason in ('Позвонить +7 (999) 123-45-67', 'Проверить tel:12345678901', 'Проверить t.me/person',
                       'DEMO abc79991234567xyz'):
            review = dict(self.reviews[0], reason=reason)
            with self.subTest(reason=reason), self.assertRaises(SyntheticPlanRejected):
                build_synthetic_1062_manager_plan(self.report, [review], today=NOW)

    def test_incomplete_lists(self):
        key = self.reviews[0]['idempotency_key']
        for result in ({'items': [], 'next': 50}, {'items': [], 'total': 1},
                       {'items': [], 'total': True}, {'items': [], 'total': '0'}):
            with self.subTest(result=result), self.assertRaises(SyntheticPlanRejected):
                inspect_remote_synthetic_key(lambda *_: result, key)

    def test_durable_attempt_fence_and_abort(self):
        initialize(self.db)
        enqueue_synthetic_plan(self.db, self.plan)
        claim = claim_next(self.db)
        fence_claimed_payload(self.db, claim['idempotency_key'], claim['params_preview_only'])
        self.assertEqual(overview(self.db)['states']['UNCERTAIN'], 1)
        with self.assertRaises(SyntheticPlanRejected):
            fence_claimed_payload(self.db, claim['idempotency_key'], claim['params_preview_only'])
        self.assertNotEqual(claim_next(self.db)['idempotency_key'], claim['idempotency_key'])
        self.assertIsNone(claim_next(self.db))

    def test_mock_stage_and_duplicate_guards(self):
        mock = LocalMockREST()
        claim = {'params_preview_only': self.plan['requests_not_sent'][0]['params']}
        mock.add(claim)
        with self.assertRaises(SyntheticPlanRejected):
            mock.add(claim)
        action = build_synthetic_1062_manager_plan(self.report, self.reviews, today=NOW)['actions_not_sent'][0]
        with self.assertRaises(SyntheticPlanRejected):
            mock.review(action)
        item = mock.items[action['idempotency_key']]
        item['stageId'] = 'DT1062_21:CLIENT'
        mock.review(action)
        with self.assertRaises(SyntheticPlanRejected):
            mock.review(action)

    def test_no_overwrite(self):
        with self.assertRaises(SyntheticPlanRejected):
            run_demo(self.folder)

    def test_partial_wrong_mapping_and_stage_readback(self):
        initialize(self.db)
        enqueue_synthetic_plan(self.db, self.plan)
        claim = claim_next(self.db)
        key = claim['idempotency_key']
        fields = claim['params_preview_only']['fields']
        original = dict(fields, id=1, entityTypeId=1062)
        for field, value in [('categoryId', 22), ('stageId', 'DT1062_21:SUCCESS'),
                             ('UF_CRM_13_SVCIT_LEAD_SCORE', True),
                             ('UF_CRM_13_SVCIT_SIGNAL_URL', None),
                             ('UF_CRM_13_SVCIT_COMPANY_INN', '7700000000')]:
            with self.subTest(field=field), self.assertRaises(SyntheticPlanRejected):
                confirm_remote_readback(self.db, key, crm_item=dict(original, **{field: value}))
        incomplete = dict(original)
        incomplete.pop('UF_CRM_13_SVCIT_SCORE_VERSION')
        with self.assertRaises(SyntheticPlanRejected):
            confirm_remote_readback(self.db, key, crm_item=incomplete)
        self.assertEqual(overview(self.db)['states']['VERIFIED'], 0)
        self.assertEqual(confirm_remote_readback(self.db, key, crm_item=original)['state'], 'VERIFIED')

    def test_concurrent_fence_has_single_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        initialize(self.db)
        enqueue_synthetic_plan(self.db, self.plan)
        claim = claim_next(self.db)
        def attempt(_):
            try:
                fence_claimed_payload(self.db, claim['idempotency_key'], claim['params_preview_only'])
                return 'FENCED'
            except SyntheticPlanRejected:
                return 'REJECTED'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(attempt, range(2))), ['FENCED', 'REJECTED'])


if __name__ == '__main__':
    unittest.main()
