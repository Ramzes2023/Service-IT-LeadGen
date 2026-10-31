"""Offline evidence checklist for potential small-business 1C leads.

Produces local review work; never asserts CRM readiness and never contacts any service.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit


def _ignored_export_path(path: str | Path) -> Path:
    p = Path(path).resolve()
    if not p.is_relative_to(Path('exports').resolve()) or p.suffix.lower() != '.json':
        raise ValueError('Review outputs must be JSON files under exports/')
    return p


def _load_research(path: str | Path) -> dict:
    report = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if (not isinstance(report, dict) or report.get('mode') != 'OFFLINE_MVP_RESEARCH'
            or report.get('crm_ready') != 0 or not isinstance(report.get('candidates'), list)):
        raise ValueError('Input must be a safe offline MVP research report')
    return report


def make_checklist(report_path: str | Path, output: str | Path) -> dict:
    """Create a fillable local checklist, leaving evidence and approval empty."""
    report = _load_research(report_path)
    check = []
    seen = set()
    for candidate in report['candidates']:
        if not isinstance(candidate, dict) or candidate.get('status') != 'RESEARCH_ONLY_NOT_CRM_READY':
            raise ValueError('Invalid candidate status')
        inn = candidate.get('inn')
        signal = candidate.get('signal')
        if (not isinstance(inn, str) or not inn.isascii() or not inn.isdigit()
                or len(inn) not in (10, 12) or inn in seen
                or not isinstance(signal, dict) or not isinstance(signal.get('url'), str)):
            raise ValueError('Invalid or duplicate candidate evidence')
        seen.add(inn)
        check.append({
            'inn': inn,
            'company_name': candidate.get('company_name'),
            'source_vacancy_url': signal['url'],
            'business_size': '',
            'size_registry_url': '',
            'size_checked_on': '',
            'corporate_contact_source_url': '',
            'corporate_contact_role': '',
            'contact_channel': '',
            'contact_checked_on': '',
            'reviewed_by_human': False,
            'crm_duplicate_check': 'NOT_PERFORMED',
            'outreach_permission': 'NOT_CONFIRMED'
        })
    target = _ignored_export_path(output)
    if target.exists():
        raise FileExistsError('Existing review checklist must not be overwritten')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({'mode': 'HUMAN_EVIDENCE_CHECKLIST', 'version': 1,
                                  'crm_writes': 'NO', 'crm_ready': 0,
                                  'items': check}, ensure_ascii=False, indent=2),
                      encoding='utf-8')
    return {'rows': len(check), 'path': str(target), 'crm_writes': 'NO', 'crm_ready': 0}


def _is_url(value: object, *, fns_only: bool) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        u = urlsplit(value)
        host = (u.hostname or '').lower()
        return (u.scheme == 'https' and u.port in (None, 443) and bool(host)
                and not u.username and not u.password and not u.fragment
                and (not fns_only or host == 'rmsp.nalog.ru'))
    except ValueError:
        return False


def _fresh(value: object, now: date, max_days: int = 60) -> bool:
    if not isinstance(value, str):
        return False
    try:
        days = (now - date.fromisoformat(value)).days
        return 0 <= days <= max_days
    except ValueError:
        return False


def audit_checklist(path: str | Path, *, today: date | None = None) -> dict:
    """Audit entries only; manual claim is not independent registry/API verification."""
    now = today or date.today()
    obj = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if (not isinstance(obj, dict) or obj.get('mode') != 'HUMAN_EVIDENCE_CHECKLIST'
            or not isinstance(obj.get('items'), list)):
        raise ValueError('Invalid checklist')
    counts = {'total': 0, 'manual_evidence_supplied': 0,
              'needs_manual_enrichment': 0, 'crm_ready': 0, 'crm_writes': 'NO'}
    reasons_count = {}
    details = []
    seen = set()
    for item in obj['items']:
        if not isinstance(item, dict) or item.get('inn') in seen:
            raise ValueError('Duplicate or malformed checklist row')
        inn = item.get('inn')
        if not isinstance(inn, str) or not inn.isascii() or not inn.isdigit() or len(inn) not in (10, 12):
            raise ValueError('Malformed INN')
        seen.add(inn)
        counts['total'] += 1
        reasons = []
        if item.get('business_size') not in ('micro', 'small'):
            reasons.append('small_business_unconfirmed')
        if not _is_url(item.get('size_registry_url'), fns_only=True) or not _fresh(item.get('size_checked_on'), now):
            reasons.append('official_fns_evidence_missing_or_stale')
        if not _is_url(item.get('corporate_contact_source_url'), fns_only=False):
            reasons.append('corporate_contact_source_missing')
        if item.get('contact_channel') not in ('web_form', 'main_office_phone', 'general_email') or not item.get('corporate_contact_role'):
            reasons.append('corporate_contact_channel_or_role_missing')
        if not _fresh(item.get('contact_checked_on'), now):
            reasons.append('corporate_contact_evidence_stale')
        if item.get('reviewed_by_human') is not True:
            reasons.append('manual_review_pending')
        if not reasons:
            counts['manual_evidence_supplied'] += 1
        else:
            counts['needs_manual_enrichment'] += 1
            for reason in reasons:
                reasons_count[reason] = reasons_count.get(reason, 0) + 1
        details.append({'inn': inn, 'status': 'MANUAL_EVIDENCE_PRESENT_NOT_CRM_READY' if not reasons
                        else 'NEEDS_MANUAL_ENRICHMENT', 'issues': reasons})
    return {'mode': 'HUMAN_EVIDENCE_AUDIT', 'counts': counts,
            'reasons': reasons_count, 'details': details,
            'limitations': ['Manual verification is not independent FNS/API verification',
                            'CRM duplicates and opt-in have not been checked',
                            'Never sends leads, makes calls, or changes CRM']}


def main():
    p = argparse.ArgumentParser(description='Offline 1C small-business enrichment checklist')
    sp = p.add_subparsers(dest='command', required=True)
    m = sp.add_parser('prepare')
    m.add_argument('--research', default='exports/mvp_research_review.json')
    m.add_argument('--output', default='exports/mvp_enrichment_checklist.json')
    a = sp.add_parser('audit')
    a.add_argument('--checklist', default='exports/mvp_enrichment_checklist.json')
    args = p.parse_args()
    result = make_checklist(args.research, args.output) if args.command == 'prepare' else audit_checklist(args.checklist)
    print('ENRICHMENT_REVIEW=' + json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
