'use strict';
const P='UF_CRM_13_SVCIT_', TYPE=1062, CATEGORY=21;
const URL_APP='https://leadgen.serviseit.ru/apps/serviceit-workspace/';
const INTEREST={YES:'Да — интерес подтверждён',NO:'Нет — отказ',UNKNOWN:'Неясно — нужно уточнить'};
const BASIS={PLAN:'План — ожидаемые суммы',ACTUAL:'Факт — получено и расходы учтены'};
function cents(value){
 const s=String(value??'').trim().replace(',','.');
 if(!s)return null;
 if(!/^\d{1,10}(\.\d{1,2})?$/.test(s))throw Error('Введите неотрицательную сумму, не более двух знаков после запятой.');
 const [a,b='']=s.split('.');return Number(a)*100+Number(b.padEnd(2,'0'));
}
function splitMoney(r,c){if(r===null||c===null)return null;const p=r-c,positive=Math.max(0,p),d=Math.floor((positive*15+50)/100),m=Math.min(positive-d,Math.floor((positive*10+50)/100));return {profit:p,developer:d,manager:m,company:p-d-m};}
function rub(c){return c===null?'—':new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB'}).format(c/100);}
function cell(title,value,color='base_90'){return {type:'withTitle',properties:{title,block:{type:'text',properties:{value:String(value||'Не заполнено'),bold:true,color}}}};}
function timelineLayout(item,kind){
 const finance=kind==='finance',v=k=>item[P+k];
 let revenue=null,cost=null,result=null;try{revenue=cents(v('FIN_REVENUE'));cost=cents(v('FIN_COSTS'));result=splitMoney(revenue,cost);}catch(e){}
 const blocks=finance?{revenue:cell('Цена / выручка',rub(revenue)),cost:cell('Расходы',rub(cost)),profit:cell('Прибыль',rub(result?.profit??null)),developer:cell('Разработчику · 15%',rub(result?.developer??null)),manager:cell('Менеджеру · 10%',rub(result?.manager??null)),company:cell('Компании · 75% / убыток',rub(result?.company??null))}:{status:cell('Результат',v('POST_BADGE')||'Ожидает разговора'),need:cell('Потребность',v('POST_NEED')),next:cell('Следующий шаг',v('POST_ACTION')),due:cell('Срок',v('POST_DUE'))};
 return {icon:{code:finance?'document':'call-completed'},header:{title:finance?'Сервис ИТ · Финансы и прибыль':'Сервис ИТ · Итог разговора'},body:{logo:{code:'call-incoming'},blocks},footer:{buttons:{edit:{title:finance?'Заполнить финансы':'Заполнить итог',type:'primary',action:{type:'openRestApp',actionParams:{cardId:String(item.id),section:kind}}}}}};
}
if(typeof module!=='undefined')module.exports={cents,splitMoney,timelineLayout};
if(typeof document!=='undefined'){
 let item=null,interest='',maps={},busy=false;
 const $=id=>document.getElementById(id),msg=(text,error=false)=>{$('status').textContent=text;$('status').classList.toggle('error',error);};
 const api=(method,args={})=>new Promise((resolve,reject)=>BX24.callMethod(method,args,r=>r.error()?reject(Error('Битрикс24: '+String(r.error()).replace(/[^A-Za-z0-9_ -]/g,'').slice(0,80))):resolve(r.data())));
 const run=async fn=>{if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await fn();}catch(e){msg(e.message,true);}finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);}};
 async function get(id){const x=(await api('crm.item.get',{entityTypeId:TYPE,id,useOriginalUfNames:'Y'})).item;if(Number(x.categoryId)!==CATEGORY||Number(x.id)<8)throw Error('Карточка вне рабочей воронки.');return x;}
 async function enumMaps(){const f=(await api('crm.item.fields',{entityTypeId:TYPE,useOriginalUfNames:'Y'})).fields;for(const [field,values] of [['POST_INTEREST',INTEREST],['FIN_BASIS',BASIS]]){maps[field]={};for(const [key,label] of Object.entries(values)){const row=(f[P+field]?.items||[]).find(x=>(x.VALUE??x.value)===label);if(!row)throw Error('Не удалось сопоставить варианты поля '+field);maps[field][key]=String(row.ID??row.id);}}}
 function choose(value){interest=value;$('badge').dataset.interest=value;document.querySelectorAll('[data-interest]').forEach(b=>b.classList.toggle('selected',b.dataset.interest===value));}
 function preview(){try{const x=splitMoney(cents($('FIN_REVENUE').value),cents($('FIN_COSTS').value));for(const k of ['profit','developer','manager','company'])$(k).textContent=rub(x?.[k]??null);$('financeNote').textContent=x&&x.profit<0?'Убыток относится на компанию. Вознаграждения — 0 ₽.':'Предварительный расчёт. Сохранённые данные обработает сервер.';}catch(e){$('financeNote').textContent=e.message;for(const k of ['profit','developer','manager','company'])$(k).textContent='—';}}
 function fill(){
 $('subtitle').textContent=item.title;$('badge').textContent=item[P+'POST_BADGE']||'Ожидает заполнения';
 for(const k of ['POST_NEED','POST_OBJECTIONS','POST_ACTION','POST_NOTES','FIN_REVENUE','FIN_COSTS'])$(k).value=item[P+k]??'';
 const date=item[P+'POST_DUE'];$('POST_DUE').value=date?localDate(new Date(date)):'';
 choose(Object.keys(INTEREST).find(k=>maps.POST_INTEREST[k]===String(item[P+'POST_INTEREST']))||'');
 $('FIN_BASIS').value=Object.keys(BASIS).find(k=>maps.FIN_BASIS[k]===String(item[P+'FIN_BASIS']))||'PLAN';
 $('summary').textContent=item[P+'POST_SUMMARY']||'Появится после сохранения и обработки.';$('totals').textContent=item[P+'ALL_TOTALS']||'Расчёт ещё не получен';preview();
 }
 function localDate(d){return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
 async function syncTimeline(x){for(const kind of ['call','finance']){
 const origin='serviceit-workspace:'+x.id+':'+kind;
 const rows=await api('crm.activity.list',{filter:{OWNER_TYPE_ID:TYPE,OWNER_ID:x.id,ORIGINATOR_ID:'serviceit_workspace_app_v1',ORIGIN_ID:origin},select:['ID']});
 if(rows.length>1)throw Error('Обнаружены повторные блоки. Нужна проверка.');
 const layout=timelineLayout(x,kind);
 if(rows.length)await api('crm.activity.configurable.update',{id:Number(rows[0].ID),layout});
 else await api('crm.activity.configurable.add',{ownerTypeId:TYPE,ownerId:Number(x.id),fields:{typeId:'CONFIGURABLE',completed:false,responsibleId:Number(x.assignedById)||undefined,originatorId:'serviceit_workspace_app_v1',originId:origin},layout});
 }}
 async function save(kind){
 const keys=kind==='call'?['POST_NEED','POST_OBJECTIONS','POST_ACTION','POST_NOTES','POST_DUE','POST_INTEREST']:['FIN_REVENUE','FIN_COSTS','FIN_BASIS'];
 const fresh=await get(item.id);for(const k of keys)if(String(fresh[P+k]??'')!==String(item[P+k]??''))throw Error('Эти данные изменил другой сотрудник. Переоткройте форму перед сохранением.');
 const fields={};if(kind==='call'){
 for(const k of keys.filter(k=>!['POST_INTEREST','POST_DUE'].includes(k))){if($(k).value.length>16000)throw Error('Слишком длинная запись.');fields[P+k]=$(k).value;}
 fields[P+'POST_INTEREST']=interest?maps.POST_INTEREST[interest]:'';fields[P+'POST_DUE']=$('POST_DUE').value?new Date($('POST_DUE').value).toISOString():'';
 }else{for(const k of ['FIN_REVENUE','FIN_COSTS']){const v=cents($(k).value);fields[P+k]=v===null?'':(v/100).toFixed(2);}fields[P+'FIN_BASIS']=maps.FIN_BASIS[$('FIN_BASIS').value];}
 await api('crm.item.update',{entityTypeId:TYPE,id:item.id,useOriginalUfNames:'Y',fields});item=await get(item.id);
 for(const [k,v] of Object.entries(fields)){const actual=item[k]??'';const same=k.endsWith('POST_DUE')?(v?new Date(actual).getTime()===new Date(v).getTime():!actual):k.endsWith('FIN_REVENUE')||k.endsWith('FIN_COSTS')?cents(actual)===cents(v):String(actual)===String(v);if(!same)throw Error('Сохранение не подтверждено. Переоткройте карточку.');}
 fill();msg('Сохранено в CRM. ИИ, статус и общие итоги обновятся в течение 5 минут.');
 try{await syncTimeline(item);}catch(e){msg('Данные сохранены. Обновление блоков ленты пока не подтверждено: '+e.message,true);}
 }
 async function install(){
 const bindings=await api('placement.get');if(!bindings.some(x=>x.placement==='CRM_DYNAMIC_1062_DETAIL_TAB'&&x.handler===URL_APP||x.PLACEMENT==='CRM_DYNAMIC_1062_DETAIL_TAB'&&x.HANDLER===URL_APP))await api('placement.bind',{PLACEMENT:'CRM_DYNAMIC_1062_DETAIL_TAB',HANDLER:URL_APP,TITLE:'Работа с клиентом'});
 let last=7,count=0;for(let page=0;page<100;page++){
 const rows=(await api('crm.item.list',{entityTypeId:TYPE,useOriginalUfNames:'Y',filter:{categoryId:CATEGORY,'>id':last},order:{id:'ASC'},select:['*'],start:0})).items;if(!rows.length)break;
 for(const x of rows){if(Number(x.id)<=last)throw Error('Ошибка порядка карточек.');await syncTimeline(x);last=Number(x.id);count++;msg('Подключено карточек: '+count);}
 if(rows.length<50)break;
 }msg('Блоки созданы. Завершаем установку…');BX24.installFinish();
 }
 document.querySelectorAll('[data-interest]').forEach(b=>b.onclick=()=>choose(b.dataset.interest));
 $('FIN_REVENUE').oninput=preview;$('FIN_COSTS').oninput=preview;$('saveCall').onclick=()=>run(()=>save('call'));$('saveFinance').onclick=()=>run(()=>save('finance'));$('installButton').onclick=()=>run(install);
 if(typeof BX24==='undefined'||window.parent===window){msg('Форма готова. Откройте её через установленное приложение Битрикс24.');}
 else BX24.init(()=>run(async()=>{
 const auth=BX24.getAuth();if(auth.domain!=='serviseit.bitrix24.ru')throw Error('Приложение доступно только для портала Сервис ИТ.');
 if(location.pathname.endsWith('/install')){$('install').hidden=false;msg('Готово к установке.');return;}
 const p=BX24.placement.info(),o=p.options||{};let id=Number(o.cardId||o.ID||o.id||o.actionParams?.cardId);
 if(!Number.isSafeInteger(id)||id<8){msg('Откройте приложение из блока «Итог разговора», «Финансы» или вкладки карточки.');return;}
 await enumMaps();item=await get(id);fill();$('workspace').hidden=false;msg('Данные загружены из CRM.');
 try{await syncTimeline(item);}catch(e){msg('Форма доступна. Блоки ленты требуют проверки: '+e.message,true);}
 BX24.fitWindow();if(o.section==='finance')document.querySelector('.finance').scrollIntoView();
 }));
}
