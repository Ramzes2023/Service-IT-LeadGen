"""ServiceIT: profit-based developer / sales-manager / company allocation.

The owner-requested 15/10/75 split is a configurable POLICY PROPOSAL.
This module has no CRM, accounting, payroll or network writes. A real accrual
must first pass serviceit_financial_motivation.calculate_approved_draft with
independently attested bank settlements, invoices, direct expenses, eligible
customer and director-approved scheme. CRM opportunity != paid revenue.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from .serviceit_financial_motivation import (
    FinanceBlocked, money, rate, require, calculate_approved_draft,
    scheme_validate,
)

DEFAULT_DEVELOPER_PERCENT = "15"
DEFAULT_SALES_MANAGER_PERCENT = "10"
DEFAULT_COMPANY_PERCENT = "75"
PENDING = "PROPOSED_PROFIT_SPLIT_NOT_PAYABLE"


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def split_profit(revenue_ex_vat, documented_direct_costs, *,
                 developer_percent=DEFAULT_DEVELOPER_PERCENT,
                 sales_manager_percent=DEFAULT_SALES_MANAGER_PERCENT):
    """Deterministic amounts in RUB, excluding VAT; never an authorization.

    Earnings are zero on non-positive RVP. A loss remains with the company,
    not falsely paid out to the developer or manager.
    """
    revenue = money(revenue_ex_vat)
    cost = money(documented_direct_costs)
    dev_rate, sales_rate = rate(developer_percent), rate(sales_manager_percent)
    require(dev_rate + sales_rate <= Decimal("100"),
            "SPLIT_RATES_EXCEED_PROFIT")
    profit = revenue - cost
    distributable = max(profit, Decimal("0.00"))
    developer = _round(distributable * dev_rate / Decimal("100"))
    sales = _round(distributable * sales_rate / Decimal("100"))
    # Prevent a fractional-cent over-allocation due to independent rounding:
    sales = min(sales, distributable - developer)
    company = profit - developer - sales
    require(developer + sales + company == profit, "SPLIT_INVARIANT_INVALID")
    return {
        "status": PENDING, "currency": "RUB", "vat_basis": "REVENUE_EX_VAT",
        "revenue_ex_vat": str(revenue),
        "documented_direct_costs": str(cost),
        "profit_rvp": str(profit),
        "developer_percent": str(dev_rate),
        "sales_manager_percent": str(sales_rate),
        "company_percent": str(Decimal("100") - dev_rate - sales_rate),
        "developer_amount": str(developer),
        "sales_manager_amount": str(sales),
        "company_remainder": str(company),
        "payout_authorized": False, "crm_writes": 0,
        "independently_finance_verified": False,
    }


def company_approved_split_draft(scheme, deal):
    """Compute a THREE-PARTY *draft* from existing nine-gate finance evidence.

    Require one developer beneficiary in scheme.team_shares, no recurring bonus
    added to the one-time profit pool, and a named manager already signed off.
    Never invent the corporate-approved rates. A director-approved *field*
    is not a cryptographically verified corporate approval by itself; the
    resulting calculation is still a draft awaiting finance release.
    """
    settings = scheme_validate(scheme)
    require(settings["subscription_limit"] == 0
            and settings["subscription_percent"] == 0,
            "SIMPLE_SPLIT_REQUIRES_NO_SUBSCRIPTION_BONUS")
    require(len(settings["shares"]) == 1
            and next(iter(settings["shares"].values())) == 100,
            "EXACT_ONE_DEVELOPER_BENEFICIARY_REQUIRED")
    require(type(deal) is dict
            and type(deal.get("sales_manager_employee_id")) is int
            and deal["sales_manager_employee_id"] > 0,
            "ACTUAL_SALES_MANAGER_REQUIRED")
    receipt = calculate_approved_draft(scheme, deal)
    result = split_profit(
        receipt["revenue_ex_vat"], receipt["documented_direct_costs"],
        developer_percent=settings["team_percent"],
        sales_manager_percent=settings["manager_percent"],
    )
    # Eligibility is a separate policy: outside project team lead window,
    # *no project developer bonus*, but manager may remain eligible.
    if not receipt["project_bonus_eligible"]:
        result = split_profit(
            receipt["revenue_ex_vat"], receipt["documented_direct_costs"],
            developer_percent="0", sales_manager_percent=settings["manager_percent"])
    require(Decimal(result["developer_amount"])
            == Decimal(receipt["team_fund"])
            and Decimal(result["sales_manager_amount"])
            == Decimal(receipt["manager_primary_bonus"]),
            "FINANCE_ALLOCATION_CONFLICT")
    result.update({
        "status": "COMPANY_FINANCE_APPROVED_INPUT_DRAFT_NOT_PAID",
        "scheme_version": receipt["scheme_version"],
        "deal_id": receipt["deal_id"],
        "signal_card_id": receipt["signal_card_id"],
        "sales_manager_employee_id": deal["sales_manager_employee_id"],
        "developer_beneficiary_code": next(iter(settings["shares"])),
        "bank_and_cost_documents_were_input_required": True,
        "project_bonus_eligible": receipt["project_bonus_eligible"],
        "audit_sha256_input": receipt["audit_sha256"],
        "payout_authorized": False,
    })
    return result


def proposed_settings():
    """User-proposed scheme only; company must approve before finance imports."""
    return {
        "status": "PENDING_COMPANY_APPROVAL",
        "currency": "RUB",
        "basis": "CONFIRMED_PAID_REVENUE_EX_VAT_MINUS_DOCUMENTED_DIRECT_COSTS",
        "developer_percent": DEFAULT_DEVELOPER_PERCENT,
        "sales_manager_percent": DEFAULT_SALES_MANAGER_PERCENT,
        "company_percent": DEFAULT_COMPANY_PERCENT,
        "negative_profit": "NO_DEVELOPER_OR_MANAGER_BONUS",
        "contact_or_payroll_authorized": False,
    }
