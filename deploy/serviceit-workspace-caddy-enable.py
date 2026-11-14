"""Add only the workspace route, validate and roll back on failure. Run as root."""
from pathlib import Path
import os,re,subprocess,shutil,time,urllib.request,json
FILE=Path('/etc/caddy/Caddyfile')
FRAGMENT='''    handle /apps/serviceit-workspace/* {
        request_body {
            max_size 64KB
        }
        reverse_proxy 127.0.0.1:18784
    }
'''
def render(old):
 if '/apps/serviceit-workspace/*' in old:
  assert FRAGMENT.strip() in old,'EXISTING_WORKSPACE_ROUTE_DIFFERS'
  return old
 matches=list(re.finditer(r'(?m)^leadgen\.serviseit\.ru\s*\{\s*$',old))
 assert len(matches)==1,'TARGET_SITE_NOT_UNIQUE'
 # Insert before every other route in the target site; preserve all original text.
 at=matches[0].end();return old[:at]+'\n'+FRAGMENT+old[at:]
def run(cmd):subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
def main():
 assert os.geteuid()==0,'ROOT_REQUIRED'
 with urllib.request.urlopen('http://127.0.0.1:18784/apps/serviceit-workspace/health',timeout=5) as r:assert json.load(r)['status']=='READY_FOR_BITRIX_INSTALLATION'
 assert FILE.is_file() and not FILE.is_symlink()
 old=FILE.read_text();new=render(old)
 if new==old:print('WORKSPACE_APP_ROUTE=ALREADY_CONFIGURED');return
 backup=FILE.with_name('Caddyfile.workspace-backup-'+str(time.time_ns()));shutil.copy2(FILE,backup);os.chmod(backup,0o600)
 try:
  FILE.write_text(new);run(['caddy','validate','--config',str(FILE),'--adapter','caddyfile']);run(['systemctl','reload','caddy'])
 except Exception:
  shutil.copy2(backup,FILE);run(['systemctl','reload','caddy']);raise
 print('WORKSPACE_APP_ROUTE=VERIFIED')
if __name__=='__main__':main()
