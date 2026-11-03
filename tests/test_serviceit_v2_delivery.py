"""Real disposable PostgreSQL + mocked Bitrix v2 outbox transport tests."""
import json
import os

import httpx
import pytest

from leadpilot.serviceit_v2_store import init_schema
from leadpilot import serviceit_v2_delivery as delivery

TID = "4661617"
SRC = "B2B-CENTER-" + TID
WEBHOOK = "https://serviseit.bitrix24.ru/rest/2/SYNTHETICTEST123456"


@pytest.fixture
def conn():
    if os.getenv("SERVICEIT_V2_TEST_ENABLE") != "YES":
        pytest.skip("isolated Postgres fixture only")
    dsn = os.environ.get("SERVICEIT_V2_TEST_POSTGRES_DSN", "")
    if not ("host=127.0.0.1" in dsn
            and "dbname=serviceitv2_test" in dsn):
        pytest.fail("NEVER_USE_A_PRODUCTION_DATABASE")
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(dsn, autocommit=True) as db:
        init_schema(db)
        db.execute("""TRUNCATE serviceit_v2_crm_outbox, serviceit_v2_versions,
                     serviceit_v2_opportunities, serviceit_v2_messages,
                     serviceit_v2_message_failures, serviceit_v2_runtime CASCADE""")
        db.execute("""
          INSERT INTO serviceit_v2_opportunities
           (source,tender_id,canonical_url,subject,organizer,deadline_unverified,
            service_groups_json,current_version,review_status)
          VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,%s)
        """, (
            "B2B-Center", TID,
            "https://www.b2b-center.ru/market/soprovozhdenie-1s/tender-4661617/",
            "Сопровождение 1С:Бухгалтерия предприятия и 1С:ЗУП",
            "АО Техническая инспекция ЕЭС",
            '["1C"]', "a"*64, "TENDER_OPPORTUNITY_HOLD"))
        yield db


def status(db):
    return db.execute(
        "SELECT state,bitrix_item_id FROM serviceit_v2_crm_outbox WHERE operation_key=%s",
        (delivery.op_key(TID),)).fetchone()


class BitrixFake:
    def __init__(self, *, existing=False, post_error=False, wrong_readback=False,
                 unverified_existing=False):
        self.calls = []
        self.existing = existing
        self.post_error = post_error
        self.wrong_readback = wrong_readback
        self.unverified_existing = unverified_existing
        self.payload = None

    def __call__(self, req):
        method = str(req.url).split("/")[-1].removesuffix(".json")
        self.calls.append(method)
        if method == "crm.item.list":
            items = ([{"id": 7, "entityTypeId": 1062,
                       "categoryId": 21, delivery.SIGNAL: SRC}]
                     if self.existing else [])
            return httpx.Response(200, json={"result": {
                "items": items, "total": len(items)}})
        if method == "crm.item.add":
            self.payload = json.loads(req.content)["fields"]
            if self.post_error:
                return httpx.Response(503, text="blocked")
            return httpx.Response(200, json={"result": {"item": {"id": 31}}})
        if method == "crm.item.get":
            rid = int(json.loads(req.content)["id"])
            if self.existing:
                item = {
                    "id": rid, "entityTypeId": 1062, "categoryId": 21,
                    delivery.SIGNAL: "other-source" if self.unverified_existing else SRC,
                    "UF_CRM_13_SVCIT_SIGNAL_KIND": "tender-opportunity",
                }
            else:
                item = {**self.payload, "id": rid, "entityTypeId": 1062}
                if self.wrong_readback:
                    item[delivery.SIGNAL] = "WRONG"
            return httpx.Response(200, json={"result": {"item": item}})
        pytest.fail("unexpected network method " + method)


def setup(monkeypatch, conn):
    monkeypatch.setenv("SERVICEIT_V2_CRM_HOLD_ENABLED", "YES")
    monkeypatch.setenv("SERVICEIT_V2_CRM_WRITES_APPROVED", "YES")
    monkeypatch.setattr(delivery, "preflight", lambda _: {"status": "READ_ONLY_PASS"})
    staged = delivery.stage_one_approved(conn, TID, approval_ref="SVCIT-OWNER-20261102-A1")
    assert staged["status"] == "STAGED_FOR_APPROVED_HOLD"


def test_preview_and_approval_gates_fail_closed(conn, monkeypatch):
    assert delivery.process_one(conn, None, "", commit_live=False)["status"] == "CRM_WRITER_DISABLED"
    assert status(conn) is None
    with pytest.raises(delivery.V2DeliveryBlocked, match="APPROVAL_REFERENCE_REQUIRED"):
        delivery.stage_one_approved(conn, TID, approval_ref="YES")
    setup(monkeypatch, conn)
    monkeypatch.setenv("SERVICEIT_V2_CRM_HOLD_ENABLED", "NO")
    with pytest.raises(delivery.V2DeliveryBlocked, match="CRM_APPROVAL_NOT_ENABLED"):
        delivery.process_one(conn, None, "", commit_live=True)
    assert status(conn) == ("PENDING", None)


