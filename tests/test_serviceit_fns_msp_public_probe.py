"""Official FNS public metadata HEAD probe: no unauthorised sources, no GET."""
import httpx

from leadpilot.serviceit_fns_msp_public_probe import (
    inspect, SOURCE, MAX_ARCHIVE,
)


def client_fn(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_official_zip_metadata_is_only_preflight_not_company_verified():
    calls = []
    def response(req):
        calls.append(req.method)
        return httpx.Response(200, headers={
            "Content-Type":"application/zip","Content-Length":"1500000000"})
    with client_fn(response) as client:
        out=inspect(client)
    assert calls==["HEAD"]
    assert out["status"]=="FNS_ZIP_HEAD_READY_FOR_CONTROLLED_INGEST"
    assert out["downloaded"] is False
    assert out["egrul_status_verified"] is False
    assert out["signature_or_digest_verified"] is False


def test_malicious_redirect_fails_before_contact():
    calls=[]
    def response(req):
        calls.append(str(req.url))
        return httpx.Response(302,headers={"Location":"https://example.com/private.zip"})
    with client_fn(response) as client:
        out=inspect(client)
    assert out["status"]=="FNS_OFFICIAL_SOURCE_IDENTITY_BLOCKED"
    assert len(calls)==1


def test_official_redirect_allowed_but_size_must_fit():
    urls=[]
    def response(req):
        urls.append(str(req.url))
        if req.url.host=="www.nalog.gov.ru":
            return httpx.Response(301,headers={"Location":
                "https://file.nalog.ru/opendata/7707329152-rsmp/"
                "data-10102026-structure-12052026.zip"})
        return httpx.Response(200,headers={"Content-Length":str(MAX_ARCHIVE+1),
            "Content-Type":"application/zip"})
    with client_fn(response) as client:
        out=inspect(client)
    assert len(urls)==2
    assert out["status"]=="FNS_ZIP_OUTSIDE_RUNNER_BUDGET"


def test_html_captcha_never_accepted_as_archive():
    with client_fn(lambda req:httpx.Response(200,headers={
            "Content-Type":"text/html","Content-Length":"2200"})) as client:
        out=inspect(client)
    assert out["status"]=="FNS_HEAD_NOT_ZIP"


def test_no_length_never_fabricates_archive_size():
    with client_fn(lambda req:httpx.Response(200,headers={
            "Content-Type":"application/zip"})) as client:
        assert inspect(client)["status"]=="FNS_HEAD_LENGTH_UNKNOWN"


def test_invalid_source_never_contacted():
    with client_fn(lambda req: (_ for _ in ()).throw(
            AssertionError("should not reach network"))) as client:
        out=inspect(client,"https://evil.example/official.zip")
    assert out["status"]=="FNS_OFFICIAL_SOURCE_IDENTITY_BLOCKED"
