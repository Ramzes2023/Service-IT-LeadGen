"""In-memory acceptance simulator only. No production transport exists here."""
from copy import deepcopy
from datetime import date
from .verified_import import is_valid_inn

CONTRACT_VERSION = "lead-signals-v1"
ROLES = {"researcher": "sources_only", "manager": "review_and_tasks",
         "crm_admin": "authorized_preflight", "finance": "later_RVP_only"}


def preflight_contract(*, authorized=False, webhook=None, client=None):
    """Reuse diagnostics only with explicit authorization; reads cannot approve writes."""
    if authorized is not True:
        return {"status": "CRM_BLOCKED", "duplicate_check": "PENDING", "write_authorized": False}
    from .bitrix_sandbox import PreflightConfig, read_only_preflight
    result = read_only_preflight(PreflightConfig(webhook), client=client)
    return {"status": "CRM_BLOCKED", "duplicate_check": "PENDING",
            "write_authorized": False, "diagnostic": result}


class MemoryCRM:
    """Explicit SIMULATION receipts; same-INN lead, independent signals and history.

    Manager reviews here are synthetic events, never credentials or real approvals.
    """
    def __init__(self):
        self.available = True
        self.leads = {}
        self.pending = {}
        self.history = []
        self.deals = {}
        self.stopped_inns = set()

    def stop(self, inn):
        """Synthetic company stop applies to queued retries and later submissions."""
        self.stopped_inns.add(inn)
        self.pending.pop(inn, None)
        if inn in self.leads:
            self.leads[inn]["stop_list"] = ["SIMULATION_COMPANY_STOP"]

    def submit(self, card, *, manager_reviewed=False, today=None):
        inn = card.get("inn")
        if not isinstance(inn, str) or len(inn) != 10 or not is_valid_inn(inn):
            raise ValueError("Exact legal-entity INN required")
        if (card.get("crm_ready") is not False or card.get("outbound_authorized") is not False
                or card.get("status") != "manager_review_candidate" or manager_reviewed is not True
                or inn in self.stopped_inns
                or any(h in card.get("holds", []) for h in
                       ("CHANNEL_STOP_LIST", "SHARED_CHANNEL_OR_DOMAIN_REVIEW", "DEMAND_UNVERIFIED"))
                or not card.get("channels") or any(c.get("opt_out") for c in card["channels"])):
            return {"status": "SIMULATION_HOLD", "crm_ready": False}
        signals = [s for s in card.get("signals", []) if s.get("status") == "CURRENT_UNVERIFIED"]
        now = today or date.today()
        try:
            if not 0 <= (now - date.fromisoformat(card["fns_snapshot"])).days <= 60:
                return {"status": "SIMULATION_HOLD", "crm_ready": False}
            if any(c.get("inn") != inn or c.get("opt_out") is not False
                   or c.get("review_state") != "PENDING"
                   or c.get("outbound_authorized") is not False
                   or not 0 <= (now - date.fromisoformat(c["observed_on"])).days <= 30
                   for c in card["channels"]):
                return {"status": "SIMULATION_HOLD", "crm_ready": False}
            signals = [s for s in signals if date.fromisoformat(s["event_on"]) <= now <= date.fromisoformat(s["expires_on"])]
        except (KeyError, ValueError, TypeError):
            return {"status": "SIMULATION_HOLD", "crm_ready": False}
        if not signals or any(s.get("inn") != inn for s in signals):
            return {"status": "SIMULATION_HOLD", "crm_ready": False}
        if not self.available:
            self.pending[inn] = deepcopy(card)
            return {"status": "SIMULATION_QUEUED_RETRY", "crm_ready": False}
        lead = self.leads.setdefault(inn, {"id": "SIM-LEAD-" + inn, "entity": "Lead",
            "inn": inn, "signals": {}, "score_version": card["score_version"],
            "score": card["score"], "provenance": [], "roles": ROLES,
            "consent": "PENDING", "stop_list": [], "tasks": []})
        lead["score"] = card["score"]
        lead["score_version"] = card["score_version"]
        for s in signals:
            if s["id"] not in lead["signals"]:
                lead["signals"][s["id"]] = deepcopy(s)
                task = {"id": "SIM-TASK-" + s["id"], "lead_id": lead["id"],
                        "signal_id": s["id"], "role": "manager", "action": card["next_action"]}
                lead["tasks"].append(task)
                lead["provenance"].append(s["source_url"])
                self.history.append({"event": "signal_linked", **task})
        self.pending.pop(inn, None)
        return {"status": "SIMULATED_LEAD_LINKED", "lead_id": lead["id"], "crm_ready": False}

    def retry(self, *, manager_reviewed=False, today=None):
        return [self.submit(card, manager_reviewed=manager_reviewed, today=today) for card in list(self.pending.values())]

    def confirm_interest(self, inn, *, confirmed=False):
        if confirmed is not True or inn not in self.leads or inn in self.stopped_inns:
            raise ValueError("Confirmed commercial interest and existing Lead required")
        self.deals.setdefault(inn, {"id": "SIM-DEAL-" + inn, "lead_id": self.leads[inn]["id"],
                                   "entity": "Deal", "commercial_interest": "SYNTHETIC_CONFIRMED"})
        return deepcopy(self.deals[inn])
