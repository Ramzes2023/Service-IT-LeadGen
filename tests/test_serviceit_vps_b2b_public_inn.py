"""B2B public INN writer is disabled by default and uses no LLM."""
from leadpilot import serviceit_vps_b2b_public_inn as module


def test_public_inn_writer_is_disabled_without_explicit_vps_flag():
    assert module.run_once({}, None, None) == {
        "status": "B2B_PUBLIC_INN_DISABLED", "writes": 0}


def test_public_inn_worker_source_has_no_model_or_outreach_invocation():
    from pathlib import Path
    src=(Path(__file__).resolve().parents[1] /
         "leadpilot/serviceit_vps_b2b_public_inn.py").read_text("utf8")
    assert 'NAMESPACE = "B2B_PUBLIC_INN_V2"' in src
    assert "postpone_public_research" in src
    assert "B2B_PUBLIC_INN_V1" not in src
    assert "source_findings" in src
    assert "source_backed_company_inn_update" in src
    assert 'fence.finalize(card, "UNCERTAIN")' in src
    assert 'fence.finalize(card, "VERIFIED")' in src
    assert 'Rest(' in src and '"crm.item.update"' in src
    for fragment in ("investigate_routerai", "config_from_env",
                     "tasks.task.add", "crm.deal.add", "send_email"):
        assert fragment not in src
