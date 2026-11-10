"""Offline B2B -> source-backed public INN + AI citation write, never RouterAI."""
from leadpilot import serviceit_vps_b2b_public_inn as writer
from leadpilot import serviceit_vps_worker
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX


class Fence:
    active = []
    def __init__(self, connection, namespace):
        assert namespace == "B2B_PUBLIC_INN_V1"
        self.receipt = None
        self.status = []
        self.active.append(self)
    @staticmethod
    def validate_card(card):
        assert card == 70
    def reserve(self, card):
        assert card == 70
        return "RESERVED"
    def finalize(self, card, status):
        self.status.append(status)
        return True


class Rest:
    def __init__(self, webhook, client):
        assert webhook == "TEST_WEBHOOK"
        self.before = {k: "Существующие подтверждённые сведения" for k in AI_FIELDS}
        self.before[PREFIX + "COMPANY_INN"] = ""
        self.before[PREFIX + "SIGNAL_ID"] = "B2B-CENTER-4673595"
        self.writes = []
        self.after = dict(self.before)
    def __call__(self, method, args):
        if method == "crm.item.get":
            return {"item": dict(self.after)}
        if method == "crm.item.update":
            assert set(args["fields"]) == {
                PREFIX + "COMPANY_INN", PREFIX + "AI_MANAGER_BRIEF",
                PREFIX + "AI_FULL_REPORT"}
            self.writes.append(dict(args["fields"]))
            self.after.update(args["fields"])
            return {"item": {}}
        raise AssertionError("unexpected REST " + method)


def sources():
    return {
        "status": "PUBLIC_PAGES_INSPECTED",
        "pages": [{"url": "https://tendem.ru/tenders/16548471",
                   "excerpt": 'Заказчик Общество с ограниченной ответственностью "ГЛОБУС-ИТ"'}],
        "independent_company_research": {
            "public_inn": {
                "inn": "5260365605", "buyer_name": "ООО «ГЛОБУС-ИТ»",
                "public_source": "https://globus-ltd.ru/contacts",
                "provenance": "PUBLIC_CORPORATE_SELF_DECLARATION",
                "fns_verified": False,
            },
            "contacts": [{"type": "email", "value": "contact@globus-ltd.com",
                          "url": "https://globus-ltd.ru/contacts"}],
        },
    }


def prepare(monkeypatch, found):
    Fence.active = []
    instance = Rest("TEST_WEBHOOK", None)
    monkeypatch.setattr(writer, "Rest", lambda *a: instance)
    monkeypatch.setattr(writer, "PostgresNativeFence", Fence)
    monkeypatch.setattr(writer, "secret_file", lambda *a: "TEST_WEBHOOK")
    monkeypatch.setattr(writer, "validate_identity", lambda *a: None)
    monkeypatch.setattr(writer, "fingerprint", lambda *a: "same")
    monkeypatch.setattr(writer, "safe_notice", lambda *a: None)
    monkeypatch.setattr(writer, "select_one", lambda *a:
                        (70, "B2B-CENTER-4673595", {"title": "synthetic"}))
    monkeypatch.setattr(writer, "source_findings", lambda *a: found)
    monkeypatch.setattr(serviceit_vps_worker, "live_permitted", lambda *a: True)
    return instance


def flags():
    return {
        writer.ENABLED: "YES",
        "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED": "YES",
        "SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED": "YES",
        "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
    }


def test_exact_three_crm_fields_with_public_evidence_and_one_receipt(monkeypatch):
    instance = prepare(monkeypatch, sources())
    result = writer.run_once(flags(), None, None)
    assert result["status"] == "B2B_PUBLIC_INN_READBACK_VERIFIED"
    assert result["writes"] == 1
    assert result["customer_contacts"] == 0
    assert result["egrul_verified"] is False
    assert len(instance.writes) == 1
    assert instance.after[PREFIX + "COMPANY_INN"] == "5260365605"
    assert "contact@globus-ltd.com" in instance.after[PREFIX + "AI_FULL_REPORT"]
    assert Fence.active[0].status == ["UNCERTAIN", "VERIFIED"]
    assert Fence.active[0].receipt["expected"] == instance.writes[0]


def test_no_independent_company_proof_is_hold_without_crm_writes(monkeypatch):
    data = sources()
    data["independent_company_research"].pop("public_inn")
    instance = prepare(monkeypatch, data)
    result = writer.run_once(flags(), None, None)
    assert result["status"] == "B2B_PUBLIC_INN_NOT_CORROBORATED"
    assert result["writes"] == 0
    assert not instance.writes
    assert Fence.active[0].status == ["HOLD"]
