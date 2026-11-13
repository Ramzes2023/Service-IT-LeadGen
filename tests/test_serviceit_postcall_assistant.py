import pytest
from leadpilot.serviceit_postcall_assistant import validated_draft,draft

def test_interest_requires_literal_evidence():
    notes='Клиент просит прислать предложение завтра.'
    raw={'summary':'Попросил предложение','interest':'YES','interest_quote':'просит прислать предложение','next_action':'Подготовить предложение для проверки менеджером','quotes':[notes]}
    assert validated_draft(raw,notes)['interest']=='YES'
    with pytest.raises(ValueError):validated_draft({**raw,'interest_quote':'договор уже подписан'},notes)
    with pytest.raises(ValueError):validated_draft({**raw,'quotes':['Несуществующая цитата']},notes)

def test_no_model_fallback_never_invents_interest():
    result=draft(None,{},'Собеседник попросил перезвонить позже.')
    assert result['interest']=='UNKNOWN'
    assert 'позже' in result['summary']


def test_contextual_manager_task_contains_dossier():
    from datetime import datetime,timezone
    from leadpilot.serviceit_stage3_all_new_task_automation import manager_task_fields
    from leadpilot.serviceit_enrichment_worker import PREFIX
    brief='Компания, сигнал, контакт и сценарий. '*8
    fields=manager_task_fields({PREFIX+'AI_MANAGER_BRIEF':brief,PREFIX+'AI_SUMMARY':'Поставка серверов'},33,605,'T426_33',now=datetime.now(timezone.utc))
    assert brief in fields['DESCRIPTION']
    assert fields['RESPONSIBLE_ID']==605
    assert 'GigaChat' not in fields['DESCRIPTION']
