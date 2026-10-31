"""Synthetic, offline EIS targeted queue: exact URLs, no CRM, no fake SME."""
from datetime import date
import csv
import hashlib
import json

import pytest

from leadpilot import eis_108_review as r
from leadpilot.eis_rss import SOURCE_NAME

DAY = date(2026, 10, 31)
PREFIX = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber="


def notice(i, *, buyer="ООО Синтетика", title="Сопровождение 1С:Предприятие"):
    url = PREFIX + str(12345678901234000000 + i)
    return {
        "source_name": SOURCE_NAME, "source_url": url,
        "signal_id": hashlib.sha256(url.encode()).hexdigest()[:24],
        "title": title, "procurement_subject_from_rss": title,
        "buyer_name_rss_unverified": buyer, "published_on": "2026-10-30",
        "buyer_inn": None, "crm_ready": False, "outbound_authorized": False,
        "manager_approved": False, "lead_score": None,
    }


def fixture(tmp_path, profiles=None):
    # The support query finds two, technical reuses the same, customization
    # adds a third; setup reuses a previously found notice.
    profiles = profiles or [
        [notice(1), notice(2, buyer="ФГБУ Синтетический центр")],
        [notice(2, buyer="ФГБУ Синтетический центр")],
        [notice(3)],
        [notice(1)],
    ]
    name = "eis_targeted_1c_20261031T153000Z"
    summary = tmp_path / (name + "_summary.json")
    seen = set()
    description = []
    for profile_id, items in zip(r.PROFILE_IDS, profiles):
        new = 0
        for row in items:
            if row["source_url"] not in seen:
                new += 1
            seen.add(row["source_url"])
        description.append({
            "profile_id": profile_id,
            "items_seen": len(items), "research_candidates": len(items),
            "new_unique_notices": new,
        })
        (tmp_path / (name + "_" + profile_id + ".json")).write_text(
            json.dumps({"mode": r.SOURCE_MODE, "source": SOURCE_NAME,
                "counts": {"items_seen": len(items),
                           "candidates_for_identity_review": len(items),
                           "qualified_small_business_leads": 0, "crm_ready": 0},
                "items": items, "crm_writes": "NO", "outbound_messages": "NO"}),
            encoding="utf-8",
        )
    summary.write_text(json.dumps({
        "mode": r.BATCH_MODE,
        "buyer_fns_status": "MANUAL_EXACT_INN_REQUIRED",
        "counts": {"unique_research_notices": len(seen),
                   "qualified_leads": 0, "crm_ready": 0},
        "profiles": description,
        "crm_writes": "NO", "outbound_messages": "NO",
    }), encoding="utf-8")
    return summary


def test_108_style_dedup_and_public_sector_hint(tmp_path):
    report = r.build_queue(fixture(tmp_path), today=DAY)
    assert report["counts"]["unique_notices_to_review"] == 3
    assert report["counts"]["suspected_public_institutions"] == 1
    assert report["counts"]["exact_inn_fns_verified"] == 0
    assert report["counts"]["qualified_b2b_leads"] == 0
    assert report["counts"]["crm_ready"] == 0
    assert report["crm_writes"] == "NO"
    p = {x["official_notice_url"]: x for x in report["items"]}
    assert len(p) == 3
    assert p[notice(2)["source_url"]]["seen_in_profiles"] == ["support", "technical"]
    assert p[notice(2)["source_url"]]["public_sector_hint"] == "POSSIBLE_PUBLIC_INSTITUTION_REVIEW"
    assert all("BUYER_EXACT_INN" in x["verification_holds"] for x in report["items"])
    assert all(x["crm_ready"] is False for x in report["items"])


def test_empty_profiles_do_not_make_leads(tmp_path):
    report = r.build_queue(fixture(tmp_path, profiles=[[], [], [], []]), today=DAY)
    assert report["counts"]["unique_notices_to_review"] == 0
    assert report["items"] == []


def test_summary_mismatch_fails_closed(tmp_path):
    file = fixture(tmp_path)
    data = json.loads(file.read_text())
    data["counts"]["unique_research_notices"] = 44
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="UNIQUE_COUNT_MISMATCH"):
        r.build_queue(file, today=DAY)


def test_fake_preapproved_notice_fails_closed(tmp_path):
    file = fixture(tmp_path)
    source = file.with_name(file.name.replace("_summary", "_support"))
    data = json.loads(source.read_text())
    data["items"][0]["crm_ready"] = True
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="PREAPPROVED"):
        r.build_queue(file, today=DAY)


def test_wrong_public_notice_host_fails_closed(tmp_path):
    file = fixture(tmp_path)
    source = file.with_name(file.name.replace("_summary", "_support"))
    data = json.loads(source.read_text())
    data["items"][0]["source_url"] = "https://fake.example/notice"
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="NOT_OFFICIAL"):
        r.build_queue(file, today=DAY)


def test_stale_notice_fails_closed(tmp_path):
    file = fixture(tmp_path)
    source = file.with_name(file.name.replace("_summary", "_support"))
    data = json.loads(source.read_text())
    data["items"][0]["published_on"] = "2026-06-01"
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="STALE"):
        r.build_queue(file, today=DAY)


def test_exclusive_export_and_formula_escaping(tmp_path, monkeypatch):
    file = fixture(tmp_path, profiles=[
        [notice(1, buyer="=WEBSERVICE(123)")], [], [], [],
    ])
    report = r.build_queue(file, today=DAY)
    monkeypatch.chdir(tmp_path)
    r.save_queue(report, "exports/review.json", "exports/review.csv")
    with (tmp_path / "exports/review.csv").open(encoding="utf-8-sig", newline="") as handle:
        lines = list(csv.DictReader(handle))
    assert len(lines) == 1
    assert lines[0]["buyer_name_rss_unverified"].startswith("'=")
    with pytest.raises(ValueError, match="NEW_EXPORT"):
        r.save_queue(report, "exports/review.json", "exports/review.csv")
    with pytest.raises(ValueError, match="NEW_EXPORT"):
        r.save_queue(report, "other/review.json", "exports/new.csv")


def test_missing_report_or_unsafe_summary_rejected(tmp_path):
    with pytest.raises(ValueError):
        r.build_queue(tmp_path / "not_here.json", today=DAY)
    file = fixture(tmp_path)
    doc = json.loads(file.read_text())
    doc["crm_writes"] = "YES"
    file.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="UNTRUSTED"):
        r.build_queue(file, today=DAY)
