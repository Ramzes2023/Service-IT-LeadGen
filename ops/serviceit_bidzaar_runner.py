"""Host runner; keeps supplier configuration out of argv, Git and logs."""
import json
import os
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parent
feed=root/'feed-url'
if not feed.is_file() or feed.is_symlink() or feed.stat().st_mode & 0o077:
    raise SystemExit('BIDZAAR_CONFIGURATION_NOT_PRIVATE')
url='' if '--smoke' in sys.argv else feed.read_text().strip()
worker=(root/'serviceit_bidzaar_worker.py').read_text()
code='import os,sys,types\n'
code+='os.environ["SERVICEIT_BIDZAAR_FEED_URL"]='+repr(url)+'\n'
code+='os.environ["SERVICEIT_BIDZAAR_WRITE_ENABLED"]="YES"\n'
code+='os.environ["SERVICEIT_BIDZAAR_EXTENDED_DOCUMENTS_OWNER"]="YES"\n'
for name in ('b2b_email_opportunity_bridge','serviceit_b2b_moscow_geo','serviceit_bidzaar_source','serviceit_stage3_all_new_task_automation','serviceit_postcall_assistant'):
    source=(root/(name+'.py')).read_text()
    code+='m=types.ModuleType('+repr('leadpilot.'+name)+');m.__package__="leadpilot"\n'
    code+='sys.modules[m.__name__]=m\nexec('+repr(source)+',m.__dict__)\n'
code+='scope={"__name__":"__main__","__package__":"leadpilot"}\nexec('+repr(worker)+',scope)\n'
try:
    result=subprocess.run(['/usr/bin/docker','exec','-i','serviceit-leadgen-vps-coordinator-1',
                           'python','-B','-'],input=code,text=True,capture_output=True,timeout=260)
    # Only status flags. Never emit input, URLs, credentials or vendor content.
    import re
    for line in result.stdout.splitlines():
        if re.fullmatch(r'BIDZAAR_[A-Z_]+=[A-Z0-9_]+',line):print(line,flush=True)
    if result.returncode:
        for line in result.stderr.splitlines():
            match=re.match(r'^([A-Za-z]+(?:Error|Exception)):',line)
            if match:print('BIDZAAR_BOOT_ERROR='+match[1].upper(),flush=True)
            missing=re.search(r"No module named '(leadpilot\.[a-z0-9_]+)'",line)
            if missing:print('BIDZAAR_MISSING_MODULE='+missing[1].replace('.','_').upper(),flush=True)
            imp=re.search(r"cannot import name '([a-zA-Z0-9_]+)'",line)
            if imp:print('BIDZAAR_MISSING_SYMBOL='+imp[1].upper(),flush=True)
        print('BIDZAAR_PROCESS_EXIT='+str(result.returncode),flush=True)
    raise SystemExit(result.returncode)
except subprocess.TimeoutExpired:
    print('BIDZAAR_STATUS=CYCLE_TIMEOUT',flush=True)
    raise SystemExit(2)
