"""Public corporate contacts: search hints never count as identity evidence."""
import re,time
from datetime import datetime,timezone
from urllib.parse import urlsplit,urljoin,unquote
from urllib.robotparser import RobotFileParser
from .serviceit_public_evidence_research import _host,_public_dns,VisibleText
from .serviceit_vps_pg_queue import secret_file

PHONE=re.compile(r'(?<!\d)(?:\+7|8)[ (.-]*\d{3}[ ).-]*\d{3}[ -]*\d{2}[ -]*\d{2}(?!\d)')
EMAIL=re.compile(r'(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}')
DENY=('rusprofile.ru','list-org.com','checko.ru','sbis.ru','audit-it.ru','zachestnyibiznes.ru','spark-interfax.ru','bidzaar.com','b2b-center.ru','zakupki.gov.ru','2gis.ru','yandex.ru','vk.com','facebook.com','t.me','hh.ru','saby.ru','xfirm.ru','tbank.ru','companies.rbc.ru','check.tochka.com','org.gorodrabot.ru')


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


def identity(page,inn,name='',scoped=False):
    text=page['text']
    matches=list(re.finditer(r'\bИНН\s*(?:/\s*КПП)?\s*[:№-]?\s*(\d{10,12})(?!\d)',text,re.I))
    if not matches or {m[1] for m in matches}!={inn}:return False
    path=unquote(urlsplit(page['url']).path)
    if not scoped and path not in ('','/') and not re.search(r'contact|requis|rekviz|about|privacy|policy|legal|company|kontak|o-komp|sveden|контакт|реквизит|компани|политик',path,re.I):
        return False
    if name:
        words=[w for w in re.findall(r'[а-яёa-z0-9]+',name.lower()) if len(w)>2 and w not in {'ооо','пао','оао','зао','общество','ограниченной','ответственностью','акционерное','публичное'}]
        if not words:return False
        # Legal name and INN must appear together in the site's own legal block.
        if not any(all(word in text[max(0,m.start()-700):m.end()+700].lower() for word in words) for m in matches):return False
    return True


def site(url):
    return (urlsplit(url).hostname or '').lower().removeprefix('www.')


def legal_sections(page,inn,name):
    # A group website can contain several legal entities. Its explicitly named
    # legal sections are separate evidence, never authority for the whole host.
    starts=list(re.finditer(r'Полное\s+наименование\s*:',page['text'],re.I))
    sections=[]
    for index,match in enumerate(starts):
        end=starts[index+1].start() if index+1<len(starts) else len(page['text'])
        text=page['text'][match.start():end]
        # Do not pull contacts from another entity or the site-wide footer.
        tail=re.search(r'Информация об адресах и контактных телефонах проверяющих|Контролирующие органы|Росздравнадзор|Роспотребнадзор',text,re.I)
        if tail:text=text[:tail.start()]
        candidate={**page,'text':text[:12000],'emails':[],'phones':[],'scoped':True}
        if name and identity(candidate,inn,name,scoped=True):sections.append(candidate)
    return sections


def extract(pages,inn,name=''):
    proof={site(p['url']):p['url'] for p in pages if not p.get('scoped') and identity(p,inn,name)}
    scoped=[part for p in pages for part in legal_sections(p,inn,name)]
    # Source documents may establish only the contacts in that document.
    scoped += [p for p in pages if p.get('scoped') and identity(p,inn,name,scoped=True)]
    contacts=[];seen={}
    for page in [p for p in pages if not p.get('scoped')]+scoped:
        host=site(page['url'])
        if not page.get('scoped') and host not in proof:continue
        # A page mentioning another labelled INN may be a group-company directory.
        inns=set(re.findall(r'\bИНН\s*(?:/\s*КПП)?\s*[:№-]?\s*(\d{10,12})(?!\d)',page['text'],re.I))
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
            pos=text.find(value);context=text[max(0,pos-100):pos+len(value)+100] if pos>=0 else ''
            if re.search(r'роспотребнадзор|росздравнадзор|надзорн|контролирующ',context,re.I):continue
            role='общий контакт компании; нужный отдел уточнить'
            for pattern,label in [(r'отдел\s+закупок|закупочн\w*\s+отдел','отдел закупок'),(r'ит[- ]отдел|отдел\s+информационных\s+технологий','ИТ-отдел'),(r'отдел\s+кадров|подбор\s+персонала','отдел персонала')]:
                if re.search(pattern,context,re.I):role=label+' (по контексту страницы; уточнить при обращении)';break
            if key in seen:
                previous=contacts[seen[key]]
                if previous['role'].startswith('общий') and not role.startswith('общий'):
                    previous.update(role=role,context=context.strip(),url=page['url'])
                continue
            seen[key]=len(contacts)
            contacts.append({'kind':kind,'value':value,'role':role,'context':context.strip(),'url':page['url'],'identity_url':page['url'] if page.get('scoped') else proof[host]})
    return sorted(contacts,key=lambda c:c['role'].startswith('общий'))[:100]


