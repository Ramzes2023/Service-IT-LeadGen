"""Public corporate contacts: search hints never count as identity evidence."""
import re,time
from datetime import datetime,timezone
from urllib.parse import urlsplit,urljoin,unquote
from urllib.robotparser import RobotFileParser
from .serviceit_public_evidence_research import _host,_public_dns,VisibleText
from .serviceit_vps_pg_queue import secret_file

PHONE=re.compile(r'(?<!\d)(?:\+7|8)[ (.-]*\d{3}[ ).-]*\d{3}[ -]*\d{2}[ -]*\d{2}(?!\d)')
EMAIL=re.compile(r'(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}')
DENY=('rusprofile.ru','list-org.com','checko.ru','sbis.ru','audit-it.ru','zachestnyibiznes.ru','spark-interfax.ru','bidzaar.com','b2b-center.ru','zakupki.gov.ru','2gis.ru','yandex.ru','vk.com','facebook.com','t.me','hh.ru')


def safe(url):
    try:
        host=_host(url,((urlsplit(url).hostname or '').lower(),))
        return not any(host==d or host.endswith('.'+d) for d in DENY) and _public_dns(host)
    except (ValueError,TypeError):return False


def read(client,url,robots,depth=0):
    if depth>3 or not safe(url):return None
    p=urlsplit(url);origin=p.scheme+'://'+p.netloc
    if origin not in robots:
        target=origin+'/robots.txt'
        for redirect in range(4):
            if not safe(target) or urlsplit(target).path!='/robots.txt':return None
            with client.stream('GET',target,timeout=7,follow_redirects=False) as response:
                if response.status_code in (301,302,303,307,308):
                    target=urljoin(target,response.headers.get('location',''));continue
                if response.status_code==404:robots[origin]=None;break
                if response.status_code!=200:return None
                data=bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data)>100000:return None
                text=bytes(data).decode('utf-8','replace')
                if re.search(r'<(?:html|body|script)\b',text,re.I):return None
                robot=RobotFileParser();robot.parse(text.splitlines());robots[origin]=robot;break
        if origin not in robots:return None
    if robots[origin] and not robots[origin].can_fetch('ServiceITResearch',url):return None
    with client.stream('GET',url,timeout=8,follow_redirects=False,headers={'User-Agent':'ServiceITResearch/1.0'}) as response:
        if response.status_code in (301,302,303,307,308):
            return read(client,urljoin(url,response.headers.get('location','')),robots,depth+1)
        media=response.headers.get('content-type','').lower()
        pdf='application/pdf' in media
        if response.status_code!=200 or not (pdf or 'text/html' in media):return None
        data=bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data)>(4000000 if pdf else 1000000):return None
        if pdf:
            from .serviceit_b2b_tender_documents import extract_text
            return {'url':url,'text':extract_text(bytes(data),'.pdf'),'links':[],'emails':[],'phones':[]}
        parser=VisibleText();parser.feed(bytes(data).decode(response.encoding or 'utf-8','replace'))
    return {'url':url,'text':'\n'.join(parser.parts)[:100000],'links':parser.links[:200],
            'emails':parser.mail[:50],'phones':parser.tel[:50]}


def identity(page,inn,name=''):
    text=page['text']
    matches=list(re.finditer(r'\bИНН\s*(?:/\s*КПП)?\s*[:№-]?\s*(\d{10,12})(?!\d)',text,re.I))
    if not matches or {m[1] for m in matches}!={inn}:return False
    path=unquote(urlsplit(page['url']).path)
    if path not in ('','/') and not re.search(r'contact|requis|rekviz|about|privacy|policy|legal|company|kontak|o-komp|sveden|контакт|реквизит|компани|политик',path,re.I):
        return False
    if name:
        words=[w for w in re.findall(r'[а-яёa-z0-9]+',name.lower()) if len(w)>2 and w not in {'ооо','пао','оао','зао','общество','ограниченной','ответственностью','акционерное','публичное'}]
        if not words:return False
        # Legal name and INN must appear together in the site's own legal block.
        if not any(all(word in text[max(0,m.start()-700):m.end()+700].lower() for word in words) for m in matches):return False
    return True


def extract(pages,inn,name=''):
    proof={urlsplit(p['url']).hostname:p['url'] for p in pages if identity(p,inn,name)}
    contacts=[];seen=set()
    for page in pages:
        host=urlsplit(page['url']).hostname
        if host not in proof:continue
        # A page mentioning another labelled INN may be a group-company directory.
        inns=set(re.findall(r'\bИНН\s*[:№-]?\s*(\d{10,12})(?!\d)',page['text'],re.I))
        if inns and inns!={inn}:continue
        text=page['text']
        values=list(EMAIL.findall(text))+list(PHONE.findall(text))
        # mailto/tel are actual public HTML links, not model guesses.
        values+=page.get('emails',[])+page.get('phones',[])
        for raw in values:
            value=raw.strip()
            if EMAIL.fullmatch(value):kind='email'
            elif PHONE.fullmatch(value):kind='телефон'
            else:continue
            key=value.lower() if kind=='email' else re.sub(r'\D','',value)[-10:]
            if key in seen:continue
            seen.add(key)
            pos=text.find(value);context=text[max(0,pos-100):pos+len(value)+100] if pos>=0 else ''
            role='общий контакт компании; нужный отдел уточнить'
            for pattern,label in [(r'отдел\s+закупок|закупочн\w*\s+отдел','отдел закупок'),(r'ит[- ]отдел|отдел\s+информационных\s+технологий','ИТ-отдел'),(r'отдел\s+кадров|подбор\s+персонала','отдел персонала')]:
                if re.search(pattern,context,re.I):role=label+' (по контексту страницы; уточнить при обращении)';break
            contacts.append({'kind':kind,'value':value,'role':role,'context':context.strip(),'url':page['url'],'identity_url':proof[host]})
    return sorted(contacts,key=lambda c:c['role'].startswith('общий'))[:30]


