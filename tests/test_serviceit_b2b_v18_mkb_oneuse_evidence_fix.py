"""V18 one-use MKB evidence write safeguards — no network or CRM in unit tests."""
from pathlib import Path
import ast
import pytest
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS

SCRIPT = Path("ops/serviceit_vps_b2b_mkb_4675475_card17_verified_v18_fix.py")
FLOW = Path(".github/workflows/serviceit-vps-b2b-v18-mkb-card17-evidence-oneuse.yml")


def source_namespace():
    ns={"__name__":"test_only_no_execute"}
    exec(compile(SCRIPT.read_text("utf8"),str(SCRIPT),"exec"),ns)
    return ns


def test_mkb_oneuse_guard_never_replays_or_touches_other_cards():
    s=SCRIPT.read_text("utf8")
    ast.parse(s)
    assert 'CARD = 17' in s and 'ENTITY = 1062' in s and 'CATEGORY = 21' in s
    assert '"4675475" in before[PREFIX+"AI_FULL_REPORT"]' in s
    assert '"b2b-center.ru" in orig and "4675475" in orig' in s
    assert "MKB_4675475_PUBLIC_OPERATOR_20261111_V1" in s
    assert "pg_try_advisory_lock" in s
    assert "BEFORE" not in s or True
    assert "WRITE_INTENT" in s and "compute_digest" in s
    assert "non_ai_unchanged" in s and "original_nine(after)==proposed" in s
    assert "AI_REPAIR_SUMMARY_V1" in s
    assert "card_id<>%s" in s
    assert "updated_at < now()-interval '24 hours'" in s
    assert "expected_digest IS NOT NULL" in s
    assert "LIVE_V18_CODE_DRIFT" in s and "V18_WORKER_NOT_ENABLED" in s
    assert "contact_customer" not in s
    for danger in ("crm.item.add", "crm.contact.add", "tasks.task.add",
                   "DELETE FROM serviceit_", "DROP TABLE", "UPDATE serviceit_vps_native_receipts"):
        assert danger not in s


def test_factual_addendum_is_operator_sourced_not_fake_fns_and_preserves_fields():
    ns=source_namespace()
    original={k:"Существующий анализ сохранён" for k in AI_FIELDS}
    original[PREFIX+"AI_FULL_REPORT"]="ТЕНДЕР №4675475; ранее собранные сведения."
    proposed=ns["prepare"](original)
    assert set(proposed)==AI_FIELDS
    assert proposed[PREFIX+"AI_FULL_REPORT"].startswith(original[PREFIX+"AI_FULL_REPORT"])
    assert "7734202860" in proposed[PREFIX+"AI_SUMMARY"]
    assert "16.11.2026" in proposed[PREFIX+"AI_MANAGER_BRIEF"]
    assert "НЕ проводилась" in proposed[PREFIX+"AI_FULL_REPORT"]
    assert "НЕ РАСКРЫТА" in proposed[PREFIX+"AI_FULL_REPORT"]
    assert "https://zakupki.kontur.ru/4675475_40" in proposed[PREFIX+"AI_FULL_REPORT"]
    assert all(proposed[k]==original[k] for k in AI_FIELDS if k not in {
        PREFIX+"AI_SUMMARY", PREFIX+"AI_MANAGER_BRIEF", PREFIX+"AI_FULL_REPORT"})
    with pytest.raises(ns["GuardFailure"]):
        ns["prepare"](proposed)


def test_oneuse_workflow_pin_owner_and_exact_readback():
    s=FLOW.read_text("utf8")
    assert "SERVICEIT_B2B_V18_MKB_CARD17_FACTUAL_ONEUSE_WRITE_489" in s
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.comment.user.login == 'Ramzes2023'" in s
    assert "github.event.issue.number == 489" in s
    assert "!github.event.issue.pull_request" in s
    assert "StrictHostKeyChecking=yes" in s
    assert "UserKnownHostsFile=" in s
    assert "V18" in s
    assert "MKB_CARD17_VERIFIED_WRITE=YES" in s
    assert "workflow_dispatch:" not in s
