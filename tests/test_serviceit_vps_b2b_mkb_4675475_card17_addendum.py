"""No-write offline contracts for a one-use externally verified public MKB addendum."""
import ast
from pathlib import Path
import pytest
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX,AI_FIELDS

SCRIPT=Path("ops/serviceit_vps_b2b_mkb_4675475_card17_verified_addendum.py")
FLOW=Path(".github/workflows/serviceit-vps-b2b-four-public-crossportal-readonly.yml")

def module_namespace():
    ns={"__name__":"offline_contract_only"}
    exec(compile(SCRIPT.read_text("utf8"),str(SCRIPT),"exec"),ns)
    return ns

def test_only_one_card_three_ai_fields_and_no_fns_certification():
    s=SCRIPT.read_text("utf8")
    ast.parse(s)
    assert "CARD = 17" in s and "ENTITY = 1062" in s and "CATEGORY = 21" in s
    assert "EXPECTED_MIRROR_SHA" in s
    assert "pg_try_advisory_lock" in s
    assert "WRITE_INTENT" in s
    assert "crm.item.get" in s and "crm.item.update" in s
    assert "non_ai_unchanged" in s and "compare_digest" in s
    assert '"4675475" in before[PREFIX+"AI_FULL_REPORT"]' in s
    assert '"b2b-center.ru" in orig and "4675475" in orig' in s
    for prohibited in ("tasks.task.add","crm.item.add", "crm.deal.add",
                      "COMPANY_INN =", "SCORE_FIELD =", "send_email(",
                      "requests.post("):
        assert prohibited not in s
    ns=module_namespace()
    a=ns["factual_addendum"]()
    assert "7734202860" in a and "775001001" in a
    assert "Московский Кредитный Банк" in a
    assert "16.11.2026 16:00" in a
    assert "цена" in a.lower() and "не раскрыта" in a.lower()
    assert "не проводилась" in a.lower()
    assert "https://zakupki.kontur.ru/4675475_40" in a
    assert "предварительный квалификационный отбор" in a.lower()

def test_old_nine_values_not_lost_and_second_version_rejected():
    ns=module_namespace()
    base={k:"Сохранённый оригинальный текст" for k in AI_FIELDS}
    base[PREFIX+"AI_FULL_REPORT"]="СЕРВИС ИТ | ТЕНДЕРНОЕ ДОСЬЕ №4675475\nИзначальные доказательства."
    changed=ns["prepare"](base)
    assert set(changed)==AI_FIELDS
    assert changed[PREFIX+"AI_FULL_REPORT"].startswith(base[PREFIX+"AI_FULL_REPORT"])
    assert "7734202860" in changed[PREFIX+"AI_MANAGER_BRIEF"]
    assert "7734202860" in changed[PREFIX+"AI_SUMMARY"]
    assert all(changed[k]==base[k] for k in AI_FIELDS if k not in (
        PREFIX+"AI_FULL_REPORT",PREFIX+"AI_MANAGER_BRIEF",PREFIX+"AI_SUMMARY"))
    with pytest.raises(ns["GuardFailure"]):
        ns["prepare"](changed)

def test_registered_owner_oneuse_workflow_no_secret_log_or_bypass():
    s=FLOW.read_text("utf8")
    assert "  mkb_4675475_oneuse_addendum:\n" in s
    assert "SERVICEIT_B2B_MKB_4675475_CARD17_VERIFIED_ADDENDUM_368" in s
    assert s.count("github.event.issue.number == 368")==5
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.comment.user.login == 'Ramzes2023'" in s
    assert "!github.event.issue.pull_request" in s
    assert "StrictHostKeyChecking=yes" in s
    assert "UserKnownHostsFile=" in s
    assert "MKB_CARD17_VERIFIED_WRITE=YES" in s
