from datetime import datetime, timezone
import pytest
from leadpilot import serviceit_bidzaar_source as s

ROW = {'link': 'https://bidzaar.com/process/light/00000000-0000-0000-0000-000000000001',
       'name': 'Поставка серверов', 'status': 'proposal',
       'acceptanceEndDate': '2026-12-01T00:00:00Z', 'inn': '7729058330',
       'deliveryAddresses': ['Москва, ул. Тверская, д. 1']}
NOW = datetime(2026,11,13,tzinfo=timezone.utc)

def test_service_delivery_and_deadline():
    assert s.decision(ROW,now=NOW)[0] == 'ELIGIBLE'
    assert s.decision({**ROW,'deliveryAddresses':['Самара']},now=NOW)[0] != 'ELIGIBLE'
    assert s.decision({**ROW,'deliveryAddresses':[],'location':['Москва']},now=NOW)[0] == 'GEO_UNKNOWN'
    assert s.decision({**ROW,'status':'ended'},now=NOW)[0] == 'NOT_OPEN'
    assert s.decision({**ROW,'name':'Поставка шаровых кранов'},now=NOW)[0] == 'OUT_OF_SCOPE'
    assert s.decision({**ROW,'acceptanceEndDate':'2026-01-01T00:00:00Z'},now=NOW)[0] == 'EXPIRED'

def test_same_timestamp_full_page_never_skips(monkeypatch):
    cursor = '2026-11-11T00:00:00Z'
    monkeypatch.setattr(s,'fetch_page',lambda *a:[{**ROW,'lastModified':cursor}]*100)
    saved=[]
    result=s.collect(None,'unused',cursor,lambda rows,c:saved.append(c))
    assert result['status']=='CURSOR_STALLED'
    assert saved==[cursor]

def test_cursor_persist_failure_stops_before_next_request(monkeypatch):
    calls=[]
    def fetch(*a):
        calls.append(1)
        return [{**ROW,'lastModified':'2026-11-12T00:00:00Z'}]*100
    monkeypatch.setattr(s,'fetch_page',fetch)
    def fail(*a): raise RuntimeError('storage unavailable')
    with pytest.raises(RuntimeError):s.collect(None,'unused','2026-11-11T00:00:00Z',fail)
    assert len(calls)==1

@pytest.mark.parametrize('url',['http://bidzaar.com/process/light/00000000-0000-0000-0000-000000000001','https://evil.test/process/light/00000000-0000-0000-0000-000000000001'])
def test_wrong_source_is_rejected(url):
    with pytest.raises(ValueError):s.identity({**ROW,'link':url})


def test_acrylic_shop_fixtures_are_not_it_equipment():
    assert s.decision({**ROW,'name':'Производство и поставка торгового оборудования из акрила'},now=NOW)[0]=='OUT_OF_SCOPE'
