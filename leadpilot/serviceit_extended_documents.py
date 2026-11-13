"""Durable all-file queue. Batch size is throughput, never a document cap."""
import hashlib,json,os,re,resource,shutil,subprocess,tempfile
from pathlib import PurePosixPath
from urllib.parse import urlsplit,urljoin,unquote
import httpx
from .serviceit_b2b_tender_documents import extract_text
from .serviceit_vps_b2b_document_worker import _model_research

DDL='''CREATE TABLE IF NOT EXISTS serviceit_extended_documents (
 card_id BIGINT NOT NULL,url TEXT NOT NULL,name TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'PENDING',attempts INTEGER NOT NULL DEFAULT 0,
 text_content TEXT,sha256 TEXT,reports JSONB,error TEXT,analyzed_chars INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(card_id,url));'''


def _limits():
    resource.setrlimit(resource.RLIMIT_CPU,(10,10))
    resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
    resource.setrlimit(resource.RLIMIT_FSIZE,(16*1024*1024,16*1024*1024))


def legacy_doc(data):
    if data.startswith(b'PK\x03\x04'):return extract_text(data,'.docx')
    if data.lstrip().startswith(b'{\\rtf'):
        from striprtf.striprtf import rtf_to_text
        return rtf_to_text(data.decode('cp1251'),encoding='cp1251')
    if not data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        raise ValueError('DOC_BINARY_SIGNATURE_INVALID')
    binary=shutil.which('antiword')
    if not binary:raise ValueError('DOC_CONVERTER_UNAVAILABLE')
    # No shell, macros, network, or privileged office application.
    with tempfile.TemporaryDirectory(prefix='serviceit-doc-') as folder:
        path=os.path.join(folder,'source.doc')
        with open(path,'wb') as output:output.write(data)
        result=subprocess.run([binary,'-m','UTF-8.txt','-w','0',path],
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,preexec_fn=_limits)
    if result.returncode or len(result.stdout)>16000000:raise ValueError('DOC_CONVERSION_FAILED')
    text=result.stdout.decode('utf-8','replace').strip()
    if not text:raise ValueError('DOC_EMPTY_TEXT')
    return text


