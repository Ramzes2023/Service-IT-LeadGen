"""Public source intake is isolated and OFF unless HMAC verified."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from leadpilot import serviceit_vps_b2b_ingress as ingress


def test_health_never_has_secrets(monkeypatch):
    monkeypatch.delenv("SERVICEIT_VPS_B2B_RECEIVER_ENABLED",raising=False)
    client=TestClient(ingress.app)
    obj=client.get("/integrations/serviceit/b2b-center/health")
    assert obj.status_code==200
    assert obj.json()=={
        "status":"B2B_INGRESS_ALIVE","source_enabled":False,"crm_writes":0}
    assert client.post("/integrations/serviceit/b2b-center/notice",
                       json={}).status_code==503


def test_public_ingress_rejects_bad_transport(monkeypatch):
    monkeypatch.setenv("SERVICEIT_VPS_B2B_RECEIVER_ENABLED","YES")
    client=TestClient(ingress.app)
    assert client.post("/integrations/serviceit/b2b-center/notice",
                       data="{}",headers={"content-type":"text/plain"}).status_code==415
    assert client.post("/integrations/serviceit/b2b-center/notice",
                       json={"fake":"notice"}).status_code==403


def test_caddy_uses_dedicated_loopback_port_only():
    root=Path(__file__).parents[1]
    code=(root/"deploy/serviceit-vps-b2b.caddy.fragment").read_text("utf-8")
    assert "127.0.0.1:18781" in code
    assert "/integrations/serviceit/b2b-center/notice" in code
    assert "reverse_proxy 127.0.0.1:18780" not in code
    script=(root/"integrations/apps_script/Code.gs").read_text("utf-8")
    assert "https://leadgen.serviseit.ru/integrations/serviceit/b2b-center/notice" in script
    assert "https://api.github.com/repos/" not in script
    assert "SERVICEIT_VPS_B2B_ACTIVATION_MS" in script
    assert "activateVpsB2BCenter" in script
