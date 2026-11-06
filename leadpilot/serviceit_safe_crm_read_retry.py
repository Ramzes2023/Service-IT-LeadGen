"""Bounded safe retries for Bitrix24 methods that ONLY READ.

Bitrix REST uses HTTP POST even for list/get methods. Retrying a read is safe;
retrying crm.item.update, tasks.task.add, crm.status.add, or GitHub dispatch
after uncertain outcomes is NOT. 429 OPERATION_TIME_LIMIT is deliberately
not retried without a verified reset time.
"""
from __future__ import annotations
import time
import httpx

READ_ONLY = frozenset({
    "crm.item.list", "crm.item.get", "crm.item.fields",
    "crm.activity.list", "crm.activity.get",
    "crm.enum.ownertype", "crm.category.list", "crm.category.get",
    "tasks.task.list", "tasks.task.get", "tasks.task.getFields",
    "user.get", "user.admin",
})
TRANSIENT_HTTP = frozenset({500, 502, 503, 504})


class UnsafeReadRetry(ValueError):
    pass


def post_read(client, base, method, params, *, timeout=25, sleep=None):
    if (method not in READ_ONLY or type(params) is not dict
            or not isinstance(base, str) or not base.startswith("https://")):
        raise UnsafeReadRetry("ONLY_TRUSTED_BITRIX_READS_CAN_RETRY")
    do_sleep = time.sleep if sleep is None else sleep
    for attempt in range(3):
        try:
            response = client.post(
                base + "/" + method + ".json", json=params,
                follow_redirects=False, timeout=timeout,
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt >= 2:
                raise
            do_sleep(2 ** attempt)
            continue
        if response.status_code in TRANSIENT_HTTP and attempt < 2:
            # Bitrix's QUERY_LIMIT_EXCEEDED may be a temporary 503. No
            # automatic retry for 429 OPERATION_TIME_LIMIT: wait for a
            # separately verified reset; do not flood the portal.
            do_sleep(2 ** attempt)
            continue
        return response
    raise UnsafeReadRetry("READ_RETRY_STATE_UNKNOWN")
