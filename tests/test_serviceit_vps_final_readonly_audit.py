"""Static guarantees for the owner-only one-click VPS READONLY cutover audit.

No remote GitHub, VPS, database, AI or CRM access from these tests.
"""
from pathlib import Path

WORKFLOW = Path(".github/workflows/serviceit-vps-final-cutover-readonly-audit.yml")
SOURCE = WORKFLOW.read_text("utf-8")

WRITERS = [
    "serviceit-bitrix-all-new-batch-ai.yml",
    "serviceit-bitrix-new-card-ai.yml",
    "serviceit-free-cloud-ai-cards.yml",
    "serviceit-crm-duplicate-auto.yml",
    "serviceit-finance-hybrid-live.yml",
    "serviceit-fns-msp-crm-result.yml",
    "serviceit-kanban-three-signal-title-auto.yml",
    "serviceit-leadgen-free-msp-internal-score.yml",
    "serviceit-own-signal-stop-auto.yml",
    "serviceit-postcall-manager-approved-auto.yml",
    "serviceit-stage3-all-new-task-automation.yml",
    "serviceit-stage4-all-new-signed-score-writer.yml",
    "serviceit-stage4-prelim-score-live.yml",
]


def test_explicit_owner_only_scope_and_no_workflow_auto_schedule():
    assert "github.actor == 'Ramzes2023'" in SOURCE
    assert "github.ref == 'refs/heads/main'" in SOURCE
    assert "inputs.approval == 'FINAL_READONLY_AUDIT_1062_21'" in SOURCE
    assert "workflow_dispatch:" in SOURCE
    assert "\n  schedule:" not in SOURCE
    assert "\n  repository_dispatch:" not in SOURCE
    assert "environment: serviceit-vps-readonly" in SOURCE
    assert "actions: read" in SOURCE


def test_all_fourteen_cloud_writer_gates_are_checked_from_main():
    for name in WRITERS:
        assert name in SOURCE
        assert (
            "vars.SERVICEIT_VPS_CUTOVER_ACTIVE != 'YES'"
            in Path(".github/workflows", name).read_text("utf-8")
        )
    assert "serviceit-bitrix-poll-fallback.yml" in SOURCE
    assert SOURCE.count("CLOUD_OVERLAPPING_WRITER_FENCES=14_OF_14") == 1


def test_readonly_gates_and_olga_605_checked_before_reporting_pass():
    for guard in (
        "SERVICEIT_VPS_WRITER_MODE READONLY",
        "SERVICEIT_VPS_CUTOVER_AUTHORIZED NO",
        "SERVICEIT_VPS_POLL_ENABLED NO",
        "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK NO",
        "SERVICEIT_VPS_TASK_ASSIGNEE_ID 605",
        "SERVICEIT_VPS_MANAGER_TASK_ENABLED NO",
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED YES",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED YES_1062_21",
        "SERVICEIT_VPS_POSTCALL_ENABLED NO",
        "SERVICEIT_VPS_FINANCE_ENABLED NO",
    ):
        assert guard in SOURCE
    assert "VPS_FOUR_HEALTHY=PASS" in SOURCE
    assert "VPS_READONLY_READINESS=PASS" in SOURCE
    assert "CLOUD_JOB_DRAIN=NOT_CERTIFIED_BY_SNAPSHOT" in SOURCE


def test_no_mutation_actions_or_customer_data_in_audit():
    for forbidden in (
        "crm.item.add", "crm.item.update", "tasks.task.add",
        "docker compose up", "docker restart", "docker stop",
        "gh variable set", "gh workflow disable", "workflow_run:",
        "SERVICEIT_VPS_CUTOVER_ACTIVE=YES",
        "SERVICEIT_VPS_WRITER_MODE=LIVE",
        "SERVICEIT_VPS_MANAGER_TASK_ENABLED=YES",
        "ROUTERAI_API_KEY",
        "SERVICEIT_BITRIX_WEBHOOK",
    ):
        assert forbidden not in SOURCE
    assert "leadpilot.serviceit_vps_poll_readonly_probe" in SOURCE
    assert "leadpilot.serviceit_vps_native_observer --once" in SOURCE
