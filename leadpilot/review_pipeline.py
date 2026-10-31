"""Local evidence -> manager queue. No network or imported authorization."""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from .sme_vacancy_audit import _read_fns_candidates, audit as vacancy_audit, _excel_safe
from .public_1c_demand_gate import review as request_review, _parse_date

SCORE_VERSION = "evidence-review-2026-10-v1"
TTL = {"public_request": 30, "1c_vacancy": 30, "it_expansion": 30, "retail_opening": 30}
SERVICES = {"public_request": "1C maintenance", "1c_vacancy": "1C maintenance",
            "it_expansion": "IT outsourcing", "retail_opening": "cash/retail hardware"}


def public_url(raw):
    if not isinstance(raw, str) or len(raw) > 1800 or re.search(r"[\s\\\x00-\x1f]", raw):
        raise ValueError("Invalid public URL")
    try:
        u = urlsplit(raw)
        host = (u.hostname or "").encode("idna").decode("ascii").lower().removeprefix("www.")
        if (u.scheme != "https" or u.username or u.password or u.port not in (None, 443)
                or "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host)
                or host.endswith((".local", ".internal")) or u.fragment):
            raise ValueError()
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
               for label in host.split(".")):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        return urlunsplit(("https", host, u.path or "/", u.query, "")), host
    except (ValueError, UnicodeError):
        raise ValueError("Invalid public URL") from None


def _manual(path, mode):
    if path is None:
        return []
    p = Path(path)
    if p.stat().st_size > 200_000:
        raise ValueError("Manual input too large")
    obj = json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict) or obj.get("mode") != mode or set(obj) != {"mode", "items"}:
        raise ValueError("Invalid manual input envelope")
    rows = obj["items"]
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError("Invalid manual rows")
    return rows


def _exact(row, fields):
    if not isinstance(row, dict) or set(row) != set(fields.split()):
        raise ValueError("Unexpected fields, including possible imported approval")


def _recent(raw, now, ttl=30):
    day = _parse_date(raw, "observed_on")
    if not 0 <= (now - day).days <= ttl:
        raise ValueError("Stale or future observation")
    return day


def _signal(inn, kind, url, event, observed, text, now, deadline=None):
    url, host = public_url(url)
    event = _parse_date(event, "event_on")
    observed = _recent(observed, now)
    if event > observed:
        raise ValueError("Event after observation")
    expiry = event + timedelta(days=TTL[kind])
    if deadline:
        expiry = min(expiry, _parse_date(deadline, "deadline_on"))
    sid = hashlib.sha256(f"{inn}|{kind}|{url}".encode()).hexdigest()[:24]
    return {"id": sid, "inn": inn, "kind": kind, "source_url": url, "source_domain": host,
            "event_on": event.isoformat(), "observed_on": observed.isoformat(),
            "observation_basis": "LOCAL_INPUT_REVIEW_NOT_LIVE_PAGE_FETCH",
            "ttl_days": TTL[kind], "expires_on": expiry.isoformat(),
            "status": "CURRENT_UNVERIFIED" if now <= expiry else "EXPIRED",
            "fact": text, "provenance": "SUPPLIED_SOURCE_NOT_LIVE_VERIFIED",
            "source_strength": "buyer_request" if kind == "public_request" else "indirect",
            "proposed_service": SERVICES[kind], "hypothesis": "Service fit requires manager verification",
            "outsourcing_purchase_confirmed": False}


