/* Offline Apps Script->VPS tests; no Google account, network or CRM writes. */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const crypto = require('crypto');

const source = fs.readFileSync(__dirname + '/../Code.gs', 'utf8');
const EMAIL = 'ramzesisayev444@gmail.com';
const SECRET = 'a'.repeat(64); // synthetic 256-bit hex; never production
const TOKEN = 'synthetic-github-token-not-valid';
const MID = '1a19b7630301bafb';
const CUTOVER = Date.now() - 60 * 60 * 1000;
const BODY = [
  'Организатор закупки: АО "СО ЕЭС"',
  'Предмет закупки: Услуги технической поддержки IT-инфраструктуры и серверов',
  'Актуально до: 10.12.2026',
  'https://www.b2b-center.ru/market/pravovoe-soprovozhdenie/tender-1234567/?utm_source=mail'
].join('\n');

function b64url(value) {
  return Buffer.from(value, 'utf8').toString('base64url');
}

function message(id=MID, options={}) {
  return {
    id,
    internalDate: String(options.internalDate || CUTOVER + 1000),
    payload: {
      headers: [
        {name:'From', value: options.from || 'B2B-Center <marketplace@b2b-center.ru>'},
        {name:'Authentication-Results',
         value: options.auth || 'mx.google.com; dkim=pass header.i=@b2b-center.ru; dmarc=pass header.from=b2b-center.ru'},
        {name:'Subject', value:'Новые закупки B2B-Center'}
      ],
      mimeType:'text/plain',
      body:{data:b64url(options.body === undefined ? BODY : options.body)}
    }
  };
}

