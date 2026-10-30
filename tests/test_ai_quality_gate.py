"""AI quality gate: offline and no external credentials."""
import json
from leadpilot.ai import _validated_answer

def wrap(**kwargs):
    return {"choices": [{"message": {"content": json.dumps(kwargs, ensure_ascii=False)}}]}

def test_good_answer_has_complete_quality():
    result = _validated_answer(wrap(service="IT-аутсорсинг", possible_need="Нужна помощь с IT",
                       confidence=.72, evidence_quote="системный администратор"),
                       "Учебный сигнал: требуется системный администратор")
    assert result["quality_status"] == "complete"
    assert result["quality_reasons"] == []

def test_empty_fields_are_not_counted_as_usable_ai():
    result = _validated_answer(wrap(service="IT-аутсорсинг", possible_need="",
                       confidence=0, evidence_quote=""),
                       "Учебный сигнал: требуется системный администратор")
    assert result["quality_status"] == "needs_review"
    assert "missing_possible_need" in result["quality_reasons"]
    assert "missing_verified_evidence" in result["quality_reasons"]
    assert "zero_confidence" in result["quality_reasons"]

def test_quote_must_be_verbatim_or_marked_for_review():
    result = _validated_answer(wrap(service="1С", possible_need="Нужна помощь с 1С",
                       confidence=.75, evidence_quote="Несуществующая цитата"),
                       "Публичная вакансия: программист 1С")
    assert result["evidence_quote"] == ""
    assert result["quality_status"] == "needs_review"
    assert "unverified_quote" in result["quality_reasons"]

def test_abstention_is_human_review_even_with_rationale():
    result = _validated_answer(wrap(service="Требуется проверка", possible_need="Данных недостаточно",
                       confidence=0, evidence_quote=""),
                       "Непонятный публичный сигнал")
    assert result["quality_status"] == "needs_review"
    assert "model_abstained" in result["quality_reasons"]
def test_readonly_audit_empty_db(tmp_path):
    from leadpilot.core import initialize
    from leadpilot.ai_quality_audit import audit_db
    db=tmp_path/'audit.sqlite3'
    initialize(db)
    data=audit_db(db)
    assert data['saved_ai_hypotheses']==0
    assert data['network_calls']=='NO'