def test_real_one_post_and_exact_readback(conn, monkeypatch):
    setup(monkeypatch, conn)
    fake = BitrixFake()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        r = delivery.process_one(conn, client, WEBHOOK, commit_live=True)
    assert r["status"] == "ONE_HOLD_VERIFIED"
    assert r["card_id"] == 31 and r["qualified_leads"] == 0 and r["crm_writes"] == 1
    assert fake.calls == ["crm.item.list", "crm.item.add", "crm.item.get"]
    assert status(conn) == ("VERIFIED", 31)
    assert fake.payload["stageId"] == delivery.HOLD_STAGE
    assert fake.payload["UF_CRM_13_SVCIT_SIGNAL_KIND"] == "tender-opportunity"


def test_old_card_identity_is_adopted_without_duplicate_post(conn, monkeypatch):
    setup(monkeypatch, conn)
    fake = BitrixFake(existing=True)
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        r = delivery.process_one(conn, client, WEBHOOK, commit_live=True)
    assert r["status"] == "PREEXISTING_CARD_VERIFIED"
    assert r["card_id"] == 7 and r["crm_writes"] == 0
    assert fake.calls == ["crm.item.list", "crm.item.get"]
    assert status(conn) == ("VERIFIED", 7)


def test_http_failure_reserves_durably_without_retry(conn, monkeypatch):
    setup(monkeypatch, conn)
    fake = BitrixFake(post_error=True)
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        result = delivery.process_one(conn, client, WEBHOOK, commit_live=True)
        second = delivery.process_one(conn, client, WEBHOOK, commit_live=True)
    assert result["status"] == "CRM_POST_UNCERTAIN_NEEDS_READONLY_RECONCILIATION"
    assert second["status"] == "NO_PENDING_APPROVED_HOLDS"
    assert fake.calls.count("crm.item.add") == 1
    assert status(conn) == ("POST_UNCERTAIN", None)


def test_readback_failure_keeps_lock_and_never_retry(conn, monkeypatch):
    setup(monkeypatch, conn)
    fake = BitrixFake(wrong_readback=True)
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        result = delivery.process_one(conn, client, WEBHOOK, commit_live=True)
    assert result["status"] == "CRM_POST_UNCERTAIN_NEEDS_READONLY_RECONCILIATION"
    assert fake.calls.count("crm.item.add") == 1
    assert status(conn) == ("POST_UNCERTAIN", None)


def test_readonly_reconcile_recovers_old_or_delayed_card(conn, monkeypatch):
    setup(monkeypatch, conn)
    fake = BitrixFake(post_error=True)
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        delivery.process_one(conn, client, WEBHOOK, commit_live=True)
        initial = delivery.reconcile_one(conn, client, WEBHOOK, TID)
    assert initial["status"] == "NOT_FOUND_YET_MANUAL_REVIEW"
    assert status(conn) == ("POST_UNCERTAIN", None)
    found = BitrixFake(existing=True)
    with httpx.Client(transport=httpx.MockTransport(found)) as client:
        recovered = delivery.reconcile_one(conn, client, WEBHOOK, TID)
    assert recovered["status"] == "EXISTING_CARD_RECONCILED"
    assert found.calls == ["crm.item.list", "crm.item.get"]
    assert status(conn) == ("VERIFIED", 7)


def test_mismatched_preexisting_card_is_never_adopted(conn, monkeypatch):
    setup(monkeypatch, conn)
    fake = BitrixFake(existing=True, unverified_existing=True)
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        result = delivery.process_one(conn, client, WEBHOOK, commit_live=True)
    assert result["status"] == "CRM_POST_UNCERTAIN_NEEDS_READONLY_RECONCILIATION"
    assert fake.calls == ["crm.item.list", "crm.item.get"]
    assert status(conn) == ("POST_UNCERTAIN", None)


def test_no_automatic_lead_claim_from_source_only(conn, monkeypatch):
    conn.execute("""UPDATE serviceit_v2_opportunities
                    SET review_status='OUT_OF_SCOPE' WHERE tender_id=%s""", (TID,))
    with pytest.raises(delivery.V2DeliveryBlocked, match="NOT_APPROVED_FOR_TENDER_HOLD"):
        delivery.stage_one_approved(conn, TID, approval_ref="SVCIT-MANAGER-20261102-A")
    assert status(conn) is None
