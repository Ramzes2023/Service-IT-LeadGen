"""Release and guard regression tests for V18 source evidence upgrade."""
import ast
from pathlib import Path

MIRROR=Path("leadpilot/serviceit_b2b_public_mirrors.py")
INDEX=Path("leadpilot/serviceit_b2b_tender_index_v18.py")
DEEP=Path("leadpilot/serviceit_vps_b2b_deep_research.py")
WORKER=Path("leadpilot/serviceit_vps_b2b_dossier_v11_worker.py")
CORE=Path("leadpilot/serviceit_vps_worker.py")
RELEASE=Path("ops/serviceit_vps_b2b_v18_multisource_activate.py")
FLOW=Path(".github/workflows/serviceit-vps-b2b-v18-multisource-release.yml")
AUDIT=Path(".github/workflows/serviceit-vps-b2b-v18-capabilities.yml")


def test_v18_has_real_search_capabilities_without_missing_legal_gates():
    for path in (MIRROR,INDEX,DEEP,WORKER,CORE,RELEASE):
        ast.parse(path.read_text("utf8"))
    s=DEEP.read_text("utf8")
    assert "SERVICEIT_B2B_V18_SEARCH_ENABLED" in s
    optional_providers = {
        node.args[0].id for node in ast.walk(ast.parse(s))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "optional" and node.args
        and isinstance(node.args[0], ast.Name)
    }
    assert {"tender_index_context", "brave_mirror_search"} <= optional_providers
    assert "enable_v18 is True" in s
    assert '"INDEX_ONLY_UNVERIFIED"' in INDEX.read_text("utf8")
    s=WORKER.read_text("utf8")
    assert "enable_v18=True" in s
    s=CORE.read_text("utf8")
    assert 'result["SERVICEIT_B2B_V18_SEARCH_ENABLED"] = "YES"' in s
    assert "INTERVAL_SECONDS = 300" in s
    s=MIRROR.read_text("utf8")
    assert "_robots(client, url, frozenset({mirror}))" in s
    assert "_get(client, url, frozenset({mirror}))" in s
    assert "BROWSER_RENDERED_PUBLIC_PAGE_UNVERIFIED" in s
    assert '"4", "40", "459"' in s


def test_v18_release_is_sha_fenced_offline_restart_only():
    s=RELEASE.read_text("utf8")
    assert 'FILES = (' in s
    assert '"serviceit_vps_b2b_dossier_v11_worker.py"' in s
    assert "AUDITED_OLD_SHAS" in s
    assert '"MISSING"' in s
    assert '"--network", "none"' in s
    assert '"--read-only"' in s
    assert '"--no-deps", "--no-build"' in s
    assert "pending_source_writes()" in s
    assert "ROLLBACK=YES" in s
    assert "V18_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in s
    assert "serviceit_vps_b2b_tender_doc_receipts" in s
    assert "crm.item.update" not in s and "crm.item.add" not in s
    assert "INTERVAL_SECONDS" in s
    assert "len(files)==len(hashes)==6" in s
    assert "V18_OFFLINE_SMOKE_PASS" in s


def test_approved_owner_exact_marker_and_bounded_live_probe():
    s=FLOW.read_text("utf8")
    assert "SERVICEIT_B2B_V18_MULTISOURCE_GUARDED_RELEASE_489" in s
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.comment.user.login == 'Ramzes2023'" in s
    assert "github.event.issue.number == 489" in s
    assert "!github.event.issue.pull_request" in s
    assert "workflow_dispatch:" not in s
    assert 'serviceit-vps-readonly' in s
    assert "StrictHostKeyChecking=yes" in s
    assert "UserKnownHostsFile=" in s
    assert "V18_RELEASE_VPS_LIVE_ENABLED=YES" in s
    assert "V18_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in s
    assert ' "$#" == 6 ' in s
    a=AUDIT.read_text("utf8")
    assert "SERVICEIT_B2B_V18_CAPABILITIES_READONLY_489" in a
    assert "github.actor == 'Ramzes2023'" in a
    assert "github.event.issue.number == 489" in a
    assert "crm.item.update" not in a


def test_index_no_direct_creds_or_crm_and_5_min_unchanged():
    text=INDEX.read_text("utf8")
    for forbidden in ("crm.item.update","crm.item.add",
                      "chromium.launch", "playwright.chromium",
                      "captcha", "ROTATING_PROXY", "requests.post("):
        assert forbidden not in text
    assert "INDEX_QUOTA_OR_AUTH" in text
    assert "MAX_INDEX_URLS = 5" in text
    assert "ONE additional official Brave" in DEEP.read_text("utf8")
