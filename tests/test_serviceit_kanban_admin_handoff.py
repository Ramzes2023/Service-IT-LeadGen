"""One-time admin Kanban handoff only, never an unauthorized UI mutation."""
from leadpilot.serviceit_kanban_admin_handoff import (
    one_time_admin_handoff,KANBAN_TILE_FIELDS,
)
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_kanban_presentation import BADGE_FIELD


def test_common_kanban_configuration_covers_all_new_ai_cards():
    manifest=one_time_admin_handoff()
    assert manifest["scope"]=={"entityTypeId":1062,"categoryId":21}
    assert "title" in KANBAN_TILE_FIELDS
    assert PREFIX+"AI_SUMMARY" in KANBAN_TILE_FIELDS
    assert PREFIX+"AI_STATUS" in KANBAN_TILE_FIELDS
    assert BADGE_FIELD in KANBAN_TILE_FIELDS
    assert manifest["future_card_auto_filled_by"]=="serviceit-bitrix-all-new-batch-ai.yml"
    assert manifest["crm_writes"]==0
    assert manifest["admin_ui_changed"] is False
    assert manifest["one_time_admin_action"]=="CONFIGURE_KANBAN_CARD_VIEW_FOR_ALL_EMPLOYEES"
    assert manifest["scores_unverified_remain_provisional"] is True
