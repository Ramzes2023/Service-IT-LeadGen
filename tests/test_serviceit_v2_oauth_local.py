"""Offline owner PKCE/OAuth consent and Gmail account-verification tests."""
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import json
import os
import stat
from threading import Thread
from urllib.request import urlopen

import httpx
import pytest

from leadpilot.serviceit_v2_oauth_local import (
    SCOPE, AUTH_URL, TOKEN_URL, PROFILE_URL, SetupBlocked,
    load_desktop_client, pkce_pair, authorization_url, exchange_code,
    verified_gmail_address, save_owner_secret, owner_loopback_code,
)

EXPECTED = "owner@example.org"
ID = "123456789000-test.apps.googleusercontent.com"
SECRET = "GOCSPX-SYNTHETIC-SECRET-DO-NOT-USE"


def desktop(tmp_path, *, kind="installed"):
    f=tmp_path/"google-client.json"
    f.write_text(json.dumps({kind:{
        "client_id":ID,"client_secret":SECRET,
        "auth_uri":"https://accounts.google.com/o/oauth2/auth",
        "token_uri":TOKEN_URL,
        "redirect_uris":["http://localhost"]
    }}))
    return f


def test_desktop_client_only_no_mixed_or_invalid_hosts(tmp_path):
    assert load_desktop_client(desktop(tmp_path))["client_id"] == ID
    other = tmp_path/"web.json"
    other.write_text(json.dumps({"web":{"client_id":ID}}))
    with pytest.raises(SetupBlocked,match="GOOGLE_DESKTOP_CLIENT_REQUIRED"):
        load_desktop_client(other)
    link=tmp_path/"linked.json"
    link.symlink_to(desktop(tmp_path))
    with pytest.raises(SetupBlocked,match="GOOGLE_CLIENT_JSON_NOT_FOUND"):
        load_desktop_client(link)


def test_pkce_authorization_scopes_are_readonly_and_offline():
    verifier,challenge=pkce_pair()
    assert 43<=len(verifier)<=128 and len(challenge)==43
    redirect="http://127.0.0.1:8767/callback"
    uri=authorization_url(ID,redirect,challenge,"test-state-is-unguessable-1234")
    assert uri.startswith(AUTH_URL+"?")
    q=parse_qs(urlsplit(uri).query)
    assert q["scope"]==[SCOPE]
    assert q["access_type"]==["offline"] and q["prompt"]==["consent"]
    assert q["code_challenge_method"]==["S256"]
    assert q["redirect_uri"]==[redirect]
    assert not any("gmail.modify" in value or "gmail.send" in value
                   for values in q.values() for value in values)


def responses(*,scope=SCOPE,refresh=True,mail=EXPECTED):
    seen=[]
    def handler(req):
        seen.append((req.method,str(req.url)))
        if str(req.url)==TOKEN_URL:
            return httpx.Response(200,json={
                "access_token":"SYNTHETIC-ACCESS-TOKEN-0123456789",
                **({"refresh_token":"SYNTHETIC-REFRESH-TOKEN-0123456789"} if refresh else {}),
                "scope":scope,"token_type":"Bearer"})
        if str(req.url)==PROFILE_URL:
            assert req.headers["Authorization"].startswith("Bearer ")
            return httpx.Response(200,json={"emailAddress":mail,"messagesTotal":0})
        pytest.fail("unexpected network call")
    return httpx.MockTransport(handler),seen


def test_exchange_offline_token_and_exact_account_no_secret_print(capsys):
    transport,seen=responses()
    cfg={"client_id":ID,"client_secret":SECRET}
    with httpx.Client(transport=transport,trust_env=False) as http:
        refresh,access=exchange_code(http,cfg,code="TEST-AUTH-CODE-123456789",
                        verifier=pkce_pair()[0],
                        redirect_uri="http://127.0.0.1:8767/callback")
        address=verified_gmail_address(http,access,EXPECTED)
    assert refresh.startswith("SYNTHETIC-REFRESH")
    assert address==EXPECTED
    assert [m for m,_ in seen]==["POST","GET"]
    out=capsys.readouterr().out
    assert "SYNTHETIC-REFRESH" not in out and SECRET not in out


def test_wrong_google_identity_denied():
    transport,_=responses(mail="different@example.org")
    with httpx.Client(transport=transport) as http:
        with pytest.raises(SetupBlocked,match="GMAIL_WRONG_ACCOUNT_REFUSED"):
            verified_gmail_address(http,"TEST-ACCESS-TOKEN-12345",EXPECTED)


@pytest.mark.parametrize("scope,refresh", [
    ("https://www.googleapis.com/auth/gmail.send",True),
    (SCOPE,False),
])
def test_wrong_scope_or_no_offline_refresh_denied(scope,refresh):
    transport,_=responses(scope=scope,refresh=refresh)
    with httpx.Client(transport=transport) as http:
        with pytest.raises(SetupBlocked):
            exchange_code(http,{"client_id":ID,"client_secret":SECRET},
                          code="TEST-AUTH-CODE-12345",
                          verifier=pkce_pair()[0],
                          redirect_uri="http://127.0.0.1:8767/callback")


def test_owner_only_file_never_overwrites_or_logs_private_token(tmp_path,capsys):
    root=tmp_path/"secrets"
    root.mkdir(mode=0o700)
    if os.name != "nt":
        root.chmod(0o700)
    target=root/"serviceit-gmail.json"
    cfg={"client_id":ID,"client_secret":SECRET}
    refresh="SYNTHETIC-REFRESH-SECRET-NEVER-PRINT"
    result=save_owner_secret(target,cfg,refresh,EXPECTED)
    assert result["refresh_token_printed"] is False
    assert result["crm_writes"]==0 and target.exists()
    body=json.loads(target.read_text("utf-8"))
    assert body["refresh_token"]==refresh and body["scope"]==SCOPE
    assert body["expected_email"]==EXPECTED
    if os.name!="nt":
        assert (target.stat().st_mode & 0o777) == 0o600
    with pytest.raises(SetupBlocked,match="PRIVATE_OUTPUT_NOT_READY_OR_ALREADY_EXISTS"):
        save_owner_secret(target,cfg,refresh,EXPECTED)
    assert json.loads(target.read_text("utf-8"))==body
    assert refresh not in capsys.readouterr().out


def test_loopback_callback_requires_random_state_and_returns_only_auth_code():
    seen=[]
    def open_browser(url):
        q=parse_qs(urlsplit(url).query)
        callback=q["redirect_uri"][0]
        state=q["state"][0]
        def visit():
            # Wrong callback may not hijack the code, then correct one is valid.
            with pytest.raises(Exception):
                urlopen(callback+"?state=incorrect&code=FAKE-CODE",timeout=5)
            response=urlopen(callback+"?state="+state+"&code=TEST-SERVER-CODE-VALID",timeout=5)
            response.read()
        t=Thread(target=visit,daemon=True)
        t.start()
        seen.append(t)
        return True
    code,verifier,redirect=owner_loopback_code(
        client_id=ID, timeout_seconds=30, browser_open=open_browser)
    seen[0].join(timeout=2)
    assert code=="TEST-SERVER-CODE-VALID"
    assert redirect.startswith("http://127.0.0.1:")
    assert len(verifier)>=43
