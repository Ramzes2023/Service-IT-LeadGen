"""Persist independently reviewed buyer evidence without qualifying a lead.

Local, approved research-only staging. Never writes or reads customer CRM,
never changes procurement identity, and never turns asserted boolean metadata
into an automatically approved CRM instruction.
"""
from __future__ import annotations

from datetime import date
import json

from .serviceit_v2_fns_qualification import (
    EvidenceRejected, IndependentEvidence, MSPRecord, decide, checked_inn,
)

SOURCE = "B2B-Center"


class ReviewStoreBlocked(ValueError):
    pass


def persist_buyer_review(conn, *, tender_id: str, msp: MSPRecord,
                         evidence: IndependentEvidence, as_of: date):
    if not isinstance(msp, MSPRecord) or not isinstance(evidence, IndependentEvidence):
        raise ReviewStoreBlocked("EVIDENCE_OBJECTS_REQUIRED")
    checked_inn(msp.inn)
    result = decide(msp, evidence, as_of=as_of)
    # Intentionally never persist QUALIFIED_LEAD based on a caller-asserted
    # evidence flag: the future official download + EGRUL + manager verification
    # adapter will require independent provenance before promotion.
    status = ("POLICY_CANDIDATE_PENDING_AUTHORIZATION"
              if result.verified_lead else "REVIEW_REQUIRED")
    with conn.transaction():
        row = conn.execute("""
            SELECT review_status,qualified_lead FROM serviceit_v2_opportunities
            WHERE source=%s AND tender_id=%s FOR SHARE
        """, (SOURCE, tender_id)).fetchone()
        if row is None:
            raise ReviewStoreBlocked("PROCUREMENT_NOT_FOUND")
        if row[0] != "TENDER_OPPORTUNITY_HOLD" or row[1] is not False:
            raise ReviewStoreBlocked("PROCUREMENT_NOT_UNQUALIFIED_HOLD")
        reasons = list(result.reasons)
        if not reasons:
            reasons = ["INDEPENDENT_ARTIFACT_AUDIT_AND_CRM_APPROVAL_NOT_IMPLEMENTED"]
        conn.execute("""
            INSERT INTO serviceit_v2_buyer_reviews
               (source,tender_id,buyer_inn,fns_snapshot,fns_category,fns_region,
                source_xml_sha256,decision,reason_codes_json,qualified_lead)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE)
            ON CONFLICT (source,tender_id) DO UPDATE SET
               buyer_inn=EXCLUDED.buyer_inn,
               fns_snapshot=EXCLUDED.fns_snapshot,
               fns_category=EXCLUDED.fns_category,
               fns_region=EXCLUDED.fns_region,
               source_xml_sha256=EXCLUDED.source_xml_sha256,
               decision=EXCLUDED.decision,
               reason_codes_json=EXCLUDED.reason_codes_json,
               reviewed_at=now(),qualified_lead=FALSE
        """, (SOURCE,tender_id,msp.inn,msp.snapshot_date,msp.category,
              msp.registry_region,msp.xml_sha256,status,
              json.dumps(reasons,ensure_ascii=False)))
    return {"status": status, "reason_codes": reasons,
            "qualified_leads": 0, "crm_writes": 0,
            "evidence_snapshot_date": msp.snapshot_date.isoformat()}
