"""Universal 1062/21 worker. Offline by default; no historical writer imports.

Corporate verification is an explicit trusted adapter boundary, never inferred
from CRM text. The shipped file adapter accepts only operator-mounted approvals;
automated customer/source verification integrations remain deployment blockers.
"""
from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx

from .ai import AIProvider, config_from_env
from .bitrix_metadata_audit import PORTAL, validate_webhook
from .bitrix_1062_mapping import FIELD_MAP, _is_valid_inn
from .serviceit_bitrix_list_shape import strict_bitrix_items, ItemListShapeError
from .serviceit_ai_lead_engine import assess_lead
from .serviceit_ai_report import build_report, plain
from .serviceit_manager_ai_card import format_verified_card
from .serviceit_b2b_hold_ai_fields import AI_FIELDS, AI_SUFFIXES, PREFIX, hold_analysis_fields
from .serviceit_bitrix_event_queue_readonly_worker import classify_item
from .serviceit_enrichment_store import Store, ModelFence
from .serviceit_kanban_presentation import fields_from_assessment, SCORE_FIELD, VERSION_FIELD, BADGE_FIELD

FIELDS = AI_FIELDS | {SCORE_FIELD, VERSION_FIELD, BADGE_FIELD}
GATES = ('company_registry','source','existing_customer','crm_dedup','stop_list',
         'legal_basis','contact','private_small_moscow','manager_criteria')


class Blocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Blocked(code)


def alias(code):
    return 'ufCrm13Svcit' + ''.join(x.title() for x in code.removeprefix(PREFIX).split('_'))


def normalized(item):
    require(isinstance(item, dict), 'ITEM_INVALID')
    item = dict(item)
    for code in set(FIELD_MAP.values()) | FIELDS | {
            PREFIX+'STOP_REASON', PREFIX+'SIGNAL_KIND', PREFIX+'EVIDENCE_STATUS'}:
        other = alias(code)
        require(not (code in item and other in item and item[code] != item[other]),
                'ALIAS_CONFLICT')
        if code not in item and other in item:
            item[code] = item[other]
        item.pop(other, None)
    return item


def fingerprint(item):
    return sha256(json.dumps(normalized(item), sort_keys=True, ensure_ascii=False,
                            separators=(',', ':')).encode()).hexdigest()


def validate_identity(item, item_id):
    require(type(item_id) is int and 0 < item_id <= 2_000_000_000
            and item_id not in (3, 7), 'HISTORICAL_OR_INVALID_ID_EXCLUDED')
    require(str(item.get('id')) == str(item_id)
            and str(item.get('entityTypeId')) == '1062'
            and str(item.get('categoryId')) == '21', 'IDENTITY_CATEGORY_CONFLICT')


def empty_analysis(item):
    # Absent fields cannot prove an empty before-image.
    require(all(k in item and item[k] in (None, '') for k in FIELDS),
            'EXISTING_OR_UNREADABLE_ANALYSIS')


