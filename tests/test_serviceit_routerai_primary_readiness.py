"""Production AI provider route audit is read-only and does not disclose secrets."""
from pathlib import Path
from leadpilot import serviceit_routerai_primary_readiness as audit


def env(tmp_path):
    ca=tmp_path/"trusted-ca.pem"
    ca.write_bytes(b"A"*200_002)
    return {
        "GITHUB_REPOSITORY":"Ramzes2023/Service-IT-LeadGen",
        "GITHUB_REF":"refs/heads/main",
        "GITHUB_EVENT_NAME":"issue_comment",
        "AI_PROVIDER":"routerai",
        "AI_ENABLE":"1",
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED":"YES",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED":"YES_1062_21",
        "ROUTERAI_API_KEY":"sk-not-a-real-token-only-test-fixture",
        "SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT":"YES",
        "GIGACHAT_CREDENTIALS":"synthetic-not-real-gigachat",
        "GIGACHAT_CA_BUNDLE_FILE":str(ca),
    }


def test_routerai_primary_and_standby_both_prepared_no_network(tmp_path):
    e=env(tmp_path)
    r=audit.snapshot(e)
    assert r["status"]=="ROUTERAI_PRIMARY_WITH_GIGACHAT_STANDBY_READY"
    assert r["primary_routerai_selected"]
    assert r["fallback_gigachat_enabled"]
    assert r["model_calls"]==r["crm_writes"]==0
    assert e["ROUTERAI_API_KEY"] not in str(r)
    assert e["GIGACHAT_CREDENTIALS"] not in str(r)


def test_no_private_data_without_approvals(tmp_path):
    e=env(tmp_path)
    for changes in [
        {"SERVICEIT_ROUTERAI_PRIVACY_APPROVED":""},
        {"SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED":"NO"},
        {"ROUTERAI_API_KEY":"not-model-api-key"},
        {"GIGACHAT_CREDENTIALS":""},
        {"SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT":"NO"},
        {"GIGACHAT_CA_BUNDLE_FILE":"/nonexistent"},
        {"AI_PROVIDER":"gigachat"},
    ]:
        r=audit.snapshot({**e,**changes})
        assert r["status"]=="ROUTERAI_PRIMARY_NOT_FULLY_READY"
        assert r["crm_writes"]==r["model_calls"]==0


def test_cannot_run_under_other_repo_branch(tmp_path):
    e=env(tmp_path)
    assert audit.snapshot({**e,"GITHUB_REF":"refs/heads/dev"})["status"]==(
        "ROUTERAI_PRIMARY_SCOPE_BLOCKED")
    assert audit.snapshot({**e,"GITHUB_REPOSITORY":"attacker/repo"})["status"]==(
        "ROUTERAI_PRIMARY_SCOPE_BLOCKED")


def test_production_workflows_are_routerai_only_and_readonly_standby_audit_is_legacy():
    base=Path(__file__).resolve().parents[1]/".github/workflows"
    for file in ["serviceit-bitrix-new-card-ai.yml",
                 "serviceit-bitrix-all-new-batch-ai.yml"]:
        body=(base/file).read_text("utf8")
        assert "AI_PROVIDER: routerai" in body
        assert "SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT: 'NO'" in body
        assert "GIGACHAT_CREDENTIALS:" not in body
        assert "serviceit_gigachat_ca_setup" not in body
    # Existing read-only standby diagnosis is retained for historical audit;
    # it is not a live card writer and does not affect production selection.
    audit=(base/"serviceit-routerai-primary-readiness.yml").read_text("utf8")
    assert "leadpilot.serviceit_routerai_primary_readiness" in audit
    assert "issue_comment:" in audit
