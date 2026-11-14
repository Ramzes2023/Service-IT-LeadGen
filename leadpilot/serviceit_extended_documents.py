"""Durable all-file queue. Batch size is throughput, never a document cap."""
import hashlib,json,os,re,time,shutil,subprocess,tempfile
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
 PRIMARY KEY(card_id,url));
 ALTER TABLE serviceit_extended_documents ADD COLUMN IF NOT EXISTS parser_version INTEGER NOT NULL DEFAULT 1;
 UPDATE serviceit_extended_documents SET parser_version=2,status='PENDING',attempts=0,next_attempt_at=now()
 WHERE parser_version<2 AND error IN ('DOCUMENT_FORMAT_UNSUPPORTED','DOCUMENT_EMPTY_TEXT');
 UPDATE serviceit_extended_documents SET parser_version=3,status='PENDING',attempts=0,next_attempt_at=now()
 WHERE parser_version<3 AND error='OCR_ENGINE_UNAVAILABLE';'''


def _limits():
    import resource
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


def recognize(data,kind):
    """Bounded full-file OCR; never silently accept only the first pages."""
    from io import BytesIO
    if kind=='.pdf':
        from pypdf import PdfReader
        count=len(PdfReader(BytesIO(data),strict=True).pages)
        if not 1<=count<=20:raise ValueError('OCR_PAGE_LIMIT_EXCEEDED')
    else:count=1
    if not shutil.which('tesseract') or (kind=='.pdf' and not shutil.which('pdftoppm')):
        raise ValueError('OCR_ENGINE_UNAVAILABLE')
    def command(args):
        result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            timeout=30,preexec_fn=_limits,env={**os.environ,'OMP_THREAD_LIMIT':'1'})
        if result.returncode or len(result.stdout)>1000000:raise ValueError('OCR_CONVERSION_FAILED')
        return result.stdout.decode('utf-8','replace').strip()
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='serviceit-ocr-') as folder:
        source=os.path.join(folder,'source'+kind)
        with open(source,'wb') as output:output.write(data)
        parts=[]
        for page in range(1,count+1):
            if time.monotonic()-started>120:raise ValueError('OCR_TIME_LIMIT_EXCEEDED')
            image=source
            if kind=='.pdf':
                prefix=os.path.join(folder,'page')
                command(['pdftoppm','-f',str(page),'-l',str(page),'-singlefile','-scale-to','2200','-png',source,prefix])
                image=prefix+'.png'
            text=command(['tesseract',image,'stdout','-l','rus+eng+chi_sim','--psm','3'])
            if not text:raise ValueError('OCR_PAGE_EMPTY')
            parts.append('Страница '+str(page)+'\n'+text)
        return 'OCR: распознанный текст; цифры и реквизиты требуют сверки с оригиналом.\n'+'\n'.join(parts)


def full_extract(data,kind):
    if kind=='.rtf':return legacy_doc(data)
    if kind in ('.png','.jpg','.jpeg'):return recognize(data,kind)
    if kind=='.pdf':
        text=extract_text(data,kind)
        return text if text.strip() else recognize(data,kind)
    if kind=='.xls':
        import xlrd
        book=xlrd.open_workbook(file_contents=data,on_demand=True)
        try:
            parts=[];size=0
            for sheet in book.sheets():
                parts.append(sheet.name)
                for row in range(sheet.nrows):
                    text=' | '.join(str(value) for value in sheet.row_values(row) if value!='')
                    size+=len(text)
                    if size>1000000:raise ValueError('DOCUMENT_TEXT_TOO_LARGE')
                    if text:parts.append(text)
            return '\n'.join(parts)
        finally:book.release_resources()
    if kind!='.xlsx':return extract_text(data,kind)
    from . import serviceit_b2b_tender_documents as parser
    ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with parser._safe_archive(data) as archive:
        if 'xl/workbook.xml' not in archive.namelist():raise ValueError('DOCUMENT_XLSX_NOT_VALID')
        shared=[];parts=[]
        if 'xl/sharedStrings.xml' in archive.namelist():
            root=parser.XML.fromstring(archive.read('xl/sharedStrings.xml'))
            shared=[' '.join(t.text for t in si.iter(ns+'t') if t.text) for si in root.iter(ns+'si')]
        for name in archive.namelist():
            if not re.fullmatch(r'xl/worksheets/sheet[0-9]+\.xml',name):continue
            root=parser.XML.fromstring(archive.read(name));parts.append(name)
            for row in root.iter(ns+'row'):
                cells=[]
                for cell in row.iter(ns+'c'):
                    value=cell.find(ns+'v')
                    if cell.get('t')=='inlineStr':text=' '.join(t.text for t in cell.iter(ns+'t') if t.text)
                    elif value is None or value.text is None:text=''
                    elif cell.get('t')=='s':
                        index=int(value.text)
                        if not 0<=index<len(shared):raise ValueError('DOCUMENT_XLSX_STRING_INVALID')
                        text=shared[index]
                    else:text=value.text
                    if text:cells.append(text)
                if cells:parts.append(' | '.join(cells))
        return '\n'.join(parts)


def download(client,url,name):
    p=urlsplit(url)
    if p.scheme!='https' or p.netloc!='bidzaar.com' or p.query or p.fragment or not re.fullmatch(r'/api/filestorage/files/download/[0-9a-fA-F-]{36}',p.path):
        raise ValueError('DOCUMENT_URL_INVALID')
    kind=PurePosixPath(name).suffix.lower()
    if kind not in ('.doc','.docx','.pdf','.xlsx','.xls','.rtf','.png','.jpg','.jpeg','.txt','.zip'):raise ValueError('DOCUMENT_FORMAT_UNSUPPORTED')
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
                            if suffix not in ('.doc','.docx','.pdf','.xlsx','.xls','.rtf','.png','.jpg','.jpeg','.txt'):
                                raise ValueError('ARCHIVE_CONTAINS_UNSUPPORTED_DOCUMENT')
                            payload=archive.read(member)
                            parts.append(member.filename+'\n'+(legacy_doc(payload) if suffix=='.doc' else full_extract(payload,suffix)))
                    text='\n'.join(parts)
                else:text=legacy_doc(raw) if kind=='.doc' else full_extract(raw,kind)
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
    conn.execute("UPDATE serviceit_extended_documents SET status='FAILED',error=COALESCE(error,'WORKER_INTERRUPTED') WHERE status='RETRY' AND attempts>=3 AND next_attempt_at<=now()")
    rows=conn.execute("SELECT card_id,url,name FROM serviceit_extended_documents WHERE status IN ('PENDING','RETRY') AND attempts<3 AND next_attempt_at<=now() ORDER BY attempts,card_id,url LIMIT %s",(batch,)).fetchall()
    done=[]
    for card,url,name in rows:
        conn.execute("UPDATE serviceit_extended_documents SET attempts=attempts+1,status='RETRY',next_attempt_at=now()+interval '10 minutes' WHERE card_id=%s AND url=%s",(card,url))
        try:
            document=download(client,url,name)
            conn.execute("UPDATE serviceit_extended_documents SET status='EXTRACTED',text_content=%s,sha256=%s,reports=%s::jsonb,error=NULL,updated_at=now() WHERE card_id=%s AND url=%s",(document['text'],document['sha256'],json.dumps([{'provider':'OCR','facts':[]}] if document['text'].startswith('OCR:') else []),card,url))
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
        if any(r.get('provider')=='OCR' for r in reports or []):lines.append('Текст распознан OCR: цифры и реквизиты сверить с оригиналом.')
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
