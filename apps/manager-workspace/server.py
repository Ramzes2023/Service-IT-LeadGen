"""Serve Bitrix application shell; never persist or log incoming OAuth values."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
ROOT=Path(__file__).parent
PREFIX='/apps/serviceit-workspace/'
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  try:n=int(self.headers.get('Content-Length','0'))
  except ValueError:return self.send_error(400)
  if not 0<=n<=65536:return self.send_error(413)
  self.rfile.read(n)  # SDK gets its own session context. Never reflect auth data.
  self.do_GET()
 def do_GET(self):
  path=self.path.split('?',1)[0]
  names={PREFIX:'index.html',PREFIX+'install':'index.html',PREFIX+'app.js':'app.js',PREFIX+'style.css':'style.css'}
  if path==PREFIX+'health':
   payload=json.dumps({'status':'READY_FOR_BITRIX_INSTALLATION','version':1,'stores_credentials':False}).encode();mime='application/json'
  elif path in names:
   name=names[path];payload=(ROOT/name).read_bytes();mime={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[name.split('.')[-1]]
  else:return self.send_error(404)
  self.send_response(200)
  for k,v in {'Content-Type':mime,'Content-Length':str(len(payload)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self' https://api.bitrix24.com https://api.bitrix24.tech; style-src 'self'; connect-src 'self' https://serviseit.bitrix24.ru; frame-ancestors https://serviseit.bitrix24.ru; base-uri 'none'; form-action 'none'"}.items():self.send_header(k,v)
  self.end_headers();self.wfile.write(payload)
if __name__=='__main__':ThreadingHTTPServer(('0.0.0.0',8000),Handler).serve_forever()
