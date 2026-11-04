"""Read-only supervisor dashboard never equates opportunity with paid revenue."""
from leadpilot.serviceit_supervisor_funnel_readonly import calculate

def test_missing_commercial_evidence_is_not_reported_as_zero(monkeypatch):
    import leadpilot.serviceit_supervisor_funnel_readonly as m
    monkeypatch.setattr(m,"validate_webhook",lambda value:
                        "https://serviseit.bitrix24.ru/rest/1615/fake")
    monkeypatch.setattr(m,"calibrate_known_card",lambda fn:None)
    monkeypatch.setattr(m,"discover",lambda fn,start,now:[10,11])
    monkeypatch.setattr(m,"probe",lambda client,hook:{
        "separate_sales_funnel_visible":False,
        "manager_fields_ready":True})
    from datetime import datetime, timezone
    r=calculate(None,{"SERVICEIT_BITRIX_WEBHOOK":"redacted"},now=datetime.now(timezone.utc))
    assert r["found_visible_new_signals"]==2
    assert r["actual_contacts"] is None
    assert r["contracts"] is None
    assert r["paid_revenue"] is None
    assert r["mrr"] is None
    assert r["crm_writes"]==0
