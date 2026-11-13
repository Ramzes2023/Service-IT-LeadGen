"""Bidzaar incremental reader. Supplier feed URL is runtime configuration only."""
from datetime import datetime, timezone
from urllib.parse import urlsplit
import re
from .b2b_email_opportunity_bridge import classify_services
from .serviceit_b2b_moscow_geo import classify_location_lines
from .bitrix_1062_mapping import _is_valid_inn

UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('BIDZAAR_DATE_INVALID')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('BIDZAAR_TIMEZONE_REQUIRED')
    return dt.astimezone(timezone.utc)


def feed_endpoint(value):
    p = urlsplit(value)
    if (p.scheme != 'https' or p.netloc != 'bidzaar.com' or p.fragment
            or not re.fullmatch('/api/aggregator-integrator/supplier/' + UUID + '/procedures/purchases', p.path)):
        raise ValueError('BIDZAAR_FEED_INVALID')
    return 'https://bidzaar.com' + p.path


def identity(row):
    p = urlsplit(row.get('link', ''))
    match = re.fullmatch(r'/process/(?:light|full)/(' + UUID + r')/?', p.path)
    if p.scheme != 'https' or p.netloc != 'bidzaar.com' or p.query or p.fragment or not match:
        raise ValueError('BIDZAAR_PROCEDURE_ID_INVALID')
    return 'BIDZAAR-' + match[1]


def decision(row, *, now=None):
    """Only current, explicit service demand and Moscow delivery may create CRM."""
    identity(row)
    groups = classify_services(row.get('name', ''))
    if not groups:
        return 'OUT_OF_SCOPE', []
    if row.get('status') != 'proposal':
        return 'NOT_OPEN', groups
    try:
        deadline = timestamp(row.get('acceptanceEndDate'))
    except (ValueError, TypeError):
        return 'DEADLINE_UNKNOWN', groups
    if deadline <= (now or datetime.now(timezone.utc)):
        return 'EXPIRED', groups
    addresses = row.get('deliveryAddresses')
    if not isinstance(addresses, list) or not addresses or not all(isinstance(a, str) for a in addresses):
        return 'GEO_UNKNOWN', groups
    # Do not substitute the publisher's registered address for delivery.
    geo = classify_location_lines(['Адрес поставки: ' + a for a in addresses])
    if geo != 'MOSCOW':
        return 'GEO_' + geo, groups
    if not _is_valid_inn(row.get('inn')):
        return 'INN_UNKNOWN', groups
    return 'ELIGIBLE', groups


def fetch_page(client, endpoint, cursor, *, page_size=100):
    endpoint = feed_endpoint(endpoint)
    since = timestamp(cursor)
    with client.stream('GET', endpoint, params={'PageSize': page_size, 'FromDate': cursor},
                       timeout=30, follow_redirects=False) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > 8000000:
                raise ValueError('BIDZAAR_RESPONSE_TOO_LARGE')
    import json
    result = json.loads(body)
    rows = result.get('items') if isinstance(result, dict) else None
    if not isinstance(rows, list) or len(rows) > page_size:
        raise ValueError('BIDZAAR_PAGE_INVALID')
    previous = since
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('BIDZAAR_ROW_INVALID')
        identity(row)
        current = timestamp(row.get('lastModified'))
        if current < previous:
            raise ValueError('BIDZAAR_CURSOR_ORDER_INVALID')
        previous = current
    return rows


def collect(client, endpoint, cursor, persist_page, *, max_pages=12):
    """Persist rows AND cursor atomically via callback before requesting more.

    The inclusive cursor intentionally repeats boundary rows. Storage must
    upsert (procedure ID, modification timestamp). Never add a microsecond:
    doing so could skip other procedures with the same timestamp.
    """
    total = 0
    for _ in range(max_pages):
        rows = fetch_page(client, endpoint, cursor)
        if not rows:
            return {'status': 'CAUGHT_UP', 'rows': total, 'cursor': cursor}
        next_cursor = rows[-1]['lastModified']
        persist_page(rows, next_cursor)
        total += len(rows)
        if len(rows) < 100:
            return {'status': 'CAUGHT_UP', 'rows': total, 'cursor': next_cursor}
        if timestamp(next_cursor) <= timestamp(cursor):
            # A full timestamp bucket needs documented pagination; no skipping.
            return {'status': 'CURSOR_STALLED', 'rows': total, 'cursor': cursor}
        cursor = next_cursor
    return {'status': 'MORE_PENDING', 'rows': total, 'cursor': cursor}
