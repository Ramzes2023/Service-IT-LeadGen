"""No network, no Bitrix and no GitHub writes in offline audit tests."""
import httpx
from leadpilot import serviceit_b2b_pilot_readonly_audit as audit


def owner_event():
    return {
        "action":"created","repository":{"full_name":audit.REPO},
        "issue":{"number":83},"sender":{"login":audit.OWNER},
        "comment":{"body":audit.MARKER,"user":{"login":audit.OWNER}},
    }


def test_owner_only():
    assert audit.permitted(owner_event())
    e=owner_event()
    e["sender"]["login"]="attacker"
    assert not audit.permitted(e)


def test_missing_secrets_reject_before_network():
    with httpx.Client(transport=httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(AssertionError("unexpected HTTP")))) as client:
        assert audit.run(owner_event(),github_token="",hook="",client=client) == {
            "status":"CREDENTIAL_OR_TOKEN_UNAVAILABLE","crm_writes":0}


def test_audit_gates_do_not_issue_writes(monkeypatch):
    monkeypatch.setattr(audit,"load_existing_review",
                        lambda client,token: {"existing":"synthetic"})
    monkeypatch.setattr(audit,"notice_from_event",lambda event: ({
        "tender_id":audit.TENDER_ID},["IT_SUPPORT"]))
    monkeypatch.setattr(audit,"hold_payload",lambda notice,services: {
        "fields":{"UF_CRM_13_SVCIT_SIGNAL_ID":"B2B-CENTER-"+audit.TENDER_ID}})
    class RO:
        def __init__(self,hook,*,client):
            self.base="https://example.invalid/rest"
    monkeypatch.setattr(audit,"ReadOnlyBitrix",RO)
    monkeypatch.setattr(audit,"preflight",lambda rest: {"status":"PASS"})
    monkeypatch.setattr(audit,"exact_duplicate",
                        lambda client,base,signal: [])
    class Ledger:
        def __init__(self,client,token): pass
        def markers(self): return []
    monkeypatch.setattr(audit,"GitHubLedger",Ledger)
    with httpx.Client(transport=httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(AssertionError("unexpected HTTP")))) as c:
        response=audit.run(owner_event(),github_token="testtoken",hook="testhook",client=c)
    assert response["status"]=="READONLY_SOURCE_AND_CRM_GATES_PASS"
    assert response["crm_writes"]==0
