"""EIS error classification tests: no retries, no TLS bypass, no secret leakage."""
from datetime import date

import httpx
import pytest

from leadpilot.eis_rss import fetch

URL = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1C"


@pytest.mark.parametrize("status,code", [
    (301, "EIS_SOURCE_UNAVAILABLE_OR_REDIRECT_HTTP_301"),
    (302, "EIS_SOURCE_UNAVAILABLE_OR_REDIRECT_HTTP_302"),
    (403, "EIS_HTTP_AUTH_OR_FORBIDDEN_403"),
    (429, "EIS_HTTP_RATE_LIMIT_429"),
    (502, "EIS_HTTP_STATUS_502"),
])
def test_status_is_explicit_and_never_retried(status, code):
    hits = []
    def handler(req):
        hits.append(req.url)
        return httpx.Response(status, headers={"location": "https://evil.example"} if status == 302 else {})
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        with pytest.raises(RuntimeError, match=code):
            fetch(url=URL, client=client, today=date(2026, 10, 31))
    assert len(hits) == 1


@pytest.mark.parametrize("error,expected", [
    (httpx.ConnectTimeout("hidden URL/path/secret"), "EIS_NETWORK_TIMEOUT"),
    (httpx.ReadTimeout("hidden URL/path/secret"), "EIS_NETWORK_TIMEOUT"),
    (httpx.ConnectError("CERTIFICATE_VERIFY_FAILED hidden.url"), "EIS_TLS_TRUST_FAILED"),
    (httpx.ConnectError("dns failure hidden.url"), "EIS_NETWORK_CONNECTION_FAILED"),
])
def test_transport_has_safe_error_code_only(error, expected):
    def fail(_):
        raise error
    with httpx.Client(transport=httpx.MockTransport(fail), follow_redirects=False) as client:
        with pytest.raises(RuntimeError) as caught:
            fetch(url=URL, client=client, today=date(2026, 10, 31))
    assert str(caught.value) == expected
    assert "hidden" not in str(caught.value)


def test_html_captcha_is_not_empty_valid_eis_results():
    with httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text="<html>captcha</html>",
                                   headers={"content-type": "text/html"}))) as client:
        with pytest.raises(RuntimeError, match="EIS_RESPONSE_NOT_XML"):
            fetch(url=URL, client=client, today=date(2026, 10, 31))
