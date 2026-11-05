"""Single-administrator setup manifest for future ServiceIT 1062/21 Kanban.

All new cards already get AI_SUMMARY, AI_STATUS and color/score after cloud
enrichment. The portal controls Kanban's shared tile presentation; no Bitrix
REST method is falsely claimed to set each employee's tile from this module.
"""
from __future__ import annotations

from .serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS
from .serviceit_kanban_presentation import BADGE_FIELD,SCORE_FIELD
from .serviceit_manager_ai_card import manager_layout_manifest

PROCESS=1062
CATEGORY=21
KANBAN_TILE_FIELDS=(
    "title", PREFIX+"AI_SUMMARY", PREFIX+"AI_STATUS",
    BADGE_FIELD, SCORE_FIELD, PREFIX+"SUGGESTED_SERVICE",
)


def one_time_admin_handoff():
    layout=manager_layout_manifest()
    assert layout["entity_type_id"]==PROCESS and layout["category_id"]==CATEGORY
    assert PREFIX+"AI_SUMMARY" in AI_FIELDS
    assert BADGE_FIELD in layout["score_fields"]
    return {
        "status":"CORPORATE_ADMIN_ONE_TIME_UI_ENABLE_REQUIRED",
        "scope":{"entityTypeId":1062,"categoryId":21},
        "field_codes_in_display_order":list(KANBAN_TILE_FIELDS),
        "highest_priority_if_tile_space_limited":[
            PREFIX+"AI_SUMMARY",BADGE_FIELD,
        ],
        "future_card_auto_filled_by":"serviceit-bitrix-all-new-batch-ai.yml",
        "pipeline_preserved":"Техническая поддержка",
        "shared_form_plan_already_available":True,
        "one_time_admin_action":"CONFIGURE_KANBAN_CARD_VIEW_FOR_ALL_EMPLOYEES",
        "post_admin_qa":"VERIFY_TWO_SEPARATE_EMPLOYEE_VIEWS_AND_FIRST_REAL_NEW_CARD",
        "scores_unverified_remain_provisional":True,
        "crm_writes":0,"old_cards_changed":0,
        "admin_ui_changed":False,
    }
