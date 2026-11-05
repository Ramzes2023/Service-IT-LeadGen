"""Section 25 finance acceptance: no invented sale, rates or payroll."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import pytest

from leadpilot.serviceit_financial_motivation import (
    FinanceBlocked, calculate_approved_draft, money, add_months,
)


def evidence(ref, *, payment=False):
    x = {"document_ref": ref, "confirmed_by_role": "finance",
         "recorded_by_role": "finance", "confirmed_at": "2026-11-05"}
    if payment:
        x["settlement_state"] = "SETTLED_IN_BANK"
    return x


def fixture():
    s = {
        "version": "CORP-SCHEME-2026-01",
        "approved_by_role": "director",
        "launch_date": "2026-11-01", "effective_from": "2026-11-01",
        "new_leads_months": 2, "tail_days": 30,
        "first_sale_days": 30, "subscription_paid_periods": 2,
        "team_percent": "10", "manager_percent": "5",
        "subscription_percent": "3", "team_shares": {"alice": "60", "bob": "40"},
    }
    d = {
        "deal_id": 881, "signal_card_id": 120,
        "category_verified": True, "customer_scope_verified": True,
        "customer_new_verified": True, "global_dedup_clear": True,
        "stop_clear": True, "scheme_version": "CORP-SCHEME-2026-01",
        "manager_responsible_confirmed": True,
        "financial_approver_role": "finance",
        "finance_approved": True, "supervisor_approved": True,
        "lead_registered_at": "2026-11-02",
        "first_confirmed_payment_at": "2026-11-05",
        "primary_payments": [{
            "paid_at": "2026-11-05", "revenue_ex_vat": "100000.00",
            "finance_proof": evidence("bank:payment:001", payment=True),
            "direct_costs": [{
                "kind": "software", "amount": "30000.00",
                "finance_proof": evidence("act:invoice:001"),
            },{
                "kind": "direct_labor", "amount": "10000.00",
                "finance_proof": evidence("act:salary:001"),
            }],
        }],
        "paid_subscription_periods": [
            {"period_id": "2026-11", "paid_at": "2026-11-06",
             "revenue_ex_vat": "2000", "direct_costs": [],
             "finance_proof": evidence("bank:subscription:001", payment=True)},
            {"period_id": "2026-12", "paid_at": "2026-12-06",
             "revenue_ex_vat": "3000", "direct_costs": [],
             "finance_proof": evidence("bank:subscription:002", payment=True)},
            {"period_id": "2027-01", "paid_at": "2027-01-06",
             "revenue_ex_vat": "9000", "direct_costs": [],
             "finance_proof": evidence("bank:subscription:003", payment=True)},
        ],
    }
    return s, d


def test_approved_draft_calculates_only_real_attested_rvp():
    s, d = fixture()
    v = calculate_approved_draft(s, d)
    assert v["status"] == "ACCRUAL_DRAFT_REQUIRES_APPROVAL"
    assert v["payout_authorized"] is False and v["crm_writes"] == 0
    assert v["revenue_ex_vat"] == "100000.00"
    assert v["rvp"] == "60000.00"
    assert v["team_fund"] == "6000.00"
    assert v["team_split"] == {"alice": "3600.00", "bob": "2400.00"}
    assert v["manager_primary_bonus"] == "3000.00"
    assert v["manager_subscription_bonus"] == "150.00"
    assert v["subscription_bonus_period_count"] == 2
    assert v["total_draft_bonus"] == "9150.00"
    assert len(v["audit_sha256"]) == 64
    assert v == calculate_approved_draft(deepcopy(s), deepcopy(d))


@pytest.mark.parametrize("bad", [0.01, True, "-1", "1.001", "NaN", "Infinity",
                                   "1e3", "01", " 4", "4 ", None, "2,30"])
def test_money_rejects_unsafe_format(bad):
    with pytest.raises(FinanceBlocked):
        money(bad)


@pytest.mark.parametrize("key,val", [
    ("customer_new_verified", False), ("global_dedup_clear", False),
    ("stop_clear", False), ("finance_approved", False),
    ("supervisor_approved", False), ("category_verified", False),
    ("manager_responsible_confirmed", False),
])
def test_missing_required_approval_blocks_any_result(key, val):
    s, d = fixture()
    d[key] = val
    with pytest.raises(FinanceBlocked):
        calculate_approved_draft(s, d)


def test_unconfirmed_payment_or_unapproved_cost_blocks():
    s, d = fixture()
    del d["primary_payments"][0]["finance_proof"]["settlement_state"]
    with pytest.raises(FinanceBlocked, match="BANK_PAYMENT_NOT_CONFIRMED"):
        calculate_approved_draft(s, d)
    s, d = fixture()
    d["primary_payments"][0]["direct_costs"][0]["finance_proof"]["confirmed_by_role"] = "manager"
    with pytest.raises(FinanceBlocked, match="FINANCE_ROLE_SEPARATION_REQUIRED"):
        calculate_approved_draft(s, d)


def test_ineligible_lead_does_not_receive_team_fund_but_manager_is_evaluated():
    s, d = fixture()
    d["lead_registered_at"] = "2027-01-02"
    d["first_confirmed_payment_at"] = "2027-01-03"
    d["primary_payments"][0]["paid_at"] = "2027-01-03"
    v = calculate_approved_draft(s, d)
    assert v["project_bonus_eligible"] is False
    assert v["team_fund"] == "0.00"
    assert v["manager_primary_bonus"] == "3000.00"


def test_cutoff_tail_expires_team_bonus():
    s, d = fixture()
    d["lead_registered_at"] = "2026-12-20"
    d["first_confirmed_payment_at"] = "2027-02-02"
    d["primary_payments"][0]["paid_at"] = "2027-02-02"
    v = calculate_approved_draft(s, d)
    assert v["project_bonus_eligible"] is False


def test_no_bonuses_for_negative_profit():
    s, d = fixture()
    d["primary_payments"][0]["direct_costs"][0]["amount"] = "200000"
    v = calculate_approved_draft(s, d)
    assert Decimal(v["rvp"]) < 0
    assert v["team_fund"] == v["manager_primary_bonus"] == "0.00"


def test_missing_scheme_or_wrong_share_totals_blocks():
    s, d = fixture()
    del s["team_percent"]
    with pytest.raises(FinanceBlocked):
        calculate_approved_draft(s, d)
    s, d = fixture()
    s["team_shares"]["bob"] = "41"
    with pytest.raises(FinanceBlocked, match="TEAM_SHARES_MUST_SUM_TO_100"):
        calculate_approved_draft(s, d)


def test_subscription_periods_count_paid_not_calendar_and_no_duplicate():
    s, d = fixture()
    d["paid_subscription_periods"][1]["paid_at"] = "2027-02-06"
    v = calculate_approved_draft(s, d)
    assert v["subscription_bonus_period_count"] == 2
    d["paid_subscription_periods"][1]["period_id"] = "2026-11"
    with pytest.raises(FinanceBlocked, match="SUBSCRIPTION_DUPLICATE_PERIOD"):
        calculate_approved_draft(s, d)


def test_purchase_window_and_proof_replay_blocked():
    s, d = fixture()
    d["primary_payments"][0]["paid_at"] = "2027-01-05"
    with pytest.raises(FinanceBlocked, match="PAYMENT_OUTSIDE_PRIMARY_WINDOW"):
        calculate_approved_draft(s, d)
    s, d = fixture()
    d["primary_payments"][0]["direct_costs"][1]["finance_proof"]["document_ref"] = "act:invoice:001"
    with pytest.raises(FinanceBlocked, match="DUPLICATED_COST_REFERENCE"):
        calculate_approved_draft(s, d)


def test_month_end_calendar_boundaries():
    assert add_months(date(2026, 12, 31), 2).isoformat() == "2027-02-28"
