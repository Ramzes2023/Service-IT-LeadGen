"""V27 controlled Tendem-only public-source activation regression."""
import importlib.util
from pathlib import Path
import pytest

def source():
    spec=importlib.util.spec_from_file_location(
        "v27","ops/serviceit_vps_b2b_v27_tendem_enable.py")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

RAW='''services:
  coordinator:
    environment:
      SERVICEIT_TINYFISH_API_KEY_FILE: /run/secrets/serviceit_tinyfish_key
      SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED: "NO"
      SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED: "NO"
      SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED: "NO"
    secrets:
      - serviceit_tinyfish_key
'''

def test_exact_two_flags_only():
    mod=source()
    new=mod.enabled_overlay(RAW)
    assert 'SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED: "YES"' in new
    assert 'SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED: "YES"' in new
    assert 'SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED: "NO"' in new
    assert new.count('"YES"')==2
    assert new.splitlines()==[
        line.replace(': "NO"',': "YES"') if
        ('SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED' in line or
         'SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED' in line)
        else line for line in RAW.splitlines()]
    with pytest.raises(mod.Blocked):
        mod.enabled_overlay(new)


def test_no_sidecar_service_or_other_flag_can_change():
    mod=source()
    before={'name':'serviceit-leadgen-vps',
            'services':{'coordinator':{'image':'sha256:abc',
                'environment':{'SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED':'NO',
                               'SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED':'NO',
                               'SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED':'NO'}},
                        'business':{'image':'sha256:one'}}}
    after={'name':before['name'],
           'services':{'coordinator':{
               'image':'sha256:abc',
               'environment':{**before['services']['coordinator']['environment'],
                              **mod.NEW_FLAGS}},
               'business':{'image':'sha256:one'}}}
    mod.verify_diff(before,after)
    after['services']['business']['image']='sha256:two'
    with pytest.raises(mod.Blocked):
        mod.verify_diff(before,after)


def test_live_guards_and_rollback_contract():
    s=Path("ops/serviceit_vps_b2b_v27_tendem_enable.py").read_text("utf8")
    w=Path(".github/workflows/serviceit-vps-b2b-v27-tendem-enable.yml").read_text("utf8")
    assert "github.actor == 'Ramzes2023'" in w
    assert "github.event.issue.number == 489" in w
    assert "SERVICEIT_B2B_V27_ENABLE_TENDEM_FREE_489" in w
    assert "StrictHostKeyChecking=yes" in w
    assert "git diff --quiet" in w
    assert "V27_ENABLE_B2B_DIRECT_LICENSE=NO" in w
    assert "V27_ENABLE_CRM_WRITES=NO" in w
    assert "PENDING_WRITER_NO_RESTART" in s
    assert "serviceit_vps_b2b_v23_primary_source_receipts" in s
    assert "pg_try_advisory_lock" not in s or "PENDING_WRITER_NO_RESTART" in s
    assert '"--force-recreate","coordinator"' in s
    assert "wait_healthy(old_image,pins)" in s
    assert "atomic_write(OVERLAY,original)" in s
    assert "V27_ENABLE_ROLLBACK=YES" in s
    assert "crm.item.update" not in s
    assert "tasks.task.add" not in s
    assert "investigate_routerai" not in s
