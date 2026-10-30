"""Read-only local demonstration dashboard. Runs only on 127.0.0.1 by default."""
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from .core import initialize, get_leads, metrics
from .finance import sample_finance

DB = os.getenv("LEADPILOT_DB", "data/leads.sqlite3")
app = FastAPI(title="Сервис ИТ — LeadGen MVP (DEMO)", version="0.1.0")


@app.on_event("startup")
def startup():
    initialize(DB)


@app.get("/health")
def health():
    return {"ok": True, "mode": "DEMO_ONLY", "crm_writes": False}


@app.get("/api/leads")
def api_leads():
    initialize(DB)
    return get_leads(DB)


@app.get("/api/metrics")
def api_metrics():
    initialize(DB)
    return metrics(DB)


@app.get("/api/finance-example")
def finance_example():
    return sample_finance()


PAGE = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Сервис ИТ · LeadGen Lab</title>
<style>
:root{color-scheme:light;--ink:#16243f;--muted:#68778b;--navy:#121f38;--blue:#2b72e8;--bg:#f5f7fc;--line:#e3e8f2;--green:#0b8b66}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;color:var(--ink)}
header{background:var(--navy);color:white;padding:23px max(24px,calc((100vw - 1180px)/2));display:flex;justify-content:space-between;align-items:center;gap:18px}
header h1{font-size:23px;margin:0;font-weight:730;letter-spacing:-.5px}header p{margin:2px 0 0;color:#aab8d1;font-size:12px}
.mode{background:#294267;border-radius:16px;padding:6px 10px;font-size:12px;color:#dde7f7;white-space:nowrap}
main{max-width:1180px;margin:auto;padding:28px 24px 50px}
.eyebrow{font-size:11px;font-weight:800;letter-spacing:1.3px;text-transform:uppercase;color:#637da7}
.headline{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:flex-end;margin-bottom:20px}.headline h2{font-size:29px;letter-spacing:-.9px;margin:0}.headline p{margin:7px 0 0;color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:22px}.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 2px 8px rgba(22,36,63,.025)}
.card strong{display:block;font-size:30px;letter-spacing:-1px;margin-top:3px}.card span{font-size:12px;color:var(--muted)}
.workspace{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(320px,1fr);gap:18px}.panel{background:white;border:1px solid var(--line);border-radius:16px;overflow:hidden}
.panel-head{padding:19px 21px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:12px}.panel h3{margin:0;font-size:16px}.panel p{margin:2px 0 0;font-size:12px;color:var(--muted)}
select{border:1px solid #cbd5e6;padding:9px;border-radius:9px;background:#fff;color:var(--ink)}
.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}thead{background:#f7f9fc;color:var(--muted);font-size:11px;letter-spacing:.4px}th{text-align:left;padding:12px 17px}td{padding:14px 17px;border-top:1px solid #eef1f6;vertical-align:top}
tr.lead{cursor:pointer}tr.lead:hover{background:#f7faff}tr.selected{background:#edf4ff}.name{font-weight:720}.small{font-size:11px;color:var(--muted)}
.pill{display:inline-block;border-radius:20px;padding:3px 9px;font-weight:690;font-size:11px;background:#edf2fb;color:#5c6b87;white-space:nowrap}.high{background:#dff5eb;color:#087853}.medium{background:#fff4da;color:#92631a}.manual_review{background:#fff0ec;color:#ae5236}.suppressed,.existing_customer{background:#f1edf8;color:#76638f}
.detail{padding:23px}.detail h4{font-size:20px;letter-spacing:-.4px;margin:4px 0}.kv{margin:17px 0}.kv label{display:block;color:var(--muted);font-size:11px;font-weight:750;text-transform:uppercase;letter-spacing:.5px}.kv div{margin-top:4px;overflow-wrap:anywhere}
.signal{background:#f3f7ff;border:1px solid #dfe8f8;border-radius:11px;padding:13px;font-size:12px}.footer{margin:20px 0;color:var(--muted);font-size:12px}
.warning{padding:13px 16px;background:#fff7e9;color:#7a561e;border:1px solid #f2e0be;border-radius:11px;font-size:13px;margin-bottom:20px}
@media(max-width:940px){.workspace{grid-template-columns:1fr}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:540px){.cards{grid-template-columns:1fr 1fr}header{padding:18px 16px}main{padding:20px 12px}td,th{padding:10px} .headline h2{font-size:24px}}
</style></head><body>
<header><div><h1>◈ Сервис ИТ <span style="color:#7baafc">/ LeadGen Lab</span></h1><p>AI-assisted B2B lead qualification • учебная демонстрация</p></div><span class="mode">● OFFLINE DEMO · без CRM-записи</span></header>
<main><div class="headline"><div><span class="eyebrow">Центр квалификации</span><h2>Потенциальные клиенты</h2><p>Сигналы потребности, проверка повторов и прозрачный Lead Score</p></div><div style="font-size:12px;color:#68778b">Scoring: mvp-2026-10-v1</div></div>
<div class="warning" id="sourcewarning"><strong>Демонстрационные данные.</strong> Запущен локальный режим. Битрикс24 не подключён. AI подключается отдельно; итоговый Lead Score рассчитывается правилами.</div>
<div class="cards"><div class="card"><span>Компаний в базе</span><strong id="total">—</strong></div><div class="card"><span>Очередь менеджера</span><strong id="queue">—</strong></div><div class="card"><span>Высокий приоритет</span><strong id="high">—</strong></div><div class="card"><span>Проверить вручную</span><strong id="review">—</strong></div></div>
<div class="workspace"><section class="panel"><div class="panel-head"><div><h3>Реестр кандидатов</h3><p>Выберите компанию, чтобы увидеть происхождение оценки</p></div><select id="filter" aria-label="Фильтр статуса"><option value="all">Все статусы</option><option value="high">Высокий</option><option value="medium">Средний</option><option value="manual_review">На проверку</option><option value="suppressed">Стоп-лист</option></select></div><div class="table-wrap"><table><thead><tr><th>Компания / регион</th><th>Балл</th><th>Статус</th></tr></thead><tbody id="rows"></tbody></table></div></section>
<aside class="panel"><div class="panel-head"><div><h3>Карточка компании</h3><p>Подтверждённые признаки отдельно от оценки</p></div></div><div class="detail" id="detail">Выберите компанию слева.</div></aside></div>
<div class="footer">Разработано для проверки архитектуры ТЗ • Официальный API вакансий доступен только по явной команде • Без рассылок, звонков и записей в рабочую CRM.</div></main>
<script>
let records=[],selected=null;
const texts={high:'Высокий',medium:'Средний',nurture:'Прогрев',low:'Низкий',manual_review:'Проверка дубля',suppressed:'Стоп-лист',existing_customer:'Клиент',expired_or_missing_signal:'Сигнал устарел'};
function el(tag,text,cls){const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls;return n}
function kv(label,value){const w=el('div',undefined,'kv');w.append(el('label',label),el('div',String(value||'—')));return w}
function render(){const root=document.getElementById('rows');root.replaceChildren();const f=document.getElementById('filter').value;for(const r of records.filter(x=>f==='all'||x.priority===f)){const tr=el('tr',undefined,'lead'+(selected===r.id?' selected':''));const a=el('td');a.append(el('div',r.name,'name'),el('div',r.region,'small'),el('div','ИНН '+r.inn,'small'));const b=el('td',String(r.score)+'/100');const c=el('td');c.append(el('span',texts[r.priority]||r.priority,'pill '+r.priority));tr.append(a,b,c);tr.onclick=()=>{selected=r.id;detail(r);render()};root.append(tr)}if(!root.children.length){const tr=el('tr');const td=el('td','Нет записей для выбранного фильтра');td.colSpan=3;tr.append(td);root.append(tr)}}
function detail(r){const d=document.getElementById('detail');d.replaceChildren();d.append(el('div','Профиль компании','eyebrow'),el('h4',r.name));d.append(kv('Рекомендуемая услуга',r.recommended_service),kv('Статус / доступность',texts[r.priority]||r.priority),kv('Проверки',r.manual_review?'Есть совпадение телефона или почты с другим ИНН':r.suppressed_channels.length?'Ограничения: '+r.suppressed_channels.join(', '):'Не выявлены локальными правилами'),kv('Происхождение оценки','Регион: '+r.explanation.region+' · Сигнал: '+r.explanation.signal+' · Свежесть: '+r.explanation.freshness+' · Сегмент: '+r.explanation.segment+' · Услуга: '+r.explanation.service_fit+' · Контакт: '+r.explanation.contact));const s=r.signals[0];if(s){const w=el('div',undefined,'signal');w.append(el('strong','Сигнал потребности'),el('div',s.evidence),el('div',s.signal_date+' • '+s.source_name,'small'));const link=document.createElement('a');link.textContent=s.source_url;link.href=s.source_url;link.rel='noopener noreferrer';link.target='_blank';w.append(link);d.append(w)}const ai=r.ai_suggestion||{};
const state=ai.origin==='AI_hypothesis'?'AI-гипотеза, требует проверки менеджером':'AI пока не запускался. Оценка рассчитана правилами.';
d.append(kv('AI-статус',state));
if(ai.origin==='AI_hypothesis'){
 d.append(kv('AI-провайдер / модель',(ai.provider||'—')+' / '+(ai.model||'—')));
 d.append(kv('Возможная услуга (гипотеза)',ai.service));
 d.append(kv('Возможная потребность',ai.possible_need));
 d.append(kv('Уверенность модели',Math.round(Number(ai.confidence)*100)+'% (не вероятность покупки)'));
 d.append(kv('Цитата из сигнала',ai.evidence_quote||'Нет точной подтверждающей цитаты'));
 d.append(kv('Важно','AI не меняет Lead Score и не запускает рекламные действия'));
}}
async function load(){const [lr,mr]=await Promise.all([fetch('/api/leads'),fetch('/api/metrics')]);records=await lr.json();const m=await mr.json();
const warning=document.getElementById('sourcewarning');
warning.textContent=m.real_signal_count>0
 ? 'Внимание: загружены публичные вакансии ('+m.real_signal_count+'). Это сигналы для проверки, НЕ подтверждённое желание компании купить услугу. Битрикс24 не подключён. Автоматические сообщения выключены.'
 : 'Демонстрационный режим: компании и сигналы взяты из синтетического CSV. Битрикс24 не подключён, исходящие коммуникации выключены.';
document.getElementById('total').textContent=m.companies;document.getElementById('queue').textContent=m.manager_queue;document.getElementById('high').textContent=m.high;document.getElementById('review').textContent=m.manual_review;render();if(records.length){selected=records[0].id;detail(records[0]);render()}}
document.getElementById('filter').onchange=render;load().catch(err=>{document.getElementById('detail').textContent='Ошибка загрузки: '+err.message});
</script></body></html>'''


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE
