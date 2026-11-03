"""No-network Bitrix detail layout regressions: never erase existing sections."""
from copy import deepcopy

import pytest

from leadpilot.serviceit_manager_layout_safe import (
    AI_SECTION_TITLE, MANAGER_SECTION_TITLE,
    AI_REQUIRED, MANAGER_REQUIRED,
    plan_shared_manager_layout, guarded_apply_shared_layout,
    ManagerLayoutBlocked,
)


def existing_layout():
    return [
        {
            "name": "section_basic",
            "title": "Общая информация",
            "type": "section",
            "elements": [
                {"name": "TITLE", "optionFlags": 0},
                {"name": "ASSIGNED_BY_ID", "optionFlags": 1,
                 "options": {"custom": "KEEP_UNCHANGED"}},
            ],
        },
        {
            "name": "section_source",
            "title": "Дополнительно",
            "type": "section",
            "elements": [
                {"name": "UF_CRM_13_SVCIT_SIGNAL_ID", "optionFlags": 0},
            ],
        },
        {"name": "section_ai", "title": AI_SECTION_TITLE,
         "type": "section", "elements": []},
    ]


def field_catalog():
    needed = set(AI_REQUIRED) | set(MANAGER_REQUIRED)
    return {k: {"type": "string"} for k in needed | {
        "TITLE", "ASSIGNED_BY_ID", "UF_CRM_13_SVCIT_SIGNAL_ID",
    }}


class FakeBitrix:
    def __init__(self, *, mutated=False, readback_mismatch=False):
        self.current = existing_layout()
        self.calls = []
        self.mutated = mutated
        self.readback_mismatch = readback_mismatch
        self.read_count = 0

    def __call__(self, method, params):
        self.calls.append(method)
        assert params["entityTypeId"] == 1062
        if method.startswith("crm.item.details.configuration"):
            assert params["scope"] == "C"
            assert params["extras"] == {"categoryId": 21}
        if method == "crm.item.details.configuration.get":
            self.read_count += 1
            if self.mutated and self.read_count == 2:
                self.current[0]["elements"].append({"name": "USER_EDIT"})
            if self.readback_mismatch and self.read_count == 3:
                self.current[0]["elements"].append({"name": "SERVER_CONCURRENT_EDIT"})
            return deepcopy(self.current)
        if method == "crm.item.fields":
            assert params["useOriginalUfNames"] == "Y"
            return {"fields": field_catalog()}
        if method == "crm.item.details.configuration.set":
            assert set(params) == {"entityTypeId", "scope", "extras", "data"}
            self.current = deepcopy(params["data"])
            return True
        pytest.fail("UNEXPECTED_CALL " + method)

    @property
    def writes(self):
        return self.calls.count("crm.item.details.configuration.set")


def test_merge_adds_ai_and_manager_while_preserving_every_existing_field():
    before = existing_layout()
    result = plan_shared_manager_layout(before, field_catalog())
    assert before == existing_layout()
    after = result["after"]
    assert [x["title"] for x in after] == [
        "Общая информация", "Дополнительно", AI_SECTION_TITLE,
        MANAGER_SECTION_TITLE,
    ]
    assert after[0] == before[0]
    assert after[1] == before[1]
    assert after[2]["name"] == before[2]["name"]
    assert {x["name"] for x in after[2]["elements"]} == set(AI_REQUIRED)
    assert {x["name"] for x in after[3]["elements"]} == set(MANAGER_REQUIRED)
    assert all(x["optionFlags"] == 1 for section in after[2:] for x in section["elements"])
    assert result["added_count"] == len(AI_REQUIRED) + len(MANAGER_REQUIRED)
    assert result["crm_writes"] == 0
    assert result["before_hash"] != result["after_hash"]


def test_existing_ai_field_elsewhere_is_not_moved_or_duplicated():
    before = existing_layout()
    before[1]["elements"].append({"name": AI_REQUIRED[0], "optionFlags": 0})
    plan = plan_shared_manager_layout(before, field_catalog())
    after = plan["after"]
    assert after[1] == before[1]
    assert AI_REQUIRED[0] not in {x["name"] for x in after[2]["elements"]}
    names = [e["name"] for sec in after for e in sec["elements"]]
    assert len(names) == len(set(names))


def test_second_run_is_idempotent_without_unnecessary_set():
    first = plan_shared_manager_layout(existing_layout(), field_catalog())
    second = plan_shared_manager_layout(first["after"], field_catalog())
    assert second["added_count"] == 0
    assert second["before_hash"] == second["after_hash"]


