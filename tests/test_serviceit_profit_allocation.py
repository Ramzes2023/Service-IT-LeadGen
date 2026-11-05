"""Confirmed expense-based profit allocation, not 15/10 on gross revenue."""
from decimal import Decimal
import pytest
from leadpilot.serviceit_profit_allocation import (
    split_profit, proposed_settings, company_approved_split_draft,
)
from leadpilot.serviceit_financial_motivation import FinanceBlocked


def test_owner_example_after_expenses_15_10_75():
    r = split_profit("5000.00", "1000.00")
    assert r["profit_rvp"] == "4000.00"
    assert r["developer_amount"] == "600.00"
    assert r["sales_manager_amount"] == "400.00"
    assert r["company_remainder"] == "3000.00"
    assert r["payout_authorized"] is False and r["crm_writes"] == 0
    assert Decimal(r["developer_amount"]) + Decimal(r["sales_manager_amount"]) + Decimal(r["company_remainder"]) == Decimal(r["profit_rvp"])


def test_percentages_are_configurable_not_hardcoded_to_tz():
    r = split_profit("1000.00", "300.00", developer_percent="20", sales_manager_percent="5")
    assert r["developer_amount"] == "140.00"
    assert r["sales_manager_amount"] == "35.00"
    assert r["company_remainder"] == "525.00"
    assert r["company_percent"] == "75"


def test_loss_never_pays_bonus():
    r = split_profit("500", "800")
    assert r["profit_rvp"] == "-300.00"
    assert r["developer_amount"] == r["sales_manager_amount"] == "0.00"
    assert r["company_remainder"] == "-300.00"


@pytest.mark.parametrize("revenue,cost,d_rate,s_rate", [
    ("100", "0", "80", "30"),
    ("100", "1", "-1", "10"),
    ("100", "1", "20", "101"),
    ("100", "1", "bad", "10"),
    ("1000.50", "10.999", "15", "10"),
    ("100", "-1", "15", "10"),
])
def test_bad_allocation_refused(revenue,cost,d_rate,s_rate):
    with pytest.raises(FinanceBlocked):
        split_profit(revenue,cost,developer_percent=d_rate,sales_manager_percent=s_rate)


def test_proposed_rates_are_not_falsely_called_corporate_approved():
    r = proposed_settings()
    assert r["status"] == "PENDING_COMPANY_APPROVAL"
    assert (r["developer_percent"],r["sales_manager_percent"],r["company_percent"]) == ("15","10","75")


def test_verified_mode_requires_company_approved_scheme():
    scheme={"version":"owner-proposal","approved_by_role":"unapproved",
            "launch_date":"2026-11-01","effective_from":"2026-11-01",
            "new_leads_months":1,"tail_days":30,"first_sale_days":30,
            "subscription_paid_periods":0,
            "team_percent":"15","manager_percent":"10",
            "subscription_percent":"0","team_shares":{"developer":"100"}}
    with pytest.raises(FinanceBlocked, match="SCHEME_DIRECTOR_APPROVAL_REQUIRED"):
        company_approved_split_draft(scheme, {"sales_manager_employee_id":42})


def test_corporate_proof_gated_example_still_does_not_authorize_payroll():
    def proof(ref, payment=False):
        return {"document_ref":ref,
                "confirmed_by_role":"finance","recorded_by_role":"finance",
                "confirmed_at":"2026-11-05",
                **({"settlement_state":"SETTLED_IN_BANK"} if payment else {})}
    scheme={
        "version":"APPROVED_V1","approved_by_role":"director",
        "launch_date":"2026-11-01","effective_from":"2026-11-01",
        "new_leads_months":12,"tail_days":60,"first_sale_days":30,
        "subscription_paid_periods":0,
        "team_percent":"15","manager_percent":"10",
        "subscription_percent":"0","team_shares":{"developer":"100"},
    }
    deal={
        "deal_id":52,"signal_card_id":120,"category_verified":True,
        "customer_scope_verified":True,"customer_new_verified":True,
        "global_dedup_clear":True,"stop_clear":True,
        "scheme_version":"APPROVED_V1",
        "manager_responsible_confirmed":True,
        "sales_manager_employee_id":41,
        "financial_approver_role":"finance","finance_approved":True,
        "supervisor_approved":True,
        "lead_registered_at":"2026-11-02",
        "first_confirmed_payment_at":"2026-11-05",
        "primary_payments":[{
            "paid_at":"2026-11-05",
            "revenue_ex_vat":"5000.00",
            "finance_proof":proof("bank:payment:051",True),
            "direct_costs":[{
                "kind":"direct_labor","amount":"1000.00",
                "finance_proof":proof("act:cost:051")
            }],
        }],
        "paid_subscription_periods":[],
    }
    result=company_approved_split_draft(scheme,deal)
    assert result["developer_amount"]=="600.00"
    assert result["sales_manager_amount"]=="400.00"
    assert result["company_remainder"]=="3000.00"
    assert result["payout_authorized"] is False
    assert result["scheme_version"]=="APPROVED_V1"
    assert result["sales_manager_employee_id"]==41
