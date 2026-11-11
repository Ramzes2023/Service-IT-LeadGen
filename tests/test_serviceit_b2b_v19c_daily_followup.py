"""V19C: retry no-data cards after 24h and enable guarded follow-up on 5min VPS."""
from pathlib import Path
import ast
import pytest

WORKER=Path("leadpilot/serviceit_vps_worker.py")
RESEARCH=Path("leadpilot/serviceit_b2b_v19_backfill.py")


def test_v19c_no_replay_verified_or_uncertain_and_retry_cooldown():
    s=RESEARCH.read_text("utf8")
    ast.parse(s)
    assert "r.status NOT IN ('NO_NEW_EVIDENCE','INSUFFICIENT_CAPACITY')" in s
    assert "r.updated_at >= now()-interval '24 hours'" in s
    assert "ON CONFLICT(card_id) DO UPDATE SET" in s
    assert "{TABLE}.status IN (" in s
    assert "'NO_NEW_EVIDENCE','INSUFFICIENT_CAPACITY')" in s
    assert "{TABLE}.updated_at < now()-interval '24 hours'" in s
    assert "after_fields=NULL,salt=NULL,digest=NULL" in s
    assert "WHERE {TABLE}.source_hash=EXCLUDED.source_hash" in s
    assert "'WRITE_INTENT'" in s
    assert "'UNCERTAIN'" in s
    assert "SELECT pg_try_advisory_lock" in s
    assert "non_ai_unchanged" in s
    assert "original_nine(after)==draft" in s


def test_v19c_exact_environment_optin_still_one_card_per_5min():
    s=WORKER.read_text("utf8")
    ast.parse(s)
    assert 'result["SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED"] = "YES"' in s
    assert "INTERVAL_SECONDS = 300" in s
    assert 'if exact_env.get("SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED") == "YES":' in s
    assert "v19 = backfill_once(exact_env, client, connection)" in s
    assert '"historical_cards_touched": v19.get("crm_writes") == 1' in s
    assert "b2b_v19_additive" in s


def test_v19c_sql_manual_key_word_spoof_or_replay_never_accepted():
    from leadpilot.serviceit_b2b_v19_backfill import prepare_addendum
    from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS,PREFIX
    fields={k:"сведения прошлой обработки" for k in AI_FIELDS}
    fields[PREFIX+"AI_FULL_REPORT"]="Тендер №4675475. Старый текст."
    with pytest.raises(Exception):
        prepare_addendum(fields,["поддельное обновление"],"x457")
    assert prepare_addendum(fields,[],"4675475") is None
