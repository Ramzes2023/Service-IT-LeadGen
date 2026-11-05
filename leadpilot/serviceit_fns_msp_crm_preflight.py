"""Read-only LIVE check for dedicated FNS-MSP field and CRM access.

Never logs INNs, company names, webhook, customer data or raw Bitrix payloads.
"""
import json
import os
import httpx
from .bitrix_metadata_audit import validate_webhook
from .serviceit_fns_msp_crm_result_writer import FIELD, rest, PORTAL

def main():
    out = {"status": "MSP_READONLY_BLOCKED", "crm_writes": 0,
           "field_exists": False, "field_writable": False,
           "field_single_string": False, "timeline_list_readable": False}
    try:
        base = validate_webhook(os.environ.get("SERVICEIT_BITRIX_WEBHOOK", ""))
        if not base.startswith(f"https://{PORTAL}/rest/"):
            raise ValueError("WRONG_CORPORATE_PORTAL")
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            fields = rest(client, base, "crm.item.fields", {
                "entityTypeId": 1062, "useOriginalUfNames": "Y",
            }).get("fields")
            # Read-only on historically known card #3; no result payload logged.
            response = client.post(
                base + "/crm.timeline.comment.list.json",
                json={"filter": {"ENTITY_ID": 3,
                                  "ENTITY_TYPE": "DYNAMIC_1062"},
                      "order": {"ID": "ASC"}, "start": 0},
                timeout=20, follow_redirects=False,
            )
            if response.status_code == 200 and 0 < len(response.content) <= 400000:
                payload = response.json()
                out["timeline_list_readable"] = (
                    type(payload) is dict and "error" not in payload
                    and type(payload.get("result")) is list)
        if not isinstance(fields, dict):
            raise ValueError("FIELDS_MISSING")
        item = fields.get(FIELD)
        out["field_exists"] = isinstance(item, dict)
        if isinstance(item, dict):
            out["field_writable"] = item.get("isReadOnly") is False
            out["field_single_string"] = (
                item.get("type") == "string"
                and item.get("isMultiple") in (False, None))
        out["status"] = ("MSP_FIELD_READY" if all((
            out["field_exists"], out["field_writable"],
            out["field_single_string"])) else
            ("MSP_TIMELINE_READABLE_FALLBACK_CANDIDATE"
             if not out["field_exists"] and out["timeline_list_readable"]
             else "MSP_NO_VERIFIED_DESTINATION"))
        print(json.dumps(out, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps(out, sort_keys=True))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
