"""Four real B2B task and candidate INN observations never mutate CRM."""
from pathlib import Path

def test_four_task_inn_readonly_contract():
    p=(Path(__file__).resolve().parents[1] /
        ".github/workflows/serviceit-vps-b2b-four-task-inn-readonly.yml")
    w=p.read_text("utf8")
    for token in ("github.actor == 'Ramzes2023'",
                  "github.event.issue.number == 314",
                  "SERVICEIT_B2B_FOUR_TASKS_INN_READONLY_314",
                  "StrictHostKeyChecking=yes",
                  "B2B_PUBLIC_INN_V1",
                  "B2B_TASKS_FOUR_PRESENT",
                  "B2B_TASKS_CARD_",
                  "INN_VALID", "FNS_PRESENT", "LIMIT 10"):
        assert token in w
    for bad in ("crm.item.update", "tasks.task.add", "INSERT INTO",
                "DELETE FROM", "docker restart", "docker compose up",
                "workflow_dispatch", "assert len(rows)==2"):
        assert bad not in w
