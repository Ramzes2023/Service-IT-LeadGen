"""Bitrix read-only retries with explicit ban on write replays."""
import httpx
import pytest
from leadpilot.serviceit_safe_crm_read_retry import post_read, UnsafeReadRetry
from leadpilot import serviceit_postcall_live_worker as postcall


BASE = "https://serviseit.bitrix24.ru/rest/1/synthetic"


def test_read_only_503_then_ok_has_two_posts_and_bounded_backoff():
    statuses = [503, 200]
    attempts, pauses = [], []
    def server(req):
        attempts.append(req.url.path)
        return httpx.Response(statuses.pop(0), json={
            "error": "QUERY_LIMIT_EXCEEDED"} if len(statuses) else
            {"result": {"items": []}}
        )
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        response = post_read(client, BASE, "crm.item.list",
                             {"entityTypeId": 1062}, sleep=pauses.append)
    assert response.status_code == 200
    assert attempts == ["/rest/1/synthetic/crm.item.list.json"] * 2
    assert pauses == [1]


def test_read_transport_timeout_is_retried_without_any_write():
    n, pauses = [], []
    def server(req):
        n.append(1)
        if len(n) < 3:
            raise httpx.ReadTimeout("temporary")
        return httpx.Response(200, json={"result": {"item": {"id": 7}}})
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        resp = post_read(client, BASE, "crm.item.get", {"id": 7},
                         sleep=pauses.append)
    assert resp.status_code == 200 and len(n) == 3
    assert pauses == [1, 2]


def test_429_operation_limit_is_not_blindly_retried():
    attempts = []
    def server(req):
        attempts.append(1)
        return httpx.Response(429, json={"error": "OPERATION_TIME_LIMIT"})
    with httpx.Client(transport=httpx.MockTransport(server)) as client:
        resp = post_read(client, BASE, "crm.item.list", {},
                         sleep=lambda _: pytest.fail("unexpected delay"))
    assert resp.status_code == 429 and len(attempts) == 1


@pytest.mark.parametrize("method", [
    "crm.item.update", "crm.item.add", "crm.status.add",
    "tasks.task.add", "crm.activity.add", "batch",
])
def test_read_retry_refuses_all_crm_and_task_writes(method):
    def no_http(req):
        pytest.fail("blocked unsafe call escaped into Bitrix")
    with httpx.Client(transport=httpx.MockTransport(no_http)) as client:
        with pytest.raises(UnsafeReadRetry):
            post_read(client, BASE, method, {"fields": {"TITLE": "unsafe"}})


def test_postcall_uses_retry_only_for_reads_but_not_task_add():
    statuses = [503, 200]
    requests = []
    def read_responder(req):
        requests.append(req.url.path)
        status = statuses.pop(0)
        return httpx.Response(status, json={"result": {"item": {"id": 42}}})
    with httpx.Client(transport=httpx.MockTransport(read_responder)) as client:
        value = postcall.api(client, BASE, "crm.item.get",
                             {"entityTypeId": 1062, "id": 42})
    assert value["result"]["item"]["id"] == 42 and len(requests) == 2
    writes = []
    def failing_write(req):
        writes.append(1)
        return httpx.Response(503, json={"error": "QUERY_LIMIT_EXCEEDED"})
    with httpx.Client(transport=httpx.MockTransport(failing_write)) as client:
        with pytest.raises(postcall.PostCallLiveBlocked):
            postcall.api(client, BASE, "tasks.task.add",
                         {"fields": {"TITLE": "synthetic"}})
    assert writes == [1]


def test_three_transient_read_failures_does_not_claim_success():
    seen, pauses = [], []
    def failure(req):
        seen.append(1)
        return httpx.Response(503, json={"error": "QUERY_LIMIT_EXCEEDED"})
    with httpx.Client(transport=httpx.MockTransport(failure)) as client:
        resp = post_read(client, BASE, "crm.item.list", {},
                         sleep=pauses.append)
    assert resp.status_code == 503
    assert len(seen) == 3 and pauses == [1, 2]
