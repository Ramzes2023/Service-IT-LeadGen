"""Manager review briefs must never contact CRM or authorize outreach."""
from datetime import date
import json
import sqlite3
import pytest

from leadpilot.core import ingest, initialize, qualify, save_ai_analysis, suppress
from leadpilot.manager_briefs import build_manager_briefs, export_manager_briefs

NOW = date(2026, 10, 30)


def company(inn='7700000001', name='Тестовый магазин', existing=False, day='2026-10-28'):
    return {'inn': inn, 'name': name, 'region': 'Москва', 'industry': 'торговля',
            'source_name': 'synthetic-fixture',
            'source_url': f'https://example.invalid/vacancy/{inn}',
            'signal_type': '1c_vacancy',
            'evidence': 'Опубликована вакансия: программист 1С',
            'signal_date': day, 'existing_customer': existing}


def db_with_lead(tmp_path):
    db = tmp_path/'research.sqlite3'
    initialize(db)
    created = ingest(db, company())
    qualify(db, today=NOW)
    return db, created


def test_brief_has_evidence_and_no_contacts_or_write(tmp_path):
    db, _ = db_with_lead(tmp_path)
    before = db.read_bytes()
    report = build_manager_briefs(db, today=NOW)
    assert report['total_briefs'] == 1
    b = report['briefs'][0]
    assert b['inn'] == '7700000001'
    assert b['lead_score'] >= 80 and b['score_version']
    assert b['source']['url'].startswith('https://example.invalid/')
    assert b['source']['signal_date'] == '2026-10-28'
    assert b['ai_hypothesis'] is None
    assert b['outreach'] == 'NOT_AUTHORIZED_BY_THIS_BRIEF'
    assert 'email' not in b and 'phone' not in b
    assert db.read_bytes() == before


def test_existing_and_suppressed_not_in_queue(tmp_path):
    db, _ = db_with_lead(tmp_path)
    ingest(db, company('7700000002', 'Существующий клиент', True))
    ingest(db, company('7700000003', 'Запрещённый контакт'))
    suppress(db,'7700000003','all','refusal')
    qualify(db,today=NOW)
    report=build_manager_briefs(db,today=NOW)
    assert {b['inn'] for b in report['briefs']} == {'7700000001'}


def test_stale_score_filtered_without_rewriting_db(tmp_path):
    db, _ = db_with_lead(tmp_path)
    report=build_manager_briefs(db,today=date(2027,2,5))
    assert report['total_briefs'] == 0
    assert report['skipped']['stale_score_or_signal'] == 1


def test_ai_validated_against_original_evidence(tmp_path):
    db, result = db_with_lead(tmp_path)
    with sqlite3.connect(db) as con:
        sig = con.execute('SELECT id FROM signals WHERE company_id=?', (result['company_id'],)).fetchone()[0]
    good={'service':'1С', 'possible_need':'Может быть нужна поддержка 1С',
          'evidence_quote':'программист 1С','confidence':.72,
          'quality_status':'complete', 'provider':'gigachat'}
    save_ai_analysis(db,result['company_id'],sig,good)
    report=build_manager_briefs(db,today=NOW)
    assert report['briefs'][0]['ai_review'] == 'review_hypothesis'
    assert report['briefs'][0]['ai_hypothesis']['origin']=='AI_hypothesis_not_verified_need'
    bad={**good,'evidence_quote':'Цитата отсутствует в источнике'}
    save_ai_analysis(db,result['company_id'],sig,bad)
    report=build_manager_briefs(db,today=NOW)
    assert report['briefs'][0]['ai_hypothesis'] is None


def test_export_refuses_outside_exports(tmp_path,monkeypatch):
    db, _ = db_with_lead(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        export_manager_briefs(db,tmp_path/'secret.json',today=NOW)
    r=export_manager_briefs(db,'exports/manager_briefs.json',today=NOW)
    assert r['briefs']==1
    saved=json.loads((tmp_path/'exports/manager_briefs.json').read_text(encoding='utf8'))
    assert saved['crm_writes']=='NO' and saved['outreach_sent']=='NO'


def test_limit_invalid(tmp_path):
    db, _=db_with_lead(tmp_path)
    with pytest.raises(ValueError): build_manager_briefs(db,today=NOW,limit=0)
