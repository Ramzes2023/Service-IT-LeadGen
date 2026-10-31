"""Read-only Bitrix24 deal-pipeline preflight. No CRM writes or production reads."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import re
from urllib.parse import urlsplit
import httpx

ENTITY_TYPE_DEAL = 2
SANDBOX_NAME = "LeadGen AI — Тестирование"
READ_METHODS = frozenset({"crm.category.list", "crm.item.fields", "crm.status.list"})
HOST_SUFFIXES = (".bitrix24.ru", ".bitrix24.com", ".bitrix24.kz", ".bitrix24.by", ".bitrix24.eu")


class PreflightError(Exception):
    """Safe error with no secret URL or response body."""


def validate_webhook(webhook: str) -> str:
    if not isinstance(webhook, str) or not webhook or webhook != webhook.strip():
        raise PreflightError("BITRIX24_TEST_WEBHOOK is missing or invalid")
    parsed = urlsplit(webhook)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        raise PreflightError("Invalid HTTPS Bitrix24 webhook URL") from None
    if (parsed.scheme != "https" or not host or
            not any(host.endswith(suffix) for suffix in HOST_SUFFIXES) or
            port not in (None, 443) or parsed.username or parsed.password or
            parsed.query or parsed.fragment or
            not re.fullmatch(r"/rest/[1-9]\d*/[A-Za-z0-9_-]{6,}/?", parsed.path)):
        raise PreflightError("Invalid HTTPS Bitrix24 webhook URL")
    return webhook.rstrip("/")


@dataclass(frozen=True)
class PreflightConfig:
    webhook: str
    category_name: str = SANDBOX_NAME
    category_id: int | None = None

    def __post_init__(self):
        validate_webhook(self.webhook)
        if not self.category_name.strip() or len(self.category_name) > 120:
            raise PreflightError("Invalid sandbox category name")
        if self.category_id is not None and (isinstance(self.category_id, bool) or
                                             not isinstance(self.category_id, int) or self.category_id <= 0):
            raise PreflightError("Category ID must be positive; default category forbidden")


def _read_call(client: httpx.Client, base_url: str, method: str, params: dict) -> object:
    if method not in READ_METHODS:
        raise PreflightError("API method not on read-only allowlist")
    try:
        response = client.post(f"{base_url}/{method}", json=params, follow_redirects=False)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or "error" in data or "result" not in data:
            raise PreflightError(f"{method}: API rejected request or insufficient permissions")
        return data["result"]
    except (httpx.HTTPError, ValueError, KeyError):
        raise PreflightError(f"{method}: HTTP, API or JSON error") from None


def read_only_preflight(cfg: PreflightConfig, client: httpx.Client | None = None) -> dict:
    """Reads category metadata, deal fields and stages. Never fetches deal records."""
    base = validate_webhook(cfg.webhook)
    owned = client is None
    client = client or httpx.Client(timeout=12, follow_redirects=False)
    report = {"mode": "READ_ONLY", "crm_writes": "NO", "outbound_messages": "NO",
              "entity_type": ENTITY_TYPE_DEAL, "sandbox_name": cfg.category_name,
              "sandbox_id": None, "status": "NOT_VERIFIED", "stages": [], "field_count": 0,
              "methods_called": []}
    try:
        categories = _read_call(client, base, "crm.category.list", {"entityTypeId": ENTITY_TYPE_DEAL})
        report["methods_called"].append("crm.category.list")
        if not isinstance(categories, dict) or not isinstance(categories.get("categories"), list):
            raise PreflightError("Unexpected category list structure")
        matched = [c for c in categories["categories"] if isinstance(c, dict) and
                   c.get("name") == cfg.category_name and
                   str(c.get("id", "")).isdigit() and int(c["id"]) > 0]
        if cfg.category_id is not None:
            matched = [c for c in matched if int(c["id"]) == cfg.category_id]
        if len(matched) != 1:
            report["status"] = "SANDBOX_NOT_FOUND_OR_AMBIGUOUS"
            return report
        category_id = int(matched[0]["id"])
        report["sandbox_id"] = category_id
        fields = _read_call(client, base, "crm.item.fields", {"entityTypeId": ENTITY_TYPE_DEAL})
        report["methods_called"].append("crm.item.fields")
        if not isinstance(fields, dict) or not isinstance(fields.get("fields"), dict):
            raise PreflightError("Unexpected fields response structure")
        report["field_count"] = len(fields["fields"])
        statuses = _read_call(client, base, "crm.status.list",
                              {"filter": {"ENTITY_ID": f"DEAL_STAGE_{category_id}"}})
        report["methods_called"].append("crm.status.list")
        if not isinstance(statuses, list):
            raise PreflightError("Unexpected status list structure")
        report["stages"] = [
            {"id": s["STATUS_ID"], "name": s.get("NAME", "")}
            for s in statuses if isinstance(s, dict) and
            s.get("ENTITY_ID") == f"DEAL_STAGE_{category_id}" and
            isinstance(s.get("STATUS_ID"), str)
        ][:30]
        report["status"] = ("READ_ACCESS_CONFIRMED" if report["stages"]
                            else "STAGES_NOT_CONFIGURED_OR_NOT_VISIBLE")
        return report
    except PreflightError as exc:
        report["status"] = "READ_FAILED"
        report["error"] = str(exc)
        return report
    finally:
        if owned:
            client.close()


def suggested_blueprint() -> dict:
    """Offline proposal, with no writes."""
    return {
        "mode": "PROPOSAL_ONLY", "crm_writes": "NO", "entityTypeId": ENTITY_TYPE_DEAL,
        "category_name": SANDBOX_NAME,
        "stages": ["Новый сигнал", "Проверка данных и дублей", "AI-гипотеза",
                   "Проверка менеджера", "Контакт разрешён / в работе",
                   "Передано в продажи", "Отказ / неподходящий"],
        "custom_fields_to_request": [
            "Локальный ID", "ИНН (текст)", "Регион", "Источник сигнала (URL)",
            "Дата сигнала", "Подтверждающая цитата", "Предполагаемая услуга",
            "Lead Score", "Версия Lead Score", "Статус AI", "Следующее действие"],
        "restrictions": ["No default deal pipeline", "No production company/contact/deal writes",
                         "No marketing automations or robots", "Separate restricted role and webhook"]
    }


def run(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Bitrix24 sandbox read-only preflight")
    parser.add_argument("--blueprint", action="store_true", help="Show offline setup proposal")
    parser.add_argument("--preflight", action="store_true", help="Check test category by REST read methods")
    args = parser.parse_args(argv)
    if args.blueprint == args.preflight:
        parser.error("Choose exactly one of --blueprint or --preflight")
    if args.blueprint:
        print("BITRIX_SANDBOX_BLUEPRINT=" + json.dumps(suggested_blueprint(), ensure_ascii=False))
        return
    raw_id = os.getenv("BITRIX24_SANDBOX_CATEGORY_ID", "").strip()
    try:
        cfg = PreflightConfig(
            webhook=os.getenv("BITRIX24_TEST_WEBHOOK", ""),
            category_name=os.getenv("BITRIX24_SANDBOX_NAME", SANDBOX_NAME),
            category_id=int(raw_id) if raw_id else None)
        report = read_only_preflight(cfg)
    except (PreflightError, ValueError) as exc:
        report = {"status": "CONFIG_ERROR", "error": str(exc),
                  "mode": "READ_ONLY", "crm_writes": "NO"}
    print("BITRIX_SANDBOX_PREFLIGHT=" + json.dumps(report, ensure_ascii=False))
    if report["status"] != "READ_ACCESS_CONFIRMED":
        raise SystemExit(2)


if __name__ == "__main__":
    run()
