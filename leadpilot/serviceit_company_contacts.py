"""Public corporate contacts: search hints never count as identity evidence."""
import re
from datetime import datetime,timezone
from urllib.parse import urlsplit,urljoin
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


def read(client,url,robots):
    if not safe(url):return None
    p=urlsplit(url);origin=p.scheme+'://'+p.netloc
    if origin not in robots:
        response=client.get(origin+'/robots.txt',timeout=7,follow_redirects=False)
        if response.status_code==404:robots[origin]=None
        elif response.status_code==200 and len(response.content)<100000:
            robot=RobotFileParser();robot.parse(response.text.splitlines());robots[origin]=robot
        else:return None
    if robots[origin] and not robots[origin].can_fetch('ServiceITResearch',url):return None
    # Redirects need a fresh host/DNS/robots check; never follow implicitly.
    with client.stream('GET',url,timeout=8,follow_redirects=False,headers={'User-Agent':'ServiceITResearch/1.0'}) as response:
        if response.status_code!=200 or 'text/html' not in response.headers.get('content-type',''):return None
        data=bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data)>600000:return None
        parser=VisibleText();parser.feed(bytes(data).decode(response.encoding or 'utf-8','replace'))
    return {'url':url,'text':'\n'.join(parser.parts)[:100000],'links':parser.links[:200],
            'emails':parser.mail[:50],'phones':parser.tel[:50]}


def identity(page,inn):
    # Require a labelled legal identifier; a random account number is not proof.
    return bool(re.search(r'\bИНН\s*(?:/\s*КПП)?\s*[:№-]?\s*'+re.escape(inn)+r'(?!\d)',page['text'],re.I))


def extract(pages,inn):
    proof={urlsplit(p['url']).hostname:p['url'] for p in pages if identity(p,inn)}
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
    return contacts[:30]


def search(client,env,inn,name):
    urls=[];provider='NONE';query=f'"{inn}" "{name[:100]}" контакты реквизиты официальный сайт'
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
    pages=[];robots={};queue=[];seen=set()
    for url in urls:
        if safe(url):queue.append(url)
    while queue and len(seen)<12:
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
    contacts=extract(pages,inn)
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
