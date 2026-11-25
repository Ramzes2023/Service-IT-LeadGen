"""Offline parity on authenticated B2B notices, 25 Nov 2026.

No Gmail, VPS or CRM interaction. Generic mobile-development RFI stays outside
the approved category list pending business-profile confirmation.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from leadpilot.b2b_email_opportunity_bridge import classify_services

CASES = json.loads(r'''[["Закупка технической поддержки платформы виртуализации VMware + EMM Airwatch на 3 года",["IT_SUPPORT"]],["Поставка сертификатов технической поддержки VipNET Coordinator для сетей 6024 и 23477, обслуживание (обновление) сети VipNET 23477",["IT_SUPPORT"]],["Продление текущих и закупка новых лицензий Dr.Web для ГК МБ.",["LICENSES"]],["Запасные части и принадлежности для ПК",["SERVERS"]],["Закупка Лицензии CodeScoring On-premise OSA",["LICENSES"]],["Право заключения договора оказания информационных услуг с использованием Системы КонсультантПлюс",["LICENSES"]],["Выбор поставщика СХД IBM Storage FlashSystem 5600 или аналог",["SERVERS"]],["RFI (анализ рынка): Разработка мобильных iOS приложений и услуги предоставления доступа",[]],["Вакансия инженера VMware",[]],["Статья о CodeScoring и Dr.Web",[]],["Запасные части и принадлежности для автомобилей",[]],["Статья о FlashSystem",[]],["Лицензирование алкоголя",[]]]''')
SCRIPT = Path(__file__).resolve().parents[1] / "integrations/apps_script/Code.gs"


@pytest.mark.parametrize(("title", "expected"), CASES)
def test_nov25_python_candidate_fit(title, expected):
    assert classify_services(title) == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_nov25_apps_script_candidate_fit():
    js = """
const fs = require('fs');
const vm = require('vm');
const ctx = {console};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
process.stdout.write(JSON.stringify(JSON.parse(process.argv[2]).map(t => ctx.classifyServices_(t))));
"""
    p = subprocess.run(["node", "-e", js, str(SCRIPT),
                        json.dumps([t for t, _ in CASES], ensure_ascii=False)],
                       capture_output=True, text=True, timeout=15, check=True)
    assert json.loads(p.stdout) == [expected for _, expected in CASES]
