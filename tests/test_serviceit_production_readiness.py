"""Offline tests for redacted, read-only 1062/21 release preflight."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from leadpilot.serviceit_production_readiness import (
    ALLOWED_METHODS, PreflightRejected, config_checks, crm_readonly_probe,
    main, readiness_result,
)
from leadpilot.serviceit_enrichment_worker import FIELDS, SCORE_FIELD, alias


def approved_env(tmp_path):
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    secret1 = tmp_path / "rest"
    secret2 = tmp_path / "model"
    secret1.write_text("test-only")
    secret2.write_text("test-only")
    return {
        "SERVICEIT_WORKER_LIVE_ENABLED": "YES_1062_21",
        "SERVICEIT_POLL_MIN_ID": "8",
        "SERVICEIT_APPROVAL_DIR": str(approvals),
        "SERVICEIT_REST_SECRET_FILE": str(secret1),
        "SERVICEIT_GIGACHAT_SECRET_FILE": str(secret2),
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
        "AI_ENABLE": "1",
        "AI_PROVIDER": "gigachat",
        "SERVICEIT_PREFLIGHT_READONLY": "YES",
    }


def fake_reader(*, automated=False, wrong_id=False):
    calls = []
    ids = {code: index for index, code in enumerate(sorted(FIELDS), 1)}
    field_defs = {
        code: {"isReadOnly": False, "isMultiple": False, "isImmutable": False,
               "type": "integer" if code == SCORE_FIELD else "string"}
        for code in FIELDS
    }
    alias_defs = {alias(code): dict(value) for code, value in field_defs.items()}

    def rest(method, params):
        calls.append(method)
        assert method in ALLOWED_METHODS
        assert method != "crm.item.update"
        assert method != "crm.item.add"
        if method == "crm.type.getByEntityTypeId":
            return {"type": {"id": 13, "entityTypeId": 1062,
                             "isAutomationEnabled": automated,
                             "isBizProcEnabled": False}}
        if method == "crm.item.fields":
            return {"fields": field_defs if params["useOriginalUfNames"] == "Y"
                    else alias_defs}
        if method == "userfieldconfig.list":
            return {"fields": [
                {"id": index, "entityId": "CRM_13", "fieldName": code}
                for code, index in ids.items()
            ]}
        if method == "userfieldconfig.get":
            code = next(c for c, i in ids.items() if i == params["id"])
            return {"field": {"fieldName": code, "entityId": "CRM_13",
                              "multiple": False,
                              "userTypeId": "integer" if code == SCORE_FIELD else "string",
                              "settings": {"MIN_LENGTH": "0", "MAX_LENGTH": "12000"}}}
        if method == "crm.item.list":
            return {"items": [{"id": 8 if not wrong_id else 7,
                               "entityTypeId": 1062, "categoryId": 21}]}
        raise AssertionError(method)

    return rest, calls


def test_live_readiness_is_not_production_acceptance(tmp_path):
    conf = config_checks(approved_env(tmp_path))
    assert conf["configuration_pass"] is True
    rest, calls = fake_reader()
    crm = crm_readonly_probe(rest, 8)
    assert crm["status"] == "READONLY_SCHEMA_AND_CATEGORY_PASS"
    assert crm["fields_writable_in_schema"] == 12
    assert crm["new_card_records_count_sampled"] == 1
    assert all(method in ALLOWED_METHODS for method in calls)
    assert all(not method.endswith("update") for method in calls)
    result = readiness_result(conf, crm=crm)
    assert result["status"] == "CORPORATE_GATES_NOT_ACCEPTED"
    assert result["all_card_production_accepted"] is False
    assert result["production_activated_by_preflight"] is False
    assert result["crm_writes"] == result["model_calls"] == 0
    assert "corporate_verifier_live" in result["unresolved_live_gates"]


def test_readonly_probe_rejects_automation_and_historical_cards():
    rest, _ = fake_reader(automated=True)
    with pytest.raises(ValueError, match="AUTOMATION_SIDE_EFFECTS_NOT_EXCLUDED"):
        crm_readonly_probe(rest, 8)
    rest, _ = fake_reader(wrong_id=True)
    with pytest.raises(PreflightRejected, match="CRM_CATEGORY_LIST_UNTRUSTED"):
        crm_readonly_probe(rest, 8)


def test_min_id_and_config_fail_closed(tmp_path):
    env = approved_env(tmp_path)
    for bad in ("0", "3", "7", "-1", "8x", " 8", "2000000001"):
        env["SERVICEIT_POLL_MIN_ID"] = bad
        result = config_checks(env)
        assert result["configuration_pass"] is False
        assert result["checks"]["poll_minimum_new_only"] == "BLOCKED"
    env["SERVICEIT_POLL_MIN_ID"] = "8"
    env["AI_ENABLE"] = "0"
    assert config_checks(env)["checks"]["model_enabled"] == "BLOCKED"


def test_missing_private_secret_and_approval_directory(tmp_path):
    env = approved_env(tmp_path)
    Path(env["SERVICEIT_REST_SECRET_FILE"]).unlink()
    Path(env["SERVICEIT_APPROVAL_DIR"]).rmdir()
    report = config_checks(env)
    assert report["configuration_pass"] is False
    assert report["checks"]["rest_secret_mount"] == "BLOCKED"
    assert report["checks"]["private_approval_mount"] == "BLOCKED"


def test_default_cli_never_calls_network_or_outputs_secrets(monkeypatch, capsys):
    import leadpilot.serviceit_production_readiness as mod

    def blocked_network(*args, **kwargs):
        raise AssertionError("network forbidden in default mode")

    monkeypatch.setattr(mod.httpx, "Client", blocked_network)
    monkeypatch.setenv("SERVICEIT_BITRIX_WEBHOOK", "FAKE_PRIVATE_WEBHOOK_DO_NOT_LOG")
    assert main([]) == 2
    output = capsys.readouterr().out
    assert "FAKE_PRIVATE_WEBHOOK_DO_NOT_LOG" not in output
    parsed = json.loads(output)
    assert parsed["crm_writes"] == parsed["model_calls"] == 0
    assert parsed["all_card_production_accepted"] is False


def test_live_readonly_requires_explicit_full_gate(monkeypatch, capsys):
    import leadpilot.serviceit_production_readiness as mod

    def blocked_network(*args, **kwargs):
        raise AssertionError("network forbidden without complete config")

    monkeypatch.setattr(mod.httpx, "Client", blocked_network)
    monkeypatch.delenv("SERVICEIT_PREFLIGHT_READONLY", raising=False)
    assert main(["--live-readonly"]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["crm_writes"] == 0


def test_invalid_minimum_is_not_coerced():
    for candidate in (0, 7, 2_000_000_001, True, "8"):
        rest, _ = fake_reader()
        with pytest.raises(PreflightRejected, match="POLL_MIN_ID_INVALID"):
            crm_readonly_probe(rest, candidate)
