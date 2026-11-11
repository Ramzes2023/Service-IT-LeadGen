"""Offline safety checks for one-use public CBR source addendum."""
from pathlib import Path
import pytest

from ops import serviceit_vps_b2b_first_cbr_official_oneuse as mod
from leadpilot.serviceit_b2b_v19_backfill import (
    prepare_addendum,REPORT,SUMMARY,BRIEF,
)
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS


def test_fixed_scope_and_official_citation():
    assert mod.CARD == 11
    assert mod.TENDER == "4673971"
    assert mod.INN == "7702077840"
    assert mod.OGRN == "1027739387411"
    assert mod.URL.startswith("https://www.cbr.ru/registries/")
    assert mod.URL in mod.CITED_LINE
    assert "не выписка ФНС" in mod.CITED_LINE
    assert mod.TABLE == "serviceit_vps_b2b_cbr_official_v1_receipts"


def test_only_three_existing_ai_fields_are_additive():
    before={k:("Исходный подтверждённый текст 2026 " + k)
            for k in AI_FIELDS}
    draft=prepare_addendum(before,[mod.CITED_LINE],mod.TENDER)
    assert draft is not None
    assert set(draft)==set(before)
    changed={k for k in before if before[k]!=draft[k]}
    assert changed=={REPORT,SUMMARY,BRIEF}
    assert draft[REPORT].startswith(before[REPORT])
    assert draft[SUMMARY].endswith(before[SUMMARY])
    assert draft[BRIEF].endswith(before[BRIEF])


def test_guarded_oneuse_code_contains_durable_receipt():
    source=(Path(__file__).parents[1]/"ops"/
            "serviceit_vps_b2b_first_cbr_official_oneuse.py").read_text()
    for required in (
      "pg_try_advisory_lock",
      "active_conflicts",
      "SELECT to_regclass",
      "WRITE_INTENT",
      "ON CONFLICT(card_id) DO NOTHING",
      "non_ai_unchanged",
      "original_nine(after)==draft",
      "secrets.compare_digest",
      "UNCERTAIN_NO_REPLAY",
      "SIGNED_BITRIX_IDENTITY_MISMATCH",
      "CBR_OFFICIAL_FULL_PAGE_NOT_VERIFIED",
      "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED",
    ):
        assert required in source
    assert "crm.item.update" in source
    assert "crm.item.create" not in source
    assert "TINYFISH_GPT" not in source


def test_official_fact_fails_closed_on_non_json_response():
    class Fake:
        def post(self,*args,**kwargs):
            class Resp:
                status_code=403
                content=b""
                headers={}
            return Resp()
    assert mod.official_fact(Fake(),"x"*40) is False
