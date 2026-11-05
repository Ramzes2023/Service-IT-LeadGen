"""Hybrid 15/10/75 input/receipt logic; zero CRM/payroll writes."""
import pytest
from leadpilot.serviceit_finance_hybrid import (
    reconcile, parse_comment_block, manager_template, HybridFinanceBlocked,
    STATUS_MISSING, STATUS_CONFLICT, STATUS_DRAFT, STATUS_DOCS,
    BEGIN, END, AUTOMATIC,
)


def manual(paid="5000", costs="1000", doc1="bank:05110", doc2="act:05110"):
    return "\n".join([
        "Предыдущий комментарий сохраняется", BEGIN,
        "PAID_EX_VAT_RUB="+paid,
        "DIRECT_COSTS_RUB="+costs,
        "PAYMENT_DOCUMENT="+doc1,
        "COST_DOCUMENT="+doc2,
        END,
        "Текст после блока",])


def test_real_user_5000_minus_1000_with_manual_fallback():
    r=reconcile({},manual())
    assert r["status"]==STATUS_DRAFT
    assert r["amounts"]=={
        "paid_ex_vat":"5000.00","direct_costs":"1000.00",
        "profit":"4000.00","developer":"600.00",
        "sales_manager":"400.00","company":"3000.00"}
    assert r["payout_authorized"] is False
    assert r["bank_verified"] is False
    assert r["input_sources"]["PAID_EX_VAT_RUB"] == "MANAGER_BITRIX_DEAL_COMMENTS"
    assert r["crm_writes"] == 0


def test_auto_first_only_missing_manager_fields():
    auto={AUTOMATIC["PAID_EX_VAT_RUB"]:"5000",
          AUTOMATIC["PAYMENT_DOCUMENT"]:"bank:05110"}
    r=reconcile(auto,manual(paid="",costs="1000",doc1="",doc2="act:05110"))
    assert r["status"]==STATUS_DRAFT
    assert r["amounts"]["developer"]=="600.00"
    assert r["input_sources"]["PAID_EX_VAT_RUB"]=="CRM_FINANCE_FIELD"
    assert r["input_sources"]["DIRECT_COSTS_RUB"]=="MANAGER_BITRIX_DEAL_COMMENTS"


def test_no_payment_does_not_treat_deal_opportunity_as_bank_settlement():
    r=reconcile({"OPPORTUNITY":"5000.00","CURRENCY_ID":"RUB"},None)
    assert r["status"]==STATUS_MISSING
    assert set(r["missing"])=={"PAID_EX_VAT_RUB","DIRECT_COSTS_RUB"}
    assert "amounts" not in r


def test_zero_costs_accepted_if_explicitly_entered():
    r=reconcile({},manual(costs="0"))
    assert r["amounts"]["developer"]=="750.00"
    assert r["amounts"]["sales_manager"]=="500.00"
    assert r["amounts"]["company"]=="3750.00"


def test_missing_costs_does_not_assume_zero():
    r=reconcile({},manual(costs=""))
    assert r["status"]==STATUS_MISSING
    assert r["missing"]==["DIRECT_COSTS_RUB"]
    assert "amounts" not in r


def test_missing_documents_still_calculates_but_not_verified():
    r=reconcile({},manual(doc1="",doc2=""))
    assert r["status"]==STATUS_DOCS
    assert set(r["missing"])=={"PAYMENT_DOCUMENT","COST_DOCUMENT"}
    assert r["amounts"]["profit"]=="4000.00"
    assert r["payout_authorized"] is False


def test_auto_manual_conflicting_amount_holds_not_overwrites():
    auto={AUTOMATIC["PAID_EX_VAT_RUB"]:"10000"}
    r=reconcile(auto,manual(paid="5000"))
    assert r["status"]==STATUS_CONFLICT
    assert r["conflicting"]==["PAID_EX_VAT_RUB"]
    assert "amounts" not in r


def test_duplicated_form_block_refused():
    with pytest.raises(HybridFinanceBlocked,match="DUPLICATE"):
        parse_comment_block(manual()+manual())


@pytest.mark.parametrize("bad", [
    manual(costs="-1"),
    manual(paid="1,5"),
    manual(doc1="x"),
    manual(costs="1000.123"),
    manual(paid="NaN"),
])
def test_invalid_money_or_evidence_rejected(bad):
    with pytest.raises(HybridFinanceBlocked):
        reconcile({},bad)


def test_manager_template_is_internal_guide_and_never_bank_proof():
    task=manager_template(121,["PAID_EX_VAT_RUB"])
    assert BEGIN in task and END in task
    assert "Оплат" not in "" # avoid asserting ungrounded proof
    assert "добавьте В КОНЕЦ" in task
    assert "ПРЯМЫЕ" not in "" # no mock financial values
    assert "PAID_EX_VAT_RUB=" in task
