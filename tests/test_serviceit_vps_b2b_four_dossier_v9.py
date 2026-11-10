"""Four real-card V9 dossier contracts: source provenance, no fabrication, no replay."""
from pathlib import Path

from ops import serviceit_vps_b2b_four_dossier_v9 as v9
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX


def base():
    row={k: "Ранее проведённая проверка" for k in AI_FIELDS}
    row[PREFIX+"AI_STATUS"]="PUBLIC_RESEARCH_ROUTERAI_CITED"
    row[PREFIX+"AI_FULL_REPORT"]=(
        "Предыдущий отчёт. Дословно на странице: «Подтверждённый фрагмент» "
        "Публичный источник: https://www.b2b-center.ru/example")
    row[PREFIX+"AI_QUOTE"]="Подтверждённый фрагмент"
    return row


def notice():
    return {
        "tender_id":"4673595", "source":"B2B-Center",
        "source_url":"https://www.b2b-center.ru/market/real-notice/tender-4673595/",
        "subject":"Оказание услуг по предоставлению ИТ-специалистов, включая 1С, DevOps и ИБ",
        "organizer_as_named_in_email":"ООО «ГЛОБУС-ИТ»",
        "email_received_at":"2026-11-10T14:00:00+03:00",
        "deadline_as_stated_in_email":"23.11.2026 17:00 МСК",
        "source_service_groups":["IT_STAFFING","1C"],
    }


def test_four_fields_no_public_pages_preserves_prior_citations():
    source={"status":"PUBLIC_TENDER_ID_NOT_FOUND_IN_READABLE_PAGES","pages":[]}
    fields,pages,facts,buyer=v9.compose_dossier(
        base(),notice(),"B2B-CENTER-4673595",notice()["subject"],source,
        {"facts":[]})
    assert set(fields)==AI_FIELDS
    assert pages==facts==0 and buyer is False
    assert "23.11.2026 17:00 МСК" in fields[v9.REPORT]
    assert "ООО «ГЛОБУС-ИТ»" in fields[v9.REPORT]
    assert "не установлен" in fields[v9.REPORT]
    assert "Junior–Lead" in fields[v9.REPORT]
    assert "Предыдущий отчёт. Дословно" in fields[v9.REPORT]
    assert "ГЛУБОКОЕ ИССЛЕДОВАНИЕ" not in fields[v9.REPORT]
    assert fields[v9.STATUS] == base()[v9.STATUS]
    assert "объём" in fields[PREFIX+"AI_RECOMMENDATION"].casefold() or "часов" in fields[PREFIX+"AI_RECOMMENDATION"]
    assert "\n" in fields[v9.BRIEF]
    assert all(type(v) is str and 0<len(v)<= (
        12000 if k==v9.REPORT else 4096) for k,v in fields.items())


def test_fake_quotes_cannot_enter_real_dossier():
    s={"status":"PUBLIC_PAGES_INSPECTED","pages":[{
        "url":"https://tendem.ru/tenders/16548471",
        "excerpt":"Оказание услуг по предоставлению ИТ-специалистов, "
                  "закупка №4673595. Заказчик ООО «ГЛОБУС-ИТ». "
                  "Подача заявок до 23.11.2026 17:00 МСК."
    }]}
    facts={"facts":[{"url":"https://tendem.ru/tenders/16548471",
                     "quote":"This assertion does not exist in the public page",
                     "category":"BUDGET"}]}
    result,pages,quotes,buyer=v9.compose_dossier(
        base(),notice(),"B2B-CENTER-4673595",notice()["subject"],s,facts)
    assert pages==1 and quotes==0
    assert "This assertion does not exist" not in result[v9.REPORT]
    assert buyer is True
    assert "ООО «ГЛОБУС-ИТ»" in result[v9.REPORT]


def test_known_public_quote_requires_url_and_exact_substring():
    from leadpilot.serviceit_b2b_routerai_deep import source_only_investigation
    text=("Закупка №4673595. Предоставление ИТ-специалистов. "
          "Заказчик ООО «ГЛОБУС-ИТ». Срок подачи заявок до 23.11.2026.")
    s={"status":"PUBLIC_PAGES_INSPECTED",
       "pages":[{"url":"https://tendem.ru/tenders/16548471","excerpt":text}]}
    fields,pages,facts,buyer=v9.compose_dossier(
        base(),notice(),"B2B-CENTER-4673595",notice()["subject"],s,
        source_only_investigation(s))
    assert pages==1 and buyer
    assert "https://tendem.ru/tenders/16548471" in fields[v9.REPORT]


def test_no_contact_or_fns_or_task_mutation_in_one_shot_script():
    s=Path("ops/serviceit_vps_b2b_four_dossier_v9.py").read_text("utf-8")
    w=Path(".github/workflows/serviceit-vps-b2b-four-dossiers-v9.yml").read_text("utf-8")
    assert "source_findings(item,notice,env,client)" in s
    assert "crm.item.update" in s and "crm.item.get" in s
    assert "crm.item.add" not in s
    assert "tasks.task.add" not in s
    assert "B2B_DOSSIER_V9" in s
    assert "ON CONFLICT(card_id) DO NOTHING" in s
    assert "WRITE_INTENT" in s and "UNCERTAIN" in s
    assert "source_only_investigation" in s
    assert "secrets.compare_digest" in s
    assert "SERVICEIT_B2B_FOUR_DOSSIER_V9_APPLY_368" in w
    assert "github.actor == 'Ramzes2023'" in w
    assert "github.event.issue.number == 368" in w
    assert "persist-credentials: false" in w
    assert chr(92) + "${{" not in w
    assert "VPS_SSH_KEY: \"${{ secrets.SERVICEIT_VPS_DEPLOY_SSH_KEY }}\"" in w
    assert "docker exec -i serviceit-leadgen-vps-coordinator-1" in w
