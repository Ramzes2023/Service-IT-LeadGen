"""Offline official FNS passport and safe ZIP download test contracts."""
from datetime import date
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from leadpilot.serviceit_v2_fns_release import (
    PASSPORT, FNSDownloadBlocked, discover_release, parse_official_passport,
    download_release, Release,
)

ZIP = "https://www.nalog.gov.ru/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip"
XSD = "https://file.nalog.ru/opendata/7707329152-rsmp/structure-12052026.xsd"


def passport(zip_url=ZIP, xsd_url=XSD, change="10.10.2026"):
    return ('''<html><body><table>
    <tr><td>8</td><td>Гиперссылка (URL) на набор</td><td>
    <a href="''' + zip_url + '''">Скачать</a></td></tr>
    <tr><td>10</td><td>Структура</td><td>
    <a href="''' + xsd_url + '''">XML-схема</a></td></tr>
    <tr><td>12</td><td>Дата последнего внесения изменений</td><td>'''
    + change + '''</td></tr></table></body></html>''').encode()


def test_passport_extracts_exact_current_zip_and_version():
    raw = passport()
    rel = parse_official_passport(raw)
    assert rel.zip_url == ZIP
    assert rel.xsd_url == XSD
    assert rel.snapshot_date == date(2026, 10, 10)
    assert rel.schema_date == date(2026, 5, 12)
    assert rel.passport_sha256 == sha256(raw).hexdigest()


def test_official_http_only_read_passport():
    seen = []
    def handler(req):
        seen.append((req.method,str(req.url)))
        return httpx.Response(200, content=passport())
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        found = discover_release(http)
    assert found.snapshot_date == date(2026,10,10)
    assert seen == [("GET",PASSPORT)]


@pytest.mark.parametrize("zip_url", [
    "http://www.nalog.gov.ru/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip",
    "https://www.nalog.gov.ru.evil.tld/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip",
    "https://www.nalog.gov.ru/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip?token=hi",
    "https://www.nalog.gov.ru/opendata/other/data-10102026-structure-12052026.zip",
    "https://file.nalog.ru/opendata/7707329152-rsmp/data-10102026-structure-12052025.zip"
])
def test_unsafe_or_mismatched_release_is_rejected(zip_url):
    with pytest.raises(FNSDownloadBlocked):
        parse_official_passport(passport(zip_url=zip_url))


def test_date_must_correspond_to_current_release():
    with pytest.raises(FNSDownloadBlocked, match="FNS_RELEASE_DATE_DISAGREES"):
        parse_official_passport(passport(change="10.09.2026"))


def test_bounded_official_download_only_after_explicit_approval(tmp_path):
    data = b"PK\x03\x04" + b"SYNTHETIC-ZIP-CONTENT-" * 70
    d = tmp_path/"actual-release.zip"
    rel = parse_official_passport(passport())
    seen=[]
    def handler(req):
        seen.append((req.method,str(req.url)))
        return httpx.Response(200,content=data)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(FNSDownloadBlocked,match="NOT_APPROVED"):
            download_release(http,rel,d,explicitly_approved=False)
        assert seen == []
        result = download_release(http,rel,d,explicitly_approved=True)
    assert result["zip_sha256"] == sha256(data).hexdigest()
    assert d.read_bytes() == data
    assert result["crm_writes"] == 0
    assert seen == [("GET",ZIP)]


def test_malicious_redirect_cannot_fetch_non_fns_host(tmp_path):
    rel = parse_official_passport(passport())
    requests=[]
    def handler(req):
        requests.append(str(req.url))
        return httpx.Response(302,headers={
            "Location": "https://evil.invalid/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip"})
    dest=tmp_path/"release.zip"
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(FNSDownloadBlocked, match="NO_PARTIAL"):
            download_release(http,rel,dest,explicitly_approved=True)
    assert requests == [ZIP]
    assert not dest.exists() and not (tmp_path/"release.zip.part").exists()


def test_too_big_download_is_stopped_and_removed(tmp_path):
    rel = parse_official_passport(passport())
    dest=tmp_path/"big.zip"
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200,headers={"Content-Length":"9000"},
                                        content=b"PK\x03\x04" +b"x"*1100))) as http:
        with pytest.raises(FNSDownloadBlocked,match="NO_PARTIAL"):
            download_release(http,rel,dest,explicitly_approved=True,
                             max_download_bytes=1024)
    assert not dest.exists() and not (tmp_path/"big.zip.part").exists()


def test_different_release_redirect_fails_closed(tmp_path):
    rel = parse_official_passport(passport())
    dest=tmp_path/"current.zip"
    calls=[]
    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(302,headers={
            "Location": "https://file.nalog.ru/opendata/7707329152-rsmp/data-10092026-structure-12052026.zip"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(FNSDownloadBlocked,match="NO_PARTIAL"):
            download_release(http,rel,dest,explicitly_approved=True)
    assert calls == [ZIP]
    assert not dest.exists()
