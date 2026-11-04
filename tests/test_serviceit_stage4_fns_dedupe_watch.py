"""Real-rest shape checks for FNS/CRM prequalification; all data synthetic."""
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

import leadpilot.serviceit_stage4_fns_dedupe_watch as m
from leadpilot.serviceit_enrichment_worker import PREFIX

NOW = datetime(2026, 11, 4, 19, 0, tzinfo=timezone.utc)
INN = "7700000009"


def env():
    return {"GITHUB_REPOSITORY":"Ramzes2023/Service-IT-LeadGen",
            "GITHUB_REF":"refs/heads/main", "GITHUB_EVENT_NAME":"schedule",
            "SERVICEIT_BITRIX_WEBHOOK":"https://serviseit.bitrix24.ru/rest/1615/mock_secret_not_real",
            "SERVICEIT_BITRIX_POLL_START_UTC":(NOW-timedelta(hours=6)).isoformat()}


def reqs_transport(inn=INN, *, rows=None, bad_filter=False):
    def handler(req):
        path = req.url.path.rsplit("/",1)[-1]
        args=json.loads(req.content)
        if path == "crm.item.get.json":
            return httpx.Response(200,json={"result":{"item":{
                "id":33,"entityTypeId":1062,"categoryId":21,"title":"Закупка 1С",
                "createdTime":(NOW-timedelta(minutes=2)).isoformat(),
                PREFIX+"COMPANY_INN":inn, PREFIX+"SIGNAL_KIND":"procurement",
                PREFIX+"SIGNAL_SOURCE":"B2B-Center", PREFIX+"REGION":"Москва"}}})
        if path == "crm.item.list.json":
            if args.get("filter",{}).get("id")==3:
                return httpx.Response(200,json={"result":{"items":[{
                    "id":3,"categoryId":21,"createdTime":(NOW-timedelta(days=60)).isoformat()
                }]}, "total":1})
            if args.get("filter",{}).get(PREFIX+"COMPANY_INN") == inn:
                return httpx.Response(200,json={"result":{"items":rows if rows is not None else [{
                    "id":33,"categoryId":21,
                    PREFIX+"COMPANY_INN":("7700000010" if bad_filter else inn)
                }]}})
            return httpx.Response(200,json={"result":{"items":[{
                "id":33,"categoryId":21,
                "createdTime":(NOW-timedelta(minutes=2)).isoformat()
            }]}, "total":1})
        if path == "crm.requisite.list.json":
            return httpx.Response(200,json={"result":[],"total":0})
        raise AssertionError("unexpected method "+path)
    return httpx.MockTransport(handler)


def test_full_live_scanner_no_fns_returns_hold_not_score(monkeypatch):
    with httpx.Client(transport=reqs_transport()) as client:
        out=m.run(client,env(),now=NOW)
    assert out["status"]=="SERVICEIT_FNS_DUPLICATES_AUTO_READONLY"
    assert out["eligible_new_cards"]==1
    assert out["outcomes"]=={"HOLD_FNS_SNAPSHOT_NOT_CONNECTED":1}
    assert out["formal_score_authorized"] is False
    assert out["crm_writes"] == out["score_writes"] == out["tasks_created"] == 0
    assert out["stop_list_status"]=="DEFERRED_NOT_CONFIGURED"
    assert "7700000009" not in json.dumps(out)


def test_missing_or_invalid_inn_stays_hold_without_any_score():
    with httpx.Client(transport=reqs_transport(inn="bad")) as client:
        out=m.run(client,env(),now=NOW)
    assert out["outcomes"]=={"HOLD_MISSING_VALID_COMPANY_INN":1}


def test_same_process_collision_is_blocking(monkeypatch):
    rows=[{"id":33,"categoryId":21,PREFIX+"COMPANY_INN":INN},
          {"id":42,"categoryId":21,PREFIX+"COMPANY_INN":INN}]
    with httpx.Client(transport=reqs_transport(rows=rows)) as client:
        out=m.run(client,env(),now=NOW)
    assert out["outcomes"]=={"HOLD_POTENTIAL_VISIBLE_CRM_DUPLICATE":1}


def test_ignored_exact_inn_filter_cannot_claim_clean():
    with httpx.Client(transport=reqs_transport(bad_filter=True)) as client:
        out=m.run(client,env(),now=NOW)
    assert out["outcomes"]=={"HOLD_EVIDENCE_OR_ACCESS_UNVERIFIED":1}
    assert out["global_crm_customer_clear"] is False


def test_valid_msp_snapshot_is_still_not_formal_egrul_score(monkeypatch):
    monkeypatch.setattr(m,"msp_archive_status",lambda env,inn,*,today:
                        "MATCH_IN_SUPPLIED_MSP_SNAPSHOT")
    with httpx.Client(transport=reqs_transport()) as client:
        out=m.run(client,env(),now=NOW)
    assert out["outcomes"]=={"DRAFT_ONLY_FNS_MSP_MATCH_GLOBAL_CHECKS_PENDING":1}
    assert out["fns_egrul_active_verified"] is False
    assert out["formal_score_authorized"] is False


def test_crm_requisite_match_also_blocks(monkeypatch):
    monkeypatch.setattr(m,"crm_inn_visibility_readonly",lambda client,webhook,inn:
                        {"visible_matches":1,"global_crm_customer_clear":False,
                         "global_dedup_verified":False})
    with httpx.Client(transport=reqs_transport()) as client:
        out=m.run(client,env(),now=NOW)
    assert out["outcomes"]=={"HOLD_POTENTIAL_VISIBLE_CRM_DUPLICATE":1}


def test_msp_malformed_or_partial_configuration_fails_closed():
    with pytest.raises(m.WatchBlocked,match="FNS_SNAPSHOT_CONFIG_PARTIAL"):
        m.msp_archive_status({"SERVICEIT_FNS_MSP_PUBLIC_URL":
            "https://www.nalog.gov.ru/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip"},
            INN,today=NOW.date())


def test_old_card_is_never_queried_as_new():
    with httpx.Client(transport=reqs_transport()) as client:
        with pytest.raises(m.WatchBlocked,match="CARD_OUT_OF_SCOPE"):
            m.read_item(client,"https://serviseit.bitrix24.ru/rest/1615/mock",7)


def test_no_crm_or_source_side_effects_in_code():
    from pathlib import Path
    code=Path("leadpilot/serviceit_stage4_fns_dedupe_watch.py").read_text("utf-8")
    assert "crm.item.update.json" not in code
    assert "crm.item.add.json" not in code
    assert "tasks.task.add.json" not in code
    assert "form_score_authorized=True" not in code
