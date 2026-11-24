"""Offline regression for authenticated B2B titles received 24 Nov 2026."""
import json
import shutil
import subprocess
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "integrations/apps_script/Code.gs"

@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not available")
def test_real_nov24_title_candidates_without_external_effects():
    cases = json.loads(r'''[["RFI на аренду вычислительных мощностей (VPS) для обеспечения бесперебойной работы AI Gateway",["CLOUD"]],["ЗнЗ-25-2416 сервис подсказок адресов и геокодирования",["CLOUD"]],["Продление лицензии SIEM системы KUMA + AI модуль",["LICENSES"]],["Рассматриваются претенденты на выполнение работ по абонентскому техническому обслуживанию веб-сайтов ЖК «Героев»",["IT_SUPPORT"]],["Вакансия консультанта SIEM",[]],["Статья о VPS и геокодировании",[]],["Аренда помещения",[]],["Техническое обслуживание автомобилей",[]],["Закупка лицензий Microsoft Teams",["LICENSES"]],["СПС КонсультантПлюс: Москва Проф 5шт.",["LICENSES"]],["Выбор поставщика лицензий для системы объектного хранения данных S3",["LICENSES"]],["Процедура выбора компании на оказание услуг предоставления доступа к программному обеспечению для кадрового электронного документооборота (КЭДО) с функциями распознавания документов, подписания инвентаризационных ведомостей и мобильным приложением,",["KKT_EDO"]],["Услуги по технической поддержке информационной системы «Центр контроля генерации» (ИС ЦКГ).",["IT_SUPPORT"]],["Услуги по обеспечению актуальности встроенного П.О, драйверов и обновлений, а также экспертной поддержке при настройке оборудования СХД",["IT_SUPPORT"]],["Статья о системе объектного хранения данных S3",[]],["Вакансия инженера СХД",[]],["Выбор поставщика оборудования S3",[]],["Экспертная поддержка при настройке мебели",[]]]''')
    js = """
const fs = require('fs');
const vm = require('vm');
const ctx = {console};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
process.stdout.write(JSON.stringify(JSON.parse(process.argv[2]).map(t => ctx.classifyServices_(t))));
"""
    p = subprocess.run(["node", "-e", js, str(SCRIPT),
                        json.dumps([t for t, _ in cases], ensure_ascii=False)],
                       capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(p.stdout) == [expected for _, expected in cases]
