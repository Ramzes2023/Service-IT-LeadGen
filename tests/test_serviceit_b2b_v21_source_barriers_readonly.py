"""No secret exposure and no bypass in V21 signed-source read-only audit."""
from pathlib import Path
import importlib.util
import httpx


def source():
    spec=importlib.util.spec_from_file_location(
        "v21source", "ops/serviceit_vps_b2b_v21_source_barriers_readonly.py")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

URL="https://www.b2b-center.ru/market/example/tender-4675475/"
TID="4675475"


def test_robots_403_and_no_page_fetch(monkeypatch):
    mod=source()
    monkeypatch.setattr(mod,"_public_dns",lambda _:True)
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        assert req.url.path=="/robots.txt"
        return httpx.Response(403, text="Forbidden")
    with httpx.Client(transport=httpx.MockTransport(handler),
                      trust_env=False,follow_redirects=False) as client:
        assert mod.inspect(client,URL,TID)==("ROBOTS_HTTP_403","NO",0)
    assert calls==["/robots.txt"]


def test_robots_disallow_and_no_page_fetch(monkeypatch):
    mod=source()
    monkeypatch.setattr(mod,"_public_dns",lambda _:True)
    def handler(req):
        assert req.url.path=="/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /market/",
                              headers={"content-type":"text/plain"})
    with httpx.Client(transport=httpx.MockTransport(handler),
                      trust_env=False) as client:
        assert mod.inspect(client,URL,TID)==("ROBOTS_200_DISALLOW","NO",0)


def test_allowed_public_source_counts_only_same_host_document(monkeypatch):
    mod=source()
    monkeypatch.setattr(mod,"_public_dns",lambda _:True)
    import leadpilot.serviceit_public_evidence_research as public
    monkeypatch.setattr(public,"_public_dns",lambda _:True)
    def handler(req):
        if req.url.path=="/robots.txt":
            return httpx.Response(200,text="User-agent: *\nAllow: /",
                                  headers={"content-type":"text/plain"})
        if req.url.path=="/market/example/tender-4675475/":
            return httpx.Response(200,text=(
                '<html><body>ПКО № 4675475 Серверы для МКБ'
                '<a href="/files/tz.pdf">Скачать ТЗ</a>'
                '<a href="https://badsite.test/malware.pdf">Скачать</a>'
                '</body></html>'),headers={"content-type":"text/html"})
        raise AssertionError("Unexpected request")
    with httpx.Client(transport=httpx.MockTransport(handler),
                      trust_env=False) as client:
        assert mod.inspect(client,URL,TID)==("ROBOTS_200_ALLOW","YES",1)


def test_audit_source_workflow_never_writes_or_spends_ai():
    flow=Path(".github/workflows/serviceit-vps-b2b-v21-source-barriers-readonly.yml").read_text("utf8")
    code=Path("ops/serviceit_vps_b2b_v21_source_barriers_readonly.py").read_text("utf8")
    assert "github.actor == 'Ramzes2023'" in flow
    assert "github.event.issue.number == 489" in flow
    assert "SERVICEIT_B2B_V21_SOURCES_READONLY_489" in flow
    assert "StrictHostKeyChecking=yes" in flow
    assert "git diff --quiet" in flow
    assert "SET TRANSACTION READ ONLY" in code
    assert 'follow_redirects=False' in code
    assert "not permitted" in code
    assert "crm.item.update" not in code
    assert "crm.timeline.comment.add" not in code
    assert "brave_mirror_search" not in code
    assert "investigate_routerai" not in code
    assert "V21_SOURCE_" in code
