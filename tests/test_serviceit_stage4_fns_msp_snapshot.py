"""Synthetic fixtures for FNS MSP format. No official ZIP or CRM secrets."""
from datetime import date
from hashlib import sha256
from pathlib import Path
import zipfile
import pytest
from leadpilot.serviceit_stage4_fns_msp_snapshot import verify, SnapshotBlocked

TODAY = date(2026, 11, 4)
SOURCE = ("https://www.nalog.gov.ru/opendata/7707329152-rsmp/"
          "data-10102026-structure-12052026.zip")
INN = "7700000009"

def make_zip(tmp_path, *, category="2", version="4.06", member="fns.xml"):
    text = (f'<Файл ТипИнф="РЕЕСТРМСП" ВерсФорм="{version}">'
            f'<Документ КатСубМСП="{category}">'
            f'<ОргВклМСП ИННЮЛ="{INN}" НаимОрг="Тестовая компания"/>'
            f'</Документ></Файл>')
    target = tmp_path/"sample.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, text.encode("utf-8"))
    return target, sha256(target.read_bytes()).hexdigest()

def test_real_xml_structure_synthetic_msp_member(tmp_path):
    path, digest = make_zip(tmp_path)
    result = verify(path, INN, SOURCE, digest, today=TODAY)
    assert result["status"] == "MATCH_IN_SUPPLIED_MSP_SNAPSHOT"
    assert result["msp_category"] == "SMALL"
    assert result["origin_independently_verified"] is False
    assert result["egrul_active_verified"] is False
    assert result["region_verified"] is False
    assert result["corporate_stop_and_contact_verified"] is False
    assert result["formal_score_authorized"] is False
    assert result["crm_writes"] == 0
    assert INN not in str(result)

def test_absence_in_supplied_sample_is_not_fns_clearance(tmp_path):
    path, digest = make_zip(tmp_path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("fns.xml", (
            '<Файл ТипИнф="РЕЕСТРМСП" ВерсФорм="4.06">'
            '<Документ КатСубМСП="1">'
            '<ОргВклМСП ИННЮЛ="7700000000"/></Документ></Файл>'))
    digest = sha256(path.read_bytes()).hexdigest()
    result = verify(path, INN, SOURCE, digest, today=TODAY)
    assert result["status"] == "NO_MATCH_IN_SUPPLIED_SNAPSHOT"
    assert result["formal_score_authorized"] is False

@pytest.mark.parametrize("bad_url", [
    "https://example.org/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip",
    "http://www.nalog.gov.ru/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip",
    "https://www.nalog.gov.ru/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip?x=1",
    "https://www.nalog.gov.ru/opendata/7707329152-rsmp/data-10102025-structure-12052025.zip",
])
def test_untrusted_or_stale_source_never_scores(tmp_path, bad_url):
    path, digest = make_zip(tmp_path)
    with pytest.raises(SnapshotBlocked):
        verify(path, INN, bad_url, digest, today=TODAY)

def test_archive_digest_tampering_rejected(tmp_path):
    path, digest = make_zip(tmp_path)
    with pytest.raises(SnapshotBlocked, match="SNAPSHOT_HASH_MISMATCH"):
        verify(path, INN, SOURCE, "f"*64, today=TODAY)

@pytest.mark.parametrize("version,category", [("9.99", "2"), ("4.06", "9")])
def test_unknown_msp_schema_or_category_blocks(tmp_path, version, category):
    path, digest = make_zip(tmp_path, category=category, version=version)
    with pytest.raises(SnapshotBlocked):
        verify(path, INN, SOURCE, digest, today=TODAY)

def test_path_traversal_xml_member_rejected(tmp_path):
    path, digest = make_zip(tmp_path, member="../fns.xml")
    with pytest.raises(SnapshotBlocked, match="SNAPSHOT_MEMBER_UNSAFE"):
        verify(path, INN, SOURCE, digest, today=TODAY)
