"""Synthetic human-review triage and observed public buyer names from EIS RSS."""
import csv
import hashlib
import json

import pytest
from leadpilot import eis_108_triage as t
from leadpilot.eis_rss import SOURCE_NAME

NOTICE = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=0138200002926000037"

SAMPLED_PUBLIC_BUYERS = (
    "ЗАКОНОДАТЕЛЬНОЕ СОБРАНИЕ КАМЧАТСКОГО КРАЯ",
    "АДМИНИСТРАЦИЯ ВНУТРИГОРОДСКОГО МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ - МУНИЦИПАЛЬНОГО ОКРУГА ВЫХИНО-ЖУЛЕБИНО В ГОРОДЕ МОСКВЕ",
    "ОБЛАСТНОЕ ГОСУДАРСТВЕННОЕ УЧРЕЖДЕНИЕ ГОСУДАРСТВЕННЫЙ АРХИВ САРАТОВСКОЙ ОБЛАСТИ",
    "АДМИНИСТРАЦИЯ ГОРОДСКОГО ОКРУГА ГОРОД ВОРОНЕЖ",
    "УПРАВЛЕНИЕ ЗАПИСИ АКТОВ ГРАЖДАНСКОГО СОСТОЯНИЯ КУЗБАССА",
    "ДЕПАРТАМЕНТ АРХИТЕКТУРЫ ГОРОДА САЯНОГОРСКА",
    "АДМИНИСТРАЦИЯ МАРИИНСКОГО МУНИЦИПАЛЬНОГО ОКРУГА",
    "МУНИЦИПАЛЬНОЕ КАЗЁННОЕ УЧРЕЖДЕНИЕ",
    "КАЗЁННОЕ УЧРЕЖДЕНИЕ НЕНЕЦКОГО АВТОНОМНОГО ОКРУГА",
    "УПРАВЛЕНИЕ ФЕДЕРАЛЬНОЙ СЛУЖБЫ ИСПОЛНЕНИЯ НАКАЗАНИЙ ПО СМОЛЕНСКОЙ ОБЛАСТИ",
    "КОМИТЕТ ФИНАНСОВ ГОРОДА КУРСКА",
    "АДМИНИСТРАЦИЯ МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ ТИХВИНСКИЙ РАЙОН",
)


@pytest.mark.parametrize("buyer", SAMPLED_PUBLIC_BUYERS)
def test_public_buyer_not_mistaken_for_unverified_private_company(buyer):
    assert t._buyer_hint(buyer) == "POSSIBLE_PUBLIC_SECTOR_BUYER"


@pytest.mark.parametrize("buyer,expected", [
    ("ООО Альфа", "LEGAL_FORM_LOOKS_COMMERCIAL_SIZE_UNVERIFIED"),
    ("АО Бета", "LEGAL_FORM_LOOKS_COMMERCIAL_SIZE_UNVERIFIED"),
    ("", "BUYER_NAME_MISSING"),
    ("Незнакомая организация", "BUYER_LEGAL_FORM_UNVERIFIED"),
])
def test_legal_form_does_not_prove_size(buyer, expected):
    assert t._buyer_hint(buyer) == expected


@pytest.mark.parametrize("title,kind", [
    ("Предоставление лицензий 1С: Кабинет сотрудника и Комплект ИТС",
     "MIXED_LICENSE_AND_SERVICE_REVIEW"),
    ("Поставка лицензий на программу 1С",
     "LICENSE_SUPPLY_NOT_VERIFIED_EXTERNAL_SUPPORT"),
    ("Информационно-технологическое сопровождение 1С ИТС",
     "ITS_SUBSCRIPTION_OR_PACKAGE_REVIEW"),
    ("Оказание услуг по сопровождению программных продуктов 1С",
     "EXPLICIT_1C_SERVICE_REVIEW"),
    ("Оказание услуг по доработке 1С",
     "EXPLICIT_1C_SERVICE_REVIEW"),
])
def test_mixed_licensing_versus_genuine_work_title(title, kind):
    assert t._procurement_hint(title) == kind


def _report(buyer="ООО Альфа",title="Оказание услуг по сопровождению 1С"):
    return {
        "mode": "EIS_PREQUALIFICATION_EVIDENCE_REVIEW_ONLY",
        "source": SOURCE_NAME,
        "crm_writes": "NO", "outbound_messages": "NO",
        "counts": {"unique_notices_to_review": 1, "qualified_b2b_leads": 0,
                   "crm_ready": 0, "exact_inn_fns_verified": 0},
        "items": [
            {"official_notice_url": NOTICE, "notice_id": hashlib.sha256(NOTICE.encode()).hexdigest()[:24],
             "published_on": "2026-10-30", "title": title,
             "buyer_name_rss_unverified": buyer, "buyer_inn": "",
             "fns_size": "UNVERIFIED", "crm_ready": False,
             "buyer_region": "UNVERIFIED", "deadline": "UNVERIFIED",
             "review_status": "RESEARCH_ONLY_NOT_A_QUALIFIED_LEAD"},
        ]
    }


