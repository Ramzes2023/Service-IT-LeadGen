"""Offline Python and actual Apps Script classifier parity for real B2B notices.
No Gmail, VPS, CRM or receipt writes; no geography approval.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from leadpilot.b2b_email_opportunity_bridge import classify_services

CASES = json.loads(r'''[["Закупка лицензий Security Vision SOAR",["LICENSES"]],["Предоставление права использования программ для ЭВМ: Цифровая экосистема Eva Team, в т.ч. техническая поддержка по тарифу Расширенный на 1 год)",["LICENSES"]],["Продление лицензии Security Vision SOAR",["LICENSES"]],["Предоставление права использования программ для ЭВМ: Eva Team",["LICENSES"]],["Вакансия специалиста Security Vision SOAR",[]],["Статья о лицензиях Security Vision SOAR",[]],["Закупка лицензий Security Vision SOARX",[]],["Статья о цифровой экосистеме Eva Team",[]],["Предоставление права использования программ для ЭВМ: Eva TeamX",[]],["Закупка лицензий на алкоголь",[]]]''')
SCRIPT = Path(__file__).resolve().parents[1] / "integrations/apps_script/Code.gs"


@pytest.mark.parametrize(("title", "expected"), CASES)
def test_nov26_python_candidate_fit(title, expected):
    assert classify_services(title) == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_nov26_apps_script_candidate_fit():
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
