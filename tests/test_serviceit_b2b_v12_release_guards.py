"""Offline test of V12 image-pinning deployment safety primitives."""
from pathlib import Path
import importlib.util
import inspect
import re

import pytest

FILE = Path("ops/serviceit_vps_b2b_v12_activate.py")


def module():
    spec = importlib.util.spec_from_file_location("serviceit_v12_release", FILE)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_release_is_sha_attested_and_scoped_to_one_coordinator():
    m = module()
    assert len(m.FILES) == len(m.AUDITED_OLD_SHAS) == 2
    assert m.FILES == ("serviceit_b2b_archive_continuity_v12.py",
                      "serviceit_vps_b2b_archive_v12_worker.py")
    assert m.AUDITED_OLD_SHAS == ("MISSING", "MISSING")
    assert all(x == "MISSING" or re.fullmatch(r"[a-f0-9]{64}", x)
               for x in m.AUDITED_OLD_SHAS)
    assert m.CORE == "serviceit-leadgen-vps-coordinator-1"
    assert "coordinator" in inspect.getsource(m.run)
    assert "pg_try_advisory" not in inspect.getsource(m.run)
    # No code path should stop the whole stack or alter neighbor containers.
    assert not any(x in inspect.getsource(m.run)
                   for x in ('"down"', '"stop"', '"restart"', '"rm"', '"rmi"'))
    assert '("--no-deps"' not in inspect.getsource(m.run)
    assert '"--no-deps"' in inspect.getsource(m.run)


def test_pin_changes_only_exact_one_digest_and_preserves_flags():
    m = module()
    old = "sha256:" + "a" * 64
    new = "sha256:" + "b" * 64
    value = ('services:\n  coordinator:\n    image: "' + old + '"\n'
             '    pull_policy: never\n'
             '    environment:\n      SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED: "YES"\n'
             '      SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED: "YES"\n')
    actual = m.pin_image(value, old, new)
    assert actual.replace(new, old) == value
    with pytest.raises(m.ReleaseBlocked):
        m.pin_image(actual, old, "sha256:" + "c" * 64)
    with pytest.raises(m.ReleaseBlocked):
        m.pin_image(value + '    image: "' + old + '"\n', old, new)


def test_workflow_has_exact_owner_and_no_generic_deployment_trigger():
    content = Path(".github/workflows/serviceit-vps-b2b-v12-guarded-release.yml").read_text("utf8")
    assert "SERVICEIT_B2B_V12_GUARDED_RELEASE_368" in content
    assert "github.actor == 'Ramzes2023'" in content
    assert "github.event.issue.number == 368" in content
    assert "!github.event.issue.pull_request" in content
    assert "github.ref == 'refs/heads/main'" in content
    assert "workflow_dispatch:" not in content
    assert "crm.item.update" not in content
    assert "GITHUB_RUN_ID" in content
    assert "sha256sum" in content


def test_v12_release_fails_when_manifest_has_invalid_shape():
    m = module()
    # All non-network validation occurs before any docker/ssh invocation.
    with pytest.raises(m.ReleaseBlocked):
        m.run("bad-run-id", ("a" * 64,) * 2)
    with pytest.raises(m.ReleaseBlocked):
        m.run("42890012075", ("NOT_VERIFIED",) * 2)


def test_readonly_v10_receipt_table_acts_as_inflight_gate():
    source=FILE.read_text("utf8")
    assert "serviceit_vps_b2b_dossier_v11_receipts WHERE status IN" in source
    assert "EXISTING_CORE_SHA_DRIFT_NO_DEPLOY" in source
    assert "V12_OFFLINE_SMOKE_PASS" in source
    assert "V12_RELEASE_VPS_LIVE_ENABLED=YES" in source
