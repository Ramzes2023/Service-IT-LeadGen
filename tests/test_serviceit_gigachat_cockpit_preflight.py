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
    monkeypatch.setattr(p, "run_preflight",
                        lambda: (_ for _ in ()).throw(RuntimeError("SYNTHETIC_PRIVATE_KEY remote payload")))
    assert p.main(["--live-synthetic"]) == 2
    out = capsys.readouterr().out
    assert json.loads(out)["safe_reason_code"] == "MODEL_OR_CONFIG_ERROR"
    assert "SYNTHETIC_PRIVATE_KEY" not in out


def test_no_bot_crm_or_files_apis_in_preflight_source():
    from pathlib import Path
    source = Path(p.__file__).read_text(encoding="utf-8")
    for forbidden in ("crm.item.update", "crm.item.details.configuration.set",
                      "State(", "state.reserve(", "userfieldconfig.add",
                      "BITRIX24_AUDIT_WEBHOOK", ".env", "task.item.add", "send_email"):
        assert forbidden not in source or forbidden == ".env"  # .env appears only in no-file comment