def search(client,env,inn,name):
    started=time.monotonic();urls=[];provider='NONE';query=f'"{inn}" контакты реквизиты официальный сайт'
    key=secret_file(env,'SERVICEIT_TINYFISH_API_KEY',optional=True)
    if key:
        try:
            response=client.get('https://api.search.tinyfish.ai',params={'query':query,'location':'RU','language':'ru','page':0},headers={'X-API-Key':key},timeout=20)
            response.raise_for_status();payload=response.json()
            urls=[r['url'] for r in payload.get('results',[]) if isinstance(r,dict) and isinstance(r.get('url'),str)];provider='TINYFISH'
        except Exception:pass
    if not urls:
        key=secret_file(env,'SERVICEIT_TAVILY_SEARCH_API_KEY',optional=True)
        if key:
            try:
                response=client.post('https://api.tavily.com/search',headers={'Authorization':'Bearer '+key},json={'query':query,'search_depth':'basic','max_results':8,'include_answer':False,'include_raw_content':False},timeout=20)
                response.raise_for_status();urls=[r['url'] for r in response.json().get('results',[]) if isinstance(r,dict) and isinstance(r.get('url'),str)];provider='TAVILY'
            except Exception:pass
    # INN often finds registries first. A separate name query discovers the
    # corporate site, whose legal page must still prove this exact INN.
    if name.strip():
        query=name[:150]+' официальный сайт контакты реквизиты'
        tiny=secret_file(env,'SERVICEIT_TINYFISH_API_KEY',optional=True)
        tavily=secret_file(env,'SERVICEIT_TAVILY_SEARCH_API_KEY',optional=True)
        try:
            if tiny:
                response=client.get('https://api.search.tinyfish.ai',params={'query':query,'location':'RU','language':'ru','page':0},headers={'X-API-Key':tiny},timeout=20)
                response.raise_for_status();extra=response.json().get('results',[]);provider='TINYFISH'
            elif tavily:
                response=client.post('https://api.tavily.com/search',headers={'Authorization':'Bearer '+tavily},json={'query':query,'search_depth':'basic','max_results':8,'include_answer':False,'include_raw_content':False},timeout=20)
                response.raise_for_status();extra=response.json().get('results',[]);provider='TAVILY'
            else:extra=[]
            urls += [r['url'] for r in extra if isinstance(r,dict) and isinstance(r.get('url'),str)]
        except Exception:pass
    pages=[];robots={};queue=[];seen=set()
    for url in urls:
        if safe(url):queue.append(url)
    while queue and len(seen)<12 and time.monotonic()-started<70:
        url=queue.pop(0)
        if url in seen:continue
        seen.add(url)
        try:page=read(client,url,robots)
        except Exception:continue
        if not page:continue
        pages.append(page)
        host=urlsplit(url).hostname
        for href,label in page['links']:
            candidate=urljoin(url,href).split('#')[0]
            if urlsplit(candidate).hostname==host and re.search(r'контакт|реквизит|contact|requis|privacy|политик|о компании|about',label+' '+href,re.I) and candidate not in seen and candidate not in queue:
                queue.append(candidate)
    contacts=extract(pages,inn,name)
    return {'status':'VERIFIED' if contacts else 'NOT_FOUND' if provider!='NONE' else 'SEARCH_UNAVAILABLE','contacts':contacts,'provider':provider,'pages_checked':len(pages),'checked_at':datetime.now(timezone.utc).isoformat()}


def render(result,inn):
    lines=['ПУБЛИЧНЫЕ КОНТАКТЫ КОМПАНИИ',f'Проверка по ИНН {inn}. Дата: '+result.get('checked_at','')]
    if not result.get('contacts'):
        lines.append('Подтверждённые публичные контакты не найдены. Это не означает, что у компании их нет.')
        return '\n'.join(lines)
    for contact in result['contacts']:
        lines.extend([contact['kind']+': '+contact['value']+' — '+contact['role'],
                      'Контекст: '+contact['context'][:200], 'Источник: '+contact['url'],
                      'Принадлежность компании подтверждена ИНН: '+contact['identity_url']])
    lines.append('Первое обращение: уточнить, кто отвечает за ИТ, сопровождение 1С или закупку из карточки, и попросить соединить. Наличие публичного контакта не подтверждает интерес клиента.')
    return '\n'.join(lines)[:12000]
