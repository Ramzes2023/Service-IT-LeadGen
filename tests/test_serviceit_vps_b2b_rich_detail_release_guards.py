"""Offline guarded release contract for exactly ONE public Kontur/Synapse adapter."""
import importlib.util
import inspect
from pathlib import Path
import re

import pytest

SCRIPT=Path("ops/serviceit_vps_b2b_rich_detail_activate.py")
WORKFLOW=Path(".github/workflows/serviceit-vps-b2b-rich-public-detail-release.yml")


def module():
    spec=importlib.util.spec_from_file_location("rich_detail_release",SCRIPT)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_scope_old_live_sha_and_immutable_lock():
    m=module()
    assert m.FILES==("serviceit_b2b_public_mirrors.py",)
    assert m.AUDITED_OLD_SHAS==(
        "e11fb8f76618371e80938516c569fd5aea8757baac8254e02ffa3ec9716ab593",)
    assert all(re.fullmatch(r"[a-f0-9]{64}",h) for h in m.AUDITED_OLD_SHAS)
    assert m.CORE=="serviceit-leadgen-vps-coordinator-1"
    assert m.GATES["SERVICEIT_VPS_POLL_ENABLED"]=="YES"
    assert m.GATES["SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED"]=="YES"
    assert m.GATES["SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED"]=="YES"
    assert m.GATES["SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED"]=="YES"
    assert "SERVICEIT_VPS_RICH_ENABLED" not in m.GATES
    script=SCRIPT.read_text("utf8")
    assert ".serviceit-five-minute-activate.lock" in script
    assert "EXISTING_CORE_SHA_DRIFT_NO_DEPLOY" in script
    assert "PENDING_AI_OR_CRM_WRITE_NO_RESTART" in script
    assert "serviceit_vps_b2b_tender_doc_receipts" in script
    assert "RICH_OFFLINE_SMOKE_PASS" in script
    assert '("b2b-rich-detail-release-" + run_id)' in script
    assert '("b2b-crossportal-release-" + run_id)' not in script
    assert 'stage="serviceit/staging/b2b-rich-detail-release-$GITHUB_RUN_ID"' in WORKFLOW.read_text("utf8")
    assert "detailed_public_excerpt(parts,'4673595',title)" in script
    assert "'MLOps-инженер, Lead'" in script
    assert "RICH_RELEASE_VPS_LIVE_ENABLED=YES" in script
    assert "RICH_RELEASE_ROLLBACK=YES" in script
    assert "RICH_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in script
    assert "crm.item.update" not in script
    assert '"--no-deps"' in script
    assert not any(term in inspect.getsource(m.run)
                   for term in ('"down"','"stop"','"rm"','"rmi"'))


def test_pinned_image_only_replaces_expected_digest():
    m=module()
    old="sha256:"+("a"*64)
    new="sha256:"+("b"*64)
    compose=('services:\n  coordinator:\n    image: "'+old+'"\n'
             '    pull_policy: never\n')
    result=m.pin_image(compose,old,new)
    assert result.replace(new,old)==compose
    with pytest.raises(m.ReleaseBlocked):
        m.pin_image(result,old,new)
    with pytest.raises(m.ReleaseBlocked):
        m.pin_image(compose+compose,old,new)


def test_owner_guard_exact_reviewed_sha_only_one_module():
    w=WORKFLOW.read_text("utf8")
    assert "github.actor == 'Ramzes2023'" in w
    assert "github.event.issue.number == 368" in w
    assert "!github.event.issue.pull_request" in w
    assert "SERVICEIT_B2B_RICH_PUBLIC_DETAIL_RELEASE_368" in w
    assert "git diff --quiet 3ea732bb8200c8e45053f3ce012f580d102a0c69 HEAD" in w
    assert "Stage exact one non-secret module" in w
    assert '== 1 ]]' in w
    assert 'workflow_dispatch:' not in w
    assert "StrictHostKeyChecking=yes" in w
    assert "UserKnownHostsFile=" in w
    assert "serviceit_b2b_public_mirrors.py" in w
    assert "serviceit_bitrix_poll_fallback.py" not in w
    assert "serviceit_vps_b2b_document_worker.py" not in w
    assert "crm.item.update" not in w