def search_urls(client,env,query,prefer_tavily=False):
    providers=['TAVILY','TINYFISH'] if prefer_tavily else ['TINYFISH','TAVILY']
    for provider in providers:
        key=secret_file(env,'SERVICEIT_TINYFISH_API_KEY' if provider=='TINYFISH' else 'SERVICEIT_TAVILY_SEARCH_API_KEY',optional=True)
        if not key:continue
        try:
            if provider=='TINYFISH':
                response=client.get('https://api.search.tinyfish.ai',params={'query':query,'location':'RU','language':'ru','page':0},headers={'X-API-Key':key},timeout=15)
            else:
                response=client.post('https://api.tavily.com/search',headers={'Authorization':'Bearer '+key},json={'query':query,'search_depth':'basic','max_results':8,'include_answer':False,'include_raw_content':False},timeout=15)
            response.raise_for_status()
            urls=[r['url'] for r in response.json().get('results',[]) if isinstance(r,dict) and isinstance(r.get('url'),str)]
            if urls:return urls,provider
        except Exception:continue
    return [],'NONE'


def candidate_url(url):
    # Search engines often index an old HTTP URL; try its HTTPS equivalent.
    if url.startswith('http://'):url='https://'+url[7:]
    return url.split('#')[0]


# Reviewed public discovery hints, never prefilled contacts or identity proof.
# Each URL is freshly fetched and must pass the same exact-INN/legal-section checks.
DISCOVERY_SEEDS={'7729674716':('https://www.k31.ru/patient/',)}

def search(client,env,inn,name,documents=()):
    started=time.monotonic();providers=set();pages=list(documents);robots={};queue=[];seen=set();attempted=0
    link_words=r'контакт|реквизит|contact|requis|rekviz|privacy|политик|о компании|about|юридическ|правов|legal|поставщик|закупк|supplier|procurement|пациент'
    def add(url,front=False):
        url=candidate_url(url)
        if url not in seen and url not in queue and safe(url):
            if front:queue.insert(0,url)
            else:queue.append(url)
    def crawl(limit):
        while queue and len(seen)<limit and time.monotonic()-started<150:
            url=queue.pop(0)
            if url in seen:continue
            seen.add(url)
            try:page=read(client,url,robots)
            except Exception:continue
            if not page:continue
            pages.append(page)
            # Give legal/contact links priority over unrelated search results.
            for href,label in reversed(page.get('links',[])):
                target=candidate_url(urljoin(page['url'],href))
                if site(target)==site(page['url']) and re.search(link_words,label+' '+href,re.I):add(target,True)
    for url in DISCOVERY_SEEDS.get(inn,()):add(url)
    crawl(2)
    queries=[f'"{inn}" контакты реквизиты официальный сайт',name[:150]+' официальный сайт контакты телефон email']
    for query in queries:
        urls,provider=search_urls(client,env,query);attempted+=1
        if provider!='NONE':providers.add(provider)
        for url in urls:add(url)
    crawl(16)
    found=extract(pages,inn,name)
    if not found and time.monotonic()-started<115:
        # A second provider is a real fallback when the first results cannot
        # establish contacts, not only when its API returns an empty list.
        urls,provider=search_urls(client,env,f'"{inn}" телефон почта реквизиты',prefer_tavily=True);attempted+=1
        if provider!='NONE':providers.add(provider)
        for url in urls:add(url,True)
        crawl(24);found=extract(pages,inn,name)
    if found and time.monotonic()-started<110 and not any(not c['role'].startswith('общий') for c in found):
        urls,provider=search_urls(client,env,name[:150]+' отдел закупок поставщикам контакты ИТ');attempted+=1
        if provider!='NONE':providers.add(provider)
        for url in urls:add(url,True)
        crawl(24);found=extract(pages,inn,name)
    return {'status':'VERIFIED' if found else 'NOT_FOUND' if providers else 'SEARCH_UNAVAILABLE','contacts':found,
            'provider':'+'.join(sorted(providers)) or 'NONE','pages_checked':len(pages)-len(documents),
            'urls_attempted':len(seen),'queries':attempted,'documents_checked':len(documents),
            'checked_at':datetime.now(timezone.utc).isoformat()}


def render(result,inn):
    lines=['ПУБЛИЧНЫЕ КОНТАКТЫ КОМПАНИИ',f'Проверка по ИНН {inn}. Дата: '+result.get('checked_at','')]
    if not result.get('contacts'):
        lines.append('Подтверждённые публичные контакты не найдены. Это не означает, что у компании их нет.')
        return '\n'.join(lines)
    lines.append('Кому обращаться: сначала контакт ИТ или закупок; если указан только общий номер — попросить соединить с ответственным.')
    shown=0
    for contact in result['contacts']:
        entry=[contact['kind']+': '+contact['value']+' — '+contact['role'],
                      'Контекст: '+contact['context'][:200], 'Источник: '+contact['url'],
                      'Принадлежность компании подтверждена ИНН: '+contact['identity_url']]
        if sum(len(line)+1 for line in lines+entry)>11200:break
        lines.extend(entry);shown+=1
    if shown<len(result['contacts']):lines.append(f'Показано {shown} из {len(result["contacts"])} контактов; остальные сохранены в результатах исследования.')
    lines.append('Первое обращение: уточнить, кто отвечает за ИТ, сопровождение 1С или закупку из карточки, и попросить соединить. Наличие публичного контакта не подтверждает интерес клиента.')
    return '\n'.join(lines)[:12000]
