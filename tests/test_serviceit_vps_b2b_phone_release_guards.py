"""Offline guarded release contract for exactly ONE verified corporate contact provenance module."""
import importlib.util
import inspect
from pathlib import Path
import re

import pytest

SCRIPT=Path("ops/serviceit_vps_b2b_verified_phone_activate.py")
WORKFLOW=Path(".github/workflows/serviceit-vps-b2b-verified-phone-guarded-release.yml")


def module():
    spec=importlib.util.spec_from_file_location("buyerphone_release",SCRIPT)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_scope_old_live_sha_and_immutable_lock():
    m=module()
    assert m.FILES==("serviceit_vps_b2b_deep_research.py",)
    assert m.AUDITED_OLD_SHAS==(
        "5cf0ff1bfed170cc208bd4adc6e29d748513392f9e852478bf94fd3cd03198ca",)
    assert all(re.fullmatch(r"[a-f0-9]{64}",h) for h in m.AUDITED_OLD_SHAS)
    assert m.CORE=="serviceit-leadgen-vps-coordinator-1"
    assert m.GATES["SERVICEIT_VPS_POLL_ENABLED"]=="YES"
    assert m.GATES["SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED"]=="YES"
    assert m.GATES["SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED"]=="YES"
    script=SCRIPT.read_text("utf8")
    assert ".serviceit-five-minute-activate.lock" in script
    assert "EXISTING_CORE_SHA_DRIFT_NO_DEPLOY" in script
    assert "PENDING_AI_OR_CRM_WRITE_NO_RESTART" in script
    assert "serviceit_vps_b2b_tender_doc_receipts" in script
    assert "PHONE_OFFLINE_SMOKE_PASS" in script
    assert "PHONE_RELEASE_ROLLBACK=YES" in script
    assert "PHONE_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in script
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
    assert "SERVICEIT_B2B_VERIFIED_PHONE_GUARDED_RELEASE_368" in w
    assert "git diff --quiet 3df31e23b990ea043a48a1a159b25e1f494278e9 HEAD" in w
    assert "Stage exact one non-secret module" in w
    assert '== 1 ]]' in w
    assert 'workflow_dispatch:' not in w
    assert "StrictHostKeyChecking=yes" in w
    assert "UserKnownHostsFile=" in w
    assert "serviceit_vps_b2b_deep_research.py" in w
    assert "serviceit_bitrix_poll_fallback.py" not in w
    assert "serviceit_vps_b2b_document_worker.py" not in w
    assert "crm.item.update" not in w