def test_no_fake_qualified_lead():
    result = t.triage(_report("АДМИНИСТРАЦИЯ ГОРОДА МОСКВЫ"))
    assert result["counts"]["public_sector_text_hints"] == 1
    assert result["counts"]["qualified_b2b_leads"] == 0
    assert result["counts"]["confirmed_small_business_buyers"] == 0
    assert result["rows"][0]["manual_work_queue"] == "PUBLIC_BUYER_REVIEW_SEPARATELY"
    assert result["rows"][0]["crm_ready"] is False
    assert result["rows"][0]["outbound_authorized"] is False
    assert "FNS_SIZE" in result["rows"][0]["review_holds"]


def test_private_looking_legal_form_still_unverified_and_not_crm():
    result = t.triage(_report())
    assert result["rows"][0]["manual_work_queue"] == "FNS_BUYER_INN_AND_GEOGRAPHY_REVIEW_REQUIRED"
    assert result["counts"]["qualified_b2b_leads"] == 0
    assert result["counts"]["crm_ready"] == 0


@pytest.mark.parametrize("field,value", [
    ("buyer_inn", "7700000000"),
    ("fns_size", "MICRO"),
    ("crm_ready", True),
    ("review_status", "APPROVED"),
    ("official_notice_url", "https://evil.example/notices/1"),
])
def test_untrusted_approval_or_url_rejected(field, value):
    report = _report()
    report["items"][0][field] = value
    with pytest.raises(ValueError):
        t.triage(report)


def test_reject_summary_pretending_real_qualified_leads():
    report = _report()
    report["counts"]["qualified_b2b_leads"] = 1
    with pytest.raises(ValueError, match="PREAPPROVAL"):
        t.triage(report)


def test_exclusive_csv_and_excel_injection(tmp_path,monkeypatch):
    report = t.triage(_report("=HYPERLINK(1)"))
    monkeypatch.chdir(tmp_path)
    json_path, csv_path = t.save(report, "exports/review.json", "exports/review.csv")
    assert json.loads(json_path.read_text(encoding="utf8"))["counts"]["crm_ready"] == 0
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        first = next(csv.DictReader(f))
    assert first["buyer_name_rss_unverified"].startswith("'=")
    with pytest.raises(ValueError):
        t.save(report, "exports/review.json", "exports/review.csv")


def test_cli_accepts_utf8_bom_review_json_and_writes_real_exports(tmp_path, monkeypatch, capsys):
    """Regression for actual Windows crash: LookupError unknown encoding utf8-sig."""
    import sys
    from pathlib import Path

    source = tmp_path / "previous_review.json"
    # Windows-produced JSON may have UTF-8 BOM; main must load either form.
    source.write_text(json.dumps(_report("АДМИНИСТРАЦИЯ ГОРОДА МОСКВЫ"),
                                 ensure_ascii=False), encoding="utf-8-sig")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "leadpilot.eis_108_triage",
        "--review-json", str(source),
        "--json", "exports/triage.json",
        "--csv", "exports/triage.csv",
    ])
    t.main()
    result = json.loads((tmp_path / "exports/triage.json").read_text(
        encoding="utf-8"))
    assert result["counts"]["unique_notices"] == 1
    assert result["counts"]["public_sector_text_hints"] == 1
    assert result["counts"]["qualified_b2b_leads"] == 0
    assert result["counts"]["crm_ready"] == 0
    assert result["crm_writes"] == "NO"
    assert (tmp_path / "exports/triage.csv").exists()
    output = capsys.readouterr().out
    assert "EIS_TRIAGE_FINISHED=YES" in output
    assert "EIS_TRIAGE_COUNTS=" in output


def test_cli_invalid_input_returns_safe_error_without_exports(tmp_path, monkeypatch, capsys):
    import sys

    broken = tmp_path / "invalid.json"
    broken.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "leadpilot.eis_108_triage",
        "--review-json", str(broken),
        "--json", "exports/triage.json",
        "--csv", "exports/triage.csv",
    ])
    with pytest.raises(SystemExit) as error:
        t.main()
    assert error.value.code == 2
    assert "EIS_TRIAGE=BLOCKED" in capsys.readouterr().out
    assert not (tmp_path / "exports/triage.json").exists()


