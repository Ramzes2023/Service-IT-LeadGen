"""Read-only status and source coverage for every signed new B2B card."""
import os
import re
import hashlib
from pathlib import Path
import httpx
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_vps_b2b_deep_research import verified_original_b2b_details, public_buyer_candidate, published_buyer_inn_evidence
from leadpilot.serviceit_b2b_routerai_deep import source_only_investigation
from leadpilot.serviceit_b2b_tender_documents import inspect_public_documents
from leadpilot.serviceit_kanban_presentation import SCORE_FIELD
from leadpilot.bitrix_1062_mapping import _is_valid_inn


def emit(k, v):
    value = str(v).upper()
    if re.fullmatch(r'[A-Z0-9_]{1,150}', value):
        print('DEPTH_' + k + '=' + value, flush=True)


def main():
    env = dict(os.environ)
    for key in ('SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED', 'SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED', 'SERVICEIT_VPS_B2B_PRIMARY_DETAIL_ENABLED', 'SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED'):
        emit(key, env.get(key, 'MISSING'))
    for name in ('serviceit_vps_b2b_deep_research.py', 'serviceit_b2b_tender_documents.py', 'serviceit_vps_b2b_document_worker.py', 'serviceit_vps_b2b_dossier_v11_worker.py', 'serviceit_b2b_dossier_consistency_v37.py'):
        emit('HASH_' + name.removesuffix('.py').upper(), hashlib.sha256((Path('/app/leadpilot') / name).read_bytes()).hexdigest())
    with db(env) as conn, conn.transaction(), httpx.Client(trust_env=False, follow_redirects=False, timeout=14) as client:
        conn.execute('SET TRANSACTION READ ONLY')
        rest = Rest(secret_file(env, 'SERVICEIT_BITRIX_WEBHOOK'), client)
        rows = conn.execute('''SELECT b.card_id,b.source_id,b.notice_json,COALESCE(v.status,'NONE'),COALESCE(d.status,'NONE')
            FROM serviceit_vps_b2b_inbox b
            LEFT JOIN serviceit_vps_b2b_dossier_v11_receipts v ON v.card_id=b.card_id
            LEFT JOIN serviceit_vps_b2b_tender_doc_receipts d ON d.card_id=b.card_id
            WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL ORDER BY b.card_id DESC LIMIT 30''').fetchall()
        emit('SIGNED_CARDS', len(rows))
        for card, source, notice, v11, docs in rows:
            key = f'CARD_{card}_'
            emit(key+'TENDER', notice['tender_id'])
            emit(key+'V11', v11)
            emit(key+'TDI', docs)
            item = normalized(rest('crm.item.get', {'entityTypeId':1062,'id':card,'useOriginalUfNames':'Y'})['item'])
            validate_identity(item, card)
            assert item.get(PREFIX+'SIGNAL_ID') == source
            emit(key+'INN_VALID', 'YES' if _is_valid_inn(item.get(PREFIX+'COMPANY_INN')) else 'NO')
            emit(key+'DECISION_EMPTY', 'YES' if not item.get(PREFIX+'REVIEW_DECISION') else 'NO')
            emit(key+'SCORE', item.get(SCORE_FIELD, 'NONE'))
            emit(key+'BRIEF_LENGTH', len(item.get(PREFIX+'AI_MANAGER_BRIEF') or ''))
            emit(key+'REPORT_LENGTH', len(item.get(PREFIX+'AI_FULL_REPORT') or ''))
            if card < 29:
                continue
            found = verified_original_b2b_details({'status':'PUBLIC_SOURCE_NOT_READABLE','pages':[]}, notice, client)
            emit(key+'PRIMARY', 'YES' if found.get('original_b2b_detail_independently_read') else 'NO')
            emit(key+'BUYER', 'YES' if public_buyer_candidate(found) else 'NO')
            emit(key+'PROFILE_LINKS', sum(len(x.get('profile_links', [])) for x in found.get('pages', [])))
            if found.get('status') != 'PUBLIC_PAGES_INSPECTED':
                continue
            report = source_only_investigation(found)
            for category in ('BUYER','DEADLINE','LOCATION','PAYMENT','SCOPE','ELIGIBILITY','BUDGET','DOCUMENTS'):
                emit(key+'FACTS_'+category, sum(f.get('category') == category for f in report['facts']))
            evidence = inspect_public_documents(client, found, notice['tender_id'], enable_ocr=True)
            emit(key+'DOC_LINKS', evidence['discovered'])
            emit(key+'DOCS_READ', len(evidence['documents']))
            for reason,count in evidence['rejections'].items():
                if re.fullmatch(r'[A-Z0-9_]{1,70}', reason):
                    emit(key+reason, count)
    emit('CRM_WRITES', 0)


if __name__ == '__main__':
    main()
