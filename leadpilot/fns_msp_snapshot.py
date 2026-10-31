"""Read a user-supplied FNS SME XML ZIP locally for shortlisted INNs.
No downloads, CRM changes, outreach or authenticity claims.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime
import json
from pathlib import Path
import re
from zipfile import ZipFile
from defusedxml.ElementTree import iterparse

PASSPORT = 'https://www.nalog.gov.ru/opendata/7707329152-rsmp/'
CATEGORY = {'1': 'micro', '2': 'small', '3': 'medium'}
ZIP_NAME = re.compile(r'data-(\d{8})-structure-\d{8}\.zip\Z', re.I)
MAX_MEMBER_SIZE = 64 * 1024 * 1024


def read_checklist(path: Path):
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(report, dict) or report.get('mode') != 'HUMAN_EVIDENCE_CHECKLIST' or not isinstance(report.get('items'), list):
        raise ValueError('Expected local HUMAN_EVIDENCE_CHECKLIST')
    inns = [r.get('inn') if isinstance(r, dict) else None for r in report['items']]
    if (not 1 <= len(inns) <= 100 or any(not isinstance(s, str) or not s.isascii() or not s.isdigit() or len(s) not in (10, 12) for s in inns)
            or len(inns) != len(set(inns))):
        raise ValueError('Checklist must contain 1-100 unique INNs')
    return report, set(inns)


def match_snapshot(zip_path: Path, inns: set[str], *, today: date | None = None):
    match = ZIP_NAME.fullmatch(zip_path.name)
    if not match or not zip_path.is_file():
        raise ValueError('Expected FNS-style local data-DDMMYYYY-structure-DDMMYYYY.zip')
    snapshot = datetime.strptime(match.group(1), '%d%m%Y').date()
    if not date(2016, 8, 1) <= snapshot <= (today or date.today()):
        raise ValueError('Invalid snapshot date')
    found = {}
    count = 0
    with ZipFile(zip_path) as z:
        files = [m for m in z.infolist() if m.filename.lower().endswith('.xml') and not m.is_dir()]
        if not 1 <= len(files) <= 12000:
            raise ValueError('Unexpected number of XML members')
        for m in files:
            if (m.file_size <= 0 or m.file_size > MAX_MEMBER_SIZE or m.compress_size <= 0 or
                    m.file_size > 200 * m.compress_size or not re.fullmatch(r'VO_RRMSPSV_[\w-]+\.xml', Path(m.filename).name, re.I)):
                raise ValueError('Unsafe XML member')
        for m in files:
            count += 1
            with z.open(m) as stream:
                root_seen = False
                for event, node in iterparse(stream, events=('start','end')):
                    if not root_seen and event == 'start':
                        root_seen = True
                        if node.tag != 'Файл' or node.get('ВерсФорм') != '4.06' or node.get('ТипИнф') != 'РЕЕСТРМСП':
                            raise ValueError('FNS XML schema version mismatch')
                    if event != 'end' or node.tag != 'Документ':
                        continue
                    company = node.find('ОргВклМСП')
                    person = node.find('ИПВклМСП')
                    inn = (company.get('ИННЮЛ') if company is not None else
                           person.get('ИННФЛ') if person is not None else None)
                    if inn in inns:
                        code = node.get('КатСубМСП')
                        if code not in CATEGORY or node.get('ДатаСост') != snapshot.strftime('%d.%m.%Y'):
                            raise ValueError('Invalid matched record')
                        item = {'inn':inn, 'category':CATEGORY[code], 'snapshot_date':snapshot.isoformat(),
                                'record_type':'legal_entity' if company is not None else 'individual_entrepreneur',
                                'source':PASSPORT, 'provenance':'UNVERIFIED_USER_PROVIDED_XML_ZIP'}
                        if inn in found and found[inn] != item:
                            raise ValueError('Conflicting category for INN')
                        found[inn] = item
                    node.clear()
            if len(found) == len(inns):
                break
    return {'matched':found, 'unmatched':sorted(inns - found.keys()),
            'snapshot':snapshot.isoformat(), 'members_scanned':count, 'members_total':len(files),
            'authenticity':'NOT_VERIFIED_LOCAL_FILE', 'source':PASSPORT}


def augment_checklist(checklist: Path, fns_zip: Path, output: Path, *, today: date | None = None):
    target = output.resolve()
    if target.suffix.lower() != '.json' or not target.is_relative_to(Path('exports').resolve()):
        raise ValueError('Output must be under exports/ and have .json extension')
    if target.exists():
        raise FileExistsError('Existing human review will not be overwritten')
    data, inns = read_checklist(checklist)
    result = match_snapshot(fns_zip, inns, today=today)
    for row in data['items']:
        row['fns_snapshot_candidate'] = result['matched'].get(row['inn'], {'status':'NOT_FOUND_IN_SUPPLIED_SNAPSHOT'})
        row['crm_duplicate_check'] = 'NOT_PERFORMED'
        row['outreach_permission'] = 'NOT_CONFIRMED'
    data['crm_ready'] = 0
    data['crm_writes'] = 'NO'
    data['fns_snapshot_review'] = {'snapshot': result['snapshot'], 'source':PASSPORT,
        'matched_count':len(result['matched']), 'archive_authenticity':result['authenticity'],
        'manual_confirmation_required':True}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2),encoding='utf-8')
    return {'rows':len(data['items']), 'fns_matches':len(result['matched']),
            'unmatched':len(result['unmatched']), 'crm_ready':0,'crm_writes':'NO',
            'archive_authenticity':'NOT_VERIFIED_LOCAL_FILE','output':str(target)}


def main():
    parser = argparse.ArgumentParser(description='Offline FNS MSP XML matching by INN')
    parser.add_argument('--zip',required=True)
    parser.add_argument('--checklist',default='exports/mvp_enrichment_checklist.json')
    parser.add_argument('--output',default='exports/mvp_enrichment_with_fns.json')
    args = parser.parse_args()
    print('FNS_MSP_REVIEW=' + json.dumps(augment_checklist(Path(args.checklist),Path(args.zip),Path(args.output)),ensure_ascii=False))

if __name__ == '__main__':
    main()