def download(client,url,name):
    p=urlsplit(url)
    if p.scheme!='https' or p.netloc!='bidzaar.com' or p.query or p.fragment or not re.fullmatch(r'/api/filestorage/files/download/[0-9a-fA-F-]{36}',p.path):
        raise ValueError('DOCUMENT_URL_INVALID')
    kind=PurePosixPath(name).suffix.lower()
    if kind not in ('.doc','.docx','.pdf','.xlsx','.txt','.zip'):raise ValueError('DOCUMENT_FORMAT_UNSUPPORTED')
    current=url
    for redirect in range(3):
        with client.stream('GET',current,timeout=20,follow_redirects=False) as response:
            if response.status_code in (301,302,303,307,308):
                target=urljoin(current,response.headers.get('location',''));dest=urlsplit(target)
                suffix=dest.path.removeprefix(p.path+'/')
                if redirect==2 or dest.scheme!='https' or dest.netloc!=p.netloc or dest.query or dest.fragment or not dest.path.startswith(p.path+'/') or '/' in unquote(suffix) or '\\' in unquote(suffix) or unquote(suffix) in ('','.','..'):
                    raise ValueError('DOCUMENT_REDIRECT_REJECTED')
                current=target;continue
            response.raise_for_status();data=bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data)>4000000:raise ValueError('DOCUMENT_TOO_LARGE')
            raw=bytes(data)
            from . import serviceit_b2b_tender_documents as parser
            previous=parser.MAX_TEXT,parser.MAX_PDF_PAGES,parser.MAX_FILES
            try:
                parser.MAX_TEXT=1000000
                parser.MAX_PDF_PAGES=200
                parser.MAX_FILES=120
                if kind=='.zip':
                    parts=[]
                    with parser._safe_archive(raw) as archive:
                        for member in archive.infolist():
                            if member.is_dir():continue
                            suffix=PurePosixPath(member.filename).suffix.lower()
                            if suffix not in ('.doc','.docx','.pdf','.xlsx','.txt'):
                                raise ValueError('ARCHIVE_CONTAINS_UNSUPPORTED_DOCUMENT')
                            payload=archive.read(member)
                            parts.append(member.filename+'\n'+(legacy_doc(payload) if suffix=='.doc' else extract_text(payload,suffix)))
                    text='\n'.join(parts)
                else:text=legacy_doc(raw) if kind=='.doc' else extract_text(raw,kind)
            finally:
                parser.MAX_TEXT,parser.MAX_PDF_PAGES,parser.MAX_FILES=previous
            if len(text)>=1000000:raise ValueError('DOCUMENT_TEXT_TOO_LARGE')
            if not text.strip():raise ValueError('DOCUMENT_EMPTY_TEXT')
            return {'url':url,'text':text,'kind':kind,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    raise ValueError('DOCUMENT_REDIRECT_REJECTED')


def register(conn,card,files):
    if not isinstance(files,list):return
    for file in files:
        if not isinstance(file,dict) or not isinstance(file.get('url'),str) or not isinstance(file.get('name'),str):continue
        conn.execute('INSERT INTO serviceit_extended_documents(card_id,url,name) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(card,file['url'],file['name']))


def process(conn,client,env,*,batch=6):
    rows=conn.execute("SELECT card_id,url,name FROM serviceit_extended_documents WHERE status IN ('PENDING','RETRY') AND attempts<3 AND next_attempt_at<=now() ORDER BY attempts,card_id,url LIMIT %s",(batch,)).fetchall()
    done=[]
    for card,url,name in rows:
        conn.execute("UPDATE serviceit_extended_documents SET attempts=attempts+1,status='RETRY',next_attempt_at=now()+interval '10 minutes' WHERE card_id=%s AND url=%s",(card,url))
        try:
            document=download(client,url,name)
            conn.execute("UPDATE serviceit_extended_documents SET status='EXTRACTED',text_content=%s,sha256=%s,error=NULL,updated_at=now() WHERE card_id=%s AND url=%s",(document['text'],document['sha256'],card,url))
            done.append((card,document))
        except Exception as exc:
            reason=str(exc) if re.fullmatch(r'[A-Z][A-Z0-9_]{3,100}',str(exc)) else type(exc).__name__.upper()
            conn.execute("UPDATE serviceit_extended_documents SET error=%s,status=CASE WHEN attempts>=3 THEN 'FAILED' ELSE 'RETRY' END,updated_at=now() WHERE card_id=%s AND url=%s",(reason,card,url))
    # Long documents are analyzed in durable windows too, not cut at 24k.
    chunks=conn.execute("SELECT card_id,url,text_content,analyzed_chars FROM serviceit_extended_documents WHERE status='EXTRACTED' ORDER BY updated_at,card_id,url LIMIT %s",(batch,)).fetchall()
    for start in range(0,len(chunks),3):
        group=chunks[start:start+3]
        inputs=[{'url':url,'text':text[offset:offset+24000]} for card,url,text,offset in group]
        reports=_model_research(client,env,inputs)
        for card,url,text,offset in group:
            window=text[offset:offset+24000];own=[]
            for report in reports:
                facts=[f for f in report.get('facts',[]) if f.get('url')==url and isinstance(f.get('quote'),str) and ' '.join(f['quote'].split()) in ' '.join(window.split())]
                if facts:own.append({'provider':report.get('provider','SOURCE'),'facts':facts})
            conn.execute("UPDATE serviceit_extended_documents SET status=%s,analyzed_chars=%s,reports=COALESCE(reports,'[]'::jsonb)||%s::jsonb,updated_at=now() WHERE card_id=%s AND url=%s",('READ' if offset+len(window)>=len(text) else 'EXTRACTED',offset+len(window),json.dumps(own,ensure_ascii=False),card,url))
    return len(chunks)


def render(conn,card):
    rows=conn.execute('SELECT name,url,status,reports,error FROM serviceit_extended_documents WHERE card_id=%s ORDER BY url',(card,)).fetchall()
    if not rows:return '',0,0,0
    read=sum(row[2]=='READ' for row in rows);pending=sum(row[2] in ('PENDING','RETRY','EXTRACTED') for row in rows)
    lines=['ДОКУМЕНТЫ: ПОЛНАЯ ОЧЕРЕДЬ',f'Всего вложений: {len(rows)}. Прочитано: {read}. Ожидают обработки: {pending}. Ошибок: {len(rows)-read-pending}.']
    titles={'SCOPE':'Предмет','TECHNOLOGY':'ТЗ','ELIGIBILITY':'Требования','PAYMENT':'Оплата','BUDGET':'Бюджет','DEADLINE':'Срок','EVALUATION':'Критерии','SUBMISSION':'Подача','GUARANTEE':'Гарантии','DOCUMENTS':'Документы заявки'}
    for name,url,status,reports,error in rows:
        lines.append(f'{name}: '+('прочитан' if status=='READ' else 'в очереди' if status in ('PENDING','RETRY','EXTRACTED') else 'не прочитан: '+str(error)))
        lines.append('Источник: '+url)
        seen=set()
        for report in reports or []:
            for fact in report.get('facts',[]):
                quote=fact['quote']
                if quote in seen:continue
                seen.add(quote)
                lines.append(titles.get(fact.get('category'),fact.get('category','Факт'))+': '+quote[:220])
                if len(seen)>=2:break
            if len(seen)>=2:break
    return '\n'.join(lines)[:16000],read,pending,len(rows)
