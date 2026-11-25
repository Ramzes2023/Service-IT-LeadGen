'use strict';
const $=id=>document.getElementById(id),base='/apps/serviceit-workspace/';
let busy=false;
const rub=x=>new Intl.NumberFormat('ru-RU',{style:'currency',currency:'RUB'}).format(x/100);
function cell(row,value){const td=document.createElement('td');td.textContent=value;row.append(td);return td;}
function render(data){
 for(const key of ['company','manager','developer','paid_manager','paid_developer','revenue','costs','profit']){$(key).textContent=rub(data.totals[key]);$(key).classList.toggle('negative',data.totals[key]<0);}
 for(const role of ['manager','developer']){$('due_'+role).textContent=rub(data.totals[role]-data.totals['paid_'+role]);}
 $('count').textContent=data.count;$('rows').replaceChildren();$('managers').replaceChildren();$('empty').hidden=data.count!==0;
 for(const item of data.rows){const tr=document.createElement('tr'),td=document.createElement('td'),a=document.createElement('a');a.textContent='#'+item.id+' · '+item.title;a.href='https://serviseit.bitrix24.ru/crm/type/1062/details/'+item.id+'/';a.target='_blank';a.rel='noopener';td.append(a);tr.append(td);for(const key of ['revenue','costs','company','manager','developer'])cell(tr,rub(item[key]));if(item.overpaid){tr.classList.add('negative');a.append(' · проверить переплату');}$('rows').append(tr);}
 for(const m of data.managers){const tr=document.createElement('tr');cell(tr,'Сотрудник #'+m.id);cell(tr,m.count);cell(tr,rub(m.allocated));cell(tr,rub(m.paid));cell(tr,rub(m.allocated-m.paid));$('managers').append(tr);}
 const e=data.excluded;$('excluded').textContent=`Проверено карточек: ${data.scanned}. Ещё не завершены успешно: ${e.not_success}. План вместо факта: ${e.not_actual}. Не заполнены суммы: ${e.missing}. Ошибки сумм: ${e.invalid}. Исключено повторов: ${e.duplicate}.`;
 $('updated').textContent='Обновлено '+new Date(data.updated_at*1000).toLocaleString('ru-RU');$('scope').textContent=data.scope+'. Автообновление открытой сводки — каждую минуту.';
}
async function refresh(){if(busy)return;busy=true;$('refresh').disabled=true;try{const auth=BX24.getAuth();if(auth.domain!=='serviseit.bitrix24.ru')throw Error('Неверный портал.');const response=await fetch(base+'dashboard-data',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({access_token:auth.access_token})});const data=await response.json();if(!response.ok||data.error)throw Error(data.error||'Не удалось обновить сводку');render(data);$('status').textContent='Актуальные данные из CRM. Факт и план не смешиваются.';$('status').classList.remove('error');BX24.fitWindow();}catch(e){$('status').textContent='Сводка не обновлена: '+e.message;$('status').classList.add('error');}finally{busy=false;$('refresh').disabled=false;}}
$('refresh').onclick=refresh;
if(typeof BX24==='undefined'||window.parent===window){$('status').textContent='Откройте «Финансы — общая прибыль» через меню «Расширения» в Битрикс24.';$('refresh').disabled=true;}
else BX24.init(refresh);
setInterval(()=>{if(!document.hidden&&typeof BX24!=='undefined'&&window.parent!==window)refresh();},60000);
