"""Real-layout synthetic FNS ZIP lookup; no access to real taxpayer records."""
from datetime import date
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from leadpilot.serviceit_v2_fns_release import parse_official_passport
from leadpilot.serviceit_v2_fns_zip import (
    inspect_local_snapshot, ArchiveRejected, sha256_file,
)

INN_77 = "7700000129"
INN_50 = "5000001236"


def release():
    return parse_official_passport(
        b'''<table><tr><td>8</td><td>ZIP</td><td>
        <a href="https://www.nalog.gov.ru/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip">zip</a>
        </td></tr><tr><td>10</td><td>XSD</td><td>
        <a href="https://file.nalog.ru/opendata/7707329152-rsmp/structure-12052026.xsd">XSD</a>
        </td></tr><tr><td>12</td><td>Last modified</td><td>10.10.2026</td></tr></table>''')


def name(i):
    return f"VO_RRMSPSV_0000_9965_20261010_{i:020d}.xml"


def xml(i, inn=INN_77, *, date_text="10.10.2026", region="77"):
    stem = name(i)[:-4]
    text = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<Файл ИдФайл="{stem}" ВерсФорм="4.06" ТипИнф="РЕЕСТРМСП" КолДок="1">'
        f'<ИдОтпр><ФИООтв Фамилия="-" Имя="-"/></ИдОтпр>'
        f'<Документ ИдДок="test-{i}" ДатаСост="{date_text}"'
        f' ДатаВклМСП="01.08.2016" ВидСубМСП="1" КатСубМСП="1">'
        f'<ОргВклМСП ИННЮЛ="{inn}" НаимОрг="ООО ТЕСТОВОЕ"/>'
        f'<СведМН КодРегион="{region}"><Регион Тип="г." Наим="Москва"/></СведМН>'
        f'</Документ></Файл>'
    )
    # Large enough to exercise the minimum archive validation size.
    return text.encode("utf-8") + b" " * 1700


def archive(tmp_path, records):
    path=tmp_path/"snapshot.zip"
    with ZipFile(path,"w",compression=ZIP_DEFLATED) as zf:
        for index, data in records:
            zf.writestr(name(index),data)
    return path


def scan(path, targets=None, **kwargs):
    return inspect_local_snapshot(
        path,release=release(),target_inns=targets or [INN_77],
        expected_archive_sha256=sha256_file(path),**kwargs)


def test_index_finds_two_distinct_subjects_and_missing_inn(tmp_path):
    path=archive(tmp_path,[(1,xml(1,inn=INN_77)),
                           (2,xml(2,inn=INN_50,region="50"))])
    result=scan(path,targets=[INN_77,INN_50,"7701234560"])
    assert result.member_count == 2
    assert result.records_scanned == 2
    assert result.matched[INN_77].category == "MICRO"
    assert result.matched[INN_50].registry_region == "50"
    assert result.missing_inns == ("7701234560",)
    assert result.verified_official_signature is False
    assert result.crm_writes == result.qualified_leads == 0


def test_detects_wrong_snapshot_date(tmp_path):
    path=archive(tmp_path,[(1,xml(1,date_text="10.09.2026"))])
    with pytest.raises(ArchiveRejected,match="FNS_SNAPSHOT_DATE_DISAGREEMENT"):
        scan(path)


def test_detects_duplicate_inn_across_two_members(tmp_path):
    path=archive(tmp_path,[(1,xml(1)),(2,xml(2))])
    with pytest.raises(ArchiveRejected,match="DUPLICATE_INN_ACROSS_XML_FILES"):
        scan(path)


def test_archive_hash_mismatch_blocks_before_xml_read(tmp_path):
    path=archive(tmp_path,[(1,xml(1))])
    with pytest.raises(ArchiveRejected,match="ZIP_ARCHIVE_DIGEST_MISMATCH"):
        inspect_local_snapshot(path,release=release(),target_inns=[INN_77],
                               expected_archive_sha256="f"*64)


@pytest.mark.parametrize("danger", [
    "../VO_RRMSPSV_0000_9965_20261010_bogus.xml",
    "folder/VO_RRMSPSV_0000_9965_20261010_bogus.xml",
    "README.txt",
    "VO_RRMSPSV_0000_9965_20261010_bogus.exe",
])
def test_refuses_non_fns_member_names_or_zip_slip(tmp_path,danger):
    path=tmp_path/"evil.zip"
    with ZipFile(path,"w") as zf:
        zf.writestr(danger,xml(1))
    with pytest.raises(ArchiveRejected,match="UNSAFE_OR_OVERSIZE_ZIP_MEMBER"):
        scan(path)


def test_zip_member_count_declared_mismatch_is_rejected(tmp_path):
    wrong=xml(1).replace(b'KolDok="1"',b'KolDok="999"')
    path=archive(tmp_path,[(1,wrong)])
    with pytest.raises(ArchiveRejected,match="ZIP_MEMBER_RECORD_COUNT_MISMATCH"):
        scan(path)


def test_xml_with_entity_expansion_does_not_execute(tmp_path):
    data=xml(1)
    evil=(b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
          +data.replace(b'<?xml version="1.0" encoding="UTF-8"?>',b""))
    path=archive(tmp_path,[(1,evil)])
    with pytest.raises(ArchiveRejected,match="ZIP_MEMBER_XML_UNSAFE"):
        scan(path)


def test_duplicate_member_name_cannot_shadow_another_member(tmp_path):
    path=tmp_path/"duplicate.zip"
    with ZipFile(path,"w") as zf:
        zf.writestr(name(1),xml(1))
        zf.writestr(name(1),xml(1))
    with pytest.raises(ArchiveRejected,match="DUPLICATE_ARCHIVE_MEMBER"):
        scan(path)


def test_archive_does_not_create_any_customer_lead_or_crm_operations(tmp_path):
    path=archive(tmp_path,[(1,xml(1))])
    out=scan(path)
    assert out.status == "LOCAL_FNS_ARCHIVE_SCAN_COMPLETE_NOT_EGRUL_OR_LEAD"
    assert out.qualified_leads == 0
    assert out.crm_writes == 0
