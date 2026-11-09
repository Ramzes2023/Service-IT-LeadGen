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
