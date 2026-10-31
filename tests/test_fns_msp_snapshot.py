import json
from datetime import date
from pathlib import Path
from zipfile import ZipFile
import pytest
from leadpilot.fns_msp_snapshot import augment_checklist, match_snapshot

NOW = date(2026,10,31)


def sample_zip(tmp_path, *, docs=None, version='4.06', malformed=None):
    p=tmp_path/'data-10102026-structure-12052026.zip'
    if docs is None:
        docs=[('5003079216','1'),('7702319337','2')]
    text=''.join(f'<Документ ДатаСост="10.10.2026" КатСубМСП="{cat}"><ОргВклМСП ИННЮЛ="{inn}"/></Документ>' for inn,cat in docs)
    xml=malformed or f'<Файл ТипИнф="РЕЕСТРМСП" ВерсФорм="{version}">{text}</Файл>'
    with ZipFile(p,'w') as z:z.writestr('VO_RRMSPSV_0000_9965_20261010_test.xml',xml.encode('utf8'))
    return p


def checklist(tmp_path):
    p=tmp_path/'original.json'
    p.write_text(json.dumps({'mode':'HUMAN_EVIDENCE_CHECKLIST','items':[
        {'inn':'5003079216','reviewed_by_human':False, 'business_size':'', 'crm_duplicate_check':'NOT_PERFORMED'},
        {'inn':'7702319337','reviewed_by_human':False, 'business_size':''}]},ensure_ascii=False),encoding='utf8')
    return p


def test_matching_official_xml_format(tmp_path):
    r=match_snapshot(sample_zip(tmp_path),{'5003079216','7702319337'},today=NOW)
    assert len(r['matched'])==2
    assert r['matched']['5003079216']['category']=='micro'
    assert r['matched']['7702319337']['category']=='small'
    assert r['authenticity']=='NOT_VERIFIED_LOCAL_FILE'


def test_missing_must_not_be_called_small(tmp_path):
    r=match_snapshot(sample_zip(tmp_path,docs=[('5003079216','1')]),{'5003079216','7702319337'},today=NOW)
    assert '7702319337' in r['unmatched']


def test_no_mutation_and_not_crm_ready(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    p=checklist(tmp_path);before=p.read_bytes()
    out=augment_checklist(p,sample_zip(tmp_path),Path('exports/with_fns.json'),today=NOW)
    assert out['fns_matches']==2 and out['crm_ready']==0
    assert p.read_bytes()==before
    obj=json.loads(Path('exports/with_fns.json').read_text('utf8'))
    assert obj['items'][0]['business_size']==''
    assert obj['items'][0]['fns_snapshot_candidate']['category']=='micro'
    assert obj['items'][0]['crm_duplicate_check']=='NOT_PERFORMED'
    assert obj['items'][0]['outreach_permission']=='NOT_CONFIRMED'


def test_no_overwrite(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    p=checklist(tmp_path);z=sample_zip(tmp_path)
    augment_checklist(p,z,Path('exports/with_fns.json'),today=NOW)
    with pytest.raises(FileExistsError):augment_checklist(p,z,Path('exports/with_fns.json'),today=NOW)


def test_format_mismatch_failclosed(tmp_path):
    with pytest.raises(ValueError):match_snapshot(sample_zip(tmp_path,version='3.0'),{'5003079216'},today=NOW)


def test_conflict_detected(tmp_path):
    with pytest.raises(ValueError):match_snapshot(sample_zip(tmp_path,docs=[('5003079216','1'),('5003079216','2')]),{'5003079216'},today=NOW)


def test_xml_entity_dtd_rejected(tmp_path):
    malicious='<!DOCTYPE foo [ <!ENTITY bad "expanded"> ]><Файл ТипИнф="РЕЕСТРМСП" ВерсФорм="4.06"><Документ ДатаСост="10.10.2026" КатСубМСП="1"><ОргВклМСП ИННЮЛ="5003079216"/></Документ></Файл>'
    z=sample_zip(tmp_path,malformed=malicious)
    with pytest.raises(Exception):match_snapshot(z,{'5003079216'},today=NOW)


def test_output_restricted_to_exports(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    p=checklist(tmp_path);z=sample_zip(tmp_path)
    with pytest.raises(ValueError):augment_checklist(p,z,Path('other/output.json'),today=NOW)
