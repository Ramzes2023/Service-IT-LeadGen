from datetime import datetime,timezone
from leadpilot.serviceit_deadline_expiry import parse_date,deadline,transition,STAGE,PREFIX
NOW=datetime(2026,11,15,12,tzinfo=timezone.utc)
VALID={STAGE,'review','won'}
def test_timezone_and_invalid_dates():
 assert parse_date('15.11.2026 15:00 МСК')==NOW
 assert parse_date('2026-11-15T15:00:00+03:00')==NOW
 for x in ('2026-11-15','2026-11-15T12:00:00','31.02.2026 12:00 МСК','не указан'):assert parse_date(x) is None

def test_source_overrides_stale_prose_and_missing_is_not_invented():
 item={'UF_AI_FULL_REPORT':'Подача до: 01.11.2026 12:00 МСК'}
 assert deadline(item,{'acceptanceEndDate':'2026-11-20T12:00:00Z'})==parse_date('2026-11-20T12:00:00Z')
 assert deadline(item,{}) is None
 assert deadline({'deadline':'2026-11-01T12:00:00Z'}) is None

def test_conflicting_passport_dates_rejected():
 assert deadline({'UF_AI_FULL_REPORT':'Подача до: 01.11.2026 12:00 МСК','UF_AI_MANAGER_BRIEF':'Подача до: 02.11.2026 12:00 МСК'}) is None

def test_expiration_boundary_and_idempotent_prefix():
 item={'stageId':'review','title':'★ Приоритет'}
 patch=transition(item,NOW,NOW,None,VALID,{'won'})
 assert patch=={'stageId':STAGE,'title':PREFIX+'★ Приоритет'}
 assert transition(patch,NOW,NOW,('review','EXPIRED'),VALID,{'won'})==patch
 assert transition(item,None,NOW,None,VALID,{'won'})=={}
 assert transition({'stageId':'won','title':'done'},NOW,NOW,None,VALID,{'won'})=={}

def test_extension_restores_owned_card_only():
 later=parse_date('2026-11-20T12:00:00Z')
 item={'stageId':STAGE,'title':PREFIX+'edited title'}
 assert transition(item,later,NOW,('review','EXPIRED'),VALID,{'won'})=={'stageId':'review','title':'edited title'}
 assert transition(item,later,NOW,None,VALID,{'won'})=={}
 assert transition({'stageId':'review','title':'manually moved'},later,NOW,('review','EXPIRED'),VALID,{'won'})=={}
 assert transition(item,later,NOW,('deleted','EXPIRED'),VALID,{'won'})=={}
