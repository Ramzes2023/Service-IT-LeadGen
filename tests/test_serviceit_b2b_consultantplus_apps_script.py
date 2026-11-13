"""Regression: real B2B-Center ConsultantPlus notices were dropped by Gmail classification.

This invokes the committed Apps Script classifier with Node without touching Gmail,
Script Properties, the VPS, CRM, or any real tender delivery state.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "integrations/apps_script/Code.gs"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not available")
def test_consultantplus_it_procurements_are_classified_as_existing_licenses():
    titles = [
        'Оказание услуг по адаптации и сопровождению экземпляров Систем "КонсультантПлюс" на основе специального лицензионного сервисного программного обеспечения для ООО «СТАРСТРОЙ» и регионов (согласно ТЗ)',
        'RFP_ поставка ИПС «Консультант Плюс».',
        "Закупка лицензий Microsoft Teams",
        "Консультант Плюс (справочно-правовые базы данных)",
        "Сопровождение консультантов по вопросам персонала",
        "Статья о КонсультантПлюс и трудовом законодательстве",
        "Поставка канцелярских принадлежностей",
    ]
    js = r"""
const fs = require('fs');
const vm = require('vm');
const script = fs.readFileSync(process.argv[1], 'utf8');
const titles = JSON.parse(process.argv[2]);
const context = {console};
vm.runInNewContext(script, context, {filename: 'Code.gs'});
process.stdout.write(JSON.stringify(titles.map(title => context.classifyServices_(title))));
"""
    completed = subprocess.run(
        ["node", "-e", js, str(SCRIPT), json.dumps(titles, ensure_ascii=False)],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    classifications = json.loads(completed.stdout)
    assert "LICENSES" in classifications[0], "4674919 missed by Gmail"
    assert "LICENSES" in classifications[1], "4680694 missed by Gmail"
    assert "LICENSES" in classifications[2], "existing Teams support regressed"
    assert "LICENSES" in classifications[3], "4681383 bare catalogue title missed by Gmail"
    assert all(not row for row in classifications[4:]), "unrelated texts matched"


def test_once_per_revision_recovery_preserves_source_receipts_and_review():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'const revision = "B2B_CLASSIFIER_V6"' in source
    assert "recoverPostCutoverMissedV6_(ids)" in source
    assert '["NO_MATCH", "DONE"].indexOf(props.getProperty(key)) < 0' in source
    assert '!props.getProperty("SERVICEIT_SOURCE_" + row.notice.tender_id)' in source
    assert 'props.setProperty(key, "PENDING")' in source
    assert 'SERVICEIT_EMAIL_' in source
