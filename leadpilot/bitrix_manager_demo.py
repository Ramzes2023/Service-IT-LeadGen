"""Local synthetic lifecycle. No transport injection and no network client.

SQLite decisions bind a complete validated snapshot; a changed snapshot must
never inherit an earlier decision. This is a demo, not corporate RBAC proof.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from copy import deepcopy
from datetime import date
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import sqlite3

from .bitrix_handoff_dryrun import build_offline_handoff, synthetic_fixture
from .bitrix_1062_mapping import (
    build_synthetic_1062_plan, SyntheticPlanRejected, FIELD_MAP, _validate_card,
)
from .bitrix_1062_manager_bridge import build_synthetic_1062_manager_plan
from .bitrix_1062_outbox import (
    initialize, enqueue_synthetic_plan, claim_next, mark_uncertain, overview,
    fence_claimed_payload,
)
from .bitrix_1062_reconcile import reconcile_uncertain_key


def snapshot(report: dict, today: date) -> str:
    plan = build_synthetic_1062_plan(report, today=today)
    return sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def persist_decisions(db: Path, report: dict, reviews: list, *, today: date) -> dict:
    """Atomic single-use demo decisions; hashes detect inconsistent local edits.

    The database owner can replace both content and hashes. This is integrity
    checking, never authenticated approval or corporate authorization.
    """
    plan = build_synthetic_1062_manager_plan(report, reviews, today=today)
    digest = snapshot(report, today)
    with closing(sqlite3.connect(db)) as con:
        con.execute('CREATE TABLE IF NOT EXISTS demo_decisions ('
                    'signal_key TEXT PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,'
                    'snapshot TEXT NOT NULL,review_json TEXT NOT NULL,'
                    'review_sha256 TEXT NOT NULL)')
        try:
            with con:
                con.execute('BEGIN IMMEDIATE')
                for review in reviews:
                    encoded = json.dumps(review, sort_keys=True, ensure_ascii=False)
                    con.execute('INSERT INTO demo_decisions VALUES(?,?,?,?,?)', (
                        review['idempotency_key'], review['event_id'], digest,
                        encoded, sha256(encoded.encode('utf-8')).hexdigest()))
        except sqlite3.IntegrityError:
            raise SyntheticPlanRejected('DURABLE_DECISION_REPLAY_OR_CONFLICT') from None
    return plan


def reviewed_plan(db: Path, report: dict, *, today: date) -> tuple[dict, dict]:
    digest = snapshot(report, today)
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as con:
            rows = con.execute('SELECT snapshot,review_json,signal_key,event_id,review_sha256 '
                               'FROM demo_decisions').fetchall()
    except sqlite3.Error:
        raise SyntheticPlanRejected('PERSISTED_DECISIONS_NOT_READY') from None
    if not rows or any(row[0] != digest for row in rows):
        raise SyntheticPlanRejected('MISSING_OR_STALE_DECISION')
    reviews = []
    for row in rows:
        try:
            review = json.loads(row[1])
            if (sha256(row[1].encode('utf-8')).hexdigest() != row[4]
                    or review['idempotency_key'] != row[2] or review['event_id'] != row[3]):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise SyntheticPlanRejected('PERSISTED_DECISION_INTEGRITY_FAILED') from None
        reviews.append(review)
    manager = build_synthetic_1062_manager_plan(report, reviews, today=today)
    # Every intent needs a persisted decision before even mock queueing.
    if manager['unreviewed_count']:
        raise SyntheticPlanRejected('MANUAL_REVIEW_REQUIRED')
    return build_synthetic_1062_plan(report, today=today), manager


class LocalMockREST:
    """Bounded REST-shaped in-memory simulator; cannot access a portal."""

    def __init__(self):
        self.items = {}
        self.calls = []

    def add(self, claim: dict) -> None:
        params = claim['params_preview_only']
        fields = params['fields']
        key = fields['UF_CRM_13_SVCIT_IDEMPOTENCY_KEY']
        _, canonical = _validate_card(dict(
            status='DRY_RUN_INTENT_ONLY', idempotency_key=key,
            logical_fields={k: fields.get(v) for k, v in FIELD_MAP.items()},
            crm_entity_type='UNCONFIRMED', actual_crm_field_codes='UNCONFIRMED',
            crm_writes='NO', real_customer_contact=False),
            date.fromisoformat(fields['UF_CRM_13_SVCIT_SIGNAL_DATE']))
        if params != dict(entityTypeId=1062, useOriginalUfNames='Y', fields=canonical):
            raise SyntheticPlanRejected('MOCK_CANONICAL_MAPPING_REQUIRED')
        if key in self.items or len(self.items) >= 2:
            raise SyntheticPlanRejected('MOCK_DUPLICATE_OR_BOUND_EXCEEDED')
        self.items[key] = dict(deepcopy(params['fields']),
                               id=len(self.items) + 1, entityTypeId=1062)
        self.calls.append('MOCK crm.item.add')

    def read(self, method: str, params: dict) -> dict:
        if params.get('entityTypeId') != 1062 or params.get('useOriginalUfNames') != 'Y':
            raise SyntheticPlanRejected('MOCK_SCOPE_INVALID')
        self.calls.append('MOCK ' + method)
        if method == 'crm.item.list':
            key = params['filter']['UF_CRM_13_SVCIT_IDEMPOTENCY_KEY']
            if params['filter']['categoryId'] != 21 or params['start'] != 0:
                raise SyntheticPlanRejected('MOCK_FILTER_INVALID')
            items = [deepcopy(self.items[key])] if key in self.items else []
            return {'items': items, 'total': len(items)}
        if method == 'crm.item.get':
            found = [x for x in self.items.values() if x['id'] == params['id']]
            if len(found) == 1:
                return {'item': deepcopy(found[0])}
        raise SyntheticPlanRejected('MOCK_METHOD_OR_ITEM_INVALID')

    def review(self, action: dict) -> None:
        item = self.items[action['idempotency_key']]
        from .bitrix_1062_manager_bridge import DECISION_TO_STAGE
        if (action.get('operation') != 'PREVIEW_ONLY_NOT_EXECUTABLE'
                or action['params_template_without_item_id']['entityTypeId'] != 1062
                or action['params_template_without_item_id']['fields']['stageId']
                not in DECISION_TO_STAGE.values()
                or action['expected_field_value'] != {
                    'UF_CRM_13_SVCIT_IDEMPOTENCY_KEY': item['UF_CRM_13_SVCIT_IDEMPOTENCY_KEY']}):
            raise SyntheticPlanRejected('MOCK_REVIEW_SCOPE_INVALID')
        if (item['categoryId'] != action['expected_category_id']
                or item['stageId'] != action['expected_current_stage']):
            raise SyntheticPlanRejected('MOCK_STALE_STAGE')
        item.update(deepcopy(action['params_template_without_item_id']['fields']))


def run_demo(output: Path, *, today: date | None = None) -> dict:
    """Write only to a fresh caller-chosen directory inside this worktree/docs."""
    root = Path(__file__).resolve().parents[1] / 'docs'
    output = output.resolve()
    if not output.is_relative_to(root) or output == root or output.exists():
        raise SyntheticPlanRejected('NEW_WORKTREE_DOCS_DIRECTORY_REQUIRED')
    output.mkdir(parents=True)
    now = today or date.today()
    report = build_offline_handoff(synthetic_fixture(now), today=now)
    mapped = build_synthetic_1062_plan(report, today=now)
    reviews = [dict(idempotency_key=op['idempotency_key'],
                    event_id=f'DEMO-REVIEW-LOCAL{i:04}', decision=decision,
                    reviewer_role='manager', reviewer_label='DEMO-MANAGER-1',
                    reason='Синтетическая проверка проекта без контактов',
                    reviewed_on=now.isoformat())
               for i, (op, decision) in enumerate(zip(mapped['requests_not_sent'],
                                                   ('relevant', 'needs_research')))]
    db = output / 'synthetic.sqlite3'
    initialize(db)
    persist_decisions(db, report, reviews, today=now)
    mapped, manager = reviewed_plan(db, report, today=now)
    enqueue_synthetic_plan(db, mapped)
    replay = enqueue_synthetic_plan(db, mapped)
    mock = LocalMockREST()
    receipts = []
    for index in range(2):
        claim = claim_next(db)
        fence_claimed_payload(db, claim['idempotency_key'], claim['params_preview_only'])
        mock.add(claim)
        # One simulated lost response: read reconciliation only, no retry.
        if index == 0:
            mark_uncertain(db, claim['idempotency_key'], reason_code='MOCK_LOST_RESPONSE')
        key = claim['idempotency_key']
        actual = mock.items[key]
        if any(actual.get(k) != v for k, v in claim['params_preview_only']['fields'].items()):
            raise SyntheticPlanRejected('MOCK_FULL_PAYLOAD_MISMATCH')
        receipts.append(reconcile_uncertain_key(str(db), mock.read, key))
        # Simulated internal triage; no real stage update transport exists here.
        if actual['stageId'] != 'DT1062_21:NEW':
            raise SyntheticPlanRejected('MOCK_INITIAL_STAGE_INVALID')
        actual['stageId'] = 'DT1062_21:CLIENT'
        action = next(a for a in manager['actions_not_sent'] if a['idempotency_key'] == key)
        mock.review(action)
    result = dict(mode='SYNTHETIC PREVIEW', real_crm_confirmation=False,
                  real_rest_calls=0, customer_contact_authorized=False,
                  production_ready=False, real_leads_delivered=False,
                  report=report, mapping=mapped, manager=manager,
                  local_outbox=overview(db), duplicate_replay=replay,
                  mock_receipts=receipts, mock_calls=mock.calls,
                  mock_final_items=list(mock.items.values()))
    (output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    stage_labels = {'DT1062_21:SUCCESS': 'Релевантный сигнал (не продажа)',
                    'DT1062_21:PREPARATION': 'Требуется исследование'}
    rows = ''.join('<tr>' + ''.join('<td>' + escape(str(item[k])) + '</td>' for k in (
        'UF_CRM_13_SVCIT_SIGNAL_ID', 'UF_CRM_13_SVCIT_COMPANY_INN',
        'UF_CRM_13_SVCIT_SIGNAL_SOURCE', 'UF_CRM_13_SVCIT_SIGNAL_URL',
        'UF_CRM_13_SVCIT_LEAD_SCORE')) + '<td>' + escape(stage_labels[item['stageId']])
        + '<br><small>' + escape(item['stageId']) + '</small></td></tr>'
        for item in mock.items.values())
    html = '''<!doctype html><html lang="ru"><meta charset="utf-8">
<title>ServiceIT — SYNTHETIC PREVIEW</title><style>
body{font:17px Segoe UI,sans-serif;background:#f2f5fa;color:#18263c;margin:40px auto;max-width:1150px;padding:20px}
article{background:white;padding:28px;border-radius:16px}h1{color:#174f86}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left;overflow-wrap:anywhere}table{width:100%;table-layout:fixed}strong{color:#9b3700}</style>
<article><strong>SYNTHETIC PREVIEW · Локальная модель · Не карточки CRM</strong>
<h1>Михаил, так будет выглядеть путь B2B-сигнала</h1>
<p>Два независимых учебных запроса проекта 1С одной вымышленной компании в Москве.
Это не вакансии, не реальные потребности и не разрешение связаться с клиентом.</p>
<p>Проверка ИНН и доказательства → оценка 86/100 (учебная, не прогноз) →
стоп-лист и действующий клиент → сохранённое решение → очередь → mock REST → сверка.</p>
<table><tr><th>Сигнал</th><th>Учебный ИНН</th><th>Источник</th><th>Доказательство</th><th>Оценка</th><th>Итог модели</th></tr>''' + rows + '''</table>
<p>Повтор сигнала пропущен. Стоп-компания удержана. Потерянный ответ восстановлен
чтением mock; повтор POST не выполнялся. VERIFIED означает локальную сверку с mock.</p>
<h2>Что уже известно</h2><p>По ранее принятой проверке: процесс 1062, тип 13,
категория 21, 16 полей UF_CRM_13_SVCIT_*, шесть стадий. В этом запуске портал не проверялся.
Ранее независимо приняты 546 общих и 237 целевых тестов; отдельный синтетический
create/get/delete завершён ранее, тестовая карточка удалена.</p>
<h2>Что появится после разрешённого подключения</h2><p>Компания, ИНН, URL и дата доказательства,
обоснование услуги, оценка и решение менеджера. Услуги: 1С, IT-аутсорсинг, CRM/Битрикс24,
ККТ/маркировка/POS, серверы, ЭДО и Криптопро. В модели показан только сценарий 1С.</p>
<p><strong>Активация заблокирована:</strong> нужны корпоративная роль, разрешённые источники,
проверка REST-фильтра, согласование запуска и эксплуатации. Реальных лидов нет.</p>
<p>Откройте report.json для полного учебного следа. Здесь нет внешних ресурсов или скриптов.</p></article></html>'''
    (output / 'index.html').write_text(html, encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run_demo(args.output)
    print('SYNTHETIC_PREVIEW=PASS; REAL_REST_CALLS=0; CRM_WRITES=NO; REAL_LEADS_DELIVERED=NO')


if __name__ == '__main__':
    main()
