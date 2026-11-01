"""Pre-approval Bitrix24 handoff DRY RUN. This module CANNOT send REST requests.

Produces logical, synthetic-only lead/task intents with a stable operation ID.
It intentionally has no live Bitrix field mappings, webhook, network client,
CRM write method, login, or contact details. Not production authorization.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .verified_import import is_valid_inn

CONTRACT_VERSION = "serviceit-bitrix-intent-v1"
REQUIRED_LOGICAL_FIELDS = (
    "company_inn", "company_name", "region", "signal_id",
    "signal_date", "signal_source", "signal_url", "suggested_service",
    "lead_score", "score_version", "manager_next_action",
)
UNCONFIRMED_GATES = (
    "ADMIN_API_READ_PERMISSION",
    "REAL_PORTAL_FIELD_CODES",
    "ISOLATED_TEST_ENTITY",
    "COMPANY_DUPLICATE_AND_STOP_RULES",
    "EXPLICIT_SANDBOX_WRITE_PERMISSION",
)
SYNTHETIC_SOURCES = frozenset({"synthetic-fixture", "synthetic-second"})


def _hold(reason: str, key: str | None = None) -> dict:
    return {"status": "HOLD", "reason": reason, "idempotency_key": key,
            "crm_writes": "NO"}


def _fingerprint(inn: str, signal_id: str) -> str:
    payload = f"{CONTRACT_VERSION}\x00{inn}\x00{signal_id}".encode("utf-8")
    return "DEMO-IDEMP-" + sha256(payload).hexdigest()[:32]


def _synthetic_card_only(card: object, today: date) -> dict:
    if not isinstance(card, dict) or card.get("synthetic") is not True:
        return _hold("NOT_EXPLICITLY_SYNTHETIC")
    if any(k in card for k in ("phone", "email", "contact", "webhook", "secret",
                                "access_token", "client_name", "crm_external_id")):
        return _hold("CUSTOMER_OR_SECRET_FIELDS_FORBIDDEN")
    inn, sid = card.get("inn"), card.get("signal_id")
    if (not isinstance(inn, str) or not re.fullmatch(r"\d{10}|\d{12}", inn)
            or not is_valid_inn(inn)):
        return _hold("VALID_TEST_INN_REQUIRED")
    if not isinstance(sid, str) or not re.fullmatch(r"DEMO-[A-Za-z0-9_-]{4,40}", sid):
        return _hold("DEMO_SIGNAL_ID_REQUIRED")
    key = _fingerprint(inn, sid)
    if (card.get("existing_customer") is not False
            or card.get("manual_review") is not False
            or card.get("review_status") != "approved_demo"):
        return _hold("REVIEW_OR_EXISTING_CLIENT_GATE", key)
    stops = card.get("stops")
    if not isinstance(stops, list) or any(not isinstance(x, str) for x in stops):
        return _hold("STOP_CHANNELS_UNVERIFIED", key)
    if stops:
        # No contact is authorized in dry-run regardless of stop channel.
        return _hold("STOP_LIST_APPLIES", key)
    name = card.get("company_name")
    if (not isinstance(name, str) or not re.fullmatch(r"DEMO[ -][\w .-]{4,80}", name)
            or any(x in name for x in ("\n", "\r", "@"))):
        return _hold("DEMO_COMPANY_NAME_REQUIRED", key)
    region = card.get("region")
    if region not in ("Москва", "Московская область"):
        return _hold("OUTSIDE_PILOT_SCOPE", key)
    source = card.get("signal_source")
    url = card.get("signal_url")
    try:
        split = urlsplit(url)
        if (source not in SYNTHETIC_SOURCES or split.scheme != "https"
                or split.hostname != "example.invalid" or split.username or split.password
                or split.port not in (None, 443) or split.query or split.fragment
                or not split.path.startswith("/serviceit-demo/")):
            return _hold("NON_SYNTHETIC_EVIDENCE_FORBIDDEN", key)
        signal_date = date.fromisoformat(card["signal_date"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return _hold("INVALID_DATE_OR_SYNTHETIC_URL", key)
    if not 0 <= (today - signal_date).days <= 30:
        return _hold("SIGNAL_NOT_CURRENT", key)
    score = card.get("lead_score")
    if type(score) is not int or not 0 <= score <= 100:
        return _hold("SCORE_NOT_VALIDATED", key)
    ver, svc, action = (card.get("score_version"), card.get("suggested_service"),
                        card.get("manager_next_action"))
    if (not isinstance(ver, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,30}", ver)
            or svc not in ("1С", "IT-аутсорсинг", "ККТ")
            or not isinstance(action, str) or not 8 <= len(action) <= 120
            or "@" in action or "http" in action.lower() or "\n" in action):
        return _hold("LOGICAL_FIELD_VALIDATION_FAILED", key)
    return {
        "status": "DRY_RUN_INTENT_ONLY", "idempotency_key": key,
        "logical_fields": {
            "company_inn": inn, "company_name": name, "region": region,
            "signal_id": sid, "signal_date": signal_date.isoformat(),
            "signal_source": source, "signal_url": url,
            "suggested_service": svc, "lead_score": score,
            "score_version": ver, "manager_next_action": action,
        },
        "crm_entity_type": "UNCONFIRMED",
        "actual_crm_field_codes": "UNCONFIRMED",
        "crm_writes": "NO", "real_customer_contact": False,
    }


def build_offline_handoff(cards: list[dict], *, today: date | None = None) -> dict:
    """Exercise de-duplication by legal INN + independent signal with zero I/O."""
    if not isinstance(cards, list) or len(cards) > 250:
        raise ValueError("Up to 250 synthetic cards required")
    now = today or date.today()
    unique: dict[tuple[str, str], dict] = {}
    outcomes = []
    for card in cards:
        outcome = _synthetic_card_only(card, now)
        if outcome["status"] == "DRY_RUN_INTENT_ONLY":
            f = outcome["logical_fields"]
            identity = (f["company_inn"], f["signal_id"])
            old = unique.get(identity)
            if old is not None:
                if old["logical_fields"] != f:
                    outcome = _hold("CONFLICTING_SAME_SIGNAL_ID",
                                    outcome["idempotency_key"])
                else:
                    outcome = {"status": "DUPLICATE_SIMULATION_SKIPPED",
                               "idempotency_key": outcome["idempotency_key"],
                               "crm_writes": "NO"}
            else:
                unique[identity] = outcome
        outcomes.append(outcome)
    holds = sum(x["status"] == "HOLD" for x in outcomes)
    return {
        "contract_version": CONTRACT_VERSION, "mode": "SYNTHETIC_OFFLINE_ONLY",
        "portal": "serviseit.bitrix24.ru", "crm_writes": "NO",
        "real_customer_records_read": 0, "outbound_messages": 0,
        "proposed_company_count": len({item["logical_fields"]["company_inn"]
                                      for item in unique.values()}),
        "independent_signal_count": len(unique),
        "held_count": holds,
        "deduplicated_count": sum(x["status"] == "DUPLICATE_SIMULATION_SKIPPED"
                                  for x in outcomes),
        "unconfirmed_requirements": list(UNCONFIRMED_GATES),
        "field_mapping_ready_for_live_write": False,
        "operations": outcomes,
        "production_activated": False,
    }


def synthetic_fixture(now: date | None = None) -> list[dict]:
    today = now or date.today()
    item = {
        "synthetic": True, "inn": "7700000009", "signal_id": "DEMO-ITREQ-001",
        "company_name": "DEMO COMPANY-001", "region": "Москва",
        "existing_customer": False, "manual_review": False,
        "review_status": "approved_demo", "stops": [],
        "signal_date": (today - timedelta(days=1)).isoformat(),
        "signal_source": "synthetic-fixture",
        "signal_url": "https://example.invalid/serviceit-demo/request/001",
        "suggested_service": "1С", "lead_score": 86, "score_version": "demo-v1",
        "manager_next_action": "Проверить синтетический запрос 1С",
    }
    second = {**item, "signal_id": "DEMO-ITREQ-002",
              "signal_source": "synthetic-second",
              "signal_url": "https://example.invalid/serviceit-demo/request/002"}
    stopped = {**item, "inn": "7800000002",
               "company_name": "DEMO COMPANY-002",
               "signal_id": "DEMO-ITREQ-003", "stops": ["all"]}
    return [item, dict(item), second, stopped]


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bitrix24 offline handoff proposal, no REST")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    root = (Path(os.environ.get("LOCALAPPDATA") or Path.home())
            / "ServiceIT" / "Bitrix24-DryRun").resolve()
    output = Path(args.output).resolve()
    if (output.suffix.lower() != ".json" or not output.is_relative_to(root)
            or output.exists()):
        parser.error("Output must be a new JSON within ServiceIT/Bitrix24-DryRun")
    report = build_offline_handoff(synthetic_fixture())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"BITRIX_HANDOFF_DRYRUN=PASS; SIGNALS={report['independent_signal_count']}; "
          f"DUPLICATES={report['deduplicated_count']}; HELDS={report['held_count']}")
    print("BITRIX_HANDOFF_CRM_WRITES=NO; REAL_CLIENT_RECORDS=0; LIVE_AUTH=PENDING")
    print("BITRIX_HANDOFF_REPORT=" + str(output))


if __name__ == "__main__":
    run()
