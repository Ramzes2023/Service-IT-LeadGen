"""Synthetic offline B2B-Center procurement gate tests: no API, CRM or outreach."""
from datetime import date
import json
import pytest
from leadpilot.b2b_center_source import MODE, classify, review, save

TODAY = date(2026, 10, 31)
INN = "7724850252"  # Checksum-valid synthetic fixture, not a claim about its demand
URL = "https://www.b2b-center.ru/search/number/l4616274-4-12-1/"


def item(**updates):
    row = {
        "procurement_id": "4616274",
        "source_url": URL,
        "source_origin": "AUTHORIZED_OPERATOR_EXPORT",
        "procurement_class": "COMMERCIAL_B2B_CENTER",
        "buyer_inn": INN,
        "buyer_name": "ООО Тестовая компания",
        "buyer_inn_role": "BUYER_CONFIRMED_FROM_PROCEDURE",
        "buyer_region": "Москва",
        "title": "Оказание услуг по сопровождению программных продуктов 1С:Предприятие",
        "description_excerpt": "Требуется подрядчик на ежемесячное сопровождение 1С:Бухгалтерия, обновление и техническую поддержку",
        "published_on": "2026-10-28",
        "deadline_on": "2026-11-05",
    }
    row.update(updates)
    return row


def write(tmp_path, rows):
    target = tmp_path / "approved.json"
    target.write_text(json.dumps({"mode": MODE, "items": rows}, ensure_ascii=False), encoding="utf8")
    return target


def test_valid_service_still_not_fns_qualified(tmp_path):
    p = write(tmp_path, [item()])
    before = p.read_bytes()
    data = review(p, today=TODAY)
    assert data["counts"] == {"input_records": 1, "fns_matched_research": 0,
                              "held": 1, "qualified_real_leads": 0, "crm_ready": 0}
    row = data["records"][0]
    assert row["service_categories"] == ["1C_SUPPORT"]
    assert row["explicit_service_request"] is True
    assert "FNS_SME_NOT_VERIFIED" in row["holds"]
    assert row["source_verified_live"] is False
    assert row["crm_ready"] is False
    assert p.read_bytes() == before


def test_fns_matching_only_research_candidate(tmp_path, monkeypatch):
    import leadpilot.sme_vacancy_audit as module
    def checker(filename, today):
        assert today == TODAY
        return {INN: {"region": "Москва", "fns_category": "SMALL"}}, "2026-10-30"
    monkeypatch.setattr(module, "_read_fns_candidates", checker)
    result = review(write(tmp_path, [item()]), today=TODAY, fns_file=tmp_path / "fns.json")
    assert result["counts"]["fns_matched_research"] == 1
    assert result["counts"]["qualified_real_leads"] == 0
    assert result["records"][0]["status"] == "FNS_MATCHED_RESEARCH_CANDIDATE"
    assert result["records"][0]["crm_ready"] is False


def test_aggregated_government_procurement_excluded(tmp_path):
    result = review(write(tmp_path, [item(procurement_class="THIRD_PARTY_223FZ")]), today=TODAY)
    assert "NON_COMMERCIAL_OR_EXTERNAL_AGGREGATED_NOTICE" in result["records"][0]["holds"]


@pytest.mark.parametrize("updates,reason", [
    ({"published_on": "2026-09-01"}, "PUBLICATION_NOT_RECENT"),
    ({"deadline_on": "2026-10-29"}, "DEADLINE_PASSED"),
    ({"deadline_on": None}, "DEADLINE_UNKNOWN"),
    ({"buyer_inn_role": "ORGANIZER_ONLY"}, "BUYER_IDENTITY_UNCONFIRMED"),
    ({"buyer_inn": None, "buyer_inn_role": "UNVERIFIED"}, "BUYER_IDENTITY_UNCONFIRMED"),
    ({"buyer_region": "Воронеж"}, "REGION_OUTSIDE_OR_UNVERIFIED"),
    ({"buyer_region": None}, "REGION_OUTSIDE_OR_UNVERIFIED"),
    ({"title": "Поставка лицензий 1С:Предприятие",
      "description_excerpt": "Покупка готовой коробочной лицензии на один год, без других услуг"}, "OUTSIDE_TARGET_EXTERNAL_SERVICE"),
])
def test_not_qualified_for_reason(tmp_path, updates, reason):
    result = review(write(tmp_path, [item(**updates)]), today=TODAY)
    assert reason in result["records"][0]["holds"]