function environment(options={}) {
  const properties = Object.assign({
    SERVICEIT_VPS_B2B_DIRECT_ENABLED:'YES',
    SERVICEIT_VPS_B2B_ACTIVATION_MS:String(CUTOVER+500)
  }, options.properties || {});
  const calls = {list:0,get:0,rest:0,dispatch:[],logs:[],triggers:[]};
  let account = options.account || EMAIL;
  let currentMessage = options.message || message();
  let mailboxMessages = options.messages || null;
  let responseCode = options.responseCode || 202;
  let uuid = 0;
  const context = {
    SERVICEIT_PRIVATE_CONFIG: {
      expectedMailbox: EMAIL,
      githubToken: TOKEN,
      hmacSecret: SECRET,
      ownerApproval: 'YES',
      cutoverMs: CUTOVER
    },
    Gmail: {
      Users: {
        getProfile: () => ({emailAddress:account}),
        Messages: {
          list: (_user, args) => {
            assert.strictEqual(_user,'me');
            assert.match(args.q,/from:\(b2b-center\.ru\)/);
            assert.strictEqual(args.maxResults,100);
            calls.list++;
            return {messages:(mailboxMessages || [currentMessage]).map(m=>({id:m.id}))};
          },
          get: (_user, id, args) => {
            assert.strictEqual(_user,'me');
            const selected=(mailboxMessages || [currentMessage]).find(m=>m.id===id);
            assert(selected, 'message not in mock Gmail');
            assert(['full','raw'].includes(args.format));
            calls.get++;
            return args.format==='raw' ? {id:selected.id,raw:selected._advancedRawMissing ? '' : (selected._raw||'')} : selected;
          }
        }
      }
    },
    PropertiesService: {
      getScriptProperties: () => ({
        getProperty: key => Object.prototype.hasOwnProperty.call(properties,key) ? properties[key] : null,
        setProperty: (key,value) => { properties[key]=String(value); return properties; }
      })
    },
    LockService: {
      getScriptLock: () => ({tryLock:()=>true,releaseLock:()=>{}})
    },
    ScriptApp: {
      getOAuthToken:()=> 'synthetic-google-oauth-access-token',
      getProjectTriggers: () => calls.triggers,
      newTrigger: handler => ({
        timeBased:()=>({
          everyMinutes:n=>({
            create:()=>{
              assert.strictEqual(n,5);
              calls.triggers.push({getHandlerFunction:()=>handler});
            }
          })
        })
      }),
      deleteTrigger: trigger => {
        const i=calls.triggers.indexOf(trigger);
        if(i>=0) calls.triggers.splice(i,1);
      }
    },
    Utilities: {
      Charset:{UTF_8:'utf8'},
      base64DecodeWebSafe: value => [...Buffer.from(value,'base64url')],
      newBlob: bytes => ({getDataAsString:()=>Buffer.from(bytes).toString('utf8'),getBytes:()=>[...Buffer.from(bytes)]}),
      base64Decode:value=>[...Buffer.from(value,'base64')],
      computeHmacSha256Signature:(value,key)=>{
        return [...crypto.createHmac('sha256',key).update(value).digest()]
          .map(x=>x>127?x-256:x);
      },
      getUuid:()=> "synthetic-dispatch-" + (++uuid)
    },
    UrlFetchApp: {
      fetch:(url,config)=>{
        if(url.startsWith('https://gmail.googleapis.com/gmail/v1/users/me/messages/')) {
          calls.rest++;
          assert.strictEqual(config.method,'get');
          assert.match(config.headers.Authorization,/^Bearer synthetic-google-oauth-access-token$/);
          assert.strictEqual(config.followRedirects,false);
          assert.strictEqual(config.validateHttpsCertificates,true);
          assert.strictEqual(config.muteHttpExceptions,true);
          assert.strictEqual(url.split('?')[1],'format=raw');
          const id=decodeURIComponent(url.split('/').pop().split('?')[0]);
          const msg=(mailboxMessages||[currentMessage]).find(x=>x.id===id);
          assert(msg,'Gmail raw requested for missing message');
          return {
            getResponseCode:()=>options.restHttpCode || 200,
            getContentText:()=>JSON.stringify({id:msg.id,raw:msg._raw||''})
          };
        }
        if(url==='https://leadgen.serviseit.ru/integrations/serviceit/b2b-center/health') {
          assert.strictEqual(config.method,'get');
          return {getResponseCode:()=>200,getContentText:()=>JSON.stringify({
            status:'B2B_INGRESS_ALIVE',source_enabled:true,crm_writes:0
          })};
        }
        if(url==='https://leadgen.serviseit.ru/integrations/serviceit/b2b-center/probe') {
          assert.strictEqual(config.method,'post');
          const payload=JSON.parse(config.payload);
          const expected='sha256='+crypto.createHmac('sha256',SECRET)
            .update('v1|b2b-probe|'+payload.timestamp+'|'+payload.nonce).digest('hex');
          assert.strictEqual(payload.signature,expected);
          return {getResponseCode:()=>200,getContentText:()=>JSON.stringify({
            status:'B2B_VPS_SIGNED_PROBE_PASS',crm_writes:0
          })};
        }
        assert.strictEqual(url,
          'https://leadgen.serviseit.ru/integrations/serviceit/b2b-center/notice');
        assert.strictEqual(config.method,'post');
        assert.strictEqual(config.followRedirects,false);
        assert.strictEqual(config.validateHttpsCertificates,true);
        assert.strictEqual(config.muteHttpExceptions,true);
        const cp=JSON.parse(config.payload);
        assert.deepStrictEqual(Object.keys(cp).sort(),
          ['timestamp','payload_json','signature'].sort());
        const expected='sha256='+crypto.createHmac('sha256',SECRET)
          .update(cp.timestamp+'.'+cp.payload_json).digest('hex');
        assert.strictEqual(cp.signature,expected);
        const payload=JSON.parse(cp.payload_json);
        assert.deepStrictEqual(Object.keys(payload).sort(),
          ['dispatch_id','email_message_id','mailbox','notice','version'].sort());
        assert.strictEqual(payload.mailbox,EMAIL);
        assert.strictEqual(payload.email_message_id,
          payload.notice.tender_id === '4673595' ? '1a1c44a89c79ccd6' : MID);
        assert(['1234567','1234568','4673595'].includes(payload.notice.tender_id));
        assert.strictEqual(payload.notice.source_url,
          payload.notice.tender_id === '4673595'
            ? 'https://www.b2b-center.ru/market/okazanie-uslug-po-predostavleniiu-it-spetsialistov/tender-4673595/'
            : payload.notice.tender_id === '1234567'
              ? 'https://www.b2b-center.ru/market/pravovoe-soprovozhdenie/tender-1234567/'
              : 'https://www.b2b-center.ru/market/podderzhka-serverov/tender-1234568/');
        assert.strictEqual(payload.notice.buyer_inn,null);
        assert.strictEqual(payload.notice.contact_approved,false);
        assert(!cp.payload_json.includes('raw_b64url'));
        assert(!cp.payload_json.includes('Актуально до'));
        assert(!cp.payload_json.includes('phone'));
        calls.dispatch.push(payload);
        return {getResponseCode:()=>responseCode,getContentText:()=>''};
      }
    },
    console:{log:value=>calls.logs.push(String(value))}
  };
  vm.createContext(context);
  vm.runInContext(source,context,{timeout:5000});
  return {
    context,properties,calls,
    setAccount:value=>{account=value;},
    setMessage:value=>{currentMessage=value;mailboxMessages=null;},
    setResponseCode:value=>{responseCode=value;}
  };
}

function test(name,fn) {
  fn();
  process.stdout.write('PASS '+name+'\n');
}

