"""Corporate STOP exact-INN precheck is a positive-only HOLD, not clearance."""
import json
import pytest
from leadpilot import serviceit_corporate_stop_inn as s

INN="7700000009"
KEY="offlinetest_secret_not_a_real_secret_"+"z"*40
APPROVED={"SERVICEIT_CORPORATE_STOP_INN_ENABLED":s.ENABLED}

def approved(*, entries=None):
    body=s.make_approved_manifest([INN] if entries is None else entries,KEY)
    return {**APPROVED,"SERVICEIT_CORPORATE_STOP_INN_KEY":KEY,
            "SERVICEIT_CORPORATE_STOP_INN_MANIFEST":json.dumps(body)}

def test_approved_list_blocks_positive_inn_without_disclosing_raw_data():
    env=approved()
    assert s.precheck(INN,env)=="HIT"
    assert INN not in env["SERVICEIT_CORPORATE_STOP_INN_MANIFEST"]
    assert KEY not in env["SERVICEIT_CORPORATE_STOP_INN_MANIFEST"]
    assert s.precheck("7700000016",env)=="NO_MATCH_IN_INN_SCOPE_ONLY"

def test_empty_list_is_not_clearance_and_missing_approval_is_unknown():
    assert s.precheck(INN,{})=="NOT_CONFIGURED"
    assert s.precheck(INN,approved(entries=[]))=="NO_MATCH_IN_INN_SCOPE_ONLY"
    assert s.precheck(INN,{
        "SERVICEIT_CORPORATE_STOP_INN_MANIFEST":"malformed",
        "SERVICEIT_CORPORATE_STOP_INN_KEY":KEY})=="NOT_CONFIGURED"

@pytest.mark.parametrize("corruption",["wrong-flag","missing-key","missing-list",
                                       "wrong-scope","invalid-digest","duplicate"])
def test_enabled_broken_config_cannot_clear_company(corruption):
    env=approved()
    if corruption=="wrong-flag":
        env["SERVICEIT_CORPORATE_STOP_INN_ENABLED"]="YES_ALL_CONTACTS_CLEARED"
    elif corruption=="missing-key":
        env.pop("SERVICEIT_CORPORATE_STOP_INN_KEY")
    elif corruption=="missing-list":
        env.pop("SERVICEIT_CORPORATE_STOP_INN_MANIFEST")
    else:
        data=json.loads(env["SERVICEIT_CORPORATE_STOP_INN_MANIFEST"])
        if corruption=="wrong-scope": data["scope"]="different.portal/123"
        elif corruption=="invalid-digest": data["digests"]=["aaa"]
        else: data["digests"]=data["digests"]*2
        env["SERVICEIT_CORPORATE_STOP_INN_MANIFEST"]=json.dumps(data)
    with pytest.raises(s.CorporateStopBlocked):
        s.precheck(INN,env)

def test_preparation_rejects_invalid_inn_before_any_manifest():
    with pytest.raises(s.CorporateStopBlocked):
        s.make_approved_manifest(["invalid-inn"],KEY)

def test_cannot_use_weak_or_wrong_scope_key():
    with pytest.raises(s.CorporateStopBlocked):
        s.inn_digest(INN,"weak")
