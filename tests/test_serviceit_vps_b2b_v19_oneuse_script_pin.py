"""Regression for exact V19 script pins in live one-use and independent audit."""
from pathlib import Path

def test_oneuse_pin_targets_actual_oneuse_source_not_old_mkb_preview():
    s=Path(".github/workflows/serviceit-vps-b2b-v19-once-real-apply.yml").read_text("utf8")
    sha="ce4624365af93397c3ec124891f4dbb5ebd67415"
    assert "git cat-file -e '"+sha+"^{commit}'" in s
    assert "git diff --quiet "+sha+" HEAD -- \"$script\"" in s
    assert "SERVICEIT_B2B_V19_ONCE_REAL_CARD_EVIDENCE_489" in s
    assert "github.event.issue.number == 489" in s

def test_postread_pin_targets_its_own_independent_source():
    s=Path(".github/workflows/serviceit-vps-b2b-v19-independent-postread.yml").read_text("utf8")
    sha="e92d218ddf94e2b6ef3072230deeca32106257db"
    assert "git cat-file -e '"+sha+"^{commit}'" in s
    assert "git diff --quiet "+sha+" HEAD -- \"$script\"" in s
    assert "SERVICEIT_B2B_V19_INDEPENDENT_POSTREAD_489" in s
    assert "github.event.issue.number == 489" in s
