"""Offline fixture-to-score-to-Kanban preview for all source batches."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pytest

from leadpilot.serviceit_source_batch_pipeline import (
    BatchRejected, CONTRACT, MAX_ENTRIES, plan_source_batch, main,
)
from leadpilot.serviceit_kanban_presentation import score_label, BADGE_FIELD
from leadpilot.core import SCORE_VERSION

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "serviceit_source_batch_demo.json"


def sample():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_merges_company_signals_and_is_safe():
    result = plan_source_batch(sample())
    assert result["status"] == "SERVICEIT_ALL_LEAD_BATCH_PREVIEW"
    assert result["unique_companies"] == 2
    assert result["input_entries"] == 3
    assert result["repeated_signals_ignored"] == 1
    assert sorted(x["source_count"] for x in result["items"]) == [1,2]
    assert len({x["company_fingerprint"] for x in result["items"]}) == 2
    assert result["crm_writes"] == 0
    assert result["automatic_production_worker"] is False
    assert result["corporate_checks_independently_verified"] is False
    assert result["outbound_contact_authorized"] is False
    for item in result["items"]:
        assert item["color_badge"] == score_label(item["score"])
        assert item["crm_fields_preview"][BADGE_FIELD] == item["color_badge"]
        assert item["crm_fields_preview"]["UF_CRM_13_SVCIT_LEAD_SCORE"] == item["score"]
        assert item["crm_fields_preview"]["UF_CRM_13_SVCIT_SCORE_VERSION"] == SCORE_VERSION
        assert item["crm_handoff_eligible_by_supplied_checks"] is False
        assert item["crm_operation"] == "NOT_SENT_REQUIRES_SEPARATE_REVIEW"
        assert item["qualification_status"] == "DEMO_REVIEW_ONLY"


def test_sorting_by_inn_and_sources_reuses_same_batch_fingerprint():
    obj=sample()
    reversed_obj=deepcopy(obj)
    reversed_obj["entries"].reverse()
    for entry in reversed_obj["entries"]:
        entry["signals"].reverse()
    a=plan_source_batch(obj)
    b=plan_source_batch(reversed_obj)
    assert a["batch_fingerprint"]==b["batch_fingerprint"]
    assert a["items"]==b["items"]


def test_duplicate_same_key_with_conflicting_evidence_is_rejected():
    payload=sample()
    payload["entries"][1]["signals"][0]["evidence"]="СОВЕРШЕННО ИНАЯ ИСТОРИЯ"
    with pytest.raises(BatchRejected,match="DUPLICATE_SOURCE_CONFLICT"):
        plan_source_batch(payload)


def test_same_inn_different_company_is_rejected():
    payload=sample()
    payload["entries"][1]["company"]["name"]="DEMO COMPETING-002"
    with pytest.raises(BatchRejected,match="COMPANY_IDENTITY_CONFLICT"):
        plan_source_batch(payload)


def test_same_inn_inconsistent_verification_is_rejected():
    payload=sample()
    payload["entries"][1]["checks"]={"source":"VERIFIED"}
    with pytest.raises(BatchRejected,match="COMPANY_CHECKS_CONFLICT"):
        plan_source_batch(payload)


def test_non_synthetic_source_url_for_synthetic_batch_is_rejected():
    payload=sample()
    payload["entries"][0]["signals"][0]["source_url"]="https://some-real-domain.example/request/123"
    with pytest.raises(BatchRejected,match="SYNTHETIC_SOURCE_REQUIRED"):
        plan_source_batch(payload)


@pytest.mark.parametrize("bad", [
    {"source":"YES"}, {"stop_list":True}, {"source":None}, {"new_gate":"VERIFIED"},
])
def test_invalid_check_states_fail_closed(bad):
    obj=sample()
    obj["entries"][0]["checks"]=bad
    with pytest.raises(BatchRejected,match="GATE"):
        plan_source_batch(obj)


def test_real_research_mode_is_also_no_write_even_if_all_checks_supplied():
    obj=sample()
    obj["source_kind"]="research"
    # Even if the upstream claims VERIFIED, it is NOT an independently
    # verified corporate allowlist and cannot be automatically delivered.
    all_checks={
        k:"VERIFIED" for k in (
            "company_registry", "source", "existing_customer", "crm_dedup",
            "stop_list", "legal_basis", "contact"
        )
    }
    for e in obj["entries"]:
        e["checks"]=all_checks
    result=plan_source_batch(obj)
    assert result["crm_writes"]==0
    assert result["automatic_production_worker"] is False
    assert result["corporate_checks_independently_verified"] is False
    assert result["outbound_contact_authorized"] is False
    assert all(i["crm_operation"]=="NOT_SENT_REQUIRES_SEPARATE_REVIEW" for i in result["items"])


def test_cli_outputs_sanitized_plan_without_company_details(capsys):
    assert main(["--input",str(FIXTURE)])==0
    text=capsys.readouterr().out
    assert "DEMO COMPANY-" not in text
    assert "example.invalid" not in text
    assert "7700000009" not in text
    data=json.loads(text)
    assert data["unique_companies"]==2
    assert data["crm_writes"]==0


def test_cli_invalid_file_does_not_print_private_inputs(tmp_path,capsys):
    p=tmp_path/"bad.json"
    p.write_text('{"private_lead":"SECRET_EMAIL_DO_NOT_PRINT"}',encoding="utf-8")
    assert main(["--input",str(p)])==2
    s=capsys.readouterr().out
    assert "SECRET_EMAIL_DO_NOT_PRINT" not in s
    assert json.loads(s)["status"]=="SOURCE_BATCH_BLOCKED"


def test_no_batch_fields_may_include_actual_contacts():
    source=Path(__file__).resolve().parents[1] / "leadpilot" / "serviceit_source_batch_pipeline.py"
    s=source.read_text(encoding="utf-8")
    for forbidden in ('crm.item.update', 'crm.item.add', 'userfieldconfig.add',
                      'tasks.task.add','send_email', 'outreach_authorized=True',
                      'httpx.post', 'requests.post'):
        assert forbidden not in s


def test_max_entries_rejected():
    obj=sample()
    obj["entries"] = obj["entries"] * 90
    assert len(obj["entries"]) > MAX_ENTRIES
    with pytest.raises(BatchRejected,match="ENTRY_COUNT_INVALID"):
        plan_source_batch(obj)