test('Apps Script manifest grants only Gmail read, outbound HTTPS and time triggers',()=>{
  const manifest=JSON.parse(fs.readFileSync(__dirname + '/../appsscript.json','utf8'));
  assert.strictEqual(manifest.runtimeVersion,'V8');
  assert.deepStrictEqual(manifest.oauthScopes.sort(),[
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/script.external_request',
    'https://www.googleapis.com/auth/script.scriptapp'
  ].sort());
  assert.strictEqual(manifest.dependencies.enabledAdvancedServices[0].serviceId,'gmail');
});

test('read-only Gmail preflight never dispatches or writes CRM',()=>{
  const e=environment();
  const result=e.context.checkB2BCenter();
  assert.strictEqual(result.mailbox_verified,true);
  assert.strictEqual(result.external_transmission,0);
  assert.strictEqual(result.crm_writes,0);
  assert.strictEqual(e.calls.dispatch.length,0);
  assert.strictEqual(e.calls.get,0);
});

test('signed minimal tender dispatch is accepted once',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'}});
  const result=e.context.pollAndDispatchB2B();
  assert.strictEqual(result.dispatched,1);
  assert.strictEqual(result.crm_writes,0);
  assert.strictEqual(e.calls.dispatch.length,1);
  assert.strictEqual(e.properties.SERVICEIT_SOURCE_1234567,'DISPATCHED:synthetic-dispatch-1');
  assert.strictEqual(e.properties['SERVICEIT_EMAIL_'+MID],'DONE');
  e.context.pollAndDispatchB2B();
  assert.strictEqual(e.calls.dispatch.length,1);
});

test('two IT tenders from one email are delivered automatically in a bounded VPS batch',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'}});
  e.setMessage(message(MID,{body:[
    'Организатор закупки: АО "СО ЕЭС"',
    'Предмет закупки: Услуги технической поддержки IT-инфраструктуры и серверов',
    'https://www.b2b-center.ru/market/pravovoe-soprovozhdenie/tender-1234567/',
    'Организатор закупки: ООО "Пример"',
    'Предмет закупки: Услуги поддержки серверов и сетевой инфраструктуры',
    'https://www.b2b-center.ru/market/podderzhka-serverov/tender-1234568/'
  ].join('\n')}));
  const first=e.context.pollAndDispatchB2B();
  assert.strictEqual(first.dispatched,2);
  assert.strictEqual(first.pending_tenders_deferred,0);
  assert.strictEqual(e.calls.dispatch.length,2);
  assert.strictEqual(e.properties['SERVICEIT_EMAIL_'+MID],'DONE');
  assert.strictEqual(e.calls.dispatch[0].notice.tender_id,'1234567');
  assert.strictEqual(e.calls.dispatch[1].notice.tender_id,'1234568');
  e.context.pollAndDispatchB2B();
  assert.strictEqual(e.calls.dispatch.length,2);
});

test('existing tender 4665948 and retired tender 4661617 are never recreated',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'}});
  e.setMessage(message(MID,{body:[
    'Организатор закупки: АО "СО ЕЭС"',
    'Предмет закупки: Услуги технической поддержки IT-инфраструктуры',
    'https://www.b2b-center.ru/market/a/tender-4665948/'
  ].join('\n')}));
  e.context.installPollingTrigger();
  assert.strictEqual(e.properties.SERVICEIT_SOURCE_4665948,'EXISTS_DO_NOT_RECREATE');
  assert.strictEqual(e.properties.SERVICEIT_SOURCE_4661617,'RETIRED_DO_NOT_RECREATE');
  e.context.pollAndDispatchB2B();
  assert.strictEqual(e.calls.dispatch.length,0);
});

