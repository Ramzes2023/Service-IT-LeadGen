"""V20 source-only versioned addenda: never invalidate past VERIFIED receipts."""
from pathlib import Path

from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from leadpilot import serviceit_b2b_v20_versioned as v20

FULL = PREFIX + "AI_FULL_REPORT"
BRIEF = PREFIX + "AI_MANAGER_BRIEF"
SUMMARY = PREFIX + "AI_SUMMARY"


def old_fields():
    result = {key: "Предыдущие проверенные сведения" for key in AI_FIELDS}
    result[FULL] = "Тендер №4675475. Старый проверенный отчёт."
    result[PREFIX + "AI_STATUS"] = "PUBLIC_RESEARCH_ROUTERAI_CITED"
    return result


def test_versioned_append_only_three_ai_fields_and_retains_all_history(monkeypatch):
    before = old_fields()
    evidence = ("ПРОЧИТАННЫЙ ИСТОЧНИК / LOCATION: "
                "Место поставки: г. Москва, проезд 2-й Перова Поля "
                "[https://example.org/4675475]")
    monkeypatch.setattr(v20, "novel_evidence", lambda findings, previous: [evidence])
    after, count = v20.versioned_addendum(before, {}, "4675475")
    assert count == 1
    assert after[FULL].startswith(before[FULL])
    assert "13  ВЕРСИОННОЕ ДОПОЛНЕНИЕ V20" in after[FULL]
    assert "09  ДОПОЛНЕНИЕ V19" not in after[FULL]
    assert after[BRIEF].endswith(before[BRIEF])
    assert after[SUMMARY].endswith(before[SUMMARY])
    assert all(after[k] == before[k] for k in AI_FIELDS
               if k not in {FULL, BRIEF, SUMMARY})
    assert len(after[FULL]) <= 12000


def test_versioned_empty_facts_cannot_write(monkeypatch):
    monkeypatch.setattr(v20, "novel_evidence", lambda findings, previous: [])
    before = old_fields()
    assert v20.versioned_addendum(before, {}, "4675475") == (None, 0)


def test_versioned_report_cannot_overflow_after_extra_marker(monkeypatch):
    monkeypatch.setattr(v20, "novel_evidence", lambda findings, previous:
                        ["ИНДЕКС BRAVE (НЕ ФНС): Документ [https://example.org/4675475]"])
    before = old_fields()
    before[FULL] += "а" * 11840
    after, n = v20.versioned_addendum(before, {}, "4675475")
    assert after is None and n == 1


def test_versioned_history_receipts_and_safe_worker_are_isolated():
    source = Path("leadpilot/serviceit_b2b_v20_versioned.py").read_text("utf8")
    worker = Path("leadpilot/serviceit_vps_worker.py").read_text("utf8")
    assert "PRIMARY KEY(card_id, attempt_no)" in source
    assert "MAX(attempt_no),0)+1" in source
    assert "last.status='VERIFIED'" in source
    assert "last.updated_at < now()-interval '72 hours'" in source
    assert "last.status IN ('NO_NEW_EVIDENCE','INSUFFICIENT_CAPACITY')" in source
    assert "last.updated_at < now()-interval '24 hours'" in source
    assert "'WRITE_INTENT','VERIFIED'" in source
    assert "non_ai_unchanged(fresh,after)" in source
    assert "original_nine(after)==draft" in source
    assert "safe_notice(notice,card,source,first)" in source
    assert "source_findings(first,notice,exact,client,enable_v18=True)" in source
    assert "pg_try_advisory_lock" in source
    assert "crm.contact.add" not in source
    assert "tasks.task.add" not in source
    assert 'env.get("SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED") != "YES"' in source
    assert 'exact_env.get("SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED", "NO") == "YES"' in worker
    assert '"b2b_v20_versioned": v20' in worker
    assert '"b2b_v19_additive": v19' in worker


def test_versioned_schema_contains_immutable_prior_evidence_digest():
    ddl = v20.DDL
    assert "before_fields JSONB NOT NULL" in ddl
    assert "after_fields JSONB" in ddl
    assert "salt BYTEA" in ddl and "digest BYTEA" in ddl
    assert "octet_length(salt)=16" in ddl
    assert "octet_length(digest)=32" in ddl
