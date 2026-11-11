"""Actual PostgreSQL durable B2B source receipts in disposable CI DB only."""
import os
import pytest

from leadpilot import serviceit_vps_pg_queue as pg
from leadpilot import serviceit_vps_b2b_bridge as src


@pytest.mark.skipif(os.environ.get("SERVICEIT_PG_CI_EPHEMERAL")!="YES",
                    reason="requires explicitly disposable GitHub Postgres")
def test_b2b_postgres_unique_source_and_one_use_post_receipt(monkeypatch):
    monkeypatch.setattr(pg,"secret_file",lambda env,name:"synthetic-ci-pg-password")
    notice={
        "schema":"serviceit-b2b-notice-v1","source":"B2B-Center",
        "email_message_id":"1a19b7630301bafb","tender_id":"1234567",
        "source_url":"https://www.b2b-center.ru/market/example-public-1s/tender-1234567/",
        "subject":"Оказание услуг по сопровождению системы 1С",
        "organizer":"ООО ПРИМЕР", "buyer_inn":None, "fns_msp":None,
        "region":None,"original_publication_verified":False,
        "contact_approved":False,"qualified_lead":False,
    }
    with pg.db({}) as connection:
        with connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS serviceit_vps_b2b_inbox")
        src.migrate(connection)
        assert src.accept(connection,notice,["1C"]) is True
        assert src.accept(connection,notice,["1C"]) is False
        candidate=src.read_candidate(connection)
        assert candidate[0]=="B2B-CENTER-1234567"
        assert candidate[1]["qualified_lead"] is False
        assert src.reserve_post(connection,candidate[0]) is True
        assert src.reserve_post(connection,candidate[0]) is False
        assert src.read_candidate(connection) is None
        assert src.statistics(connection)["POST_UNCERTAIN"] == 1
        assert src.attempted_24h(connection)==1
        src.confirm(connection,candidate[0],45)
        assert src.statistics(connection)["VERIFIED"]==1
        assert src.reserve_post(connection,candidate[0]) is False
        with connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS serviceit_vps_b2b_inbox")


@pytest.mark.skipif(os.environ.get("SERVICEIT_PG_CI_EPHEMERAL")!="YES",
                    reason="V19C retry requires explicitly disposable PostgreSQL")
def test_v19c_no_evidence_24h_upsert_and_immutable_verified_receipt(monkeypatch):
    """Prove PostgreSQL ON CONFLICT cooldown actually works, not just text."""
    from leadpilot.serviceit_b2b_v19_backfill import (
        TABLE, DDL, reserve, move,
    )
    from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
    monkeypatch.setattr(pg,"secret_file",lambda env,name:"synthetic-ci-pg-password")
    card=198765
    source="B2B-CENTER-4675475"
    old={k:"Old signed AI field" for k in AI_FIELDS}
    old[PREFIX+"AI_FULL_REPORT"]="Old evidence: 4675475"
    with pg.db({}) as connection:
        connection.execute(DDL)
        try:
            with connection.cursor() as cur:
                cur.execute("DELETE FROM "+TABLE+" WHERE card_id=%s",(card,))
            assert reserve(connection,card,source,old) is True
            move(connection,card,"RESERVED","NO_NEW_EVIDENCE")
            assert reserve(connection,card,source,old) is False
            with connection.cursor() as cur:
                cur.execute("UPDATE "+TABLE+" SET updated_at=now()-interval '25 hours' WHERE card_id=%s",(card,))
            assert reserve(connection,card,source,old) is True
            with connection.cursor() as cur:
                cur.execute("SELECT status, after_fields, salt, digest FROM "+TABLE+
                            " WHERE card_id=%s",(card,))
                assert cur.fetchone()==("RESERVED",None,None,None)
            desired=dict(old)
            desired[PREFIX+"AI_FULL_REPORT"] += " + new verified evidence"
            salt,digest=move(connection,card,"RESERVED","WRITE_INTENT",desired=desired)
            assert len(salt)==16 and len(digest)==32
            assert reserve(connection,card,source,old) is False
            move(connection,card,"WRITE_INTENT","VERIFIED")
            with connection.cursor() as cur:
                cur.execute("UPDATE "+TABLE+" SET updated_at=now()-interval '45 days' WHERE card_id=%s",(card,))
            assert reserve(connection,card,source,old) is False
            with connection.cursor() as cur:
                cur.execute("SELECT status, after_fields, salt, digest FROM "+TABLE+
                            " WHERE card_id=%s",(card,))
                status, after, saved_salt, saved_digest=cur.fetchone()
                assert status=="VERIFIED"
                assert after==desired
                assert bytes(saved_salt)==salt and bytes(saved_digest)==digest
        finally:
            with connection.cursor() as cur:
                cur.execute("DELETE FROM "+TABLE+" WHERE card_id=%s",(card,))