def schema_limits(rest):
    typ = rest('crm.type.getByEntityTypeId', {'entityTypeId':1062})['type']
    require(str(typ.get('id'))=='13' and str(typ.get('entityTypeId'))=='1062'
            and typ.get('isAutomationEnabled') in (False,'N')
            and typ.get('isBizProcEnabled') in (False,'N'), 'AUTOMATION_SIDE_EFFECTS_NOT_EXCLUDED')
    modes = []
    for mode in ('Y','N'):
        fields = rest('crm.item.fields', {'entityTypeId':1062,'useOriginalUfNames':mode})['fields']
        require(isinstance(fields, dict), 'SCHEMA_INVALID')
        modes.append(fields)
    limits = {}
    for code in FIELDS:
        a, b = modes[0].get(code), modes[1].get(alias(code))
        require(isinstance(a,dict) and isinstance(b,dict) and a == b,
                'SCHEMA_ALIAS_CONTRACT_UNVERIFIED')
        require(a.get('isReadOnly') is False and a.get('isMultiple') in (None,False)
                and a.get('isImmutable') in (None,False),
                'SCHEMA_FIELD_NOT_WRITABLE_SCALAR')
        require(a.get('type') == ('integer' if code == SCORE_FIELD else 'string'),
                'SCHEMA_FIELD_TYPE')
    listing = rest('userfieldconfig.list', {'moduleId':'crm','select':['*'],
                   'filter':{'entityId':'CRM_13'}})
    rows = listing.get('fields')
    require(isinstance(rows,list) and 'next' not in listing, 'SCHEMA_CONFIG_PAGINATED')
    matching = [r for r in rows if isinstance(r,dict) and r.get('fieldName') in FIELDS]
    require(len(matching)==12 and {r['fieldName'] for r in matching}==FIELDS,
            'SCHEMA_CONFIG_MISSING_OR_DUPLICATE')
    for row in matching:
        code = row['fieldName']
        detail = rest('userfieldconfig.get', {'moduleId':'crm','id':row['id']})['field']
        require(detail.get('fieldName')==code and detail.get('entityId')=='CRM_13'
                and detail.get('multiple') in ('N',False)
                and detail.get('userTypeId')==('integer' if code==SCORE_FIELD else 'string'),
                'SCHEMA_CONFIG_CONFLICT')
        settings = detail.get('settings') or {}
        require(not settings.get('REGEXP'), 'SCHEMA_REGEX_REQUIRES_REVIEW')
        limit = settings.get('MAX_LENGTH', 0)
        require(str(limit).isdigit() and str(settings.get('MIN_LENGTH',0)).isdigit()
                and int(settings.get('MIN_LENGTH',0))==0, 'SCHEMA_LIMIT_INVALID')
        limits[code] = min(int(limit) or 12000, 12000)
    return limits


def approval_check(approval, item, item_id):
    require(isinstance(approval, dict), 'CORPORATE_APPROVAL_MISSING')
    require(approval.get('portal') == PORTAL and approval.get('entity_type_id') == 1062
            and approval.get('category_id') == 21 and approval.get('item_id') == item_id
            and approval.get('before_hash') == fingerprint(item), 'APPROVAL_IDENTITY_SNAPSHOT')
    now = time.time()
    require(type(approval.get('expires_at')) in (int,float)
            and now < approval['expires_at'] <= now+3600, 'APPROVAL_EXPIRED')
    require(approval.get('write_authorized') is True
            and approval.get('exclusive_edit_window') is True
            and approval.get('automation_side_effects_reviewed') is True,
            'WRITE_AUTHORIZATION_MISSING')


