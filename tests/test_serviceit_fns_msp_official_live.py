"""No live FNS download in CI. Verify the official-only, HOLD-only contract."""
from datetime import date, datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import zipfile

from cryptography.fernet import Fernet
import httpx
import pytest

import leadpilot.serviceit_fns_msp_official_live as m


TODAY = date(2026, 11, 5)
URL = "https://www.nalog.gov.ru/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip"
INN = "7700000009"


def xml_zip(*, category="2", version="4.06", member="segment.xml", inn=INN):
    content = (f'<Файл ТипИнф="РЕЕСТРМСП" ВерсФорм="{version}">'
               f'<Документ КатСубМСП="{category}">'
               f'<ОргВклМСП ИННЮЛ="{inn}"/></Документ></Файл>')
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, content)
    return buffer.getvalue()


def test_only_current_official_msp_zip_url_is_accepted():
    assert m.dated_archive_url(URL, TODAY) == date(2026, 10, 10)
    for bad in [
        "https://evil.test/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip",
        "http://www.nalog.gov.ru/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip",
        URL + "?inn=7700000009",
        "https://www.nalog.gov.ru/opendata/7707329152-rsmp/data-10092026-structure-12052026.zip",
    ]:
        with pytest.raises(m.OfficialMspBlocked):
            m.dated_archive_url(bad, TODAY)


def test_picks_latest_only_from_official_passport():
    html = ('<a href="data-10092026-structure-12052026.zip">Old</a>'
            '<a href="data-10102026-structure-12052026.zip">Latest</a>'
            '<a href="https://hostile.example/opendata/7707329152-rsmp/'
            'data-10102026-structure-12052026.zip">Evil</a>')
    calls = []
    def server(req):
        calls.append(str(req.url))
        assert str(req.url) == m.PUBLIC_PAGE
        return httpx.Response(200, text=html)
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        url, date_ = m.discover_official_url(client, today=TODAY)
    assert url == URL
    assert date_ == date(2026, 10, 10)
    assert len(calls) == 1


def test_picks_nothing_from_html_without_verified_current_link():
    with httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<a href='https://evil.example/fake.zip'>X</a>")
            )) as client:
        with pytest.raises(m.OfficialMspBlocked, match="NO_CURRENT"):
            m.discover_official_url(client, today=TODAY)


def test_head_get_are_bounded_exact_sha256_and_no_inn_sent(tmp_path):
    payload = xml_zip()
    methods = []
    def server(req):
        methods.append((req.method, str(req.url)))
        assert str(req.url) == URL
        assert "inn" not in str(req.url).lower()
        return httpx.Response(200, content=payload if req.method == "GET" else b"",
            headers={"content-length":str(len(payload)),
                     "content-type":"application/zip"})
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        result = m.fetch_archive(client, URL, date(2026,10,10),
                                 TODAY, tmp_path/"official.zip")
    assert [v[0] for v in methods] == ["HEAD", "GET"]
    assert result["sha256"] == sha256(payload).hexdigest()
    assert result["size_bytes"] == len(payload)


def test_official_zip_msp_membership_cannot_authorize_egrul_or_score(tmp_path):
    p = tmp_path / "msp.zip"
    p.write_bytes(xml_zip())
    actual, count = m.scan_zip_for_inns(p, {INN})
    assert actual == {INN: "2"}
    assert count == 1
    assert m.NO_SIDE_EFFECTS["egrul_active_verified"] is False
    assert m.NO_SIDE_EFFECTS["corporate_stop_verified"] is False
    assert m.NO_SIDE_EFFECTS["formal_score_authorized"] is False
    assert m.NO_SIDE_EFFECTS["crm_writes"] == 0


def test_absent_company_in_supplied_snapshot_is_not_disproof(tmp_path):
    p = tmp_path / "msp.zip"
    p.write_bytes(xml_zip(inn="7700000000"))
    actual, count = m.scan_zip_for_inns(p, {INN})
    assert actual == {}
    assert count == 1
    assert m.NO_SIDE_EFFECTS["independent_fns_signature_verified"] is False


@pytest.mark.parametrize("version, member", [
    ("9.99", "a.xml"), ("4.06", "../escaped.xml"),
    ("4.06", "fake.txt"),
])
def test_unknown_format_or_unsafe_member_fails_closed(tmp_path, version, member):
    p = tmp_path/"snapshot.zip"
    p.write_bytes(xml_zip(version=version, member=member))
    with pytest.raises(m.OfficialMspBlocked):
        m.scan_zip_for_inns(p, {INN})


