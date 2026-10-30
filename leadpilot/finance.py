"""Auditable financial formula sample only; not payment confirmation or payroll."""
from decimal import Decimal


def d(value):
    return Decimal(str(value))


def calculate_rvp(revenue_ex_vat, software=0, equipment=0, outside_services=0,
                  direct_labor=0, other_direct=0):
    parts = [d(software), d(equipment), d(outside_services), d(direct_labor), d(other_direct)]
    if any(x < 0 for x in parts) or d(revenue_ex_vat) < 0:
        raise ValueError("Отрицательные входные суммы недопустимы")
    return d(revenue_ex_vat) - sum(parts, Decimal("0"))


def sample_finance():
    return {"mode": "formula_demo_no_payments", "revenue_ex_vat": 180000,
            "software": 45000, "equipment": 65000, "direct_labor": 10000,
            "rvp": str(calculate_rvp(180000, software=45000, equipment=65000, direct_labor=10000))}
