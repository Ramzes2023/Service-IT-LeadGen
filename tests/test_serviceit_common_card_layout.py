"""Pure offline and synthetic transport tests; never contact real Bitrix."""
from copy import deepcopy
import json

import pytest

from leadpilot.serviceit_common_card_layout import (
    FIELDS, PREFIX, SCOPE, LayoutBlocked,
    common_layout_plan, apply_common_once,
)


def base():
    return [{
        "name": "base_customer",
        "title": "Основные сведения",
        "type": "section",
        "elements": [
            {"name": "title", "optionFlags": 1},
            {"name": PREFIX + "AI_SUMMARY", "optionFlags": 1},
            {"name": PREFIX + "COMPANY_NAME"},
        ],
    }]


def test_preserves_existing_fields_and_groups_all_12():
    orig = base()
    out = common_layout_plan(orig, {code: {} for code in FIELDS})
    assert orig == base()
    assert out[0]["title"] == "Сервис ИТ | AI и приоритет"
    assert out[1]["title"] == "Сервис ИТ | Доказательства"
    assert out[2]["title"] == "Сервис ИТ | План менеджера"
    assert out[3]["title"] == "Сервис ИТ | Полный AI-отчёт"
    assert [el["name"] for s in out[:4] for el in s["elements"]] == [
        PREFIX + suffix for suffix in (
            "AI_SCORE_BADGE", "LEAD_SCORE", "SCORE_VERSION", "AI_STATUS",
            "AI_SUMMARY", "AI_NEXT_ACTION", "AI_QUOTE", "AI_LIMITATIONS",
            "AI_HYPOTHESIS", "AI_RECOMMENDATION", "AI_MANAGER_BRIEF",
            "AI_FULL_REPORT",
        )
    ]
    assert out[4]["elements"] == [
        {"name": "title", "optionFlags": 1},
        {"name": PREFIX + "COMPANY_NAME"},
    ]


def test_no_missing_schema_or_null_baseline():
    with pytest.raises(LayoutBlocked, match="TWELVE_FIELD_CONTRACT_MISSING"):
        common_layout_plan(base(), {PREFIX + "AI_STATUS": {}})
    with pytest.raises(ValueError, match="LAYOUT_EMPTY_OR_AMBIGUOUS"):
        common_layout_plan([], {k: {} for k in FIELDS})


class FakeRest:
    def __init__(self, initial, fail_write=False):
        self.current = deepcopy(initial)
        self.fail_write = fail_write
        self.writes = 0
        self.history = []

    def __call__(self, method, params):
        self.history.append(method)
        assert params.get("entityTypeId") == 1062
        if method == "crm.item.details.configuration.get":
            assert params["scope"] == "C"
            assert params["extras"] == {"categoryId": 21}
            return deepcopy(self.current)
        if method == "crm.item.fields":
            assert params["useOriginalUfNames"] == "Y"
            return {"fields": {code: {} for code in FIELDS}}
        if method == "crm.item.details.configuration.set":
            assert params["scope"] == "C"
            assert params["extras"] == {"categoryId": 21}
            self.writes += 1
            self.current = deepcopy(params["data"])
            if self.fail_write:
                raise TimeoutError("synthetic ambiguous layout update")
            return True
        pytest.fail("UNEXPECTED_METHOD " + method)


def test_one_saved_common_layout_and_exact_readback(tmp_path):
    rest = FakeRest(base())
    out = apply_common_once(rest, tmp_path, authorized=True)
    assert out["status"] == "COMMON_DETAIL_LAYOUT_APPLIED_EXACT_READBACK"
    assert out["ai_fields"] == 9 and out["score_fields"] == 3
    assert out["kanban_tile_changed"] is False
    assert out["crm_item_writes"] == 0
    assert rest.writes == 1
    assert (tmp_path / "common-detail-1062-21-before.json").exists()
    fence = json.loads((tmp_path / "common-detail-1062-21-write-fence.json").read_text())
    assert fence["state"] == "EXACT_READBACK"
    assert apply_common_once(rest, tmp_path, authorized=True)["status"] == "COMMON_DETAIL_LAYOUT_ALREADY_CONFIGURED"
    assert rest.writes == 1
    assert "crm.item.update" not in rest.history


def test_missing_admin_or_saved_config_fails_closed(tmp_path):
    rest = FakeRest(base())
    with pytest.raises(LayoutBlocked, match="ADMIN_LAYOUT_AUTHORIZATION_REQUIRED"):
        apply_common_once(rest, tmp_path, authorized=False)
    missing = FakeRest(None)
    with pytest.raises(LayoutBlocked, match="NO_SAVED_COMMON_LAYOUT_SAFE_STOP"):
        apply_common_once(missing, tmp_path, authorized=True)
    assert missing.writes == rest.writes == 0


def test_ambiguous_common_layout_write_never_repeats(tmp_path):
    rest = FakeRest(base(), fail_write=True)
    with pytest.raises(TimeoutError):
        apply_common_once(rest, tmp_path, authorized=True)
    assert rest.writes == 1
    assert (tmp_path / "common-detail-1062-21-write-fence.json").exists()
    # The server may have applied the write; subsequent call recognises exact
    # already-configured state but never calls the setter again.
    assert apply_common_once(rest, tmp_path, authorized=True)["status"] == "COMMON_DETAIL_LAYOUT_ALREADY_CONFIGURED"
    assert rest.writes == 1