def assemble(fns, *, requests=None, folders=None, offsets=None, facts=None, contacts=None, today=None):
    now = today or date.today()
    companies, snapshot = _read_fns_candidates(fns, now)
    if any(not re.fullmatch(r"[0-9]{10}", inn) for inn in companies):
        raise ValueError("Legal entities only")
    signals = {inn: {} for inn in companies}
    unmatched = 0
    if requests is not None:
        report = request_review(fns, requests, today=now)
        unmatched = report["counts"]["requests_not_matched_to_fns_50"]
        for c in report["companies"]:
            for r in c["source_requests"]:
                s = _signal(c["inn"], "public_request", r["source_url"], r["published_on"],
                            now.isoformat(), r["evidence_excerpt"], now, r["deadline_on"])
                if r["deadline_on"] is None:
                    s["status"] = "DEADLINE_UNKNOWN"
                signals[c["inn"]][s["id"]] = s
    if folders is not None:
        report = vacancy_audit(fns, folders, offsets, today=now)
        for c in report["companies"]:
            for r in c["vacancy_signals"]:
                s = _signal(c["inn"], "1c_vacancy", r["vacancy_url"], r["published_on"],
                            now.isoformat(), r["title"], now)
                signals[c["inn"]][s["id"]] = s
    for r in _manual(facts, "PUBLIC_CORPORATE_FACTS"):
        _exact(r, "inn kind source_url event_on observed_on excerpt review_state")
        if (r["inn"] not in companies or r["kind"] not in {"it_expansion", "retail_opening"}
                or r["review_state"] != "PENDING" or not isinstance(r["excerpt"], str)
                or not 20 <= len(r["excerpt"]) <= 700):
            raise ValueError("Invalid corporate fact")
        s = _signal(r["inn"], r["kind"], r["source_url"], r["event_on"], r["observed_on"], r["excerpt"], now)
        if s["id"] in signals[r["inn"]]:
            raise ValueError("Duplicate corporate fact")
        signals[r["inn"]][s["id"]] = s
    channels = {inn: [] for inn in companies}
    for r in _manual(contacts, "PUBLIC_CORPORATE_CHANNELS"):
        _exact(r, "inn channel value source_url corporate_domain observed_on review_state purpose opt_out")
        if (r["inn"] not in companies or r["review_state"] != "PENDING"
                or r["purpose"] not in {"corporate_review", "published_bid"}
                or type(r["opt_out"]) is not bool):
            raise ValueError("Invalid channel state")
        url, host = public_url(r["source_url"])
        if not isinstance(r["corporate_domain"], str):
            raise ValueError("Corporate domain required")
        domain_url, domain = public_url("https://" + r["corporate_domain"])
        if domain_url != "https://" + domain + "/":
            raise ValueError("Corporate domain must not include path or query")
        _recent(r["observed_on"], now)
        value = r["value"]
        if not isinstance(value, str):
            raise ValueError("Invalid channel identifier")
        if r["channel"] == "email":
            # Only role mailboxes; no names or personal mail addresses.
            if not re.fullmatch(r"(?:info|office|sales|support|tender|zakupki)@" + re.escape(domain), value.lower()):
                raise ValueError("Corporate role mailbox required")
            value = value.lower()
        elif r["channel"] == "phone":
            if not re.fullmatch(r"\+7[0-9]{10}", value):
                raise ValueError("Canonical corporate phone required")
        elif r["channel"] == "bid_url":
            value, _ = public_url(value)
            from .public_1c_demand_gate import _url_allowed
            if r["purpose"] != "published_bid" or not _url_allowed(value) or value != url:
                raise ValueError("Bid must cite original procurement page")
        else:
            raise ValueError("Unsupported channel")
        if r["channel"] != "bid_url" and host != domain:
            raise ValueError("Corporate source/domain mismatch")
        channels[r["inn"]].append({**r, "value": value, "source_url": url,
            "corporate_domain": domain, "legal_use": "PENDING", "outbound_authorized": False})
    rows = []
    for inn, c in sorted(companies.items()):
        ss = sorted(signals[inn].values(), key=lambda s: s["id"])
        active = [s for s in ss if s["status"] == "CURRENT_UNVERIFIED"]
        cc = channels[inn]
        holds = ["CRM_DUPLICATE_PENDING", "LEGAL_USE_PENDING", "MANAGER_APPROVAL_PENDING"]
        if not active:
            holds.append("DEMAND_UNVERIFIED")
        if not cc:
            holds.append("CONTACT_UNRESOLVED")
        if any(x["opt_out"] for x in cc):
            holds.append("CHANNEL_STOP_LIST")
        domains = {s["source_domain"] for s in active}
        weights = {"target_size_region": 20, "current_evidence": 30 if active else 0,
                   "explicit_buyer_request": 20 if any(s["kind"] == "public_request" for s in active) else 0,
                   "independent_source_domains": 10 if len(domains) >= 2 else 0,
                   "supplied_corporate_channel": 5 if cc else 0}
        status = "candidate_target" if not ss else "evidence_review"
        if active and cc and "CHANNEL_STOP_LIST" not in holds:
            status = "manager_review_candidate"
        rows.append({"inn": inn, "company_name": c["company_name"], "region": c["region"],
            "segment": c["fns_category"], "fns_source": c["fns_source"], "fns_snapshot": snapshot,
            "signals": ss, "channels": cc, "status": status, "score": sum(weights.values()),
            "score_version": SCORE_VERSION, "score_weights": weights, "score_as_of": now.isoformat(),
            "ai_status": "AI_UNAVAILABLE", "ai_confidence": None,
            "service_hypotheses": sorted({s["proposed_service"] for s in active}),
            "why_now": "Review cited events" if active else "UNVERIFIED",
            "holds": holds, "source_conflict_review": bool(ss), "crm_ready": False,
            "crm_duplicate_check": "PENDING", "existing_client_check": "PENDING",
            "outbound_authorized": False, "draft_call_notes": None,
            "next_action": "Review original sources, identity, legal basis and channel; no outreach"})
    # Shared channels/domains only flag review; never merge legal identities.
    owners = {}
    for row in rows:
        for c in row["channels"]:
            for key in ((c["channel"], c["value"]), ("domain", c["corporate_domain"])):
                owners.setdefault(key, set()).add(row["inn"])
    for row in rows:
        if any(len(owners[key]) > 1 for c in row["channels"] for key in
               ((c["channel"], c["value"]), ("domain", c["corporate_domain"]))):
            row["holds"].append("SHARED_CHANNEL_OR_DOMAIN_REVIEW")
            row["status"] = "evidence_review" if row["signals"] else "candidate_target"
    return {"mode": "LOCAL_MANAGER_QUEUE", "checked_on": now.isoformat(), "companies": rows,
        "optional_inputs": {"requests": "SUPPLIED" if requests else "NOT_SUPPLIED",
                            "vacancies": "SUPPLIED" if folders else "NOT_SUPPLIED",
                            "facts": "SUPPLIED" if facts else "NOT_SUPPLIED",
                            "contacts": "SUPPLIED" if contacts else "NOT_SUPPLIED"},
        "counts": {"seen": len(rows), "fns_size_verified": len(rows),
                   "with_current_evidence": sum(any(s["status"] == "CURRENT_UNVERIFIED" for s in r["signals"]) for r in rows),
                   "ready_for_review": sum(r["status"] == "manager_review_candidate" for r in rows),
                   "qualified_leads": 0, "crm_ready": 0, "unmatched_requests": unmatched},
        "sales": None, "contracts": None, "mrr": None, "crm_status": "CRM_BLOCKED",
        "crm_writes": "NO", "outbound_messages": "NO"}


