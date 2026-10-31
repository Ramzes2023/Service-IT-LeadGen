"""Offline FNS batch INN preparation must preserve CRM privacy and source data."""
import json
from pathlib import Path
import pytest

from leadpilot.fns_batch_prepare import collect_inns, prepare, FNS_BATCH_URL


def review_file(tmp_path, *, inns=("7701234560", "5003079216"), mode="PILOT_SCOPE_REVIEW"):
    p = tmp_path / "scope.json"
    p.write_text(json.dumps({
        "mode": mode,
        "items": [{"inn": inn, "company_name": "Internal company",
                   "email": "never-export@example.org",
                   "decision": "PENDING"} for inn in inns]
    }, ensure_ascii=False), encoding="utf-8")
    return p


def test_creates_legal_inn_batch_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = review_file(tmp_path)
    old = src.read_bytes()
    result = prepare(src, "exports/fns_batch_inns.txt")
    assert result["organization_count"] == 2
    assert result["fns_categories_verified"] == 0
    assert result["crm_ready"] == 0
    assert result["crm_writes"] == "NO"
    assert result["official_browser_url"] == FNS_BATCH_URL
    output = Path("exports/fns_batch_inns.txt").read_text(encoding="utf-8")
    assert output == "5003079216\n7701234560\n"
    assert "Internal" not in output and "never-export" not in output
    assert src.read_bytes() == old


def test_cannot_overwrite_manual_batch_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = review_file(tmp_path)
    prepare(src, "exports/fns_batch_inns.txt")
    with pytest.raises(FileExistsError):
        prepare(src, "exports/fns_batch_inns.txt")


def test_output_forbidden_outside_exports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        prepare(review_file(tmp_path), "data/fns.txt")


@pytest.mark.parametrize("inns", [
    ("7701234560", "7701234560"),
    ("770123456",),
    ("77012345601",),
    ("770123456x",),
    ("123456789012",),
])
def test_reject_malformed_or_duplicate_inns(tmp_path, inns):
    p = review_file(tmp_path, inns=inns)
    with pytest.raises(ValueError):
        collect_inns(p)


def test_accepts_enrichment_review(tmp_path):
    src = review_file(tmp_path, mode="HUMAN_EVIDENCE_CHECKLIST")
    assert collect_inns(src) == ["5003079216", "7701234560"]


def test_reject_wrong_review_mode(tmp_path):
    p = review_file(tmp_path, mode="POTENTIAL_CRM_READY")
    with pytest.raises(ValueError):
        collect_inns(p)


def test_comma_list_is_optional(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = prepare(review_file(tmp_path), "exports/fns_batch_csv.txt", separator="comma")
    assert result["organization_count"] == 2
    assert Path(result["output"]).read_text(encoding="utf-8") == "5003079216,7701234560\n"
