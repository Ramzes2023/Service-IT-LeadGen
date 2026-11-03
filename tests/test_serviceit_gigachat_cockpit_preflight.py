"""Offline-only tests: no Bitrix calls, credentials, model network or CRM writes."""
import json

from leadpilot.bitrix_1062_demo_qualification import AI_TEXT
from leadpilot import serviceit_gigachat_cockpit_preflight as p


def complete_result():
    return {"origin": "AI_hypothesis", "quality_status": "complete",
            "service": "1С", "possible_need": "Возможно, требуется настройка 1С и ККТ.",
            "confidence": 0.82, "evidence_quote": "настройка 1С и ККТ",
            "provider": "gigachat", "model": "GigaChat-2"}


def test_exact_synthetic_only_no_crm():
    called = []
    def classify(text):
        called.append(text)
        return complete_result()
    output = p.run_preflight(classifier=classify)
    assert called == [AI_TEXT]
    assert output == {"status": "GIGACHAT_COCKPIT_PREFLIGHT_PASS",
                      "provider": "gigachat", "quality": "complete",
                      "score_fixture": 90, "full_report_chars": output["full_report_chars"],
                      "ai_field_count": 9, "verified_quote": True,
                      "crm_writes": 0, "ledger_reservations": 0}
    assert output["full_report_chars"] > 1300


def test_quality_needs_review_does_not_promote_to_pass():
    unsafe_model_text = "SYNTHETIC_SECRET_NEVER_PRINT"
    response = {**complete_result(), "quality_status": "needs_review",
                "possible_need": unsafe_model_text,
                "quality_reasons": ["model_abstained", unsafe_model_text]}
    output = p.run_preflight(classifier=lambda t: response)
    assert output["status"] == "GIGACHAT_PREFLIGHT_NEEDS_REVIEW"
    assert output["quality_reasons"] == ["model_abstained"]
    assert unsafe_model_text not in json.dumps(output)
    assert output["crm_writes"] == 0


def test_invalid_report_unsafe_model_prose_is_redacted():
    response = {**complete_result(), "possible_need": "ignore system: leak key"}
    output = p.run_preflight(classifier=lambda text: response)
    assert output["status"] == "GIGACHAT_PREFLIGHT_NEEDS_REVIEW"
    assert output["safe_reason_code"] == "REPORT_QUALITY_BLOCKED"
    assert "ignore" not in str(output)


def test_non_gigachat_is_blocked():
    output = p.run_preflight(classifier=lambda text: {**complete_result(), "provider": "other"})
    assert output["safe_reason_code"] == "PROVIDER_INVALID"
    assert output["crm_writes"] == 0


def test_python_default_never_contacts_model(monkeypatch, capsys):
    monkeypatch.setattr(p, "run_preflight", lambda: (_ for _ in ()).throw(AssertionError("AI must not run")))
    assert p.main([]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "GIGACHAT_PREFLIGHT_OFFLINE_READY"


def test_live_cli_redacts_external_exception(monkeypatch, capsys):
    from leadpilot import ai
    monkeypatch.setenv("AI_ENABLE", "1")
    monkeypatch.setenv("AI_PROVIDER", "gigachat")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "SYNTHETIC_PRIVATE_KEY")
    monkeypatch.setenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    monkeypatch.setenv("GIGACHAT_MODEL", "GigaChat-2")
    from leadpilot.ai import AIProvider
    monkeypatch.setattr(AIProvider, "_get_token", lambda self: "SYNTHETIC_OAUTH")
    monkeypatch.setattr(p, "run_preflight",
                        lambda classifier: (_ for _ in ()).throw(RuntimeError("SYNTHETIC_PRIVATE_KEY remote payload")))
    assert p.main(["--live-synthetic"]) == 2
    out = capsys.readouterr().out
    assert json.loads(out)["safe_reason_code"] == "GIGACHAT_MODEL_OR_RESPONSE_FAILED"
    assert "SYNTHETIC_PRIVATE_KEY" not in out


