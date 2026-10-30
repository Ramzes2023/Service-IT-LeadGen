"""Tests for saved snapshot audit; no network or CRM writes."""
import json
from datetime import date
from pathlib import Path
from leadpilot.verified_import import eligible, is_valid_inn, read_snapshots, run


def make_inn(prefix='770123456'):
    d = [int(x) for x in prefix]
    return prefix + str(sum(a*b for a,b in zip(d,(2,4,10,3,5,9,4,6,8)))%11%10)


def sample(inn=None):
    return {'source_name':'trudvsem-open-vacancies','source_url':'https://trudvsem.ru/vacancy/123',
            'name':'Учебная компания','inn':inn or make_inn(),'region':'Москва',
            'signal_type':'1c_vacancy','evidence':'Опубликована вакансия: Программист 1С',
            'signal_date':'2026-10-30','email':'hidden@example.com'}


def test_checksum():
    good=make_inn()
    assert is_valid_inn(good)
    assert not is_valid_inn(good[:9] + str((int(good[-1])+1)%10))
    assert not is_valid_inn('1234567890')
    assert not is_valid_inn('')


def test_date_and_region_and_contact_stripping():
    v=sample()
    item,why=eligible(v,'77',date(2026,10,30))
    assert why=='ok' and item['email']=='' and item['inn']==v['inn']
    assert eligible(v,'50',date(2026,10,30))[0] is None
    v['signal_date']='2026-07-01'
    assert eligible(v,'77',date(2026,10,30))[1]=='not_fresh'


def test_saved_import_is_idempotent(tmp_path):
    p=tmp_path/'exports';p.mkdir()
    (p/'trudvsem_77.json').write_text(json.dumps({'source':'trudvsem-open-vacancies','region':'Москва','signals':[sample(),sample()]}),encoding='utf-8')
    (p/'trudvsem_50.json').write_text(json.dumps({'source':'trudvsem-open-vacancies','region':'Московская область','signals':[]}),encoding='utf-8')
    db=tmp_path/'research.sqlite3'
    audit=run(p,db,False,date(2026,10,30))
    assert audit['audit']['validated_records']==1
    assert not db.exists()
    initial=run(p,db,True,date(2026,10,30))
    assert initial['import']['new_companies']==1 and initial['import']['new_signals']==1
    again=run(p,db,True,date(2026,10,30))
    assert again['import']['new_companies']==0 and again['import']['new_signals']==0
