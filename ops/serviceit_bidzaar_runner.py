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
url=feed.read_text().strip()
source=(root/'serviceit_bidzaar_source.py').read_text()
worker=(root/'serviceit_bidzaar_worker.py').read_text()
code='import os,sys,types\n'
code+='os.environ["SERVICEIT_BIDZAAR_FEED_URL"]='+repr(url)+'\n'
code+='os.environ["SERVICEIT_BIDZAAR_WRITE_ENABLED"]="YES"\n'
code+='m=types.ModuleType("leadpilot.serviceit_bidzaar_source");m.__package__="leadpilot"\n'
code+='sys.modules[m.__name__]=m\nexec('+repr(source)+',m.__dict__)\n'
code+='scope={"__name__":"__main__","__package__":"leadpilot"}\nexec('+repr(worker)+',scope)\n'
try:
    result=subprocess.run(['/usr/bin/docker','exec','-i','serviceit-leadgen-vps-coordinator-1',
                           'python','-B','-'],input=code,text=True,capture_output=True,timeout=260)
    # Only status flags. Never emit input, URLs, credentials or vendor content.
    import re
    for line in result.stdout.splitlines():
        if re.fullmatch(r'BIDZAAR_[A-Z_]+=[A-Z0-9_]+',line):print(line,flush=True)
    raise SystemExit(result.returncode)
except subprocess.TimeoutExpired:
    print('BIDZAAR_STATUS=CYCLE_TIMEOUT',flush=True)
    raise SystemExit(2)
