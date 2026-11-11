"""V25 guarded old one-field summary proof cannot write Bitrix or Postgres."""
from pathlib import Path
import ast


def test_first_legacy_summary_supersession_only_when_v11_exact():
    src=Path("ops/serviceit_vps_b2b_v25_first_supersession_readonly.py").read_text("utf8")
    workflow=Path(".github/workflows/serviceit-vps-b2b-v25-first-supersession-readonly.yml").read_text("utf8")
    ast.parse(src)
    assert "SET TRANSACTION READ ONLY" in src
    assert 'NS="AI_REPAIR_SUMMARY_V1"' in src
    assert 'KEY=PREFIX+"AI_SUMMARY"' in src
    assert "original_nine(current)==v11after" in src
    assert "compute_digest(v11after,v11salt)" in src
    assert "digested({KEY:current.get(KEY)},bytes(salt))" in src
    assert "SUPERSESSION_PROOF" in src
    assert "crm.item.update" not in src
    assert "UPDATE serviceit_vps_native_receipts" not in src
    assert "DELETE FROM" not in src
    assert "github.actor == 'Ramzes2023'" in workflow
    assert "github.event.issue.number == 489" in workflow
    assert "SERVICEIT_B2B_V25_FIRST_SUPERSESSION_READONLY_489" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "git diff --quiet" in workflow
