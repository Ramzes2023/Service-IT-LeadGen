"""Explicit source-zone parsing and review deadlines before submission."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo

def submission_time(value):
    moment=datetime.fromisoformat(value.replace('Z','+00:00'))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('SUBMISSION_TIMEZONE_UNKNOWN')
    return moment

def moscow_time(value):
    return submission_time(value).astimezone(ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M МСК')

def manager_deadline(value,now):
    end=submission_time(value)
    if now.tzinfo is None:raise ValueError('CURRENT_TIMEZONE_UNKNOWN')
    remaining=end-now
    if remaining<=timedelta(0):return {'status':'SUBMISSION_CLOSED','deadline':None}
    desired=min(now+timedelta(days=1),end-timedelta(days=1))
    if desired<=now:desired=now+min(timedelta(hours=1),remaining/2)
    return {'status':'URGENT' if remaining<=timedelta(days=1) else 'READY','deadline':desired.isoformat()}
