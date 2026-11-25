from datetime import datetime,timezone
import pytest
from leadpilot.serviceit_tender_manager_timing import moscow_time,manager_deadline,submission_time

def test_utc_is_converted_not_relabelled():
    assert moscow_time('2026-11-16T13:00:00Z')=='16.11.2026 16:00 МСК'

def test_unknown_timezone_is_rejected():
    with pytest.raises(ValueError,match='TIMEZONE'):moscow_time('2026-11-16T13:00:00')

@pytest.mark.parametrize('hours',[0.01,1,12,24,48,168])
def test_manager_deadline_is_strictly_before_submission(hours):
    from datetime import timedelta
    now=datetime(2026,11,15,tzinfo=timezone.utc);end=now+timedelta(hours=hours)
    result=manager_deadline(end.isoformat(),now)
    assert now<submission_time(result['deadline'])<end

def test_closed_submission_never_receives_future_deadline():
    assert manager_deadline('2026-11-14T13:00:00Z',datetime(2026,11,15,tzinfo=timezone.utc))['deadline'] is None

def test_deadline_has_bitrix_second_precision():
 now=datetime(2026,11,15,10,5,30,123456,tzinfo=timezone.utc)
 value=manager_deadline('2026-11-25T13:00:00Z',now)['deadline']
 assert submission_time(value).microsecond==0

def test_less_than_one_second_remaining_does_not_create_late_task():
 now=datetime(2026,11,15,10,5,30,800000,tzinfo=timezone.utc)
 assert manager_deadline('2026-11-15T10:05:30.9Z',now)['deadline'] is None