test('HTML-only B2B-Center bulletin parses a database-maintenance tender safely',()=>{
  const e=environment();
  const html=[
    '<a href="https://www.b2b-center.ru/market/pravo-zakliucheniia-dogovora/tender-1234568/?utm_source=mail">Конкурс № 1234568</a>',
    '<div>Организатор: <a href="https://www.b2b-center.ru/firms/ao/1/">АО "СО ЕЭС"</a></div>',
    '<div>Предмет&nbsp;договора:&nbsp;Право заключения договора оказания услуг по техническому обслуживанию базы данных</div>'
  ].join('');
  const msg=message(MID);
  msg.payload.mimeType='text/html';
  msg.payload.body.data=b64url(html);
  e.setMessage(msg);
  const parsed=e.context.parseEmailMessage_(msg,MID);
  const debugText=e.context.messageText_(msg);
  const debugUrls=debugText.match(/https:\/\/[^\s<>'"\[\]]{1,2000}/ig)||[];
  assert.strictEqual(parsed.observations.length,1, JSON.stringify({
    text:debugText,
    title:e.context.getField_(debugText,'Предмет договора'),
    organizer:e.context.getField_(debugText,'Организатор'),
    cleaned:debugUrls.map(x=>e.context.cleanTenderUrl_(x)),
    services:e.context.classifyServices_(e.context.getField_(debugText,'Предмет договора')),
    parsed:parsed
  }));
  assert.strictEqual(parsed.observations[0].notice.tender_id,'1234568');
  assert.strictEqual(parsed.observations[0].notice.organizer,'АО "СО ЕЭС"');
  assert.strictEqual(parsed.observations[0].notice.subject,
    'Право заключения договора оказания услуг по техническому обслуживанию базы данных');
  assert.deepStrictEqual(Array.from(parsed.observations[0].services),['IT_SUPPORT']);
  assert.strictEqual(parsed.observations[0].notice.qualified_lead,false);
});

test('wrong Gmail mailbox is blocked before message reads or dispatch',()=>{
  const e=environment({account:'wrong@example.org',properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'}});
  assert.throws(()=>e.context.pollAndDispatchB2B(),/SERVICEIT_GMAIL_IDENTITY_BLOCKED/);
  assert.strictEqual(e.calls.get,0);
  assert.strictEqual(e.calls.dispatch.length,0);
});

test('spoofed sender domain is rejected and not dispatched',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'},
    message:message(MID,{from:'marketplace@b2b-center.ru.evil.test'})});
  const result=e.context.pollAndDispatchB2B();
  assert.strictEqual(result.review_required,1);
  assert.strictEqual(e.calls.dispatch.length,0);
});

test('spoofed Gmail authentication result cannot dispatch',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'},
    message:message(MID,{auth:'mx.google.com; dkim=fail; dmarc=fail'})});
  const result=e.context.pollAndDispatchB2B();
  assert.strictEqual(result.dispatched,0);
  assert.strictEqual(result.review_required,1);
  assert.strictEqual(e.calls.dispatch.length,0);
});

test('non-official source URL is rejected and not dispatched',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'},
    message:message(MID,{body:[
      'Организатор закупки: АО "СО ЕЭС"',
      'Предмет закупки: Услуги технической поддержки IT-инфраструктуры',
      'https://b2b-center.ru.evil.test/market/a/tender-1234567/'
    ].join('\n')})});
  const result=e.context.pollAndDispatchB2B();
  assert.strictEqual(result.dispatched,0);
  assert.strictEqual(e.calls.dispatch.length,0);
});

test('non-IT tender is ignored without dispatch',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'},
    message:message(MID,{body:[
      'Организатор закупки: АО "СО ЕЭС"',
      'Предмет закупки: Поставка офисной мебели',
      'https://www.b2b-center.ru/market/a/tender-1234567/'
    ].join('\n')})});
  const result=e.context.pollAndDispatchB2B();
  assert.strictEqual(result.dispatched,0);
  assert.strictEqual(e.properties['SERVICEIT_EMAIL_'+MID],'NO_MATCH');
});

test('baseline email older than cutover is not dispatched',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'},
    message:message(MID,{internalDate:CUTOVER-1000})});
  e.context.pollAndDispatchB2B();
  assert.strictEqual(e.calls.dispatch.length,0);
  assert.strictEqual(e.properties['SERVICEIT_EMAIL_'+MID],'BASELINE');
});

test('VPS 503 fences source and never blindly retries',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'},responseCode:503});
  const result=e.context.pollAndDispatchB2B();
  assert.strictEqual(result.review_required,0);
  assert.strictEqual(result.uncertain_dispatches,1);
  assert.strictEqual(e.calls.dispatch.length,1);
  assert.match(e.properties.SERVICEIT_SOURCE_1234567,/DISPATCH_UNCERTAIN/);
  e.setResponseCode(202);
  e.context.pollAndDispatchB2B();
  assert.strictEqual(e.calls.dispatch.length,1);
});

test('trigger install is idempotent and seeds known source IDs',()=>{
  const e=environment();
  e.context.installPollingTrigger();
  e.context.installPollingTrigger();
  assert.strictEqual(e.calls.triggers.length,1);
  assert.strictEqual(e.properties.SERVICEIT_AUTOMATION_ENABLED,'YES');
  assert.strictEqual(e.properties.SERVICEIT_SOURCE_4665948,'EXISTS_DO_NOT_RECREATE');
  assert.strictEqual(e.properties.SERVICEIT_SOURCE_4661617,'RETIRED_DO_NOT_RECREATE');
  e.context.stopPollingTrigger();
  assert.strictEqual(e.calls.triggers.length,0);
  assert.strictEqual(e.properties.SERVICEIT_AUTOMATION_ENABLED,'NO');
});

