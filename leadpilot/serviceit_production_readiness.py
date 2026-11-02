"""Read-only production readiness diagnostics for ServiceIT 1062/21.

This module NEVER dispatches a CRM mutation, model completion or callback binding.
A positive preflight is NOT evidence of corporate verification, deployment or
permission to enable the live worker. No sensitive data or payload is printed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Callable

import httpx

from .serviceit_bitrix_new_card_events import TYPE, MAX_ITEM_ID
from .serviceit_enrichment_worker import Rest, schema_limits

EXPECTED_CATEGORY = 21
ALLOWED_METHODS = frozenset({
    "crm.type.getByEntityTypeId", "crm.item.fields",
    "userfieldconfig.list", "userfieldconfig.get",
    "crm.item.list",
})
REQUIRED_GATES = (
    "corporate_verifier_live", "fns_and_source_provenance",
    "global_customer_and_dedup", "stop_and_legal_checks",
    "approved_https_callback", "bitrix_event_bind_delivery",
    "manager_exclusive_edit_window", "gigachat_real_canary",
    "new_crm_card_exact_readback", "manager_kanban_acceptance",
)


class PreflightRejected(ValueError):
    pass


def _checked_int(value):
    if type(value) is not str or not value.isascii() or not value.isdecimal():
        raise PreflightRejected("POLL_MIN_ID_INVALID")
    number = int(value)
    if not 8 <= number <= MAX_ITEM_ID:
        raise PreflightRejected("POLL_MIN_ID_INVALID")
    return number


def config_checks(env: dict, *, exists: Callable[[Path], bool] | None = None) -> dict:
    """Offline configuration checks with stable, non-sensitive labels."""
    check_exists = exists if exists is not None else lambda p: p.is_file()
    statuses = {}
    statuses["worker_opt_in"] = env.get("SERVICEIT_WORKER_LIVE_ENABLED") == "YES_1062_21"
    try:
        minimum = _checked_int(env.get("SERVICEIT_POLL_MIN_ID"))
    except PreflightRejected:
        minimum = None
    statuses["poll_minimum_new_only"] = minimum is not None
    approvals = Path(env.get("SERVICEIT_APPROVAL_DIR") or "/run/serviceit-approvals")
    statuses["private_approval_mount"] = bool(
        approvals.is_absolute() and approvals.is_dir() and not approvals.is_symlink()
    )
    rest_path = Path(env.get("SERVICEIT_REST_SECRET_FILE") or "/run/secrets/serviceit_rest_webhook")
    statuses["rest_secret_mount"] = bool(
        rest_path.is_absolute() and not rest_path.is_symlink() and check_exists(rest_path)
    )
    statuses["model_privacy_opt_in"] = (
        env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES"
    )
    statuses["model_enabled"] = env.get("AI_ENABLE") == "1" and env.get("AI_PROVIDER") == "gigachat"
    model_path = Path(env.get("SERVICEIT_GIGACHAT_SECRET_FILE")
                      or "/run/secrets/serviceit_gigachat_credentials")
    statuses["model_secret_mount"] = bool(
        model_path.is_absolute() and not model_path.is_symlink() and check_exists(model_path)
    )
    statuses["explicit_readonly_probe_opt_in"] = (
        env.get("SERVICEIT_PREFLIGHT_READONLY") == "YES"
    )
    return {"checks": {k: "PASS" if ok else "BLOCKED" for k, ok in statuses.items()},
            "poll_min_id": minimum,
            "configuration_pass": all(statuses.values())}


def crm_readonly_probe(rest, minimum: int) -> dict:
    """Only methods in ALLOWED_METHODS; requires an injected scoped reader."""
    if not callable(rest):
        raise PreflightRejected("READONLY_REST_REQUIRED")
    if type(minimum) is not int or not 8 <= minimum <= MAX_ITEM_ID:
        raise PreflightRejected("POLL_MIN_ID_INVALID")
    calls = []
    def read(method, params):
        if method not in ALLOWED_METHODS:
            raise PreflightRejected("UNAPPROVED_METHOD")
        calls.append(method)
        return rest(method, params)

    limits = schema_limits(read)
    if len(limits) != 12:
        raise PreflightRejected("REQUIRED_TWELVE_FIELDS_MISSING")
    items = read("crm.item.list", {
        "entityTypeId": TYPE, "useOriginalUfNames": "Y",
        "filter": {"categoryId": EXPECTED_CATEGORY, ">id": minimum - 1},
        "order": {"id": "ASC"},
        "select": ["id", "entityTypeId", "categoryId"],
        "start": 0,
    })
    records = items.get("items") if isinstance(items, dict) else None
    if not isinstance(records, list) or len(records) > 50:
        raise PreflightRejected("CRM_CATEGORY_LIST_UNTRUSTED")
    for rec in records:
        if not isinstance(rec, dict):
            raise PreflightRejected("CRM_CATEGORY_LIST_UNTRUSTED")
        try:
            number = int(rec["id"])
        except (KeyError, ValueError, TypeError):
            raise PreflightRejected("CRM_CATEGORY_LIST_UNTRUSTED") from None
        if (number < minimum or number > MAX_ITEM_ID
                or str(rec.get("entityTypeId")) != str(TYPE)
                or str(rec.get("categoryId")) != str(EXPECTED_CATEGORY)):
            raise PreflightRejected("CRM_CATEGORY_LIST_UNTRUSTED")
    return {
        "status": "READONLY_SCHEMA_AND_CATEGORY_PASS",
        "entity_type_id": TYPE,
        "category_id": EXPECTED_CATEGORY,
        "fields_writable_in_schema": len(limits),
        "new_card_records_count_sampled": len(records),
        "methods": sorted(set(calls)),
        "read_calls": len(calls),
        "crm_writes": 0, "model_calls": 0,
    }


def readiness_result(config: dict, *, crm: dict | None = None) -> dict:
    """Positive checks never imply readiness for unattended sales qualification."""
    checks = config["checks"]
    return {
        "status": ("CORPORATE_GATES_NOT_ACCEPTED"
                   if config["configuration_pass"] and crm is not None
                   else "LOCAL_OR_READONLY_PREFLIGHT_BLOCKED"),
        "configuration": checks,
        "crm_readonly": crm["status"] if crm else "NOT_RUN",
        "read_calls": crm["read_calls"] if crm else 0,
        "unresolved_live_gates": list(REQUIRED_GATES),
        "all_card_production_accepted": False,
        "production_activated_by_preflight": False,
        "crm_writes": 0, "model_calls": 0,
        "historical_cards_3_and_7_touched": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-only", action="store_true")
    parser.add_argument("--live-readonly", action="store_true")
    args = parser.parse_args(argv)
    if args.config_only and args.live_readonly:
        parser.error("Choose exactly one mode")
    env = dict(os.environ)
    config = config_checks(env)
    crm = None
    if args.live_readonly and config["configuration_pass"]:
        try:
            secret_file = Path(env.get("SERVICEIT_REST_SECRET_FILE")
                               or "/run/secrets/serviceit_rest_webhook")
            webhook = secret_file.read_text(encoding="utf-8").strip()
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
                crm = crm_readonly_probe(Rest(webhook, client), config["poll_min_id"])
        except Exception:
            # Never print URL, server payload, exception details, token or INN.
            print(json.dumps({"status": "CRM_READONLY_PREFLIGHT_BLOCKED",
                              "crm_writes": 0, "model_calls": 0,
                              "production_activated_by_preflight": False}))
            return 2
    result = readiness_result(config, crm=crm)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    # Explicitly not an activation gate: corporate checks and canary still missing.
    return 0 if crm is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
