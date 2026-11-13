import os,re
import httpx
from leadpilot.serviceit_vps_pg_queue import db,secret_file
from leadpilot.serviceit_enrichment_worker import Rest,normalized
with db(dict(os.environ)) as conn,httpx.Client(trust_env=False,timeout=20) as client:
    rest=Rest(secret_file(dict(os.environ),'SERVICEIT_BITRIX_WEBHOOK'),client)
    for status,count in conn.execute('SELECT status,count(*) FROM serviceit_extended_documents GROUP BY status').fetchall():print('EXT_DOCUMENTS_'+status+'='+str(count))
    for reason,count in conn.execute("SELECT coalesce(error,'NONE'),count(*) FROM serviceit_extended_documents WHERE status IN ('FAILED','RETRY') GROUP BY 1").fetchall():
        if re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}',reason):print('EXT_DOC_ERROR_'+reason+'='+str(count))
    for card,total,read in conn.execute("SELECT card_id,count(*),count(*) FILTER (WHERE status='READ') FROM serviceit_extended_documents GROUP BY card_id ORDER BY card_id").fetchall():
        print('EXT_CARD_'+str(card)+'_FILES_TOTAL='+str(total));print('EXT_CARD_'+str(card)+'_FILES_READ='+str(read))
    print('EXT_LEGACY_DOC_READ='+str(conn.execute("SELECT count(*) FROM serviceit_extended_documents WHERE name ILIKE '%.doc' AND status='READ'").fetchone()[0]))
    print('EXT_CARDS_OVER_SIX_COMPLETE='+str(conn.execute("SELECT count(*) FROM (SELECT card_id FROM serviceit_extended_documents GROUP BY card_id HAVING count(*)>6 AND bool_and(status='READ')) a").fetchone()[0]))
    for status,count in conn.execute("SELECT coalesce(result->>'status','PENDING'),count(*) FROM serviceit_company_contact_cache GROUP BY 1").fetchall():print('EXT_COMPANIES_'+status+'='+str(count))
    count=0;bad=0
    for card,expected,status in conn.execute('SELECT card_id,expected,status FROM serviceit_extended_evidence_receipts').fetchall():
        actual=normalized(rest('crm.item.get',{'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
        ok=str(actual.get('categoryId'))=='21' and status=='VERIFIED' and all(str(actual.get(k,''))==str(v) for k,v in expected.items())
        count+=ok;bad+=not ok
    print('EXT_CARDS_READBACK='+str(count));print('EXT_CARDS_MISMATCH='+str(bad))
    print('EXT_CRM_WRITES=0')
    if bad:raise SystemExit(2)