@pytest.mark.parametrize("bad", [
    None, [], "broken",
    [{"name": "one", "title": "A", "elements": [{"name": "TITLE"}]},
     {"name": "one", "title": "B", "elements": []}],
    [{"name": "one", "title": "A", "elements": [{"name": "TITLE"}]},
     {"name": "two", "title": "B", "elements": [{"name": "TITLE"}]}],
    [{"name": "one", "title": "A", "elements": ["x"]}],
])
def test_missing_malformed_or_duplicate_form_never_overwritten(bad):
    with pytest.raises(ManagerLayoutBlocked):
        plan_shared_manager_layout(bad, field_catalog())


def test_missing_bitrix_field_definition_fails_closed():
    catalog = field_catalog()
    del catalog[AI_REQUIRED[0]]
    with pytest.raises(ManagerLayoutBlocked, match="MANDATORY_CRM_FIELDS_NOT_FOUND"):
        plan_shared_manager_layout(existing_layout(), catalog)


def test_preview_default_never_calls_set_or_reserve():
    crm = FakeBitrix()
    def no_reserve(*args):
        pytest.fail("RESERVE_MUST_NEVER_RUN_IN_PREVIEW")
    result = guarded_apply_shared_layout(crm, no_reserve)
    assert result["status"] == "PREVIEW_ONLY"
    assert crm.writes == 0
    assert crm.calls == ["crm.item.details.configuration.get", "crm.item.fields"]


def test_explicit_admin_scope_and_durable_backup_first_for_one_update():
    crm = FakeBitrix()
    acked = []
    def reserve(before, after, sha):
        assert crm.writes == 0
        assert before == existing_layout()
        assert sha["before_hash"] != sha["after_hash"]
        acked.append(sha)
        return True
    result = guarded_apply_shared_layout(crm, reserve, apply=True,
                                          admin_approved=True)
    assert result["status"] == "MANAGER_LAYOUT_APPLIED_EXACT"
    assert result["crm_writes"] == 1
    assert len(acked) == 1 and crm.writes == 1
    assert crm.calls == [
        "crm.item.details.configuration.get", "crm.item.fields",
        "crm.item.details.configuration.get",
        "crm.item.details.configuration.set",
        "crm.item.details.configuration.get",
    ]
    assert guarded_apply_shared_layout(crm, reserve, apply=True,
                                       admin_approved=True)["status"] == "ALREADY_PRESENT"
    assert crm.writes == 1


def test_admin_approval_and_backup_mandatory():
    crm = FakeBitrix()
    with pytest.raises(ManagerLayoutBlocked, match="SHARED_LAYOUT_WRITE_NOT_APPROVED"):
        guarded_apply_shared_layout(crm, lambda *_: True,
                                    apply=True, admin_approved=False)
    assert crm.writes == 0
    crm = FakeBitrix()
    with pytest.raises(ManagerLayoutBlocked, match="MANAGER_LAYOUT_BACKUP_NOT_ACKED"):
        guarded_apply_shared_layout(crm, lambda *_: False,
                                    apply=True, admin_approved=True)
    assert crm.writes == 0


def test_concurrent_manager_ui_edit_blocks_before_write_and_backup():
    crm = FakeBitrix(mutated=True)
    called = []
    with pytest.raises(ManagerLayoutBlocked, match="MANAGER_LAYOUT_CHANGED_BEFORE_WRITE"):
        guarded_apply_shared_layout(crm, lambda *_: called.append(1),
                                    apply=True, admin_approved=True)
    assert crm.writes == 0 and not called


def test_after_set_readback_mismatch_is_uncertain_not_retryable():
    crm = FakeBitrix(readback_mismatch=True)
    with pytest.raises(ManagerLayoutBlocked, match="LAYOUT_WRITE_UNCERTAIN_NO_RETRY"):
        guarded_apply_shared_layout(crm, lambda *_: True,
                                    apply=True, admin_approved=True)
    assert crm.writes == 1


def test_unexpected_overwrite_response_is_uncertain():
    crm = FakeBitrix()
    original = crm.__call__
    def rest(method, params):
        result = original(method, params)
        if method == "crm.item.details.configuration.set":
            return False
        return result
    with pytest.raises(ManagerLayoutBlocked, match="LAYOUT_WRITE_UNCERTAIN_NO_RETRY"):
        guarded_apply_shared_layout(rest, lambda *_: True,
                                    apply=True, admin_approved=True)
    assert crm.writes == 1
