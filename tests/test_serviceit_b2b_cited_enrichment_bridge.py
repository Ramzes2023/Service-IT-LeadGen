"""Cited B2B research MUST retain independently sourced contacts in the CRM report."""
from leadpilot import serviceit_vps_b2b_deep_research as runner
from leadpilot import serviceit_b2b_routerai_deep as deep
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX


def base_item():
    item = {key: "Existing verified nine-field data" for key in AI_FIELDS}
    item[PREFIX + "SIGNAL_KIND"] = "tender"
    return item


def verified_findings(*, contacts=True):
    data = {
        "status": "PUBLIC_PAGES_INSPECTED",
        "checked_at": "2026-11-10T12:00:00+00:00",
        "pages": [{
            "url": "https://b2b-center.ru/market/synthetic-test",
            "excerpt": "Учебная закупка ИТ-поддержки и DevOps.",
        }],
        "contacts": ([{
            "type": "email", "value": "info@example.org",
            "role": "публичный корпоративный email",
            "url": "https://example.org/contacts",
        }] if contacts else []),
        "independent_company_research": {
            "status": "INDEX_SEARCH_DONE",
            "checked_at": "2026-11-10T12:00:00+00:00",
            "search_backend": "BRAVE_WEB_INDEX",
            "pages": [{
                "url": "https://example.org/about",
                "excerpt": "Учебная организация опубликовала сведения об ИТ.",
            }],
            "contacts": [{
                "type": "email", "value": "tender@example.org",
                "role": "публичный корпоративный email",
                "url": "https://example.org/contacts",
            }] if contacts else [],
            "hypotheses": [],
        },
    }
    return data


def test_cited_model_path_preserves_verified_contact_and_public_page():
    findings = verified_findings()
    research = deep.source_only_investigation(findings)
    fields = runner.cited_manager_fields(base_item(), findings, research)
    assert set(fields) == AI_FIELDS
    report = fields[PREFIX + "AI_FULL_REPORT"]
    assert "info@example.org" in report
    assert "tender@example.org" in report
    assert "https://example.org/about" in report
    assert "ПУБЛИЧНО ОПУБЛИКОВАННЫЕ КОНТАКТЫ" in report
    assert "провер" in fields[PREFIX + "AI_NEXT_ACTION"].lower()
    assert len(fields[PREFIX + "AI_MANAGER_BRIEF"]) <= 4096
    assert len(report) <= 12000
    assert "HOLD" in report


def test_no_verified_contacts_means_no_invented_addresses():
    findings = verified_findings(contacts=False)
    research = deep.source_only_investigation(findings)
    fields = runner.cited_manager_fields(base_item(), findings, research)
    assert "info@example.org" not in fields[PREFIX + "AI_FULL_REPORT"]
    assert "tender@example.org" not in fields[PREFIX + "AI_FULL_REPORT"]


def test_appended_public_intelligence_still_respects_bitrix_limits():
    findings = verified_findings()
    findings["independent_company_research"]["pages"][0]["excerpt"] = "Данные " * 2500
    research = deep.source_only_investigation(findings)
    fields = runner.cited_manager_fields(base_item(), findings, research)
    assert set(fields) == AI_FIELDS
    assert all(0 < len(v) <= (12000 if k == PREFIX + "AI_FULL_REPORT" else 4096)
               for k, v in fields.items())
