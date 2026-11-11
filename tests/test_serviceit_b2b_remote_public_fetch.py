"""Offline checks: nothing reaches a real source, provider, VPS or Bitrix."""
import httpx
from leadpilot.serviceit_b2b_remote_public_fetch import (
    approved_url, verified_excerpt, fetch_remote_public_evidence,
)

ID = "4673595"
TITLE = "Оказание услуг по предоставлению ИТ-специалистов"
TENDEM = "https://tendem.ru/tenders/16548471"
B2B = ("https://www.b2b-center.ru/app/market-next/"
       "zakupka-serverov-zokii-s-pnr-i-uslugami-rasshirennoi-tp-na-3-goda/"
       "tender-4673971/")
KEY = "synthetic-valid-key-12345"
TEXT = ("Запрос котировок · B2B-Center · опубликовано 09.11.2026\n"
        + TITLE + "\nЗаказчик ООО ГЛОБУС-ИТ\n"
        + "Номер у источника: " + ID + "\n"
        + "Подача заявок до 23.11.2026 17:00 МСК")


def test_tendem_full_text_proof_and_provider():
    assert "ГЛОБУС-ИТ" in verified_excerpt(TENDEM, TEXT, ID, TITLE)

    def transport(req):
        assert req.method == "POST"
        assert req.url.host == "api.fetch.tinyfish.ai"
        assert req.headers["x-api-key"] == KEY
        assert b"4673595" not in req.content
        return httpx.Response(
            200, json={"results": [{"url": TENDEM, "final_url": TENDEM,
                                     "text": TEXT}], "errors": []},
            headers={"content-type": "application/json"})

    with httpx.Client(transport=httpx.MockTransport(transport),
                      trust_env=False, follow_redirects=False) as c:
        result = fetch_remote_public_evidence(
            c, urls=[TENDEM], tender_id=ID, subject=TITLE, api_key=KEY)
    assert result["status"] == "REMOTE_PUBLIC_EVIDENCE_VALIDATED"
    assert result["pages"][0]["url"] == TENDEM
    assert result["contacts"] == []


def test_official_primary_requires_exact_number_title_and_path():
    title = "Закупка серверов ЗОКИИ с ПНР и услугами расширенной ТП на 3 года"
    text = title + "\n№ 4673971\nКонтакты только зарегистрированным"
    assert verified_excerpt(B2B, text, "4673971", title)
    assert not verified_excerpt(B2B, text, "4673595", title)
    assert not verified_excerpt(B2B, text, "4673971", TITLE)


def test_blocked_source_wrong_id_and_redirect_never_valid():
    assert not verified_excerpt(
        TENDEM, TEXT.replace(ID, "4673596"), ID, TITLE)
    def blocked(_):
        return httpx.Response(
            200, json={"results": [],
                       "errors": [{"url": TENDEM, "error": "target_http_error",
                                   "status": 403}]},
            headers={"content-type": "application/json"})
    with httpx.Client(transport=httpx.MockTransport(blocked),
                      trust_env=False) as c:
        assert fetch_remote_public_evidence(
            c, urls=[TENDEM], tender_id=ID, subject=TITLE, api_key=KEY) is None
    def redirected(_):
        return httpx.Response(
            200, json={"results": [{"url": TENDEM,
                                    "final_url": "https://example.com/login",
                                    "text": TEXT}]},
            headers={"content-type": "application/json"})
    with httpx.Client(transport=httpx.MockTransport(redirected),
                      trust_env=False) as c:
        assert fetch_remote_public_evidence(
            c, urls=[TENDEM], tender_id=ID, subject=TITLE, api_key=KEY) is None


def test_no_key_and_only_approved_public_detail_urls():
    assert approved_url(TENDEM)
    assert approved_url(B2B)
    for url in ("http://tendem.ru/tenders/16548471",
                "https://zakupki.kontur.ru/4673595_4",
                "https://127.0.0.1/tenders/16548471",
                "https://tendem.ru.evil.org/tenders/16548471",
                "https://tendem.ru/tenders/16548471?x=y",
                "https://me:secret@tendem.ru/tenders/16548471"):
        assert not approved_url(url)
    def no_network(_):
        raise AssertionError("Network called when fallback disabled")
    with httpx.Client(transport=httpx.MockTransport(no_network),
                      trust_env=False) as c:
        assert fetch_remote_public_evidence(
            c, urls=[TENDEM], tender_id=ID, subject=TITLE, api_key="") is None
