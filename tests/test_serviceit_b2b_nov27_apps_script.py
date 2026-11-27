"""Offline Python and actual Apps Script classifier parity for real B2B notices.
No Gmail, VPS, CRM or receipt writes; no geography approval.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from leadpilot.b2b_email_opportunity_bridge import classify_services

CASES = [["Лицензии WorksPad и Xello",["LICENSES"]],["Приобретение и продление ПО Visiology",["LICENSES"]],["  ЛИЦЕНЗИИ WORKSPAD И XELLO. ",["LICENSES"]],["Приобретение и продление ПО Visiology.",["LICENSES"]],["Вакансия WorksPad и Xello",[]],["Статья о лицензиях WorksPad и Xello",[]],["Лицензии WorksPad и XelloX",[]],["Приобретение и продление ПО VisiologyX",[]],["Новости Visiology",[]],["Лицензии на алкоголь",[]]]
SCRIPT = Path(__file__).resolve().parents[1] / "integrations/apps_script/Code.gs"


@pytest.mark.parametrize(("title", "expected"), CASES)
def test_nov27_python_candidate_fit(title, expected):
    assert classify_services(title) == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_nov27_apps_script_candidate_fit():
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
