"""Hybrid LIVE sale-finance intake: prefer verified CRM facts, else ask manager.

Standard Bitrix deal.OPPORTUNITY and TAX_VALUE are quotations, NOT proof of
settlement. Manager edits only the Comments of an actual LeadGen deal, adding
a labeled finance block. The system extracts values and calculates a *draft*;
manual text, even with document numbers, never represents bank verification.

NO CRM writes, payroll, task creation, marketing or external finance calls.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re

from .serviceit_financial_motivation import money, FinanceBlocked
from .serviceit_profit_allocation import split_profit

# Company-approved distribution as reported by project owner on 2026-11-05.
SHARES = (15,10,75)
BEGIN = "SERVICEIT_FINANCE_V1_BEGIN"
END = "SERVICEIT_FINANCE_V1_END"
MANUAL_KEYS = (
    "PAID_EX_VAT_RUB", "DIRECT_COSTS_RUB",
    "PAYMENT_DOCUMENT", "COST_DOCUMENT",
)
AUTOMATIC = {
    "PAID_EX_VAT_RUB": "UF_CRM_SVCIT_FIN_PAID_EX_VAT",
    "DIRECT_COSTS_RUB": "UF_CRM_SVCIT_FIN_DIRECT_COSTS",
    "PAYMENT_DOCUMENT": "UF_CRM_SVCIT_FIN_PAYMENT_DOC",
    "COST_DOCUMENT": "UF_CRM_SVCIT_FIN_COST_DOC",
}
STATUS_MISSING="FINANCE_MANAGER_INPUT_REQUIRED"
STATUS_CONFLICT="FINANCE_VALUES_CONFLICT_MANAGER_REVIEW"
STATUS_DRAFT="FINANCE_CALCULATED_PROVISIONAL_NO_PAYOUT"
STATUS_DOCS="FINANCE_CALCULATED_NEEDS_DOCUMENTS_NO_PAYOUT"


class HybridFinanceBlocked(ValueError):
    pass


def need(ok,code):
    if not ok: raise HybridFinanceBlocked(code)


def parse_comment_block(comments):
    """Strict bounded plain-text block, manager-entered NOT independently true.

    A missing block is normal. A duplicated/malformed one blocks calculation
    (never silently picks the last amount). Manager must preserve other comments.
    """
    if comments in ("",None):
        return {}
    need(type(comments) is str and len(comments) <= 100_000,
         "FINANCE_COMMENTS_UNVERIFIED")
    n_begin=comments.count(BEGIN)
    n_end=comments.count(END)
    if n_begin==n_end==0:
        return {}
    need(n_begin==n_end==1,"FINANCE_DUPLICATE_OR_TRUNCATED_BLOCK")
    s=comments.index(BEGIN)
    e=comments.index(END)
    need(s<e and e-s<=3000,"FINANCE_BLOCK_LIMIT_OR_ORDER")
    block=comments[s+len(BEGIN):e].strip()
    need("<" not in block and ">" not in block and "\r" not in block,
         "FINANCE_BLOCK_RICHTEXT_OR_UNTRUSTED")
    parsed={}
    for line in block.split("\n"):
        line=line.strip()
        if not line: continue
        need("=" in line, "FINANCE_BLOCK_LINE_INVALID")
        k,v=line.split("=",1)
        need(k in MANUAL_KEYS and k not in parsed,
             "FINANCE_BLOCK_FIELD_UNKNOWN_OR_DUPLICATE")
        need(len(v)<=160 and not any(ord(ch)<32 or ord(ch)==127 for ch in v),
             "FINANCE_BLOCK_VALUE_INVALID")
        parsed[k]=v.strip()
    need(set(parsed)<=set(MANUAL_KEYS),
         "FINANCE_BLOCK_EXTRA_FIELDS")
    return parsed


def _candidate(value):
    if value in (None,""):return None
    try:return str(money(value))
    except FinanceBlocked:raise HybridFinanceBlocked(
        "FINANCE_AMOUNT_NOT_DECIMAL") from None


def _doc(value):
    if value in (None,""):return None
    need(type(value) is str and
         re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9_./:#-]{5,159}",
                      value) is not None,
         "FINANCE_DOCUMENT_REF_INVALID")
    return value


def reconcile(automatic, comments, *, bank_evidence=None, expense_evidence=None):
    """Choose present auto data first; manager supplies only genuinely missing.

    Important: auto CRM fields are not independently verified bank receipts.
    An explicit external *bank* verifier can mark outputs verified, never the
    text author, AI, an owner-provided flag, or deal amount.
    """
    need(type(automatic) is dict,"FINANCE_AUTO_FIELDS_INVALID")
    manual=parse_comment_block(comments)
    merged, sources, conflict = {},{},[]
    for key in MANUAL_KEYS:
        raw_auto=automatic.get(AUTOMATIC[key])
        raw_manual=manual.get(key)
        def norm(v):
            return (_candidate(v) if key in MANUAL_KEYS[:2] else _doc(v))
        a,b=norm(raw_auto),norm(raw_manual)
        if a is not None and b is not None and a != b:
            conflict.append(key)
        if a is not None:
            merged[key]=a
            sources[key]="CRM_FINANCE_FIELD"
        elif b is not None:
            merged[key]=b
            sources[key]="MANAGER_BITRIX_DEAL_COMMENTS"
    result={
        "status": STATUS_MISSING,
        "percentages":{"developer":15,"sales_manager":10,"company":75},
        "input_sources":sources,
        "missing":[],
        "conflicting":sorted(conflict),
        "bank_verified":False,
        "expenses_verified":False,
        "payout_authorized":False,
        "crm_writes":0,"tasks_created":0,
    }
    # Never calculate if a conflict could change a payable figure.
    if conflict:
        result["status"]=STATUS_CONFLICT
        return result
    for name in MANUAL_KEYS[:2]:
        if name not in merged:result["missing"].append(name)
    if result["missing"]:
        return result
    calc=split_profit(merged["PAID_EX_VAT_RUB"],
                      merged["DIRECT_COSTS_RUB"])
    result["amounts"]={
        "paid_ex_vat":calc["revenue_ex_vat"],
        "direct_costs":calc["documented_direct_costs"],
        "profit":calc["profit_rvp"],
        "developer":calc["developer_amount"],
        "sales_manager":calc["sales_manager_amount"],
        "company":calc["company_remainder"],
    }
    # Document references from manager are hints, never bank settlement proof.
    docs=[x for x in MANUAL_KEYS[2:] if x not in merged]
    result["missing"]=docs
    result["document_refs_present"]={
        "payment": "PAYMENT_DOCUMENT" in merged,
        "direct_costs": "COST_DOCUMENT" in merged,
    }
    if docs:
        result["status"]=STATUS_DOCS
    else:
        result["status"]=STATUS_DRAFT
    # Only an external trusted finance adapter may supply authenticated proof.
    # This module never accepts a Boolean or alleged approval string as proof.
    for kind, evidence in (("bank",bank_evidence),("expense",expense_evidence)):
        if evidence is not None:
            need(type(evidence) is dict
                 and evidence.get("verification_backend") ==
                 "INDEPENDENT_COMPANY_FINANCE_ADAPTER_V1"
                 and type(evidence.get("signed_receipt_digest")) is str
                 and re.fullmatch(r"[a-f0-9]{64}",
                                  evidence["signed_receipt_digest"]),
                 "FINANCE_EXTERNAL_EVIDENCE_UNVERIFIED")
    # Even if flags are syntactically present, no security claim: verification
    # of signatures/receipts belongs in the independent adapter, not here.
    result["bank_verified"]=False
    result["expenses_verified"]=False
    canonical=json.dumps({"inputs":merged,"calculation":result["amounts"]},
                         sort_keys=True,ensure_ascii=False,separators=(",",":"))
    result["version_sha256"]=hashlib.sha256(
        canonical.encode("utf-8")).hexdigest()
    return result


def manager_template(deal_id, missing):
    need(type(deal_id) is int and deal_id>0,"FINANCE_DEAL_ID_INVALID")
    need(type(missing) is list and len(missing)>0
         and len(missing)==len(set(missing))
         and set(missing)<=set(MANUAL_KEYS),
         "FINANCE_MISSING_LIST_INVALID")
    lines=["Пожалуйста, внесите данные только если они отсутствуют в учёте.",
           "Откройте СДЕЛКУ «Лидогенерация» (не воронку техподдержки),",
           "добавьте В КОНЕЦ поля «Комментарии» этот блок,",
           "не удаляя существующий текст. Если блок уже есть — ИСПРАВЬТЕ его,",
           "НЕ добавляйте второй блок. Суммы только в рублях БЕЗ НДС.",
           "Не путайте сумму сделки с фактической оплатой.",
           "После сохранения автоматическая обработка пересчитает суммы.",
           "",
           BEGIN]
    for key in MANUAL_KEYS:
        if key in missing:
            lines.append(key+"=")
    lines.extend((END,"",
                  "PAID_EX_VAT_RUB — реально поступившая выручка без НДС.",
                  "DIRECT_COSTS_RUB — ВСЕ прямые расходы (включая 0, если их нет).",
                  "PAYMENT_DOCUMENT — номер/ссылка на документ оплаты, если известен.",
                  "COST_DOCUMENT — номер/ссылка на подтверждение затрат.",
                  "Если суммы неизвестны, оставьте их пустыми и уточните в бухгалтерии.",
                  "Суммы от менеджера — предварительные, не разрешение на выплату."))
    return "\n".join(lines)
