"""Service IT v3.0 Section 25: evidence-gated, deterministic RVP/motivation.

NO Bitrix REST, payroll, customer contact or finance writes. All percentages,
dates and role attestations must come from the approved corporate scheme.
A quote, deal opportunity or an AI model output is NEVER a confirmed payment.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re

CENT = Decimal("0.01")
COST_KINDS = frozenset({
    "software", "equipment", "outside_services", "direct_labor", "other_direct",
})
STATUS_DRAFT = "ACCRUAL_DRAFT_REQUIRES_APPROVAL"


class FinanceBlocked(ValueError):
    """Safe refusal, no financial result should be recorded in CRM."""


def require(ok, code):
    if not ok:
        raise FinanceBlocked(code)


def money(value):
    # Reject floats/bools and all NaN, infinity, scientific or excessive precision.
    require(type(value) in (str, int, Decimal), "MONEY_TYPE_INVALID")
    raw = str(value)
    require(re.fullmatch(r"(?:0|[1-9][0-9]{0,13})(?:\.[0-9]{1,2})?", raw)
            is not None, "MONEY_FORMAT_INVALID")
    try:
        v = Decimal(raw).quantize(CENT)
    except (InvalidOperation, ValueError):
        raise FinanceBlocked("MONEY_INVALID") from None
    return v


def rate(value):
    require(type(value) in (str, int, Decimal), "RATE_TYPE_INVALID")
    raw = str(value)
    require(re.fullmatch(r"(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?", raw)
            is not None, "RATE_FORMAT_INVALID")
    v = Decimal(raw)
    require(v <= 100, "RATE_EXCEEDS_100")
    return v


def iso_day(value):
    require(type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            is not None, "DATE_FORMAT_INVALID")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise FinanceBlocked("DATE_INVALID") from None


def add_months(day, count):
    require(type(count) is int and 0 < count <= 120, "MONTHS_INVALID")
    ordinal = day.year * 12 + day.month - 1 + count
    year, offset = divmod(ordinal, 12)
    month = offset + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def proof(entry, role, *, payment=False):
    """Check an independent document and finance attestation, not free-text AI."""
    require(type(entry) is dict, "EVIDENCE_NOT_OBJECT")
    required = ("document_ref", "confirmed_by_role", "confirmed_at", "recorded_by_role")
    require(all(type(entry.get(x)) is str and 1 <= len(entry[x]) <= 160
                for x in required), "EVIDENCE_INCOMPLETE")
    require(entry["confirmed_by_role"] == role
            and entry["recorded_by_role"] != "beneficiary"
            and entry["confirmed_at"] >= "2020-01-01",
            "FINANCE_ROLE_SEPARATION_REQUIRED")
    iso_day(entry["confirmed_at"])
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:#-]{5,159}",
                         entry["document_ref"]) is not None, "PROOF_REFERENCE_INVALID")
    if payment:
        require(entry.get("settlement_state") == "SETTLED_IN_BANK",
                "BANK_PAYMENT_NOT_CONFIRMED")
    return entry["document_ref"]


def scheme_validate(s):
    require(type(s) is dict, "SCHEME_MISSING")
    for k in ("version", "approved_by_role"):
        require(type(s.get(k)) is str and 1 <= len(s[k]) <= 80,
                "APPROVED_SCHEME_METADATA_REQUIRED")
    require(s["approved_by_role"] == "director", "SCHEME_DIRECTOR_APPROVAL_REQUIRED")
    start = iso_day(s.get("launch_date"))
    effective = iso_day(s.get("effective_from"))
    require(effective <= start, "SCHEME_EFFECTIVE_AFTER_LAUNCH")
    months = s.get("new_leads_months")
    tail = s.get("tail_days")
    window = s.get("first_sale_days")
    nsub = s.get("subscription_paid_periods")
    require(type(months) is int and 1 <= months <= 120, "PROGRAM_MONTHS_INVALID")
    require(type(tail) is int and 0 <= tail <= 730, "PROGRAM_TAIL_INVALID")
    require(type(window) is int and 1 <= window <= 365, "PRIMARY_WINDOW_INVALID")
    require(type(nsub) is int and 0 <= nsub <= 120, "SUBSCRIPTION_PERIOD_LIMIT_INVALID")
    values = {
        "team_percent": rate(s.get("team_percent")),
        "manager_percent": rate(s.get("manager_percent")),
        "subscription_percent": rate(s.get("subscription_percent")),
    }
    shares = s.get("team_shares")
    require(type(shares) is dict and bool(shares)
            and all(type(k) is str and re.fullmatch(r"[a-zA-Z0-9_-]{2,60}", k)
                    for k in shares), "TEAM_SHARES_INVALID")
    shares = {k: rate(v) for k, v in shares.items()}
    require(sum(shares.values(), Decimal("0")) == Decimal("100"),
            "TEAM_SHARES_MUST_SUM_TO_100")
    return {**values, "shares": shares, "start": start,
            "cutoff": add_months(start, months), "tail_days": tail,
            "window_days": window, "subscription_limit": nsub}


def _rvp_one(entry, *, payment):
    require(type(entry) is dict, "SALE_ENTRY_INVALID")
    gross = money(entry.get("revenue_ex_vat"))
    proof(entry.get("finance_proof"), "finance", payment=payment)
    costs = entry.get("direct_costs")
    require(type(costs) is list, "DIRECT_COSTS_MISSING")
    total = Decimal("0")
    refs = set()
    for c in costs:
        require(type(c) is dict and c.get("kind") in COST_KINDS,
                "COST_KIND_INVALID")
        cost = money(c.get("amount"))
        ref = proof(c.get("finance_proof"), "finance")
        require(ref not in refs, "DUPLICATED_COST_REFERENCE")
        refs.add(ref)
        total += cost
    return gross, total, gross - total


def _bonus(base, pct):
    return (max(base, Decimal("0")) * pct / Decimal("100")).quantize(
        CENT, rounding=ROUND_HALF_UP)


def calculate_approved_draft(scheme, deal):
    """Produce an auditable DRAFT, never a payable instruction.

    Input is from independently approved corporate finance/CRM evidence;
    cannot be generated by LLM or inferred from the CRM deal amount.
    """
    s = scheme_validate(scheme)
    require(type(deal) is dict, "DEAL_MISSING")
    require(type(deal.get("deal_id")) is int and deal["deal_id"] > 0
            and type(deal.get("signal_card_id")) is int
            and deal["signal_card_id"] >= 8
            and deal["signal_card_id"] not in (3, 7),
            "REAL_DEAL_SIGNAL_LINK_REQUIRED")
    require(deal.get("category_verified") is True
            and deal.get("customer_scope_verified") is True
            and deal.get("customer_new_verified") is True
            and deal.get("global_dedup_clear") is True
            and deal.get("stop_clear") is True,
            "CUSTOMER_AND_STOP_EVIDENCE_UNVERIFIED")
    require(deal.get("scheme_version") == scheme["version"]
            and deal.get("manager_responsible_confirmed") is True,
            "SCHEME_OR_MANAGER_NOT_VERIFIED")
    require(deal.get("financial_approver_role") == "finance"
            and deal.get("finance_approved") is True
            and deal.get("supervisor_approved") is True,
            "FINANCIAL_OR_DIRECTOR_APPROVAL_MISSING")
    lead_date = iso_day(deal.get("lead_registered_at"))
    first_paid = iso_day(deal.get("first_confirmed_payment_at"))
    require(lead_date >= s["start"] and first_paid >= lead_date,
            "CUSTOMER_DATES_NOT_ELIGIBLE")
    require(lead_date >= iso_day(scheme["effective_from"]),
            "SCHEME_NOT_ACTIVE_FOR_LEAD")
    require(type(deal.get("primary_payments")) is list
            and bool(deal["primary_payments"]), "NO_CONFIRMED_PRIMARY_PAYMENTS")
    window_end = first_paid + timedelta(days=s["window_days"])
    revenues = costs = rvp = Decimal("0")
    references = set()
    for entry in deal["primary_payments"]:
        paid = iso_day(entry.get("paid_at"))
        require(first_paid <= paid <= window_end, "PAYMENT_OUTSIDE_PRIMARY_WINDOW")
        ref = proof(entry.get("finance_proof"), "finance", payment=True)
        require(ref not in references, "DUPLICATED_PRIMARY_PAYMENT_PROOF")
        references.add(ref)
        part_revenue, part_cost, part_rvp = _rvp_one(entry, payment=True)
        revenues += part_revenue
        costs += part_cost
        rvp += part_rvp
    team_eligible = (
        lead_date < s["cutoff"]
        and first_paid <= s["cutoff"] + timedelta(days=s["tail_days"])
    )
    team_fund = _bonus(rvp, s["team_percent"]) if team_eligible else Decimal("0.00")
    manager = _bonus(rvp, s["manager_percent"])
    team_split = {k: _bonus(team_fund, v) for k, v in s["shares"].items()}
    # Allocate rounding residual deterministically to one named participant.
    residual = team_fund - sum(team_split.values(), Decimal("0"))
    if residual:
        team_split[sorted(team_split)[0]] += residual
    subs = deal.get("paid_subscription_periods", [])
    require(type(subs) is list, "SUBSCRIPTION_PERIODS_INVALID")
    selected, subscriptions, seen = [], Decimal("0"), set()
    for p in sorted(subs, key=lambda e:(str(e.get("paid_at")),str(e.get("period_id")))):
        require(type(p) is dict and type(p.get("period_id")) is str
                and re.fullmatch(r"[A-Za-z0-9_-]{2,60}",p["period_id"]),
                "SUBSCRIPTION_PERIOD_ID_INVALID")
        require(p["period_id"] not in seen, "SUBSCRIPTION_DUPLICATE_PERIOD")
        seen.add(p["period_id"])
        iso_day(p.get("paid_at"))
        _, _, period_rvp = _rvp_one(p, payment=True)
        if len(selected) < s["subscription_limit"]:
            selected.append(p["period_id"])
            subscriptions += _bonus(period_rvp, s["subscription_percent"])
    out = {
        "status": STATUS_DRAFT, "payout_authorized": False,
        "crm_writes": 0, "scheme_version": scheme["version"],
        "deal_id": deal["deal_id"], "signal_card_id": deal["signal_card_id"],
        "project_bonus_eligible": team_eligible,
        "primary_sale_window_end": window_end.isoformat(),
        "revenue_ex_vat": str(revenues), "documented_direct_costs": str(costs),
        "rvp": str(rvp), "team_fund": str(team_fund),
        "team_split": {k: str(v) for k, v in team_split.items()},
        "manager_primary_bonus": str(manager),
        "manager_subscription_bonus": str(subscriptions),
        "subscription_bonus_period_count": len(selected),
        "subscription_bonus_period_limit": s["subscription_limit"],
        "total_draft_bonus": str(team_fund + manager + subscriptions),
    }
    canonical = json.dumps(out, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
    out["audit_sha256"] = hashlib.sha256(canonical).hexdigest()
    return out