@pytest.mark.parametrize("raw", [
    "http://www.b2b-center.ru/search/number/l4616274-4-12-1/",
    "https://b2b-center.ru.evil.example/search/number/l4616274-4-12-1/",
    "https://www.b2b-center.ru/search/industry/crm-sistemy/",
    "https://evil@www.b2b-center.ru/search/number/l4616274-4-12-1/",
    "https://www.b2b-center.ru/search/number/l4616274-4-12-1/?q=secret",
    "https://www.b2b-center.ru/search/number/l4616274-4-12-1/#fragment",
    "https://www.b2b-center.ru/market/view.html?id=invalid",
])
def test_reject_untrusted_non_notice_links(tmp_path, raw):
    with pytest.raises(ValueError, match="B2B_SOURCE_URL_INVALID"):
        review(write(tmp_path, [item(source_url=raw)]), today=TODAY)


def test_accept_direct_market_notice_url(tmp_path):
    result = review(write(tmp_path, [item(
        source_url="https://www.b2b-center.ru/market/view.html?id=4616274")]), today=TODAY)
    assert result["counts"]["input_records"] == 1


@pytest.mark.parametrize("bad", [
    {"buyer_inn": "0000000000"}, {"buyer_inn": "7724850253"},
    {"source_origin": "SCRAPED_WITHOUT_PERMISSION"},
    {"source_origin": "UNKNOWN_RAW_API_SCHEMA"},
    {"buyer_inn": None, "buyer_inn_role": "BUYER_CONFIRMED_FROM_PROCEDURE"},
    {"published_on": "2026-10-32"},
    {"deadline_on": "2026-10-20"},
    {"procurement_id": "abc"},
])
def test_invalid_records_raise(tmp_path, bad):
    with pytest.raises(ValueError):
        review(write(tmp_path, [item(**bad)]), today=TODAY)


def test_duplicate_notice_rejected(tmp_path):
    with pytest.raises(ValueError, match="DUPLICATE"):
        review(write(tmp_path, [item(), item()]), today=TODAY)


def test_independent_notice_ids_on_same_buyer(tmp_path):
    another = item(procurement_id="4616275",
                   source_url="https://www.b2b-center.ru/search/number/l4616275-4-12-1/")
    result = review(write(tmp_path, [item(), another]), today=TODAY)
    assert len({r["stable_signal_id"] for r in result["records"]}) == 2


def test_nine_service_categories():
    examples = {
        "1C_SUPPORT": "Сопровождение 1С",
        "IT_OUTSOURCING": "Техническая поддержка рабочих мест",
        "KKT_MARKING": "Настройка касс и маркировки",
        "TRADE_EQUIPMENT": "Установка торгового оборудования",
        "EDO_SIGNATURE": "Подключение ЭДО Диадок",
        "CRM_BITRIX": "Внедрение CRM Битрикс24",
        "SERVERS_NETWORKS": "Администрирование серверов и сети",
        "INTEGRATIONS": "Настройка интеграции API",
        "BOTS_AI": "Разработка чат-бота с ИИ",
    }
    for category, title in examples.items():
        labels, wanted = classify(title, "Ищем подрядчика для настройки системы")
        assert category in labels
        assert wanted is True


def test_safe_save_no_overwrite_or_path_escape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = review(write(tmp_path, [item()]), today=TODAY)
    save(result, "exports/new.json", "exports/new.csv")
    assert (tmp_path / "exports/new.json").exists()
    assert "crm_ready" in (tmp_path / "exports/new.csv").read_text("utf-8-sig")
    with pytest.raises(ValueError, match="NEW_FILES"):
        save(result, "exports/new.json", "exports/another.csv")
    with pytest.raises(ValueError, match="NEW_FILES"):
        save(result, "../outside.json", "exports/another.csv")
