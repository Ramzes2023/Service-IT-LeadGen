"""Serve portal application and authenticated background reconciliation endpoints."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import threading
import automation
import finance_dashboard
ROOT=Path(__file__).parent
PREFIX='/apps/serviceit-workspace/'
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  try:n=int(self.headers.get('Content-Length','0'))
  except ValueError:return self.send_error(400)
  if not 0<=n<=65536:return self.send_error(413)
  raw=self.rfile.read(n)
  path=self.path.split('?',1)[0]
  if path.endswith('/connect') or path.endswith('/sync') or path.endswith('/dashboard-data'):
   if self.headers.get('Origin')!='https://leadgen.serviseit.ru':return self.send_error(403)
   if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send_error(415)
   try:
    data=json.loads(raw)
    if path.endswith('/connect'):result=automation.connect(data)
    elif path.endswith('/dashboard-data'):result=finance_dashboard.snapshot(data.get('access_token'))
    else:result=automation.sync_card(data.get('access_token'),data.get('card_id'))
    return self.send_json(result)
   except automation.Failure as e:return self.send_json({'error':str(e)},400)
   except Exception:return self.send_json({'error':'REQUEST_FAILED'},400)
  self.do_GET()
 def send_json(self,value,status=200):
  payload=json.dumps(value).encode()
  self.send_response(status)
  self.send_header('Content-Type','application/json')
  self.send_header('Cache-Control','no-store')
  self.send_header('Content-Length',str(len(payload)))
  self.end_headers();self.wfile.write(payload)
 def do_GET(self):
  path=self.path.split('?',1)[0]
  names={PREFIX:'index.html',PREFIX+'install':'index.html',PREFIX+'app.js':'app.js',PREFIX+'style.css':'style.css',PREFIX+'dashboard':'dashboard.html',PREFIX+'dashboard.js':'dashboard.js',PREFIX+'dashboard.css':'dashboard.css'}
  if path==PREFIX+'health':
   payload=json.dumps({'status':'READY_FOR_BITRIX_INSTALLATION','version':2,'background_worker':True}).encode();mime='application/json'
  elif path in names:
   name=names[path];payload=(ROOT/name).read_bytes();mime={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[name.split('.')[-1]]
  else:return self.send_error(404)
  self.send_response(200)
  for k,v in {'Content-Type':mime,'Content-Length':str(len(payload)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self' https://api.bitrix24.com https://api.bitrix24.tech; style-src 'self'; connect-src 'self' https://serviseit.bitrix24.ru https://api.bitrix24.com https://api.bitrix24.tech; frame-src 'self' https://serviseit.bitrix24.ru https://api.bitrix24.com https://api.bitrix24.tech; frame-ancestors https://serviseit.bitrix24.ru; base-uri 'none'; form-action 'none'"}.items():self.send_header(k,v)
  self.end_headers();self.wfile.write(payload)
if __name__=='__main__':
 threading.Thread(target=automation.worker,daemon=True).start()
 ThreadingHTTPServer(('0.0.0.0',8000),Handler).serve_forever()
