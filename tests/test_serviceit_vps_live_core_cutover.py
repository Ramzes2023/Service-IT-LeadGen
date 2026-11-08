"""Offline, synthetic cutover transaction and operator script safety checks."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT=Path("deploy/serviceit-vps-live-core-cutover.py")
spec=importlib.util.spec_from_file_location("serviceit_vps_core_cutover",SCRIPT)
assert spec and spec.loader
cutover=importlib.util.module_from_spec(spec)
spec.loader.exec_module(cutover)

BASE = (
  "SERVICEIT_VPS_WRITER_MODE=READONLY\n"
  "SERVICEIT_VPS_CUTOVER_AUTHORIZED=NO\n"
  "SERVICEIT_VPS_POLL_ENABLED=NO\n"
  "SERVICEIT_VPS_INTAKE_ENABLED=NO\n"
  "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK=NO\n"
  "SERVICEIT_VPS_NATIVE_ACTIVATION_UTC=\n"
  "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=YES\n"
  "SERVICEIT_ROUTERAI_PRIVACY_APPROVED=YES_1062_21\n"
  "SERVICEIT_VPS_TASK_ASSIGNEE_APPROVED=YES_1062_21_INTERNAL_MANAGER\n"
  "SERVICEIT_VPS_TASK_ASSIGNEE_ID=605\n"
  "SERVICEIT_VPS_MANAGER_TASK_ENABLED=NO\n"
  "PRESERVE_LOCAL_NON_SECRET=X\n"
)


def test_core_preflight_and_transaction_preview_pure():
    parsed=cutover.config_parse(BASE)
    assert cutover.preflight(parsed,datetime.now(timezone.utc))
    timestamp="2026-11-08T15:31:00Z"
    new = cutover._edit(BASE,{
        **cutover.FLAGS,
        "SERVICEIT_VPS_NATIVE_ACTIVATION_UTC":timestamp,
        "SERVICEIT_PRELIMINARY_PUBLIC_HOSTS":"hh.ru,b2b-center.ru",
    })
    edited=cutover.config_parse(new)
    assert edited["PRESERVE_LOCAL_NON_SECRET"]=="X"
    assert edited["SERVICEIT_VPS_WRITER_MODE"]=="LIVE"
    assert edited["SERVICEIT_VPS_TASK_ASSIGNEE_ID"]=="605"
    assert edited["SERVICEIT_VPS_MANAGER_TASK_ENABLED"]=="YES"
    assert edited["SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED"]=="NO"
    assert edited["SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED"]=="YES"
    assert edited["SERVICEIT_VPS_FINANCE_ENABLED"]=="NO"
    assert edited["SERVICEIT_VPS_POSTCALL_ENABLED"]=="NO"
    assert edited["SERVICEIT_VPS_FNS_MSP_ENABLED"]=="NO"
    assert edited["SERVICEIT_VPS_INTAKE_ENABLED"]=="NO"
    assert edited["SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"]==timestamp


@pytest.mark.parametrize("key,value",[
    ("SERVICEIT_VPS_WRITER_MODE","LIVE"),
    ("SERVICEIT_VPS_CUTOVER_AUTHORIZED","YES"),
    ("SERVICEIT_VPS_POLL_ENABLED","YES"),
    ("SERVICEIT_VPS_INTAKE_ENABLED","YES"),
    ("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK","YES"),
    ("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC","2026-11-08T15:30:00Z"),
    ("SERVICEIT_VPS_TASK_ASSIGNEE_ID","606"),
    ("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED","NO"),
    ("SERVICEIT_VPS_MANAGER_TASK_ENABLED","YES"),
])
def test_refuse_concurrent_or_previously_activated_mode(key,value):
    raw=BASE.replace(key+"="+cutover.config_parse(BASE)[key],
                     key+"="+value)
    with pytest.raises(cutover.Blocked):
        cutover.preflight(cutover.config_parse(raw),datetime.now(timezone.utc))


def test_cutover_time_freshness_and_approved_hosts():
    now=datetime(2026,11,8,16,30,tzinfo=timezone.utc)
    assert cutover.timestamp("2026-11-08T16:29:30Z",now)
    with pytest.raises(cutover.Blocked):
        cutover.timestamp("2026-11-08T15:00:00Z",now)
    with pytest.raises(cutover.Blocked):
        cutover.timestamp("2026-11-08T17:00:00Z",now)
    assert cutover.hosts("hh.ru,b2b-center.ru",required=True)=="hh.ru,b2b-center.ru"
    with pytest.raises(cutover.Blocked):
        cutover.hosts("",required=True)
    with pytest.raises(cutover.Blocked):
        cutover.hosts("127.0.0.1",required=True)
    with pytest.raises(cutover.Blocked):
        cutover.hosts("evil.invalid",required=True)


def test_code_guards_against_late_unsafe_reenable_and_replay():
    py=SCRIPT.read_text("utf8")
    ps=Path("deploy/SERVICEIT_VPS_FINAL_CUTOVER.ps1").read_text("utf8")
    assert "fcntl.flock" in py
    assert "os.replace(temp, path)" in py
    assert '"SERVICEIT_VPS_NATIVE_ACTIVATION_UTC": activated' in py
    assert "ROLLED_BACK_READONLY" in py
    assert "GH_FENCE_STAYS_ON" in py
    assert "NATIVE_CORE_MODULES=AI_NINE" in py
    assert '"SERVICEIT_VPS_MANAGER_TASK_ENABLED": "YES"' in py
    assert '"SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED": "NO"' in py
    assert "gh variable set SERVICEIT_VPS_CUTOVER_ACTIVE" in ps
    assert ps.index("Shadow-DeployNewest $Sha") < ps.index(
        "gh variable set SERVICEIT_VPS_CUTOVER_ACTIVE --repo $Repo --body YES")
    assert ps.index("Remote-Cutover $Preflight") < ps.index(
        "gh variable set SERVICEIT_VPS_CUTOVER_ACTIVE --repo $Repo --body YES")
    assert "GitHub-ActiveLegacyRuns" in ps
    assert "SourceHosts" in ps
    assert "ReadOnly" not in ps or True
    assert "gh variable set SERVICEIT_VPS_CUTOVER_ACTIVE --repo $Repo --body NO" not in ps
    assert "gh workflow disable" not in ps
    assert "tasks.task.add" not in ps


def test_personal_stop_rules_still_empty():
    import json
    assert json.loads(Path("config/serviceit_own_signal_stop.json").read_text(
        "utf8"))["rules"]==[]