def save(report, json_path, csv_path):
    paths = [Path(json_path).resolve(), Path(csv_path).resolve()]
    root = Path(__file__).resolve().parents[1] / "exports"
    if any(not p.is_relative_to(root) or p.exists() for p in paths) or [p.suffix for p in paths] != [".json", ".csv"]:
        raise ValueError("Use new JSON/CSV files exclusively under repository exports")
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
    with paths[0].open("x", encoding="utf8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
    fields = ["inn", "company_name", "region", "segment", "status", "score", "score_version", "why_now", "holds", "next_action", "signals"]
    with paths[1].open("x", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for row in report["companies"]:
            writer.writerow({k: _excel_safe(json.dumps(row[k], ensure_ascii=False) if isinstance(row[k], list) else row[k]) for k in fields})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fns", default="exports/sme_first_moscow_50.json")
    for arg in ("requests", "facts", "contacts"):
        p.add_argument("--" + arg)
    p.add_argument("--folders", nargs="+")
    p.add_argument("--offsets", nargs="+", type=int)
    p.add_argument("--output", default="exports/integrated_manager_queue.json")
    p.add_argument("--csv", default="exports/integrated_manager_queue.csv")
    a = p.parse_args()
    report = assemble(a.fns, requests=a.requests, facts=a.facts, contacts=a.contacts, folders=a.folders, offsets=a.offsets)
    save(report, a.output, a.csv)
    print(json.dumps(report["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
