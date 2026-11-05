"""Independent 9 AI fields+task metadata audit: no writes by construction."""
import httpx

from leadpilot.serviceit_nine_ai_task_readiness import inspect
from leadpilot.serviceit_b2b_hold_ai_fields import AI_SUFFIXES, PREFIX

HOOK = "https://serviseit.bitrix24.ru/rest/1615/REDACTED_TEST_ONLY"


def run_audit(missing=None, readonly=None):
    methods = []
    def handler(req):
        path = req.url.path
        methods.append(path)
        if path.endswith("/crm.type.getByEntityTypeId.json"):
            return httpx.Response(200, json={"result": {"type": {
                "id": 13, "entityTypeId": 1062,
                "isUseInUserfieldEnabled": "Y",
                "linkedUserFields": {"TASKS_TASK|UF_CRM_TASK": "Y"}
            }}})
        if path.endswith("/crm.item.fields.json"):
            fields = {
                PREFIX + suffix: {
                    "type": "text" if suffix == "AI_FULL_REPORT" else "string",
                    "isReadOnly": suffix == readonly,
                    "isMultiple": False,
                } for suffix in AI_SUFFIXES if suffix != missing
            }
            return httpx.Response(200, json={"result": {"fields": fields}})
        if path.endswith("/tasks.task.getFields.json"):
            return httpx.Response(200, json={"result": {"fields": {
                "TITLE": {"type": "string"},
                "DESCRIPTION": {"type": "text"},
                "RESPONSIBLE_ID": {"type": "integer"},
                "UF_CRM_TASK": {"type": "crm"},
            }}})
        raise AssertionError("Forbidden write or cross-portal API")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = inspect(client, HOOK)
    return result, methods


def test_all_nine_writable_and_task_binding_is_ready():
    result, calls = run_audit()
    assert result["nine_ai_fields_present_and_writable"] == 9
    assert len(result["per_field_metadata"]) == 9
    assert all(result["per_field_metadata"].values())
    assert result["status"] == "NINE_AI_TASK_METADATA_READY"
    assert result["task_link_configured"] is True
    assert result["task_api_fields_visible"] is True
    assert result["real_task_add_readback_verified_by_this_check"] is False
    assert result["real_nine_ai_write_verified_by_this_check"] is False
    assert result["crm_writes"] == 0
    assert result["tasks_created"] == 0
    assert len(calls) == 3
    assert not any(x.endswith(".add.json") or x.endswith(".update.json")
                   for x in calls)


def test_missing_field_is_reported_without_fabricated_success():
    result, _ = run_audit(missing="AI_QUOTE")
    assert result["nine_ai_fields_present_and_writable"] == 8
    assert result["per_field_metadata"]["AI_QUOTE"] is False
    assert result["status"] == "NINE_AI_TASK_METADATA_GAPS"


def test_readonly_field_is_reported():
    result, _ = run_audit(readonly="AI_FULL_REPORT")
    assert result["per_field_metadata"]["AI_FULL_REPORT"] is False
    assert result["status"] == "NINE_AI_TASK_METADATA_GAPS"
