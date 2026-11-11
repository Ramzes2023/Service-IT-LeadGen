"""V19 additive source evidence / no-replay 5-min coordinator tests."""
from pathlib import Path
from leadpilot.serviceit_b2b_v19_backfill import prepare_addendum,novel_evidence
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS,PREFIX
from leadpilot.serviceit_b2b_tender_index_v18 import _safe_index_candidate

TID="4675475"
TITLE="Приобретение серверного оборудования для нужд МКБ"
URL="https://zakupki.kontur.ru/4675475_40"

def old_fields():
    old={k:"Ранее проверенные данные — не изменять" for k in AI_FIELDS}
    old[PREFIX+"AI_FULL_REPORT"]="ТЕНДЕР №4675475: Анализ закупки и условия."
    old[PREFIX+"AI_STATUS"]="PUBLIC_RESEARCH_ROUTERAI_CITED"
    return old

def test_v19_index_to_additive_dossier_preserves_nine_and_unrelated():
    index=_safe_index_candidate(URL,"№4675475 серверы для МКБ",
        "B2B-Center 4675475. Серверное оборудование для МКБ. "
        "Заказчик: ПАО Московский Кредитный Банк. ИНН: 7734202860",
        TID,TITLE)
    assert index and index["evidence_level"]=="INDEX_ONLY_UNVERIFIED"
    found={"status":"PUBLIC_TENDER_ID_NOT_FOUND_IN_READABLE_PAGES",
           "pages":[],"indexed_candidates":[index]}
    old=old_fields()
    lines=novel_evidence(found,old[PREFIX+"AI_FULL_REPORT"])
    assert any("ИНН" in line and "7734202860" in line for line in lines)
    assert all("ИНДЕКС BRAVE" in line and "НЕ ФНС" in line for line in lines)
    prepared=prepare_addendum(old,lines,TID)
    assert set(prepared)==AI_FIELDS
    assert prepared[PREFIX+"AI_FULL_REPORT"].startswith(old[PREFIX+"AI_FULL_REPORT"])
    assert "7734202860" in prepared[PREFIX+"AI_MANAGER_BRIEF"]
    assert "7734202860" in prepared[PREFIX+"AI_SUMMARY"]
    assert "требуют проверки" in prepared[PREFIX+"AI_FULL_REPORT"]
    assert prepared[PREFIX+"AI_STATUS"]==old[PREFIX+"AI_STATUS"]
    assert all(prepared[k]==old[k] for k in AI_FIELDS if k not in
               (PREFIX+"AI_SUMMARY",PREFIX+"AI_MANAGER_BRIEF",PREFIX+"AI_FULL_REPORT"))
    assert prepare_addendum(prepared,novel_evidence(found,prepared[PREFIX+"AI_FULL_REPORT"]),TID) is None

def test_v19_no_source_or_no_space_means_no_mutation():
    before=old_fields()
    assert prepare_addendum(before,[],"4675475") is None
    copied=dict(before);copied[PREFIX+"AI_FULL_REPORT"]+="X"*7600
    assert prepare_addendum(copied,["ИНДЕКС BRAVE: новый заголовок ["+URL+"]"],TID) is None
    assert copied[PREFIX+"AI_STATUS"]==before[PREFIX+"AI_STATUS"]

def test_ambiguous_platform_flag_survives_into_manager_report():
    row=_safe_index_candidate(URL,"№4675475 серверы для МКБ",
        "4675475. Серверное оборудование для МКБ. ИНН: 7734202860",
        TID,TITLE)
    assert row and row["title_match"]=="EXACT_ID_TOPIC_PLATFORM_NOT_CONFIRMED"
    found={"status":"PUBLIC_TENDER_ID_NOT_FOUND_IN_READABLE_PAGES",
           "pages":[],"indexed_candidates":[row]}
    lines=novel_evidence(found,old_fields()[PREFIX+"AI_FULL_REPORT"])
    assert any("НЕ ПРОВЕРЕН ИСТОЧНИК И ПЛОЩАДКА" in s for s in lines)

def test_worker_no_duplicate_or_plaintext_external_api():
    text=Path("leadpilot/serviceit_b2b_v19_backfill.py").read_text("utf8")
    core=Path("leadpilot/serviceit_vps_worker.py").read_text("utf8")
    assert "ON CONFLICT(card_id) DO NOTHING" in text
    assert "WRITE_INTENT" in text and "UNCERTAIN" in text
    assert "pg_try_advisory_lock" in text
    assert "non_ai_unchanged" in text
    assert "original_nine(after)==draft" in text
    assert "source_findings(first,notice,exact,client,enable_v18=True)" in text
    assert "MAX_FACTS = 6" in text
    assert "crm.contact.add" not in text and "tasks.task.add" not in text
    assert "SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED" in core
    assert '"b2b_v19_additive": v19' in core
    assert '"historical_cards_touched": v19.get("crm_writes") == 1' in core
    assert "INTERVAL_SECONDS = 300" in core