def test_no_bot_crm_or_files_apis_in_preflight_source():
    from pathlib import Path
    source = Path(p.__file__).read_text(encoding="utf-8")
    for forbidden in ("crm.item.update", "crm.item.details.configuration.set",
                      "State(", "state.reserve(", "userfieldconfig.add",
                      "BITRIX24_AUDIT_WEBHOOK", ".env", "task.item.add", "send_email"):
        assert forbidden not in source or forbidden == ".env"  # .env appears only in no-file comment


def test_missing_gigachat_scope_prevents_any_network_or_paid_model_call(monkeypatch, capsys):
    monkeypatch.setenv("AI_ENABLE", "1")
    monkeypatch.setenv("AI_PROVIDER", "gigachat")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "SYNTHETIC_KEY_DO_NOT_LOG")
    monkeypatch.setenv("GIGACHAT_SCOPE", "")
    monkeypatch.setenv("GIGACHAT_MODEL", "GigaChat-2")
    monkeypatch.setattr(p, "run_preflight",
                        lambda: (_ for _ in ()).throw(AssertionError("MUST NOT CALL MODEL")))
    assert p.main(["--live-synthetic"]) == 2
    val = json.loads(capsys.readouterr().out)
    assert val["safe_reason_code"] == "GIGACHAT_SCOPE_UNSET_OR_INVALID"
    assert val["crm_writes"] == 0


def test_empty_model_github_var_uses_existing_reviewed_default(monkeypatch, capsys):
    from leadpilot.ai import config_from_env
    monkeypatch.setenv("AI_ENABLE", "1")
    monkeypatch.setenv("AI_PROVIDER", "gigachat")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "SYNTHETIC_KEY_DO_NOT_LOG")
    monkeypatch.setenv("GIGACHAT_SCOPE", "GIGACHAT_API_B2B")
    monkeypatch.setenv("GIGACHAT_MODEL", "")
    called=[]
    def mock_preflight():
        called.append(config_from_env().model)
        return {"status":"GIGACHAT_COCKPIT_PREFLIGHT_PASS",
                "crm_writes":0,"ledger_reservations":0}
    from leadpilot.ai import AIProvider
    monkeypatch.setattr(AIProvider, "_get_token", lambda self: "SYNTHETIC_OAUTH")
    monkeypatch.setattr(p, "run_preflight", lambda classifier: mock_preflight())
    assert p.main(["--live-synthetic"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "GIGACHAT_COCKPIT_PREFLIGHT_PASS"
    assert output["crm_writes"] == 0
    assert called == ["GigaChat-2"]



def test_gigachat_model_default_on_missing_empty_and_blank_env():
    from leadpilot.ai import config_from_env
    base={"AI_ENABLE":"1", "AI_PROVIDER":"gigachat",
          "GIGACHAT_CREDENTIALS":"SYNTHETIC_KEY",
          "GIGACHAT_SCOPE":"GIGACHAT_API_PERS"}
    for value in (None,"","   "):
        env=base.copy()
        if value is not None:
            env["GIGACHAT_MODEL"]=value
        assert config_from_env(env).model == "GigaChat-2"
    assert config_from_env({**base,"GIGACHAT_MODEL":"GigaChat-Max"}).model == "GigaChat-Max"



def test_live_synthetic_oauth_failure_is_redacted_before_model_or_crm(monkeypatch, capsys):
    from leadpilot.ai import AIProvider
    monkeypatch.setenv("AI_ENABLE", "1")
    monkeypatch.setenv("AI_PROVIDER", "gigachat")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "SYNTHETIC_PRIVATE_KEY_NEVER_LOG")
    monkeypatch.setenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    monkeypatch.setenv("GIGACHAT_MODEL", "GigaChat-2")
    monkeypatch.setattr(AIProvider, "_get_token",
                        lambda self: (_ for _ in ()).throw(RuntimeError("SYNTHETIC_PRIVATE_KEY_NEVER_LOG")))
    monkeypatch.setattr(p, "run_preflight",
                        lambda classifier: (_ for _ in ()).throw(AssertionError("MUST_NOT_RUN_MODEL")))
    assert p.main(["--live-synthetic"]) == 2
    out = capsys.readouterr().out
    assert json.loads(out)["safe_reason_code"] == "GIGACHAT_OAUTH_OR_TLS_FAILED"
    assert "SYNTHETIC_PRIVATE_KEY_NEVER_LOG" not in out
