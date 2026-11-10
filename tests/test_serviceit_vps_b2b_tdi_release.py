"""Guarded TDI install invariants; no unreviewed VPS or CRM modifications."""
from pathlib import Path
import re


ACT = Path("ops/serviceit_vps_b2b_tdi_activate.py").read_text("utf8")
WF = Path(".github/workflows/serviceit-vps-b2b-tdi-guarded-release.yml").read_text("utf8")
CORE = Path("leadpilot/serviceit_vps_worker.py").read_text("utf8")


def test_guarded_release_pins_exact_previously_observed_v11_sha():
    assert "9ff0cd40266b23bc2bfb8da14ed1198054e8a118be94c5624255bc22a7fb8ea8" in ACT
    assert '"MISSING"' in ACT
    assert "runtime_shas() == AUDITED_OLD_SHAS" in ACT
    assert "pending_source_writes() == 0" in ACT
    assert "serviceit_vps_b2b_dossier_v11_receipts" in ACT
    assert 'status IN (\'RESERVED\',\'WRITE_INTENT\')' in ACT
    assert "fcntl.flock(" in ACT
    assert "AUDITED_OLD_SHAS" in ACT and "NEW_TDI_IMAGE" in ACT


def test_single_coordinator_rebuild_is_offline_and_protected():
    assert 'docker", "build", "--network", "none"' in ACT
    assert "USER 10001:10001" in ACT
    assert "pypdf-6.21.4-py3-none-any.whl" in ACT
    assert "TDI_WHEEL_PIN_CHECK_FAILED" in ACT
    assert "WHEEL_SHA256" in ACT
    assert "EXISTING_CORE_SHA_DRIFT_NO_DEPLOY" in ACT
    assert '"--no-deps", "--no-build"' in ACT
    assert '"--force-recreate", "coordinator"' in ACT
    assert "TDI_ADJACENT_CONTAINER_CHANGED" in ACT
    assert "TDI_COMPOSE_UNAPPROVED_DRIFT" in ACT
    assert "TDI_RELEASE_ROLLBACK=YES" in ACT
    assert "atomic_write(PIN, old_pin)" in ACT
    assert 'TDI_FLAG] = "YES"' in ACT
    assert 'TDI_FLAG not in core_before.get("environment"' in ACT
    assert "TDI_OPT_IN_NOT_VISIBLE_IN_CONTAINER" in ACT


def test_only_new_flag_plus_image_may_change_and_fns_untouched():
    assert "next_core.get(\"environment\") == expected_env" in ACT
    assert "V11_ADJACENT_COMPOSE_DRIFT" in ACT or "TDI_ADJACENT_COMPOSE_DRIFT" in ACT
    assert "SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED" in ACT
    assert "SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED" in CORE
    assert "SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED" not in CORE


def test_github_owner_exact_comment_and_sha_pinned_wheel():
    for text in (
        "SERVICEIT_B2B_TDI_GUARDED_RELEASE_368",
        "github.actor == 'Ramzes2023'",
        "github.event.issue.number == 368",
        "issue_comment",
        "StrictHostKeyChecking=yes",
        "git diff --quiet",
        "sha256sum --check --status",
        "pypdf==6.21.4",
        "33e4fb41ca352810eb4e9838aa0d82d6888a5f8bf682f43e99cce5faa666255c",
        "TDI_RELEASE_CRM_WRITES_BY_INSTALLER=NO",
    ):
        assert text in WF
    assert "workflow_dispatch" not in WF
    assert "SERVICEIT_B2B_V11_GUARDED_RELEASE_368" not in WF
    assert not re.search(r'\bTOKEN\b', WF)


def test_tdi_smoke_import_matches_real_module_export():
    from leadpilot.serviceit_vps_b2b_document_worker import run_once, build_report
    assert callable(run_once) and callable(build_report)
    assert ("from leadpilot.serviceit_vps_b2b_document_worker "
            "import run_once, build_report") in ACT
    assert "import worker_once, build_report" not in ACT


def test_canonical_tdi_pin_is_clean_yaml_and_never_appends_old_tail():
    from ops.serviceit_vps_b2b_tdi_activate import pin_image, TDI_FLAG
    old = "sha256:" + "a" * 64
    target = "sha256:" + "b" * 64
    existing = (
        'services:\n  coordinator:\n    image: "' + old +
        '"\n    pull_policy: never\n'
        '  another-service:\n    image: "placeholder"\n'
    )
    carry = {
        "SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED": "YES",
        "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED": "YES",
        "SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED": "NO",
    }
    rendered = pin_image(existing, old, target, carry)
    assert rendered == (
        'services:\n  coordinator:\n    image: "' + target +
        '"\n    pull_policy: never\n'
        '    environment:\n'
        '      SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED: "YES"\n'
        '      SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED: "YES"\n'
        '      SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED: "NO"\n'
        '      ' + TDI_FLAG + ': "YES"\n'
    )
    assert rendered.count('image:') == 1
    assert "another-service" not in rendered


def test_canonical_tdi_pin_rejects_an_existing_enablement():
    import pytest
    from ops.serviceit_vps_b2b_tdi_activate import (
        pin_image, TDI_FLAG, ReleaseBlocked,
    )
    old = "sha256:" + "a" * 64
    target = "sha256:" + "b" * 64
    original = ('services:\n  coordinator:\n    image: "' + old +
                '"\n    pull_policy: never\n')
    with pytest.raises(ReleaseBlocked):
        pin_image(original + "    " + TDI_FLAG + ": YES\n",
                  old, target,
                  {"SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED": "YES",
                   "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED": "YES",
                   "SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED": "YES"})


def test_four_card_acceptance_verifies_real_bitrix_file_metadata():
    batch = Path(".github/workflows/serviceit-vps-b2b-tdi-four-batch.yml").read_text("utf8")
    assert "TDI_ATTACHMENT_READBACK_MISMATCH" in batch
    assert "TDI_ATTACHMENT_FILENAME_UNEXPECTED" in batch
    assert "TDI_ATTACHMENT_RECEIPT_MISSING" in batch
    assert "TDI_FOUR_READBACK_ATTACHMENTS" in batch
    assert "TDI_FOUR_READBACK_WITH_FILES" in batch
    assert "crm.timeline.comment.get" in batch
    assert "Файлы для прикрепления к карточке:" in batch
    assert "SERVICEIT_B2B_TDI_FOUR_BATCH_368" in batch
    assert "workflow_dispatch" not in batch


def test_preserved_flags_fail_closed_without_exact_boolean_live_keys():
    import pytest
    from ops.serviceit_vps_b2b_tdi_activate import (
        pin_image, ReleaseBlocked, PRESERVE_CORE_FLAGS,
    )
    old = "sha256:" + "a" * 64
    new = "sha256:" + "b" * 64
    pin = 'services:\n  coordinator:\n    image: "' + old + '"\n    pull_policy: never\n'
    valid = dict.fromkeys(PRESERVE_CORE_FLAGS, "YES")
    assert all(('      ' + key + ': "YES"') in pin_image(pin, old, new, valid)
               for key in PRESERVE_CORE_FLAGS)
    with pytest.raises(ReleaseBlocked):
        pin_image(pin, old, new, {PRESERVE_CORE_FLAGS[0]: "YES"})
    with pytest.raises(ReleaseBlocked):
        pin_image(pin, old, new, {**valid, PRESERVE_CORE_FLAGS[1]: "unexpected"})
    with pytest.raises(ReleaseBlocked):
        pin_image(pin, old, new, {**valid, "UNREVIEWED_ENV_FLAG": "YES"})