test('direct VPS source does not require legacy GitHub PAT',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'}});
  delete e.context.SERVICEIT_PRIVATE_CONFIG.githubToken;
  const result=e.context.pollAndDispatchB2B();
  assert.strictEqual(result.dispatched,1);
  assert.strictEqual(e.calls.dispatch.length,1);
});

test('missing owner config cannot install a trigger',()=>{
  const e=environment();
  delete e.context.SERVICEIT_PRIVATE_CONFIG;
  assert.throws(()=>e.context.installPollingTrigger(),/SERVICEIT_PRIVATE_CONFIG_BLOCKED/);
  assert.strictEqual(e.calls.triggers.length,0);
});

test('dispatch payload never contains raw email body or private contact data',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'},
    message:message(MID,{body:BODY+'\nКонтакт: person@example.org, +7 999 000-00-00'})});
  e.context.pollAndDispatchB2B();
  assert.strictEqual(e.calls.dispatch.length,1);
  const notice=e.calls.dispatch[0].notice;
  assert.deepStrictEqual(Object.keys(notice).sort(),[
    'buyer_inn','contact_approved','email_message_id','fns_msp','organizer',
    'original_publication_verified','qualified_lead','region','schema',
    'source','source_url','subject','tender_id'
  ].sort());
  assert(!JSON.stringify(notice).includes('person@example.org'));
  assert(!JSON.stringify(notice).includes('+7 999'));
});

test('old GitHub route stays OFF until explicit VPS activation',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES',
    SERVICEIT_VPS_B2B_DIRECT_ENABLED:'NO'}});
  const result=e.context.pollAndDispatchB2B();
  assert.strictEqual(result.status,'SERVICEIT_VPS_B2B_SOURCE_DISABLED');
  assert.strictEqual(e.calls.dispatch.length,0);
  assert.strictEqual(e.calls.get,0);
});

test('one-time VPS activation creates fresh-cutover trigger',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'NO',
    SERVICEIT_VPS_B2B_DIRECT_ENABLED:'NO'}});
  const result=e.context.activateVpsB2BCenter();
  assert.strictEqual(result.status,'SERVICEIT_AUTOMATION_TRIGGER_INSTALLED');
  assert.strictEqual(e.calls.triggers.length,1);
  assert.strictEqual(e.properties.SERVICEIT_VPS_B2B_DIRECT_ENABLED,'YES');
  assert(Number(e.properties.SERVICEIT_VPS_B2B_ACTIVATION_MS) > CUTOVER);
  assert.strictEqual(e.calls.dispatch.length,0);
  // Existing mail is older than fresh activation; must NEVER backfill.
  e.context.pollAndDispatchB2B();
  assert.strictEqual(e.calls.dispatch.length,0);
});


test('existing standalone Gmail V8 project can use owner-approved Script Properties HMAC',()=>{
  const e=environment({properties:{
    SERVICEIT_AUTOMATION_ENABLED:'YES',
    SERVICEIT_B2B_HMAC_SECRET:SECRET,
    SERVICEIT_B2B_OWNER_APPROVAL:'YES'
  }});
  delete e.context.SERVICEIT_PRIVATE_CONFIG;
  const result=e.context.pollAndDispatchB2B();
  assert.strictEqual(result.dispatched,1);
  assert.strictEqual(e.calls.dispatch.length,1);
});

test('standalone V8 refuses missing owner approval or invalid HMAC',()=>{
  const e=environment({properties:{
    SERVICEIT_AUTOMATION_ENABLED:'YES',
    SERVICEIT_B2B_HMAC_SECRET:SECRET
  }});
  delete e.context.SERVICEIT_PRIVATE_CONFIG;
  assert.throws(()=>e.context.pollAndDispatchB2B(),
    /SERVICEIT_PRIVATE_CONFIG_BLOCKED/);
});


test('read-only diagnostic identifies quarantined valid email without any writes',()=>{
  const e=environment({properties:{
    SERVICEIT_AUTOMATION_ENABLED:'YES',
    ['SERVICEIT_EMAIL_'+MID]:'REVIEW_REQUIRED'
  }});
  const before=JSON.stringify(e.properties);
  const result=e.context.diagnoseB2BCenterReadOnly();
  assert.strictEqual(result.status,'SERVICEIT_B2B_REVIEW_DIAGNOSTIC');
  assert.strictEqual(result.flagged_messages,1);
  assert.strictEqual(result.inspected_messages,1);
  assert.strictEqual(result.samples[0].phase,'PARSED');
  assert.strictEqual(result.samples[0].recognized_tenders,1);
  assert.strictEqual(result.vps_posts,0);
  assert.strictEqual(result.crm_writes,0);
  assert.strictEqual(e.calls.get,1);
  assert.strictEqual(e.calls.dispatch.length,0);
  assert.strictEqual(JSON.stringify(e.properties),before);
  assert(!JSON.stringify(result).includes(MID));
  assert(!JSON.stringify(result).includes(SECRET));
});

