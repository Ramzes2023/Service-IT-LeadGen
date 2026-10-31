"""Deterministic, synthetic manager acceptance; no network or persistent data."""
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .manager_briefs import build_evidence_briefs
from .crm_contract import MemoryCRM, preflight_contract


def demonstrate():
    now = date(2026, 10, 31)
    # Checksum-valid test identifier; not a claim about a real company's needs.
    inn = "7700000009"
    row = dict(inn=inn, company_name="SYNTHETIC DEMO ONLY", region="Москва",
               fns_category="MICRO", fns_snapshot=now.isoformat(),
               fns_source="https://rmsp.nalog.ru/search.html?mode=inn-list",
               okved_public="46.49", business_size_verified_from_export=True,
               qualification="SIZE_ONLY_RESEARCH", demand_for_1c="NOT_CHECKED",
               lead_score=None, crm_ready=False, outbound_authorized=False)
    with TemporaryDirectory(prefix="serviceit-synthetic-") as directory:
        root = Path(directory)
        def put(name, data):
            path = root / name
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
            return path
        fns = put("fns.json", dict(mode="SME_FIRST_FNS_SIZE_ONLY", snapshot=now.isoformat(),
            counts={"size_eligible_for_research": 1, "crm_ready": 0},
            crm_writes="NO", outbound_messages="NO", companies=[row]))
        facts = put("facts.json", dict(mode="PUBLIC_CORPORATE_FACTS", items=[dict(
            inn=inn, kind="it_expansion", source_url="https://example.invalid/demo",
            event_on=now.isoformat(), observed_on=now.isoformat(),
            excerpt="Synthetic IT expansion for acceptance demonstration only",
            review_state="PENDING")]))
        contacts = put("contacts.json", dict(mode="PUBLIC_CORPORATE_CHANNELS", items=[dict(
            inn=inn, channel="email", value="info@example.invalid",
            source_url="https://example.invalid/contacts", corporate_domain="example.invalid",
            observed_on=now.isoformat(), review_state="PENDING", purpose="corporate_review", opt_out=False)]))
        briefs = build_evidence_briefs(fns, facts=facts, contacts=contacts, today=now)
        card = briefs["briefs"][0]
        crm = MemoryCRM()
        crm.available = False
        queued = crm.submit(card, manager_reviewed=True, today=now)
        crm.available = True
        retried = crm.retry(manager_reviewed=True, today=now)
        repeated = crm.submit(card, manager_reviewed=True, today=now)
        return dict(mode="SYNTHETIC_ACCEPTANCE_ONLY", briefs=briefs,
                    queued=queued, retried=retried, repeated=repeated,
                    simulated_leads=len(crm.leads), simulated_tasks=len(crm.history),
                    preflight=preflight_contract(), crm_ready=False,
                    crm_writes="NO", outbound_messages="NO")