# These are names in the *actual* 2026-10-31 108-notice operator sample,
# previously incorrectly grouped as "buyer legal form unverified".
MISSED_PUBLIC_BUYERS_FROM_LIVE_OPERATOR_REPORT = (
    "ВОРОНЕЖСКИЙ ОБЛАСТНОЙ СУД",
    "КОМИТЕТ ПО ГРАДОСТРОИТЕЛЬСТВУ, ЗЕМЕЛЬНЫМ И ИМУЩЕСТВЕННЫМ ОТНОШЕНИЯМ АДМИНИСТРАЦИИ ШПАКОВСКОГО МУНИЦИПАЛЬНОГО ОКРУГА СТАВРОПОЛЬСКОГО КРАЯ",
    'ХАБАРОВСКИЙ ФИЛИАЛ ФЕДЕРАЛЬНОГО ГОСУДАРСТВЕННОГО БЮДЖЕТНОГО УЧРЕЖДЕНИЯ "НАЦИОНАЛЬНЫЙ МЕДИЦИНСКИЙ ИССЛЕДОВАТЕЛЬСКИЙ ЦЕНТР ОТОРИНОЛАРИНГОЛОГИИ"',
    "АРБИТРАЖНЫЙ СУД ДАЛЬНЕВОСТОЧНОГО ОКРУГА",
    "УПРАВЛЕНИЕ СУДЕБНОГО ДЕПАРТАМЕНТА В ЕВРЕЙСКОЙ АВТОНОМНОЙ ОБЛАСТИ",
    "ХАБАРОВСКИЙ ФИЛИАЛ ФЕДЕРАЛЬНОГО ГОСУДАРСТВЕННОГО БЮДЖЕТНОГО УЧРЕЖДЕНИЯ НАЦИОНАЛЬНЫЙ МЕДИЦИНСКИЙ ИССЛЕДОВАТЕЛЬСКИЙ ЦЕНТР",
    "ГОСУДАРСТВЕННАЯ ИНСПЕКЦИЯ ПО НАДЗОРУ ЗА ТЕХНИЧЕСКИМ СОСТОЯНИЕМ САМОХОДНЫХ МАШИН И ДРУГИХ ВИДОВ ТЕХНИКИ АМУРСКОЙ ОБЛАСТИ",
    "АППАРАТ ГЛАВЫ АДМИНИСТРАЦИИ ГОРОДА БАЙКОНУР",
    "КОМИТЕТ ПО ОБРАЗОВАНИЮ АДМИНИСТРАЦИИ ГОРОДСКОГО ОКРУГА ЗАТО СИБИРСКИЙ АЛТАЙСКОГО КРАЯ",
    "КОМИТЕТ ПО ФИНАНСАМ АДМИНИСТРАЦИИ ГОРОДСКОГО ОКРУГА ЗАТО СИБИРСКИЙ АЛТАЙСКОГО КРАЯ",
    "УПРАВЛЕНИЕ КУЛЬТУРЫ АДМИНИСТРАЦИИ СЕВЕРО-ЕНИСЕЙСКОГО МУНИЦИПАЛЬНОГО ОКРУГА",
    "ГЛАВНОЕ СЛЕДСТВЕННОЕ УПРАВЛЕНИЕ СЛЕДСТВЕННОГО КОМИТЕТА РОССИЙСКОЙ ФЕДЕРАЦИИ ПО ГОРОДУ САНКТ-ПЕТЕРБУРГУ",
)


@pytest.mark.parametrize("buyer", MISSED_PUBLIC_BUYERS_FROM_LIVE_OPERATOR_REPORT)
def test_twelve_previously_unverified_live_public_buyers_held_out_of_sme_queue(buyer):
    assert t._buyer_hint(buyer) == "POSSIBLE_PUBLIC_SECTOR_BUYER"


def test_eis_official_procurement_always_separated_from_mikhail_sme_marketing_leads():
    source = t._buyer_hint("ООО Гипотетический поставщик")
    assert source == "LEGAL_FORM_LOOKS_COMMERCIAL_SIZE_UNVERIFIED"
    report = t.triage(_report("ООО Гипотетический поставщик"))
    assert report["rows"][0]["workstream"] == (
        "OFFICIAL_TENDER_OPPORTUNITY_REVIEW_NOT_MIKHAIL_SME_LEAD")
    assert report["counts"]["mikhail_sme_b2b_ready"] == 0
    assert report["counts"]["official_tender_research_notices"] == 1
    assert report["counts"]["qualified_b2b_leads"] == 0
