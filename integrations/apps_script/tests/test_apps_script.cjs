/* Offline V8 tests. No Google account, real email, GitHub API or Bitrix writes. */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const crypto = require('crypto');

const source = fs.readFileSync(__dirname + '/../Code.gs', 'utf8');
const EMAIL = 'ramzesisayev444@gmail.com';
const SECRET = 'synthetic-serviceit-hmac-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ';
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
        {name:'Subject', value:'Новые закупки B2B-Center'}
      ],
      mimeType:'text/plain',
      body:{data:b64url(options.body === undefined ? BODY : options.body)}
    }
  };
}

function environment(options={}) {
  const properties = Object.assign({}, options.properties || {});
  const calls = {list:0,get:0,dispatch:[],logs:[],triggers:[]};
  let account = options.account || EMAIL;
  let currentMessage = options.message || message();
  let responseCode = options.responseCode || 204;
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
            return {messages:[{id:currentMessage.id}]};
          },
          get: (_user, id, args) => {
            assert.strictEqual(_user,'me');
            assert.strictEqual(id,currentMessage.id);
            assert.strictEqual(args.format,'full');
            calls.get++;
            return currentMessage;
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
      newBlob: bytes => ({getDataAsString:()=>Buffer.from(bytes).toString('utf8')}),
      computeHmacSha256Signature:(value,key)=>{
        return [...crypto.createHmac('sha256',key).update(value).digest()]
          .map(x=>x>127?x-256:x);
      },
      getUuid:()=> "synthetic-dispatch-" + (++uuid)
    },
    UrlFetchApp: {
      fetch:(url,config)=>{
        assert.strictEqual(url,'https://api.github.com/repos/Ramzes2023/Service-IT-LeadGen/dispatches');
        assert.strictEqual(config.method,'post');
        assert.strictEqual(config.followRedirects,false);
        assert.strictEqual(config.validateHttpsCertificates,true);
        assert.strictEqual(config.muteHttpExceptions,true);
        const req=JSON.parse(config.payload);
        assert.strictEqual(req.event_type,'serviceit_b2b_notice');
        const cp=req.client_payload;
        const expected='sha256='+crypto.createHmac('sha256',SECRET)
          .update(cp.timestamp+'.'+cp.payload_json).digest('hex');
        assert.strictEqual(cp.signature,expected);
        const payload=JSON.parse(cp.payload_json);
        assert.deepStrictEqual(Object.keys(payload).sort(),
          ['dispatch_id','email_message_id','mailbox','notice','version'].sort());
        assert.strictEqual(payload.mailbox,EMAIL);
        assert.strictEqual(payload.email_message_id,MID);
        assert.strictEqual(payload.notice.tender_id,'1234567');
        assert.strictEqual(payload.notice.source_url,
          'https://www.b2b-center.ru/market/pravovoe-soprovozhdenie/tender-1234567/');
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
    setMessage:value=>{currentMessage=value;},
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
  assert.strictEqual(parsed.observations.length,1, JSON.stringify({text:e.context.messageText_(msg),parsed:parsed}));
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

test('GitHub 503 fences source and never blindly retries',()=>{
  const e=environment({properties:{SERVICEIT_AUTOMATION_ENABLED:'YES'},responseCode:503});
  const result=e.context.pollAndDispatchB2B();
  assert.strictEqual(result.review_required,0);
  assert.strictEqual(result.uncertain_dispatches,1);
  assert.strictEqual(e.calls.dispatch.length,1);
  assert.match(e.properties.SERVICEIT_SOURCE_1234567,/DISPATCH_UNCERTAIN/);
  e.setResponseCode(204);
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

process.stdout.write('ALL_APPS_SCRIPT_TESTS_PASS\n');
