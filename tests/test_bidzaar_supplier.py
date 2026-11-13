"""Synthetic normalization fixtures only: no customers, live responses or leads."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from leadpilot.bidzaar_supplier import MODE, SERVICES, decode_contract, review

NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
ID = "00000000-0000-0000-0000-000000000001"


def fixture():
    return {"mode": MODE, "authorization": "COMPANY_AUTHORIZED_EXPORT", "items": [{
        "id": ID, "url": f"https://bidzaar.com/app/process/light/{ID}",
        "title": "SYNTHETIC закупка сопровождения 1С", "excerpt": "SYNTHETIC требуется платное сопровождение 1С подрядчиком",
        "category": "SYNTHETIC IT", "origin": "bidzaar_original", "kind": "procurement",
        "paid_external": True, "status": "open", "posted_at": "2026-10-30T10:00:00+03:00",
        "deadline_at": "2026-11-02T12:00:00+03:00", "observed_at": "2026-10-31T11:00:00Z",
        "buyer_name": "SYNTHETIC NOT A CUSTOMER", "buyer_inn": "", "inn_role": "unknown",
        "buyer_region": "77", "execution_region": "06", "ownership": "private", "access": "authorized"}]}


def run(doc=None, **kw):
    return review(doc or fixture(), now=NOW, **kw)


def test_truthful_zero_and_no_mutation():
    doc = fixture()
    before = deepcopy(doc)
    report = run(doc)
    assert doc == before
    assert report["QUALIFIED_REAL_LEADS"] == 0
    assert report["LIVE_SOURCE"] == "BLOCKED_NOT_CONNECTED"
    assert set(report["real_live_counts"].values()) == {0}
    row = report["items"][0]
    assert row["service_evidence"] == {"1c": ["1с"]}
    assert row["buyer_region_priority"] == "PRIMARY"
    assert row["score_status"] == "NOT_SCORED"
    assert not row["crm_ready"] and not row["outbound_authorized"]
    assert {"EXISTING_CLIENTS_PENDING", "STOP_LIST_PENDING", "FNS_EXACT_CATEGORY_PENDING"} <= set(row["holds"])


@pytest.mark.parametrize("service", SERVICES)
def test_nine_services_have_traceable_text(service):
    doc = fixture()
    r = doc["items"][0]
    r.update(title="SYNTHETIC закупка", excerpt="SYNTHETIC требуется " + SERVICES[service][0])
    assert SERVICES[service][0] in run(doc)["items"][0]["service_evidence"][service]


def test_category_is_not_intent():
    doc = fixture()
    doc["items"][0].update(title="SYNTHETIC paper", excerpt="SYNTHETIC paper", category="1С CRM ERP")
    assert run(doc)["items"][0]["service_evidence"] == {}


@pytest.mark.parametrize("field,value,hold", [
    ("kind", "vacancy", "NOT_EXTERNAL_PAID_PROCUREMENT"),
    ("kind", "article", "NOT_EXTERNAL_PAID_PROCUREMENT"),
    ("kind", "marketing", "NOT_EXTERNAL_PAID_PROCUREMENT"),
    ("kind", "supplier_registry", "NOT_EXTERNAL_PAID_PROCUREMENT"),
    ("excerpt", "SYNTHETIC вакансия программист 1С", "NOT_EXTERNAL_PAID_PROCUREMENT"),
    ("paid_external", False, "NOT_EXTERNAL_PAID_PROCUREMENT"),
    ("origin", "aggregated_government", "NOT_DIRECT_ORIGINAL"),
    ("origin", "unknown", "NOT_DIRECT_ORIGINAL"),
    ("ownership", "state", "STATE_OR_UNKNOWN_OWNERSHIP"),
    ("ownership", "unknown", "STATE_OR_UNKNOWN_OWNERSHIP"),
    ("access", "paid_required", "ACCESS_ENTITLEMENT_PENDING"),
    ("status", "closed", "NOT_OPEN_BEFORE_DEADLINE"),
    ("deadline_at", None, "NOT_OPEN_BEFORE_DEADLINE"),
    ("deadline_at", "2026-10-31T15:00:00+03:00", "NOT_OPEN_BEFORE_DEADLINE"),
    ("posted_at", None, "POSTED_UNKNOWN_OR_NOT_NEW"),
    ("posted_at", "2026-09-01T00:00:00Z", "POSTED_UNKNOWN_OR_NOT_NEW"),
    ("observed_at", "2026-10-30T10:00:00Z", "OBSERVATION_STALE"),
    ("buyer_region", "06", "BUYER_REGION_NOT_MOSCOW_MO"),
    ("buyer_inn", "7700000000", "BUYER_LEGAL_INN_UNVERIFIED"),
])
def test_holds(field, value, hold):
    doc = fixture()
    doc["items"][0][field] = value
    assert hold in run(doc)["items"][0]["holds"]


@pytest.mark.parametrize("field,value", [
    ("url", "https://bidzaar.com.evil.test/app/process/light/" + ID),
    ("url", f"https://bidzaar.com/app/process/light/{ID}?token=SECRET"),
    ("url", f"https://user:SECRET@bidzaar.com/app/process/light/{ID}"),
    ("deadline_at", "2026-11-01"), ("observed_at", "2026-11-01T00:00:00Z"),
    ("posted_at", "2026-11-01T00:00:00Z"), ("deadline_at", "2026-09-01T00:00:00Z"),
    ("title", "Bearer SECRET"), ("excerpt", "personal@example.test"),
    ("buyer_name", "+79991234567"), ("paid_external", "true"), ("origin", "invented"),
    ("id", "../SECRET"), ("title", ["SECRET"]),
])
def test_invalid_and_sensitive_inputs_do_not_echo(field, value):
    doc = fixture()
    doc["items"][0][field] = value
    with pytest.raises(ValueError) as exc:
        run(doc)
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_status_fail_closed(status):
    with pytest.raises(ValueError):
        decode_contract(b'{"items":[]}', status=status)


@pytest.mark.parametrize("body", [b'<html>captcha SECRET</html>', b'{"login":"SECRET"}', b'null SECRET', b'{"x":1,"x":2}', b'\xff'])
def test_response_fail_closed(body):
    with pytest.raises(ValueError) as exc:
        decode_contract(body)
    assert "SECRET" not in str(exc.value)


def test_schema_bounds_and_response():
    doc = fixture()
    assert decode_contract(json.dumps(doc).encode()) == doc
    for invalid in (None, [], {**doc, "token": "SECRET"}, {**doc, "items": [doc["items"][0]] * 101}, {**doc, "authorization": "UNKNOWN"}):
        with pytest.raises(ValueError):
            review(invalid, now=NOW)
    with pytest.raises(ValueError):
        decode_contract(b" " * 1_000_001)
    with pytest.raises(ValueError):
        decode_contract(b"{}", content_type="text/html")


def test_dedup_observations_and_preserve_new_signals_same_firm():
    doc = fixture()
    row = doc["items"][0]
    row.update(buyer_inn="7707083893", inn_role="buyer")  # checksum example, synthetic tender
    older = {**row, "observed_at": "2026-10-30T12:00:00Z"}
    second_id = ID[:-1] + "2"
    other = {**row, "id": second_id, "url": row["url"].replace(ID, second_id)}
    doc["items"] = [row, older, other, deepcopy(row)]
    report = run(doc)
    assert report["local_unverified_counts"]["duplicate_observations"] == 2
    assert len(report["companies"]["7707083893"]) == 2
    assert report["items"][0]["first_observed_at"] == "2026-10-30T12:00:00+00:00"
    assert report["items"][0]["last_observed_at"] == "2026-10-31T11:00:00+00:00"


def test_organizer_inn_never_becomes_buyer():
    doc = fixture()
    doc["items"][0].update(buyer_inn="7707083893", inn_role="organizer")
    assert run(doc)["local_unverified_counts"]["buyer_exact_checksum_inn"] == 0


def test_conflicting_identity_fails():
    doc = fixture()
    doc["items"].append({**doc["items"][0], "buyer_name": "SYNTHETIC DIFFERENT"})
    with pytest.raises(ValueError):
        run(doc)


def test_zero_eligible():
    doc = fixture()
    doc["items"] = []
    report = run(doc)
    assert report["items"] == []
    assert report["local_unverified_counts"]["raw"] == 0
    assert report["QUALIFIED_REAL_LEADS"] == 0


def test_exact_fns_join_and_region_mismatch(tmp_path):
    from test_sme_vacancy_audit import setup
    # Shared existing FNS fixture uses the repository's strict evidence contract.
    path, _ = setup(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    doc = fixture()
    doc["items"][0].update(buyer_inn=data["companies"][0]["inn"], inn_role="buyer")
    report = run(doc, fns_file=path)
    assert report["items"][0]["fns_category"] in {"MICRO", "SMALL"}
    assert report["QUALIFIED_REAL_LEADS"] == 0
    assert report["local_unverified_counts"]["fns_moscow_mo_local_matches"] == 1
    assert report["items"][0]["queue"] == "TARGET_EVIDENCE_REVIEW"
    doc["items"][0]["buyer_region"] = "50"
    mismatch = run(doc, fns_file=path)
    assert mismatch["local_unverified_counts"]["fns_moscow_mo_local_matches"] == 0
    assert mismatch["items"][0]["queue"] == "NON_TARGET_OR_UNKNOWN_BUYER_REVIEW"
    data["snapshot"] = "2025-01-01"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        run(doc, fns_file=path)


def test_equivalent_timezone_observations_deduplicate():
    doc = fixture()
    doc["items"].append({**doc["items"][0], "observed_at": "2026-10-31T14:00:00+03:00"})
    assert run(doc)["local_unverified_counts"]["unique_tenders"] == 1


def test_no_network_or_imported_approval(monkeypatch):
    import socket
    import httpx
    def denied(*args, **kwargs):
        pytest.fail("Offline module attempted network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(httpx.Client, "send", denied)
    assert run()["crm_writes"] == "NO"
    doc = fixture()
    doc["items"][0]["crm_ready"] = True
    with pytest.raises(ValueError):
        run(doc)


def test_cli_exclusive_output_and_no_secret_diagnostics(tmp_path, monkeypatch, capsys):
    import sys
    from leadpilot.bidzaar_supplier import main
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "input.json"
    src.write_text(json.dumps(fixture()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["bidzaar", "--input", str(src), "--output", "exports/review.json"])
    main()
    output = tmp_path / "exports/review.json"
    assert json.loads(output.read_text())["QUALIFIED_REAL_LEADS"] == 0
    before = output.read_bytes()
    with pytest.raises(SystemExit):
        main()
    assert output.read_bytes() == before
    src.write_text('{"token":"SECRET"}', encoding="utf-8")
    with pytest.raises(SystemExit):
        main()
    captured = capsys.readouterr()
    assert "SECRET" not in captured.out + captured.err
