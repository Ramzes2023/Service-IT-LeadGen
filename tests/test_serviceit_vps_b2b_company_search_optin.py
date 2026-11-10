"""Offline contract for owner-approved deep public buyer search flag activation.

Only one Compose coordinator environment boolean may change. Never modify
source, scrape pages or write CRM in installer tests.
"""
import importlib.util
from pathlib import Path
import pytest

P=Path("ops/serviceit_vps_b2b_company_search_optin.py")


def module():
    spec=importlib.util.spec_from_file_location("buyer_flag",P)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def baseline_pin():
    m=module()
    digest="sha256:"+("a"*64)
    return (digest, 'services:\n  coordinator:\n    image: "'+digest+
            '"\n    pull_policy: never\n    environment:\n'
            '      SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED: "YES"\n'
            '      SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED: "YES"\n'
            '      SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED: "NO"\n'
            '      SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED: "YES"\n')


def test_exact_one_feature_is_added_and_all_old_toggles_preserved():
    m=module()
    digest,old=baseline_pin()
    new=m.pinned_add_only_one_flag(old,digest)
    assert new==old+'      SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED: "YES"\n'
    assert new.count('SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED')==1
    assert old in new


def test_pin_rejects_unreviewed_flags_missing_tdi_or_image_mismatch():
    m=module()
    digest,old=baseline_pin()
    for bad in (
        old.replace("SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED","TDI_FLAG_MISSING"),
        old.replace('SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED: "YES"',
                    'SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED: "NO"'),
        old.replace("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED","B2B_FLAG_MISSING"),
        old.replace('SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED: "YES"',
                    'SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED: "NO"'),
        old.replace("a"*64,"b"*64),
        old+'      SERVICEIT_UNREVIEWED: "YES"\n',
        old+'      SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED: "YES"\n',
    ):
        with pytest.raises(m.FlagBlocked):
            m.pinned_add_only_one_flag(bad,digest)


def test_guarded_release_preserves_current_image_and_all_adjacent_services():
    m=module()
    src=P.read_text("utf8")
    assert m.FLAG=="SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED"
    assert len(m.EXPECTED_SOURCE_SHAS)==3
    assert m.EXISTING_FLAGS==(
        "SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED",
        "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED",
        "SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED",
        "SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED",
    )
    for fragment in (
        ".serviceit-five-minute-activate.lock",
        "PENDING_AI_DOCUMENT_OR_CRM_WRITE_NO_RESTART",
        "COMPOSE_DIFF_NOT_EXACT_ONE_FLAG",
        "REVIEWED_PUBLIC_RESEARCH_CODE_DRIFT",
        "BRAVE_MOUNTED_SEARCH_TOKEN_REQUIRED",
        "SOURCE_SHA_CHANGED_AFTER_UPDATE",
        "UNRELATED_ENV_CHANGE_AFTER_UPDATE",
        "BUYER_FLAG_REUSED_IMAGE=YES",
        "BUYER_FLAG_INSTALLER_CRM_WRITES=NO",
        "BUYER_FLAG_ROLLBACK=YES",
        "serviceit_vps_b2b_tender_doc_receipts",
        "300",
    ):
        assert fragment in src
    for forbidden in ("crm.item.update","crm.item.add",
                      "tasks.task.add","crm.timeline.comment.add",
                      "BRAVE_TOKEN_VALUE="):
        assert forbidden not in src


def test_bad_inputs_fail_before_any_external_calls():
    m=module()
    with pytest.raises(m.FlagBlocked):
        m.run("not-a-run")
    with pytest.raises(m.FlagBlocked):
        m.run("678")
