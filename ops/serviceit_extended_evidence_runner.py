"""Host-side isolated document converter and public contact worker."""
import json,re,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent
names=('serviceit-leadgen-vps-coordinator-1','serviceit-b2b-vps-writer-1')
settings=json.loads(subprocess.run(['/usr/bin/docker','inspect',*names],capture_output=True,text=True,check=True).stdout)
env=dict(s.split('=',1) for s in settings[0]['Config']['Env'])
other=dict(s.split('=',1) for s in settings[1]['Config']['Env'])
for key in ('SERVICEIT_TINYFISH_API_KEY_FILE','SERVICEIT_TAVILY_SEARCH_API_KEY_FILE'):
    if key in other:env[key]=other[key]
code='import os,sys,types\nos.environ.update('+repr(env)+')\n'
# Current reviewed dependencies, independent of the coordinator image's age.
for name in ('serviceit_b2b_tender_documents','serviceit_company_contacts','serviceit_extended_documents'):
    source=(root/(name+'.py')).read_text()
    code+='m=types.ModuleType('+repr('leadpilot.'+name)+');m.__package__="leadpilot";sys.modules[m.__name__]=m\nexec('+repr(source)+',m.__dict__)\n'
if '--audit' in sys.argv:worker=(root/'serviceit_extended_evidence_audit.py').read_text()
else:worker=(root/'serviceit_extended_evidence_worker.py').read_text()
code+='exec('+repr(worker)+',{"__name__":"__main__","__package__":"leadpilot"})\n'
image=(root/'image').read_text().strip()
if not re.fullmatch(r'serviceit-evidence:[0-9]{7,16}',image):raise SystemExit('EXT_IMAGE_INVALID')
args=['/usr/bin/docker','run','--rm','-i','--network','host','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128','--memory','1g','--tmpfs','/tmp:rw,noexec,nosuid,size=128m','--volumes-from',names[0]+':ro']
# Mount only missing provider secret files, not the other worker's data volumes.
mounted={m['Destination'] for m in settings[0]['Mounts']}
for m in settings[1]['Mounts']:
    if m['Destination'] in [env.get(k) for k in ('SERVICEIT_TINYFISH_API_KEY_FILE','SERVICEIT_TAVILY_SEARCH_API_KEY_FILE')] and m['Destination'] not in mounted:
        if m['Type']!='bind' or not m['Destination'].startswith('/run/secrets/'):raise SystemExit('EXT_SECRET_MOUNT_INVALID')
        args+=['--mount','type=bind,src='+m['Source']+',dst='+m['Destination']+',readonly']
args +=[image,'python','-B','-']
try:
    result=subprocess.run(args,input=code,text=True,capture_output=True,timeout=540)
    for line in result.stdout.splitlines():
        if re.fullmatch(r'EXT_[A-Z0-9_]+=[A-Z0-9_]+',line):print(line,flush=True)
    if result.returncode:
        for line in result.stderr.splitlines():
            match=re.match(r'^([A-Za-z]+(?:Error|Exception)):',line)
            if match:print('EXT_BOOT_ERROR='+match[1].upper(),flush=True)
        print('EXT_EXIT='+str(result.returncode),flush=True)
    raise SystemExit(result.returncode)
except subprocess.TimeoutExpired:
    print('EXT_STATUS=CYCLE_TIMEOUT',flush=True);raise SystemExit(2)
