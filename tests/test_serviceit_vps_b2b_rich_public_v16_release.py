"""V16 deployment must retain single-module SHA-fenced and rollback-only semantics."""
import ast
from pathlib import Path

SCRIPT = Path("ops/serviceit_vps_b2b_rich_public_v16_activate.py")
FLOW = Path(".github/workflows/serviceit-vps-b2b-rich-public-v16-release.yml")
MIRROR = Path("leadpilot/serviceit_b2b_public_mirrors.py")


def test_source_release_only_without_crm_side_effects():
    text = SCRIPT.read_text("utf-8")
    ast.parse(text)
    assert 'FILES = ("serviceit_b2b_public_mirrors.py",)' in text
    assert "b2841b8dafce749659b56868877e72ca6fb752943eb44b06f71e717705b04895" in text
    assert '"--network", "none"' in text
    assert '"--no-deps", "--no-build"' in text
    assert "pending_source_writes()" in text
    assert "serviceit_vps_b2b_dossier_v11_receipts" in text
    assert "serviceit_vps_b2b_tender_doc_receipts" in text
    assert "CORE_MIRROR_SOURCE_SHA_MISMATCH" in text
    assert "RICH_RELEASE_ROLLBACK=YES" in text
    assert "RICH_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in text
    assert "crm.item.update" not in text and "crm.item.add" not in text
    assert "SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED" in text


def test_release_requires_owner_exact_comment_and_pinned_previous_code():
    text = FLOW.read_text("utf-8")
    assert "SERVICEIT_B2B_RICH_PUBLIC_V16_RELEASE_368" in text
    assert "github.actor == 'Ramzes2023'" in text
    assert "github.event.comment.user.login == 'Ramzes2023'" in text
    assert "github.event.issue.number == 368" in text
    assert "!github.event.issue.pull_request" in text
    assert "workflow_dispatch:" not in text
    assert "bfb9c15207d6588ad62e06eb870374dc5e0e1906" in text
    assert "git diff --quiet" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "UserKnownHostsFile=" in text
    assert "RICH_RELEASE_VPS_LIVE_ENABLED=YES" in text
    assert "ops/serviceit_vps_b2b_rich_public_v16_activate.py" in text
    assert "secrets.SERVICEIT_VPS_DEPLOY_SSH_KEY" in text
    assert "secrets.SERVICEIT_VPS_SSH_KNOWN_HOSTS" in text


def test_future_automation_still_reads_new_module_without_new_worker():
    text = MIRROR.read_text("utf-8")
    assert "brave_unavailable" in text
    assert "direct = \"https://zakupki.kontur.ru/\"" in text
    assert "per_family = {}" in text
    assert "families = list(per_family)[:20]" in text
    worker = Path("leadpilot/serviceit_vps_worker.py").read_text("utf-8")
    assert "INTERVAL_SECONDS = 300" in worker
    assert "from .serviceit_vps_b2b_dossier_v11_worker import worker_once" in worker