def test_malicious_redirect_fails_without_external_get(tmp_path):
    calls=[]
    def server(req):
        calls.append(str(req.url))
        return httpx.Response(302, headers={"location":"https://example.com/secret.zip"})
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(m.OfficialMspBlocked):
            m.fetch_archive(client, URL, date(2026,10,10), TODAY,
                            tmp_path/"unused.zip")
    assert calls == [URL]


def test_no_new_cards_means_no_fns_fetch_and_no_ledger_write(monkeypatch):
    monkeypatch.setattr(m, "validate_webhook",
                        lambda x:"https://serviseit.bitrix24.ru/rest/123/secret")
    monkeypatch.setattr(m, "after_activation", lambda env,now:now)
    monkeypatch.setattr(m, "calibrate_known_card", lambda *args: True)
    monkeypatch.setattr(m, "discover", lambda *args:[])
    monkeypatch.setattr(m, "discover_official_url",
                        lambda *args,**kwargs:pytest.fail("Do not contact FNS"))
    monkeypatch.setattr(m, "OfficialMspFence",
                        lambda *args,**kwargs:pytest.fail("Do not write ledger"))
    env = {
        "GITHUB_REPOSITORY":m.REPOSITORY, "GITHUB_REF":"refs/heads/main",
        "GITHUB_EVENT_NAME":"schedule",
        "SERVICEIT_FNS_MSP_AUTO_ENABLED":m.ENABLED,
    }
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("Network must not be touched"))) as client:
        out = m.run(client, env, now=datetime(2026,11,5,18,tzinfo=timezone.utc))
    assert out["status"] == "FNS_NO_NEW_CARDS"
    assert out["official_archive_downloaded"] is False
    assert out["encrypted_holding_receipts_saved"] == 0
    assert out["crm_writes"] == 0


def test_invalid_new_inn_means_no_fns_download(monkeypatch):
    monkeypatch.setattr(m, "validate_webhook",
                        lambda x:"https://serviseit.bitrix24.ru/rest/123/secret")
    monkeypatch.setattr(m, "after_activation", lambda env,now:now)
    monkeypatch.setattr(m, "calibrate_known_card", lambda *args:True)
    monkeypatch.setattr(m, "discover", lambda *args:[123])
    monkeypatch.setattr(m, "read_item",
                        lambda *args:{m.PREFIX+"COMPANY_INN":"not-an-inn"})
    monkeypatch.setattr(m, "discover_official_url",
                        lambda *args,**kwargs:pytest.fail("Do not contact FNS"))
    env = {
        "GITHUB_REPOSITORY":m.REPOSITORY, "GITHUB_REF":"refs/heads/main",
        "GITHUB_EVENT_NAME":"schedule",
        "SERVICEIT_FNS_MSP_AUTO_ENABLED":m.ENABLED,
    }
    with httpx.Client() as client:
        out=m.run(client,env,now=datetime(2026,11,5,18,tzinfo=timezone.utc))
    assert out["status"]=="FNS_NO_VALID_NEW_INNS"
    assert out["invalid_inn"]==1
    assert out["official_archive_downloaded"] is False


def test_owner_marker_unauthorized_issue_context_fails_before_network():
    env={
        "GITHUB_REPOSITORY":m.REPOSITORY, "GITHUB_REF":"refs/heads/main",
        "GITHUB_EVENT_NAME":"issue_comment",
        "SERVICEIT_FNS_MSP_AUTO_ENABLED":m.ENABLED,
        "GITHUB_EVENT_PATH":"/never-exists-owner-event.json"
    }
    with httpx.Client() as client:
        with pytest.raises(m.OfficialMspBlocked):
            m.run(client,env,now=datetime(2026,11,5,18,tzinfo=timezone.utc))


def test_card_and_inn_ledger_paths_are_keyed_not_enumerable():
    key = Fernet.generate_key().decode()
    with httpx.Client() as client:
        receipt=m.OfficialMspFence(
            client,"test-github-token-123456",key,date(2026,10,10))
        url=receipt.url(12345)
        assert "12345" not in url
        assert INN not in url
        assert INN not in receipt.inn_digest(INN)
        assert receipt.url(12345) != receipt.url(12346)
