"""Safe CRM preview generator. No network calls and no automatic CRM writes."""
import json
from pathlib import Path
from .core import get_leads


def preview_payload(lead):
    if lead["priority"] not in ("high", "medium"):
        raise ValueError("Только квалифицированные лиды отправляются на CRM-review")
    selected = lead["signals"][0] if lead["signals"] else {}
    # Custom UF_CRM_* mappings require a real audit of the client's portal.
    # No live API writes are made by this prototype.
    return {"method": "crm.item.add", "params": {
        "entityTypeId": 1,
        "fields": {
            "title": "[DEMO] " + lead["name"],
            "comments": (
                "DEMO ONLY — confirm CRM schema and authorization before writing.\n"
                + "ИНН: " + lead["inn"] + "\n"
                + "Регион: " + lead["region"] + "\n"
                + "Lead Score: " + str(lead["score"]) + "\n"
                + "Версия scoring: " + lead["score_version"] + "\n"
                + "Предлагаемая услуга: " + lead["recommended_service"] + "\n"
                + "Источник: " + selected.get("source_url", "") + "\n"
                + "Каналы запрета: " + ", ".join(lead["suppressed_channels"])
            )
        }
    }, "local_company_id": lead["id"], "mode": "preview_not_sent"}


def export_preview(db_path, target):
    payloads = [preview_payload(x) for x in get_leads(db_path, only_queue=True)]
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payloads, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(payloads)
