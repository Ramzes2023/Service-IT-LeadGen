"""Fail-closed FNS MSP + EGRUL evidence policy for Service IT V2.

Do not call undocumented endpoints or pretend a local XML came from FNS.
FNS official data proves registry membership at a particular snapshot date,
NOT buyer intent, contractual authority or the current EGRUL legal address.

All records in this module are hypotheses/eligibility reviews; they NEVER
write CRM or automatically promote a tender to QUALIFIED_LEAD.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import re
from typing import Optional
from urllib.parse import urlsplit

from defusedxml import ElementTree as SafeET

OFFICIAL_MSP_PASSPORT = "https://www.nalog.gov.ru/opendata/7707329152-rsmp/"
ACCEPTED_REGIONS = frozenset({"77", "50"})
MAX_XML_BYTES = 256_000
MISSING = "MISSING_EVIDENCE"


class EvidenceRejected(ValueError):
    pass


def require(value, reason):
    if not value:
        raise EvidenceRejected(reason)


def checked_inn(value: object) -> str:
    """Return valid Russian legal entity (10) or sole proprietor (12) INN."""
    require(isinstance(value, str) and re.fullmatch(r"\d{10}|\d{12}", value),
            "INN_FORMAT_INVALID")
    digits = [int(c) for c in value]
    if len(digits) == 10:
        check = sum(a * b for a, b in zip(digits[:9], (2, 4, 10, 3, 5, 9, 4, 6, 8))) % 11 % 10
        require(digits[9] == check, "INN_CHECKSUM_FAILED")
    else:
        check11 = sum(a * b for a, b in zip(digits[:10],
                                             (7, 2, 4, 10, 3, 5, 9, 4, 6, 8))) % 11 % 10
        check12 = sum(a * b for a, b in zip(digits[:11],
                                             (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8))) % 11 % 10
        require(digits[10] == check11 and digits[11] == check12,
                "INN_CHECKSUM_FAILED")
    return value


def _date_ru(value: str) -> date:
    require(isinstance(value, str) and re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value),
            "FNS_SNAPSHOT_DATE_INVALID")
    try:
        d, m, y = map(int, value.split("."))
        return date(y, m, d)
    except ValueError:
        raise EvidenceRejected("FNS_SNAPSHOT_DATE_INVALID") from None


def _hash(value):
    require(isinstance(value, str) and
            re.fullmatch(r"[0-9a-f]{64}", value), "SOURCE_DIGEST_MISSING")
    return value


def official_fns_source(value: str) -> bool:
    """A passport reference, never proof that any given file is authentic."""
    try:
        u = urlsplit(value)
        return (u.scheme == "https"
                and u.hostname in {"www.nalog.gov.ru", "nalog.gov.ru"}
                and u.path.rstrip("/") == "/opendata/7707329152-rsmp"
                and not (u.username or u.password or u.query or u.fragment))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class MSPRecord:
    inn: str
    legal_type: str          # legal_entity / sole_proprietor
    category: str            # MICRO / SMALL / MEDIUM
    registry_region: str
    snapshot_date: date
    organization: str
    xml_sha256: str
    passport_url: str


def extract_msp_record(xml: bytes, *, target_inn: str,
                       passport_url: str = OFFICIAL_MSP_PASSPORT,
                       expected_xml_sha256: str) -> MSPRecord:
    """Inspect a bounded FNS XML member with externally pinned digest.

    The checksum pins the byte identity against a trusted out-of-band copy;
    it does NOT establish provenance by itself. Caller must authenticate the
    official ZIP and XSD separately and persist provenance before eligibility.
    """
    target_inn = checked_inn(target_inn)
    require(official_fns_source(passport_url), "FNS_PASSPORT_NOT_OFFICIAL")
    require(isinstance(xml, bytes) and 0 < len(xml) <= MAX_XML_BYTES,
            "FNS_XML_SIZE_INVALID")
    require(_hash(expected_xml_sha256) == sha256(xml).hexdigest(),
            "FNS_XML_DIGEST_MISMATCH")
    try:
        root = SafeET.fromstring(xml)
    except (SafeET.ParseError, ValueError, TypeError):
        raise EvidenceRejected("FNS_XML_PARSE_REJECTED") from None
    require(root.tag == "Файл" and root.get("ТипИнф") == "РЕЕСТРМСП"
            and root.get("ВерсФорм") in {"4.06", "4.07", "4.08"},
            "FNS_XML_NOT_MSP")
    documents = root.findall("Документ")
    require(documents and len(documents) <= 1000
            and root.get("КолДок") == str(len(documents)),
            "FNS_XML_DOCUMENT_COUNT_MISMATCH")
    matches = []
    for doc in documents:
        org, ip = doc.find("ОргВклМСП"), doc.find("ИПВклМСП")
        if (org is None) == (ip is None):
            raise EvidenceRejected("FNS_XML_BUYER_TYPE_AMBIGUOUS")
        entity = org if org is not None else ip
        inn = entity.get("ИННЮЛ") if org is not None else entity.get("ИННФЛ")
        if inn != target_inn:
            continue
        checked_inn(inn)
        require(doc.get("ВидСубМСП") in ("1", "2", "3"),
                "FNS_SUBJECT_TYPE_INVALID")
        legal_type = "legal_entity" if org is not None else "sole_proprietor"
        require((doc.get("ВидСубМСП") == "1") == (legal_type == "legal_entity"),
                "FNS_BUYER_TYPE_MISMATCH")
        category = {"1": "MICRO", "2": "SMALL", "3": "MEDIUM"}.get(doc.get("КатСубМСП"))
        require(category is not None, "FNS_CATEGORY_UNKNOWN")
        region_element = doc.find("СведМН")
        region = region_element.get("КодРегион") if region_element is not None else None
        require(isinstance(region, str) and re.fullmatch(r"\d{2}", region),
                "FNS_REGION_MISSING")
        when = _date_ru(doc.get("ДатаСост"))
        organization = entity.get("НаимОрг") or entity.get("НаимОргСокр") or ""
        require(isinstance(organization, str) and len(organization) <= 255,
                "FNS_COMPANY_NAME_INVALID")
        matches.append(MSPRecord(inn=inn,legal_type=legal_type,
                                 category=category,registry_region=region,
                                 snapshot_date=when,organization=organization,
                                 xml_sha256=sha256(xml).hexdigest(),
                                 passport_url=passport_url))
    require(len(matches) == 1, "FNS_MATCH_NOT_UNIQUE_OR_NOT_FOUND")
    return matches[0]


@dataclass(frozen=True)
class IndependentEvidence:
    """Independently audited, provenance-pinned source facts.

    These booleans are NOT inferred from an email or an AI. Keep as false if
    official retrieval, permission, documentary evidence, and checks are absent.
    """
    official_zip_provenance_verified: bool = False
    official_egrul_provenance_verified: bool = False
    egrul_inn: Optional[str] = None
    egrul_legal_region: Optional[str] = None
    egrul_active_legal_entity: bool = False
    buyer_is_same_legal_entity: bool = False
    original_notice_verified_active: bool = False
    original_it_demand_verified: bool = False
    authorized_corporate_contact_verified: bool = False
    bitrix_existing_customer_check_clear: bool = False
    bitrix_global_duplicate_check_clear: bool = False
    bitrix_stoplist_check_clear: bool = False
    manager_approval_verified: bool = False


@dataclass(frozen=True)
class QualificationDecision:
    status: str
    reasons: tuple[str, ...]
    verified_lead: bool
    eligible_region: bool
    eligible_small_business: bool


def decide(msp: MSPRecord | None, facts: IndependentEvidence,
           *, as_of: date, max_snapshot_age_days: int = 45) -> QualificationDecision:
    """A reason-coded policy decision, never an instruction to write CRM."""
    require(type(as_of) is date
            and type(max_snapshot_age_days) is int
            and 1 <= max_snapshot_age_days <= 62, "POLICY_DATE_INVALID")
    require(isinstance(facts, IndependentEvidence), "FACTS_NOT_VERIFIED")
    if msp is None:
        return QualificationDecision("REVIEW_REQUIRED", ("FNS_MSP_UNKNOWN",),
                                     False, False, False)
    reasons = []
    try:
        checked_inn(msp.inn)
    except EvidenceRejected:
        reasons.append("FNS_INN_INVALID")
    if not facts.official_zip_provenance_verified:
        reasons.append("OFFICIAL_MSP_SNAPSHOT_NOT_AUTHENTICATED")
    if msp.snapshot_date > as_of or as_of - msp.snapshot_date > timedelta(days=max_snapshot_age_days):
        reasons.append("FNS_SNAPSHOT_STALE_OR_FUTURE")
    eligible_small = msp.category in {"MICRO", "SMALL"}
    if not eligible_small:
        reasons.append("NOT_MICRO_OR_SMALL")
    if msp.legal_type != "legal_entity" or len(msp.inn) != 10:
        reasons.append("REQUIRES_PRIVATE_INDIVIDUAL_REVIEW")
    if not facts.official_egrul_provenance_verified:
        reasons.append("EGRUL_NOT_AUTHENTICATED")
    if not facts.egrul_active_legal_entity:
        reasons.append("LEGAL_ENTITY_NOT_CONFIRMED_ACTIVE")
    if facts.egrul_inn != msp.inn:
        reasons.append("EGRUL_INN_MISMATCH")
    if not facts.buyer_is_same_legal_entity:
        reasons.append("REAL_BUYER_IDENTITY_NOT_VERIFIED")
    region = (msp.registry_region in ACCEPTED_REGIONS and
              facts.egrul_legal_region in ACCEPTED_REGIONS and
              facts.egrul_legal_region == msp.registry_region)
    if not region:
        reasons.append("LEGAL_REGION_77_50_NOT_VERIFIED")
    if not facts.original_notice_verified_active:
        reasons.append("ORIGINAL_NOTICE_ACTIVE_NOT_VERIFIED")
    if not facts.original_it_demand_verified:
        reasons.append("IT_DEMAND_NOT_VERIFIED")
    if not facts.authorized_corporate_contact_verified:
        reasons.append("CORPORATE_CONTACT_UNVERIFIED")
    if not facts.bitrix_existing_customer_check_clear:
        reasons.append("EXISTING_CUSTOMER_CHECK_REQUIRED")
    if not facts.bitrix_global_duplicate_check_clear:
        reasons.append("CRM_DUPLICATE_CHECK_REQUIRED")
    if not facts.bitrix_stoplist_check_clear:
        reasons.append("CRM_STOPLIST_CHECK_REQUIRED")
    if not facts.manager_approval_verified:
        reasons.append("MANAGER_APPROVAL_REQUIRED")
    return QualificationDecision(
        status="ELIGIBLE_FOR_SEPARATELY_APPROVED_LEAD_DELIVERY" if not reasons else "REVIEW_REQUIRED",
        reasons=tuple(reasons), verified_lead=not reasons,
        eligible_region=region, eligible_small_business=eligible_small)
