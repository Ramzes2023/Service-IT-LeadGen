"""Isolated host runner. Uses existing mounted credentials inside coordinator."""
from pathlib import Path
import re,subprocess,sys,base64
root=Path(__file__).resolve().parent
code='import os,sys,types\n'
if '--setup-encrypted' in sys.argv:
 encrypted=base64.b64decode((root/'serviceit-workspace-setup.enc.b64').read_bytes(),validate=False)
 decrypted=subprocess.run(['openssl','pkeyutl','-decrypt','-inkey',str(root.parent/'bidzaar/recipient.pem'),'-pkeyopt','rsa_padding_mode:oaep','-pkeyopt','rsa_oaep_md:sha256','-pkeyopt','rsa_mgf1_md:sha256'],input=encrypted,capture_output=True)
 if decrypted.returncode:raise SystemExit('SETUP_DECRYPT_FAILED')
 hook=decrypted.stdout.decode().strip()
 if not re.fullmatch(r'https://serviseit\.bitrix24\.ru/rest/1607/[A-Za-z0-9]+/?',hook):raise SystemExit('SETUP_HOOK_INVALID')
 code+='os.environ["SERVICEIT_WORKSPACE_SETUP_WEBHOOK"]='+repr(hook)+'\n'
 code+='os.environ["SERVICEIT_WORKSPACE_SETUP"]="YES"\n'
source=(root/'serviceit_postcall_assistant.py').read_text()
code+='m=types.ModuleType("leadpilot.serviceit_postcall_assistant");m.__package__="leadpilot"\n'
code+='sys.modules[m.__name__]=m\nexec('+repr(source)+',m.__dict__)\n'
source=(root/'serviceit_manager_workspace.py').read_text()
code+='scope={"__name__":"__main__","__package__":"leadpilot"}\nexec('+repr(source)+',scope)\n'
try:
 result=subprocess.run(['/usr/bin/docker','exec','-i','serviceit-leadgen-vps-coordinator-1','python','-B','-'],input=code,text=True,capture_output=True,timeout=260)
 for line in result.stdout.splitlines():
  if re.fullmatch(r'WORKSPACE_[A-Z_]+=[A-Z0-9_]+',line):print(line,flush=True)
 if result.returncode:
  print('WORKSPACE_PROCESS_EXIT='+str(result.returncode),flush=True)
  for line in result.stderr.splitlines():
   match=re.match(r'^([A-Za-z]+(?:Error|Exception)):',line)
   if match:print('WORKSPACE_BOOT_ERROR='+match[1].upper(),flush=True)
 raise SystemExit(result.returncode)
except subprocess.TimeoutExpired:
 print('WORKSPACE_STATUS=CYCLE_TIMEOUT',flush=True)
 raise SystemExit(2)