def verified_fields(approval, item, store, item_id, owner, model):
    checks = approval.get('checks', {})
    for gate in GATES:
        check = checks.get(gate, {})
        require(isinstance(check,dict) and check.get('state') == 'VERIFIED'
                and isinstance(check.get('evidence_ref'),str) and check['evidence_ref'].strip(),
                'CORPORATE_'+gate.upper()+'_HOLD')
    require(approval.get('privacy_approved') is True and model is not None,
            'MODEL_PRIVACY_OR_PERMISSION_MISSING')
    company, signals = approval.get('company'), approval.get('signals')
    require(isinstance(company,dict) and _is_valid_inn(company.get('inn','')),
            'COMPANY_INN_INVALID')
    require(company['inn'] == item.get(FIELD_MAP['company_inn'])
            and company.get('name') == item.get(FIELD_MAP['company_name']), 'COMPANY_BINDING_CONFLICT')
    require(company.get('region') in ('Москва','Московская область'), 'CORPORATE_REGION_HOLD')
    require(isinstance(signals,list) and len(signals)==1, 'PRIMARY_SIGNAL_REQUIRED')
    signal = signals[0]
    source_url = urlsplit(signal.get('source_url',''))
    require(source_url.scheme=='https' and source_url.hostname
            and not source_url.hostname.endswith(('.invalid','.localhost'))
            and not source_url.username and not source_url.password, 'PRIMARY_SOURCE_URL_INVALID')
    require(signal.get('source_url') == item.get(FIELD_MAP['signal_url'])
            and signal.get('signal_date') == item.get(FIELD_MAP['signal_date'])
            and approval.get('source_name') == item.get(FIELD_MAP['signal_source']),
            'SOURCE_BINDING_CONFLICT')
    text = signal.get('evidence')
    plain(text, 1200)  # Reject instructions, contacts, URLs and unsafe source text before model.
    assessment = assess_lead(company, signals, checks={k:checks[k]['state'] for k in GATES}, today=date.today())
    require(not assessment['blocking_checks'] and not assessment['unverified_checks'],
            'QUALIFICATION_HOLD')
    source_key = sha256((company['inn']+'\0'+signal['source_url']+'\0'+text).encode()).hexdigest()
    try:
        store.reserve(item_id, owner, 'model', source_key=source_key)
    except ModelFence as exc:
        raise Blocked(str(exc)) from None
    ai = model(text)
    store.model_output(item_id,sha256(json.dumps(ai,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
    require(isinstance(ai,dict) and ai.get('evidence_quote')
            and ai['evidence_quote'] in text, 'MODEL_QUOTE_INVALID')
    report = build_report(assessment, source_text=text, evidence_quote=ai['evidence_quote'],
                          reference='evidence-sha256-'+sha256(text.encode()).hexdigest(), ai=ai)
    return format_verified_card(report, assessment)


class Worker:
    def __init__(self, store, rest, approvals, model=None, *, enabled=False):
        self.store, self.rest, self.approvals, self.model = store, rest, approvals, model
        self.enabled = enabled

    def read(self, item_id):
        result = self.rest('crm.item.get', {'entityTypeId':1062,'id':item_id,'useOriginalUfNames':'Y'})
        item = normalized(result['item'])
        validate_identity(item, item_id)
        return item

    def once(self, item_id=None):
        if not self.enabled:
            return 'BLOCKED_OFFLINE'
        job = self.store.claim(item_id)
        if job is None:
            return 'IDLE'
        item_id, owner = job
        posted = False
        try:
            item = self.read(item_id)
            empty_analysis(item)
            decision = classify_item(item, requested_id=item_id)
            approval = self.approvals(item_id)
            approval_check(approval, item, item_id)
            limits = schema_limits(self.rest)
            is_hold = decision['decision'] == 'HOLD_MISSING_PRELIMINARY_AI'
            if is_hold:
                tid = item[PREFIX+'SIGNAL_ID'].removeprefix('B2B-CENTER-')
                notice = {'source':'B2B-Center','tender_id':tid,
                          'original_publication_verified':False,'qualified_lead':False,
                          'contact_approved':False,'buyer_inn':None,'fns_msp':None,
                          'subject':approval.get('safe_notice_subject')}
                require(approval.get('preliminary_hold_authorized') is True, 'HOLD_UPDATE_NOT_AUTHORIZED')
                fields = hold_analysis_fields(notice, approval.get('services'))
            else:
                require(decision['source_kind'] not in ('synthetic','b2b_center_hold'),
                        'SOURCE_CONTRACT_HOLD')
                require(not item.get(PREFIX+'STOP_REASON'), 'CRM_STOP_REASON_HOLD')
                fields = verified_fields(approval,item,self.store,item_id,owner,self.model)
            require(set(fields) == (AI_FIELDS if is_hold else FIELDS), 'WRITE_FIELDS_NOT_ALLOWLISTED')
            require(all((type(v) is int and k == SCORE_FIELD and 0<=v<=100)
                        or (type(v) is str and 0<len(v)<=limits[k]) for k,v in fields.items()),
                    'WRITE_FIELD_LIMIT')
            current = self.read(item_id)
            require(fingerprint(current) == fingerprint(item), 'MANAGER_EDIT_CONFLICT')
            approval_check(approval, current, item_id)
            self.store.reserve(item_id,owner,'write',expected=fields,before_hash=fingerprint(item))
            posted = True  # Even malformed/429/error responses are ambiguous writes.
            self.rest('crm.item.update', {'entityTypeId':1062,'id':item_id,
                      'useOriginalUfNames':'Y','fields':fields})
            readback = self.read(item_id)
            require(all(readback.get(k) == v for k,v in fields.items()), 'READBACK_MISMATCH')
            # No source, manager or stage mutation permitted by the worker.
            require(all(readback.get(k) == v for k,v in item.items() if k not in FIELDS
                        and k not in ('updatedTime','updatedBy','lastActivityTime','lastActivityBy')),
                    'NON_TARGET_READBACK_CHANGED')
            state, reason = ('HOLD','SOURCE_UNVERIFIED_PRELIMINARY_VERIFIED') if is_hold else ('VERIFIED','EXACT_READBACK')
        except Blocked as exc:
            state, reason = ('UNCERTAIN' if posted else 'HOLD'), str(exc)
        except Exception:
            # No provider/HTTP exceptions or source payloads go into logs.
            with self.store.db() as db:
                row = db.execute('SELECT model_state FROM enrichment WHERE item_id=?',(item_id,)).fetchone()
            state = 'UNCERTAIN' if posted else ('NEEDS_REVIEW' if row[0] else 'PENDING_SOURCE_CHECK')
            reason = 'EXTERNAL_OPERATION_UNVERIFIED'
        if not self.store.finish(item_id,owner,state,reason):
            return 'LEASE_LOST'
        return state

    def reconcile_uncertain(self, item_id):
        """Read only. Mismatch leaves immutable reservation fenced forever."""
        with self.store.db() as db:
            row = db.execute('SELECT expected FROM enrichment WHERE item_id=? AND write_state IS NOT NULL',
                             (item_id,)).fetchone()
        require(row is not None and row[0], 'NO_WRITE_RESERVATION')
        expected = json.loads(row[0])
        item = self.read(item_id)
        return {'status':'EXACT_TARGET_READBACK' if all(item.get(k)==v for k,v in expected.items())
                else 'UNCERTAIN_REMAINS', 'crm_writes':0}


class Rest:
    def __init__(self, webhook, client):
        self.base, self.client = validate_webhook(webhook), client
        self.field_ids = set()

    def __call__(self, method, params):
        if method in ('crm.item.get','crm.item.fields','crm.item.list','crm.item.update'):
            require(params.get('entityTypeId') == 1062
                    and params.get('useOriginalUfNames') in ('Y','N'), 'REST_SCOPE_BLOCKED')
        elif method == 'crm.type.getByEntityTypeId':
            require(params=={'entityTypeId':1062}, 'REST_SCOPE_BLOCKED')
        elif method == 'userfieldconfig.list':
            require(params=={'moduleId':'crm','select':['*'],'filter':{'entityId':'CRM_13'}},
                    'REST_SCOPE_BLOCKED')
        elif method == 'userfieldconfig.get':
            require(params.get('moduleId')=='crm' and params.get('id') in self.field_ids,
                    'REST_SCOPE_BLOCKED')
        else:
            raise Blocked('REST_SCOPE_BLOCKED')
        if method == 'crm.item.update':
            require(type(params.get('id')) is int and params['id'] not in (3,7)
                    and set(params.get('fields',{})) in (AI_FIELDS,FIELDS), 'REST_UPDATE_BLOCKED')
        response = self.client.post(self.base+'/'+method+'.json', json=params,
                                    follow_redirects=False, timeout=20)
        require(response.status_code == 200 and len(response.content)<=512000, 'REST_HTTP_UNVERIFIED')
        payload = response.json()
        require(isinstance(payload,dict) and 'error' not in payload
                and isinstance(payload.get('result'),dict), 'REST_RESPONSE_UNVERIFIED')
        result = payload['result']
        if method == 'userfieldconfig.list':
            self.field_ids = {r['id'] for r in result.get('fields',[]) if isinstance(r,dict)
                              and r.get('fieldName') in FIELDS and r.get('entityId')=='CRM_13'}
        return result


def poll(store, rest, minimum_id):
    """One bounded ID-ordered page; cursor advances only after durable enqueue."""
    require(type(minimum_id) is int and minimum_id >= 8, 'POLL_START_NOT_AUTHORIZED')
    with store.db() as db:
        row = db.execute("SELECT value FROM worker_meta WHERE key='cursor'").fetchone()
    cursor = max(minimum_id-1, int(row[0]) if row else 0)
    result = rest('crm.item.list', {'entityTypeId':1062,'useOriginalUfNames':'Y',
                  'filter':{'categoryId':21,'>id':cursor},'order':{'id':'ASC'},
                  'select':['id','categoryId'],'start':0})
    fallback_broad = False
    try:
        items = strict_bitrix_items(result)
    except ItemListShapeError as exc:
        if exc.kind != 'EMPTY':
            raise Blocked('POLL_RESPONSE_UNTRUSTED') from None
        # Category-filtered Bitrix24 returned a nonstandard empty nested
        # array. Recheck against all categories with the SAME >id cursor.
        # Never interpret [[]] as evidence that no new cards exist.
        fallback_broad = True
        broad = rest('crm.item.list', {
            'entityTypeId':1062,'useOriginalUfNames':'Y',
            'filter':{'>id':cursor},'order':{'id':'ASC'},
            'select':['id','categoryId'],'start':0})
        try:
            items = strict_bitrix_items(broad)
        except ValueError:
            raise Blocked('POLL_BROAD_RESPONSE_UNTRUSTED') from None
        # If the filtered result is structurally corrupt and the alternate
        # list is empty, an explicit total=0 is required. Otherwise we might
        # skip a real newly created item while advancing a cursor.
        if not items and (broad.get('total') != 0 or 'next' in broad):
            raise Blocked('POLL_BROAD_EMPTY_UNVERIFIED')
    except ValueError:
        raise Blocked('POLL_RESPONSE_UNTRUSTED') from None
    inserted = 0
    for item in items:
        require(isinstance(item,dict), 'POLL_ITEM_INVALID')
        # Bitrix24 crm.item.list silently drops entityTypeId from select.
        # The request is scoped to type 1062; full identity is checked by
        # crm.item.get before any write in Worker and preliminary_once.
        raw_id = item.get('id')
        require(type(raw_id) is int or (
            isinstance(raw_id,str) and raw_id.isascii() and raw_id.isdecimal()),
            'POLL_ID_INVALID')
        item_id = int(raw_id)
        require(8 <= item_id <= 2_000_000_000
                and ('entityTypeId' not in item or
                     str(item['entityTypeId']) == '1062')
                and (type(item.get('categoryId')) is int and
                     0 <= item['categoryId'] <= 10_000_000 or
                     type(item.get('categoryId')) is str and
                     item['categoryId'].isascii() and
                     item['categoryId'].isdecimal() and
                     0 <= int(item['categoryId']) <= 10_000_000),
                'POLL_ITEM_IDENTITY_INVALID')
        if not fallback_broad:
            require(str(item['categoryId']) == '21',
                    'POLL_ITEM_IDENTITY_INVALID')
        require(item_id > cursor, 'POLL_ORDER_INVALID')
        if fallback_broad:
            # Only trust all-category IDs after a separate scoped read.
            full = rest('crm.item.get', {
                'entityTypeId':1062,'id':item_id,'useOriginalUfNames':'Y'}).get('item')
            require(isinstance(full,dict)
                    and str(full.get('id')) == str(item_id)
                    and str(full.get('entityTypeId')) == '1062'
                    and str(full.get('categoryId')) == str(item.get('categoryId')),
                    'POLL_BROAD_IDENTITY_UNVERIFIED')
        if str(item['categoryId']) == '21':
            store.enqueue(item_id)
            inserted += 1
        cursor = item_id
    with store.db() as db:
        db.execute("INSERT OR REPLACE INTO worker_meta VALUES ('cursor',?)", (str(cursor),))
    return inserted


def load_approval(directory, item_id):
    path = directory / f'{item_id}.json'
    if not path.is_file():
        return None
    require(path.stat().st_size<=32768 and not path.is_symlink(), 'APPROVAL_FILE_INVALID')
    return json.loads(path.read_text(encoding='utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--health', action='store_true')
    parser.add_argument('--inspect', action='store_true', help='local redacted queue/dead-letter status only')
    parser.add_argument('--canary-id', type=int, default=0, help='one authorized new card; never starts poller')
    args = parser.parse_args(argv)
    if not args.serve and not args.health and not args.inspect and not args.canary_id:
        print(json.dumps({'status':'BLOCKED_OFFLINE','crm_writes':0,'gigachat_calls':0}))
        return 0
    path = Path(os.environ.get('SERVICEIT_BITRIX_EVENT_QUEUE_DB','/var/lib/serviceit/new-card-events.sqlite3'))
    try:
        require(path.is_absolute(), 'QUEUE_PATH_INVALID')
        require(path.name == 'new-card-events.sqlite3', 'DEDICATED_PRODUCTION_QUEUE_REQUIRED')
        if args.inspect:
            print(json.dumps({'queue':Store(path).inspect(),'crm_writes':0}))
            return 0
        if args.health:
            with Store(path).db() as db:
                row = db.execute("SELECT value FROM worker_meta WHERE key='heartbeat'").fetchone()
            return 0 if row and time.time()-float(row[0])<120 else 2
        store = Store(path)
        enabled = os.environ.get('SERVICEIT_WORKER_LIVE_ENABLED') == 'YES_1062_21'
        require(enabled, 'LIVE_AUTHORIZATION_MISSING')
        minimum = int(os.environ.get('SERVICEIT_POLL_MIN_ID','0'))
        require(minimum>=8, 'POLL_START_NOT_AUTHORIZED')
        approvals = Path(os.environ.get('SERVICEIT_APPROVAL_DIR','/run/serviceit-approvals'))
        require(approvals.is_absolute() and approvals.is_dir(), 'CORPORATE_APPROVAL_ADAPTER_MISSING')
        webhook = Path('/run/secrets/serviceit_rest_webhook').read_text().strip()
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            rest = Rest(webhook, client)
            provider = None
            model = None
            if os.environ.get('SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED') == 'YES':
                model_env = dict(os.environ)
                model_env['GIGACHAT_CREDENTIALS'] = Path('/run/secrets/serviceit_gigachat_credentials').read_text().strip()
                config = config_from_env(model_env)
                require(config.provider == 'gigachat', 'GIGACHAT_REQUIRED')
                provider = AIProvider(config, retry_unauthorized=False)
                def model(text):
                    return {**provider.classify(text), 'provider':'gigachat','model':config.model}
            worker = Worker(store, rest, lambda i:load_approval(approvals,i), model, enabled=True)
            try:
                if args.canary_id:
                    require(not args.serve and 8<=args.canary_id<=2_000_000_000, 'CANARY_ID_INVALID')
                    store.enqueue(args.canary_id)
                    result = worker.once(args.canary_id)
                    print(json.dumps({'status':result,'canary_item_id':args.canary_id}))
                    return 0 if result=='VERIFIED' else 2
                while True:
                    try:
                        poll(store, rest, minimum)
                        for _ in range(10):
                            if worker.once() == 'IDLE':
                                break
                        status = 'RUNNING_GATED'
                    except Exception:
                        status = 'BLOCKED_READ_OR_CONFIG'
                    with store.db() as db:
                        db.execute("INSERT OR REPLACE INTO worker_meta VALUES ('heartbeat',?)", (str(time.time()),))
                    print(json.dumps({'status':status,'queue':store.metrics()}), flush=True)
                    time.sleep(30)
            finally:
                if provider:
                    provider.close()
    except Exception:
        print(json.dumps({'status':'BLOCKED_CONFIGURATION','live_acceptance':'LIVE_UNVERIFIED'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