test('read-only diagnostic reports safe error code and preserves REVIEW_REQUIRED',()=>{
  const e=environment({properties:{
    ['SERVICEIT_EMAIL_'+MID]:'REVIEW_REQUIRED'
  },message:message(MID,{auth:'mx.google.com; dkim=fail; dmarc=fail'})});
  const before=JSON.stringify(e.properties);
  const result=e.context.diagnoseB2BCenterReadOnly();
  assert.strictEqual(result.samples[0].phase,'PARSE');
  assert.strictEqual(result.samples[0].code,'SERVICEIT_GMAIL_DKIM_DMARC_NOT_VERIFIED');
  assert.strictEqual(e.calls.dispatch.length,0);
  assert.strictEqual(JSON.stringify(e.properties),before);
  assert(!JSON.stringify(result).includes(MID));
});


test('realistic Russian quoted-printable HTML via Gmail raw fallback is parsed and sent',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'}});
  const html=[
    '<a href="https://www.b2b-center.ru/market/pravovoe-soprovozhdenie/tender-1234567/?utm_source=mail">Запрос предложений № 1234567</a>',
    '<div>Организатор:</div><div>ООО "ГЛОБУС-ИТ"</div>',
    '<div>Предмет договора: Приобретение лицензий 1С</div>'
  ].join('\r\n');
  const qp=[...Buffer.from(html,'utf8')].map(v=>'='+v.toString(16).toUpperCase().padStart(2,'0')).join('');
  const mime='From: B2B-Center <marketplace@b2b-center.ru>\r\nContent-Type: text/html; charset=utf-8\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n'+qp;
  const msg=message();
  msg.payload={mimeType:'text/html',headers:msg.payload.headers,body:{size:0}};
  msg._raw=Buffer.from(mime).toString('base64url');
  e.setMessage(msg);
  const parsed=e.context.parseEmailMessage_(msg,MID);
  assert.strictEqual(parsed.observations.length,1);
  assert.deepStrictEqual(Array.from(parsed.observations[0].services),['1C']);
  assert.strictEqual(parsed.observations[0].notice.tender_id,'1234567');
  assert.strictEqual(e.context.pollAndDispatchB2B().dispatched,1);
  assert.strictEqual(e.calls.dispatch.length,1);
});

test('read-only quarantine diagnostic uses raw fallback and does not release review',()=>{
  const msg=message();
  const html='<a href="https://www.b2b-center.ru/market/servers/tender-1234567/">Запрос цен № 1234567</a>'+
    '<div>Организатор: ООО "ГЛОБУС-ИТ"</div><div>Предмет договора: Закупка серверов</div>';
  msg.payload={mimeType:'text/html',headers:msg.payload.headers,body:{}};
  msg._raw=Buffer.from('Content-Type: text/html; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n'+html).toString('base64url');
  const e=environment({message:msg,properties:{['SERVICEIT_EMAIL_'+MID]:'REVIEW_REQUIRED'}});
  const before=JSON.stringify(e.properties);
  const result=e.context.diagnoseB2BCenterReadOnly();
  assert.strictEqual(result.samples[0].phase,'PARSED');
  assert.strictEqual(result.samples[0].recognized_tenders,1);
  assert.strictEqual(e.calls.dispatch.length,0);
  assert.strictEqual(JSON.stringify(e.properties),before);
});

test('unexpected raw multipart input stays quarantined without external writes',()=>{
  const msg=message();
  msg.payload={mimeType:'text/html',headers:msg.payload.headers,body:{}};
  msg._raw=Buffer.from('Content-Type: multipart/mixed; boundary=x\r\n\r\nx').toString('base64url');
  const e=environment({message:msg,properties:{['SERVICEIT_EMAIL_'+MID]:'REVIEW_REQUIRED'}});
  const result=e.context.diagnoseB2BCenterReadOnly();
  assert.strictEqual(result.samples[0].code,'SERVICEIT_EMAIL_RAW_MIME_UNSUPPORTED');
  assert.strictEqual(e.properties['SERVICEIT_EMAIL_'+MID],'REVIEW_REQUIRED');
  assert.strictEqual(e.calls.dispatch.length,0);
});

