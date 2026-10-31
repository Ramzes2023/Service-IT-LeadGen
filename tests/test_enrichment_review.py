import json
from datetime import date
from pathlib import Path
import pytest
from leadpilot.enrichment_review import make_checklist, audit_checklist

NOW = date(2026, 10, 31)


def fake_report(tmp_path, *, candidate_count=2):
    p = tmp_path / 'input.json'
    p.write_text(json.dumps({'mode':'OFFLINE_MVP_RESEARCH','crm_ready':0,
        'candidates':[{'status':'RESEARCH_ONLY_NOT_CRM_READY',
                       'inn':f'770000000{i+1}', 'company_name':f'Company{i}',
                       'signal':{'url':f'https://example.invalid/vacancy/{i}'}}
                      for i in range(candidate_count)]}), encoding='utf-8')
    return p


def test_prepares_blank_local_checklist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    x = make_checklist(fake_report(tmp_path), 'exports/checklist.json')
    assert x['rows']==2 and x['crm_ready']==0 and x['crm_writes']=='NO'
    obj=json.loads(Path('exports/checklist.json').read_text('utf8'))
    assert obj['items'][0]['business_size']==''
    assert obj['items'][0]['reviewed_by_human'] is False
    assert obj['items'][0]['crm_duplicate_check']=='NOT_PERFORMED'
    assert 'email' not in str(obj)
    assert audit_checklist('exports/checklist.json', today=NOW)['counts']['crm_ready']==0


def test_does_not_overwrite_human_work(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    source=fake_report(tmp_path)
    make_checklist(source,'exports/checklist.json')
    with pytest.raises(FileExistsError):make_checklist(source,'exports/checklist.json')


def test_outside_exports_forbidden(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):make_checklist(fake_report(tmp_path),'data/notes.json')


def test_small_business_claim_requires_official_fns_and_human_review(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_checklist(fake_report(tmp_path, candidate_count=1),'exports/checklist.json')
    p=Path('exports/checklist.json');r=json.loads(p.read_text('utf8')); row=r['items'][0]
    row.update(business_size='small', size_registry_url='https://rmsp.nalog.ru/',
               size_checked_on='2026-10-30',corporate_contact_source_url='https://company.example/contact',
               corporate_contact_role='бухгалтерия',contact_channel='web_form',
               contact_checked_on='2026-10-30',reviewed_by_human=True)
    p.write_text(json.dumps(r),encoding='utf8')
    audited=audit_checklist(p,today=NOW)
    assert audited['counts']['manual_evidence_supplied']==1
    assert audited['counts']['crm_ready']==0
    assert 'NOT_CRM_READY' in audited['details'][0]['status']


def test_rejects_fake_fns_domain(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_checklist(fake_report(tmp_path, candidate_count=1),'exports/checklist.json')
    p=Path('exports/checklist.json');r=json.loads(p.read_text('utf8')); row=r['items'][0]
    row.update(business_size='micro',size_registry_url='https://rmsp.nalog.ru.evil.net',
               size_checked_on='2026-10-30',contact_checked_on='2026-10-30')
    p.write_text(json.dumps(r),encoding='utf8')
    x=audit_checklist(p,today=NOW)
    assert x['counts']['manual_evidence_supplied']==0
    assert 'official_fns_evidence_missing_or_stale' in x['details'][0]['issues']


def test_stale_size_evidence_not_counted(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_checklist(fake_report(tmp_path,candidate_count=1),'exports/checklist.json')
    p=Path('exports/checklist.json');r=json.loads(p.read_text('utf8')); row=r['items'][0]
    row.update(business_size='small',size_registry_url='https://rmsp.nalog.ru',
               size_checked_on='2026-01-01',contact_checked_on='2026-01-01',
               corporate_contact_source_url='https://company.example/contact',
               corporate_contact_role='бухгалтерия',contact_channel='web_form',reviewed_by_human=True)
    p.write_text(json.dumps(r),encoding='utf8')
    x=audit_checklist(p,today=NOW)
    assert x['counts']['manual_evidence_supplied']==0
    assert x['counts']['crm_ready']==0


def test_fake_ready_input_not_accepted(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    p=fake_report(tmp_path); r=json.loads(p.read_text('utf8'));r['crm_ready']=42
    p.write_text(json.dumps(r),encoding='utf8')
    with pytest.raises(ValueError):make_checklist(p,'exports/checklist.json')
