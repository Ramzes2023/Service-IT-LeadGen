"""Static synthetic FNS passport/ZIP headers: zero CRM and zero ZIP GET."""
from datetime import datetime, timezone

import httpx
import pytest

from leadpilot import serviceit_fns_msp_passport_probe as p
from leadpilot.serviceit_fns_msp_official_live import OfficialMspBlocked

NOW = datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)
PASS = "https://www.nalog.gov.ru/opendata/7707329152-rsmp/"
ZIP = PASS + "data-10102026-structure-12052026.zip"
FILE = "https://file.nalog.ru/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip"


def test_official_current_passport_then_one_head_and_no_zip_download():
    calls=[]
    def server(req):
        calls.append((req.method,str(req.url)))
        if str(req.url)==PASS:
            assert req.method=="GET"
            return httpx.Response(200,text='<a href="data-10102026-structure-12052026.zip">Release</a>')
        assert req.method=="HEAD" and str(req.url)==ZIP
        return httpx.Response(200,headers={
            "content-length":"2246598554", "content-type":"application/zip"
        })
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        r=p.inspect(client,now=NOW)
    assert calls==[("GET",PASS),("HEAD",ZIP)]
    assert r["status"]=="FNS_MSP_PUBLIC_PASSPORT_READY"
    assert r["official_snapshot_date"]=="2026-10-10"
    assert r["zip_size_bytes"]==2246598554
    assert r["zip_downloaded"] is False
    assert r["egrul_egrip_verified"] is False
    assert r["formal_score_authorized"] is False
    assert r["crm_writes"]==0


def test_approved_same_dataset_redirect_keeps_head_only():
    calls=[]
    def server(req):
        calls.append((req.method,str(req.url)))
        if str(req.url)==PASS:
            return httpx.Response(200,text='<a href="'+ZIP+'">Open</a>')
        if str(req.url)==ZIP:
            return httpx.Response(302,headers={"location":FILE})
        assert str(req.url)==FILE and req.method=="HEAD"
        return httpx.Response(200,headers={
            "content-type":"application/zip","content-length":"2000000"})
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        r=p.inspect(client,now=NOW)
    assert r["zip_size_bytes"]==2000000
    assert [v[0] for v in calls]==["GET","HEAD","HEAD"]


def test_untrusted_redirect_stops_before_foreign_host():
    calls=[]
    def server(req):
        calls.append(str(req.url))
        if str(req.url)==PASS:
            return httpx.Response(200,text='<a href="'+ZIP+'">Open</a>')
        return httpx.Response(302,headers={"location":"https://other.example/zip"})
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(OfficialMspBlocked):
            p.inspect(client,now=NOW)
    assert calls==[PASS,ZIP]


@pytest.mark.parametrize("headers",[
    {"content-type":"text/html","content-length":"2000000"},
    {"content-type":"application/zip"},
    {"content-type":"application/zip","content-length":"0"},
    {"content-type":"application/zip","content-length":str(5*1024**3)},
])
def test_invalid_zip_response_stays_unknown(headers):
    def server(req):
        if str(req.url)==PASS:
            return httpx.Response(200,text='<a href="'+ZIP+'">Open</a>')
        return httpx.Response(200,headers=headers)
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(OfficialMspBlocked):
            p.inspect(client,now=NOW)