test('owner exact Nov10 recovery only releases two reviewed emails with zero VPS POSTs',()=>{
  const ids=['1a1c44a89c79ccd6','1a1c449c537d5613'];
  const e=environment({messages:[
    message(ids[0],{body:BODY,internalDate:CUTOVER+1000}),
    message(ids[1],{body:BODY,internalDate:CUTOVER+1000})
  ],properties:{
    SERVICEIT_AUTOMATION_ENABLED:'YES',
    ['SERVICEIT_EMAIL_'+ids[0]]:'REVIEW_REQUIRED',
    ['SERVICEIT_EMAIL_'+ids[1]]:'REVIEW_REQUIRED',
    ['SERVICEIT_EMAIL_'+MID]:'REVIEW_REQUIRED'
  }});
  const result=e.context.recoverNov10B2BCenterAfterFix();
  assert.strictEqual(result.messages_released,2);
  assert.strictEqual(result.vps_posts_during_recovery,0);
  assert.strictEqual(result.crm_writes_during_recovery,0);
  assert.strictEqual(e.properties['SERVICEIT_EMAIL_'+ids[0]],'PENDING');
  assert.strictEqual(e.properties['SERVICEIT_EMAIL_'+ids[1]],'PENDING');
  assert.strictEqual(e.properties['SERVICEIT_EMAIL_'+MID],'REVIEW_REQUIRED');
  assert.strictEqual(e.calls.dispatch.length,0);
});

test('recovery blocks existing uncertain source receipt without changing any state',()=>{
  const ids=['1a1c44a89c79ccd6','1a1c449c537d5613'];
  const e=environment({messages:[
    message(ids[0],{body:BODY,internalDate:CUTOVER+1000}),
    message(ids[1],{body:BODY,internalDate:CUTOVER+1000})
  ],properties:{
    SERVICEIT_AUTOMATION_ENABLED:'YES',
    ['SERVICEIT_EMAIL_'+ids[0]]:'REVIEW_REQUIRED',
    ['SERVICEIT_EMAIL_'+ids[1]]:'REVIEW_REQUIRED',
    SERVICEIT_SOURCE_1234567:'DISPATCH_UNCERTAIN:do-not-retry'
  }});
  assert.throws(()=>e.context.recoverNov10B2BCenterAfterFix(),
    /SERVICEIT_B2B_RECOVERY_EXISTING_RECEIPT/);
  assert.strictEqual(e.properties['SERVICEIT_EMAIL_'+ids[0]],'REVIEW_REQUIRED');
  assert.strictEqual(e.calls.dispatch.length,0);
});

test('client service relevance matches VPS: 1C, servers, explicit IT staffing yes; UPS no',()=>{
  const e=environment();
  assert(e.context.classifyServices_('Приобретение лицензий 1С').includes('1C'));
  assert(e.context.classifyServices_('Закупка серверов ЗОКИИ').includes('SERVERS'));
  assert(e.context.classifyServices_('Оказание услуг по предоставлению ИТ-специалистов').includes('IT_STAFFING'));
  assert.strictEqual(e.context.classifyServices_('Источники бесперебойного питания').length,0);
});


test('Gmail advanced raw omission uses same-provider authenticated read-only REST fallback',()=>{
  const html='<a href="https://www.b2b-center.ru/market/pravovoe-soprovozhdenie/tender-1234567/">Запрос цен № 1234567</a>'+
    '<div>Организатор: ООО "Пример"</div><div>Предмет договора: Лицензии 1С</div>';
  const msg=message();
  msg.payload={mimeType:'text/html',headers:msg.payload.headers,body:{}};
  msg._raw=Buffer.from('Content-Type: text/html; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n'+html).toString('base64url');
  msg._advancedRawMissing=true;
  const e=environment({message:msg,properties:{
    ['SERVICEIT_EMAIL_'+MID]:'REVIEW_REQUIRED'
  }});
  const saved=JSON.stringify(e.properties);
  const result=e.context.diagnoseB2BCenterReadOnly();
  assert.strictEqual(result.samples[0].phase,'PARSED');
  assert.strictEqual(result.samples[0].recognized_tenders,1);
  assert.strictEqual(e.calls.rest,1);
  assert.strictEqual(e.calls.dispatch.length,0);
  assert.strictEqual(JSON.stringify(e.properties),saved);
});

test('Gmail direct REST 403 is quarantined with safe code and no side effects',()=>{
  const msg=message();
  msg.payload={mimeType:'text/html',headers:msg.payload.headers,body:{}};
  msg._advancedRawMissing=true;
  const e=environment({message:msg,restHttpCode:403,properties:{
    ['SERVICEIT_EMAIL_'+MID]:'REVIEW_REQUIRED'
  }});
  const before=JSON.stringify(e.properties);
  const result=e.context.diagnoseB2BCenterReadOnly();
  assert.strictEqual(result.samples[0].code,'SERVICEIT_EMAIL_RAW_REST_HTTP_403');
  assert.strictEqual(e.calls.rest,1);
  assert.strictEqual(e.calls.dispatch.length,0);
  assert.strictEqual(JSON.stringify(e.properties),before);
});


