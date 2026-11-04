"""Fail-closed exact generated provisional score upgrade contract."""
import pytest

from leadpilot.serviceit_preliminary_score_markers import (
    VERSION, DUP_STATUSES, provisional_payload,
    is_exact_own_provisional, is_score_empty_or_ours,
)
from leadpilot.serviceit_kanban_presentation import SCORE_FIELD, BADGE_FIELD, VERSION_FIELD


@pytest.mark.parametrize("status", DUP_STATUSES)
@pytest.mark.parametrize("score", [0, 59, 60, 80, 100])
def test_exact_generated_provisional_marker_can_be_replaced_later(score,status):
    item=provisional_payload(score,status)
    assert item[VERSION_FIELD]==VERSION
    assert is_exact_own_provisional(item)
    assert is_score_empty_or_ours(item)


@pytest.mark.parametrize("key,value",[
    (SCORE_FIELD,99), (VERSION_FIELD, "VERIFIED"),
    (BADGE_FIELD,"🟢 90/100"),
])
def test_altered_or_imitated_provisional_marker_is_blocked(key,value):
    item=provisional_payload(55,"НЕ ПРОВЕРЕНЫ")
    item[key]=value
    assert not is_exact_own_provisional(item)
    assert not is_score_empty_or_ours(item)


def test_missing_field_not_assumed_empty():
    assert not is_score_empty_or_ours({})
    assert not is_score_empty_or_ours({SCORE_FIELD:None,VERSION_FIELD:""})
    assert is_score_empty_or_ours({
        SCORE_FIELD:None,VERSION_FIELD:"",BADGE_FIELD:None,
    })


@pytest.mark.parametrize("bad",[-1, 101, True, "90"])
def test_bad_provisional_score_cannot_be_recorded(bad):
    with pytest.raises(ValueError,match="PROVISIONAL_NUMBER_INVALID"):
        provisional_payload(bad,"ИНН НЕТ")
