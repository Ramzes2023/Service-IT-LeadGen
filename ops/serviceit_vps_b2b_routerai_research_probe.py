"""Exercise one model decision using public data from a known tender."""
import os

import httpx

from leadpilot.ai import config_from_env
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_b2b_routerai_deep import plan_research_step, public_documents
from leadpilot.serviceit_public_evidence_research import research_public
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import (
    RESEARCH_STEPS, explicitly_other_tender, tender_scoped_findings,
)
from leadpilot.serviceit_vps_worker import exact_environment

TENDER = "4677404"
URL = "https://www.b2b-center.ru/market/priobretenie-licenzii-1s/tender-4677404/"
WRONG = "https://www.b2b-center.ru/market/litsenzii-1s/tender-4677407/"


def main():
    assert explicitly_other_tender(WRONG, TENDER)
    print("ROUTERAI_RESEARCH_PROBE_FOREIGN_TENDER_REJECTED=YES", flush=True)
    env = exact_environment(dict(os.environ))
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=15) as client:
        findings = research_public(
            {PREFIX + "SIGNAL_URL": URL, PREFIX + "SIGNAL_KIND": "tender-opportunity"},
            client, frozenset({"b2b-center.ru"}), detail_excerpt_chars=4000,
        )
        findings = tender_scoped_findings(findings, TENDER)
        docs = public_documents(findings)
        print(f"ROUTERAI_RESEARCH_PROBE_PUBLIC_PAGES={len(docs)}", flush=True)
        action = plan_research_step(
            client, config_from_env(env), TENDER, findings,
            (*RESEARCH_STEPS, "read_documents"), [],
        )
        print("ROUTERAI_RESEARCH_PROBE_ACTION=" + action.upper(), flush=True)
        print("ROUTERAI_RESEARCH_PROBE_CRM_WRITES=0", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print transport exceptions, URLs, provider payloads or keys.
        print("ROUTERAI_RESEARCH_PROBE_FAILURE=" + type(error).__name__.upper(), flush=True)
        raise SystemExit(2) from None