test('B2B IT-staffing tender subject is service fit; generic vacancy is not',()=>{
  const e=environment();
  assert.deepStrictEqual(Array.from(
    e.context.classifyServices_('Оказание услуг по предоставлению ИТ-специалистов')),
    ['IT_STAFFING']);
  assert.deepStrictEqual(Array.from(
    e.context.classifyServices_('Привлечение IT-специалистов для проектов')),
    ['IT_STAFFING']);
  assert.strictEqual(e.context.classifyServices_('Вакансия IT-специалиста').length,0);
  assert.strictEqual(e.context.classifyServices_('Предоставление персонала для клининга').length,0);
});

test('owner-only exact tender 4673595 sends one notice and never repeats',()=>{
  const id='1a1c44a89c79ccd6';
  const msg=message(id,{body:[
    'Организатор закупки: ООО "ГЛОБУС-ИТ"',
    'Предмет закупки: Оказание услуг по предоставлению ИТ-специалистов',
    'https://www.b2b-center.ru/market/okazanie-uslug-po-predostavleniiu-it-spetsialistov/tender-4673595/?utm_source=mail'
  ].join('\n'),internalDate:CUTOVER+1000});
  const e=environment({messages:[msg],properties:{
    SERVICEIT_AUTOMATION_ENABLED:'YES',
    ['SERVICEIT_EMAIL_'+id]:'DONE'
  }});
  assert.strictEqual(e.context.dispatchB2B4673595Once().status,
    'SERVICEIT_B2B_STAFFING_NOTICE_ACCEPTED');
  assert.strictEqual(e.calls.dispatch.length,1);
  assert.strictEqual(e.calls.dispatch[0].notice.tender_id,'4673595');
  assert.strictEqual(e.calls.dispatch[0].notice.qualified_lead,false);
  assert.match(e.properties.SERVICEIT_SOURCE_4673595,/^DISPATCHED:/);
  assert.strictEqual(e.context.dispatchB2B4673595Once().status,
    'SERVICEIT_B2B_STAFFING_ALREADY_RESERVED_OR_HANDLED');
  assert.strictEqual(e.calls.dispatch.length,1);
});

test('ambiguous source receipt for IT staffing prevents any replay',()=>{
  const id='1a1c44a89c79ccd6';
  const msg=message(id,{body:[
    'Организатор закупки: ООО "ГЛОБУС-ИТ"',
    'Предмет закупки: Оказание услуг по предоставлению ИТ-специалистов',
    'https://www.b2b-center.ru/market/okazanie-uslug-po-predostavleniiu-it-spetsialistov/tender-4673595/'
  ].join('\n'),internalDate:CUTOVER+1000});
  const e=environment({messages:[msg],responseCode:503,properties:{
    SERVICEIT_AUTOMATION_ENABLED:'YES'
  }});
  assert.strictEqual(e.context.dispatchB2B4673595Once().status,
    'SERVICEIT_B2B_STAFFING_DISPATCH_UNCERTAIN_NO_RETRY');
  assert.match(e.properties.SERVICEIT_SOURCE_4673595,/^DISPATCH_UNCERTAIN:/);
  assert.strictEqual(e.context.dispatchB2B4673595Once().status,
    'SERVICEIT_B2B_STAFFING_ALREADY_RESERVED_OR_HANDLED');
  assert.strictEqual(e.calls.dispatch.length,1);
});

process.stdout.write('ALL_APPS_SCRIPT_TESTS_PASS\n');

{
  const html = [
    '<strong>Организатор закупки</strong>: АО &quot;ПРИМЕР&quot;<br>',
    '<strong>Предмет закупки</strong>:<br>Услуги ТП для серверов Sparc M8-8<br>',
    '<a href="https://www.b2b-center.ru/app/market-next/uslugi-tp/tender-4677837/?utm_source=procedure_invite">',
    'Посмотреть процедуру №4677837 на площадке</a>'
  ].join('');
  const actual = message();
  actual.payload.mimeType = 'text/html';
  actual.payload.body.data = b64url(html);
  const e = environment({message: actual, properties: {
    SERVICEIT_AUTOMATION_ENABLED: 'YES',
    ['SERVICEIT_EMAIL_' + MID]: 'NO_MATCH'
  }});
  const parsed = e.context.parseEmailMessage_(actual, MID);
  assert.strictEqual(parsed.observations.length, 1);
  assert.strictEqual(parsed.observations[0].notice.tender_id, '4677837');
  const recovery = e.context.recoverB2B4677837AfterParserFix();
  assert.strictEqual(recovery.status, 'SERVICEIT_B2B_EXACT_RECOVERY_QUEUED');
  assert.strictEqual(e.properties['SERVICEIT_EMAIL_' + MID], 'PENDING');
  assert.strictEqual(e.calls.dispatch.length, 0);
}
