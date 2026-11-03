"""Disposable PG tests for FNS review persistence; no real clients and no CRM."""
from dataclasses import replace
from datetime import date
import json
import os

import pytest

from leadpilot.serviceit_v2_fns_qualification import MSPRecord, IndependentEvidence
from leadpilot.serviceit_v2_buyer_reviews import persist_buyer_review, ReviewStoreBlocked
from leadpilot.serviceit_v2_store import init_schema


@pytest.fixture
def conn():
    if os.environ.get("SERVICEIT_V2_TEST_ENABLE") != "YES":
        pytest.skip("disposable PostgreSQL only")
    dsn = os.environ.get("SERVICEIT_V2_TEST_POSTGRES_DSN", "")
    if not ("host=127.0.0.1" in dsn
            and "dbname=serviceitv2_test" in dsn):
        pytest.fail("PRODUCTION_DB_FORBIDDEN")
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(dsn,autocommit=True) as db:
        init_schema(db)
        db.execute("""TRUNCATE serviceit_v2_buyer_reviews, serviceit_v2_crm_outbox,
            serviceit_v2_versions, serviceit_v2_opportunities,
            serviceit_v2_messages, serviceit_v2_message_failures,
            serviceit_v2_runtime CASCADE""")
        db.execute("""
          INSERT INTO serviceit_v2_opportunities
          (source,tender_id,canonical_url,subject,organizer,service_groups_json,
           current_version,review_status)
          VALUES('B2B-Center','4661617',
             'https://www.b2b-center.ru/market/soprovozhdenie-1s/tender-4661617/',
             'Сопровождение 1С:Бухгалтерия', 'ООО ВЫДУМАННАЯ',
             '["1C"]',%s,'TENDER_OPPORTUNITY_HOLD')
        """, ("a"*64,))
        yield db


def msp():
    return MSPRecord(inn="7700000129",legal_type="legal_entity",
                     category="MICRO",registry_region="77",
                     snapshot_date=date(2026,10,10),
                     organization="ООО ВЫДУМАННАЯ",
                     xml_sha256="b"*64,
                     passport_url="https://www.nalog.gov.ru/opendata/7707329152-rsmp/")


def review(db):
    return db.execute("""
        SELECT decision,reason_codes_json,qualified_lead
        FROM serviceit_v2_buyer_reviews
        WHERE source=%s AND tender_id=%s
    """, ("B2B-Center","4661617")).fetchone()


def test_unknown_original_buyer_remains_hard_hold(conn):
    r = persist_buyer_review(
        conn,tender_id="4661617",msp=msp(),evidence=IndependentEvidence(),
        as_of=date(2026,11,2))
    assert r["status"] == "REVIEW_REQUIRED"
    assert "REAL_BUYER_IDENTITY_NOT_VERIFIED" in r["reason_codes"]
    row = review(conn)
    assert row[0] == "REVIEW_REQUIRED" and row[2] is False
    assert "CRM_DUPLICATE_CHECK_REQUIRED" in json.loads(row[1])
    assert conn.execute("SELECT count(*) FROM serviceit_v2_crm_outbox").fetchone()[0] == 0


def test_all_synthetic_flags_still_never_promote_or_create_crm_event(conn):
    # This is deliberately an asserted test fixture, NOT a real FNS/EGRUL check.
    fake = IndependentEvidence(
        official_zip_provenance_verified=True,official_egrul_provenance_verified=True,
        egrul_inn="7700000129",egrul_legal_region="77",
        egrul_active_legal_entity=True,buyer_is_same_legal_entity=True,
        original_notice_verified_active=True,original_it_demand_verified=True,
        authorized_corporate_contact_verified=True,
        bitrix_existing_customer_check_clear=True,
        bitrix_global_duplicate_check_clear=True,
        bitrix_stoplist_check_clear=True,
        manager_approval_verified=True)
    result = persist_buyer_review(
        conn,tender_id="4661617",msp=msp(),evidence=fake,
        as_of=date(2026,11,2))
    assert result["status"] == "POLICY_CANDIDATE_PENDING_AUTHORIZATION"
    assert result["qualified_leads"] == result["crm_writes"] == 0
    assert "INDEPENDENT_ARTIFACT_AUDIT_AND_CRM_APPROVAL_NOT_IMPLEMENTED" in result["reason_codes"]
    assert review(conn)[2] is False
    assert conn.execute("SELECT count(*) FROM serviceit_v2_crm_outbox").fetchone()[0] == 0


def test_repeat_reconciles_to_updated_review_not_duplicate(conn):
    persist_buyer_review(conn,tender_id="4661617",msp=msp(),
                         evidence=IndependentEvidence(),as_of=date(2026,11,2))
    persist_buyer_review(conn,tender_id="4661617",msp=replace(msp(),category="SMALL"),
                         evidence=IndependentEvidence(),as_of=date(2026,11,2))
    assert conn.execute("SELECT count(*) FROM serviceit_v2_buyer_reviews").fetchone()[0] == 1
    assert conn.execute("SELECT fns_category FROM serviceit_v2_buyer_reviews").fetchone()[0] == "SMALL"


def test_no_lead_promotion_possible_at_database_layer(conn):
    persist_buyer_review(conn,tender_id="4661617",msp=msp(),
                         evidence=IndependentEvidence(),as_of=date(2026,11,2))
    with pytest.raises(Exception):
        conn.execute("UPDATE serviceit_v2_buyer_reviews SET qualified_lead=TRUE")
    assert review(conn)[2] is False


def test_unknown_procurement_cannot_attach_an_fns_company(conn):
    with pytest.raises(ReviewStoreBlocked, match="PROCUREMENT_NOT_FOUND"):
        persist_buyer_review(conn,tender_id="9999999",msp=msp(),
                             evidence=IndependentEvidence(),as_of=date(2026,11,2))
    assert conn.execute("SELECT count(*) FROM serviceit_v2_buyer_reviews").fetchone()[0] == 0
