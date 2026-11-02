"""Synthetic network smoke of local container; no Bitrix or GigaChat API calls."""
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT="http://127.0.0.1:18780"
TOKEN="SYNTHETIC-CI-NOT-A-REAL-BITRIX-APPLICATION-TOKEN-00000"


def post(token):
    body=json.dumps({
        "event":"ONCRMDYNAMICITEMADD",
        "data":{"FIELDS":{"ID":"123","ENTITY_TYPE_ID":"1062"}},
        "auth":{"domain":"serviseit.bitrix24.ru",
                "application_token":token,
                "access_token":"CI_SECRET_NEVER_STORE"},
    }).encode()
    request=Request(ROOT+"/integrations/serviceit/bitrix24/new-card",
                    data=body,headers={"Content-Type":"application/json"},
                    method="POST")
    try:
        with urlopen(request,timeout=4) as response:
            return response.status,response.read().decode()
    except HTTPError as exc:
        return exc.code,exc.read().decode()


if __name__=="__main__":
    with urlopen(ROOT+"/health",timeout=4) as resp:
        assert resp.status==200
        assert json.loads(resp.read())["production_writer_enabled"] is False
    first,first_data=post(TOKEN)
    second,second_data=post(TOKEN)
    unauthorized,bad_data=post("WRONG-SECRET-NOT-REAL-XXXXXXXXXXXXXXXX")
    assert first==202 and json.loads(first_data)["status"]=="QUEUED_FOR_SOURCE_CHECK"
    assert second==200 and json.loads(second_data)["status"]=="DUPLICATE_EVENT_IGNORED"
    assert unauthorized==403 and json.loads(bad_data)["status"]=="EVENT_REJECTED"
    for body in (first_data,second_data,bad_data):
        assert TOKEN not in body and "CI_SECRET_NEVER_STORE" not in body
    print("SERVICEIT_CONTAINER_SYNTHETIC_EVENT_E2E=PASS; CRM_WRITES=0; GIGACHAT_CALLS=0")
