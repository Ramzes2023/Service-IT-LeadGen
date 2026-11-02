/* Offline V8 tests — no Gmail/HTTP/Bitrix account access. node test_apps_script.cjs */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const crypto = require('crypto');

const source = fs.readFileSync(__dirname + '/../Code.gs', 'utf8');
const MID = '1a19b7630301bafb';
const EMAIL = 'ramzesisayev444@gmail.com';
const SECRET = 'SYNTHETIC-SERVICEIT-RECEIVER-HMAC-SECRET-0123456789';
const URL = 'https://procurement.example.org/api/v2/gmail/ingest';
const raw = Buffer.from(
  'From: marketplace@b2b-center.ru\r\n' +
  'To: ramzesisayev444@gmail.com\r\n' +
  'Subject: synthetic\r\n\r\n' +
  'Procurement 4661617'
).toString('base64url');

function environment(options = {}) {
  const properties = {
    SERVICEIT_RECEIVER_URL: URL,
    SERVICEIT_HMAC_SECRET: SECRET,
    SERVICEIT_SEND_ENABLED: 'YES'
  };
  Object.assign(properties, options.properties || {});
  let account = options.account || EMAIL;
  const calls = {listed:0,got:0,sent:0,log:[],triggers:[]};
  const cache = new Map();
  const gmail = {
    Users: {
      getProfile: () => ({emailAddress: account}),
      Messages: {
        list: (user, options) => {
          assert.strictEqual(user,'me');
          assert.match(options.q,/marketplace@b2b-center.ru/);
          calls.listed++; return {messages:[{id:MID}]};
        },
        get: (user,id,request) => {
          assert.strictEqual(request.format,'raw');
          // The environment-level option is the fake vendor payload. Do not
          // shadow it with the Gmail API request options (which contain format).
          calls.got++;
          return {id, raw:Object.prototype.hasOwnProperty.call(options,'raw')
            ? options.raw : raw};
        }
      }
    }
  };
  const context = {
    Gmail: gmail,
    Date,
    console: {log: s=>calls.log.push(String(s)),error:s=>calls.log.push(String(s))},
    PropertiesService: {getScriptProperties:()=>({
      getProperty:k=>properties[k],
      setProperty:(k,v)=>properties[k]=v
    })},
    CacheService: {getScriptCache:()=>({
      get:k=>cache.get(k),
      put:(k,v,ttl)=>{assert.strictEqual(ttl,21600);cache.set(k,v);}
    })},
    LockService: {getScriptLock:()=>({
      tryLock:()=>true,releaseLock:()=>{}
    })},
    ScriptApp: {
      getProjectTriggers:()=>calls.triggers,
      newTrigger:handler=>({
        timeBased:()=>({
          everyMinutes:num=>({
            create:()=>{assert.strictEqual(num,5);
              calls.triggers.push({getHandlerFunction:()=>handler});}
          })
        })
      }),
      deleteTrigger:trigger=>calls.triggers.splice(calls.triggers.indexOf(trigger),1)
    },
    Utilities: {
      Charset:{UTF_8: 'utf8'},
      computeHmacSha256Signature: (value,key)=>{
        return [...crypto.createHmac('sha256',key).update(value).digest()]
          .map(x=>x>127?x-256:x);
      }
    },
    UrlFetchApp: {fetch:(url,config)=>{
      calls.sent++;
      assert.strictEqual(url,URL);
      assert.strictEqual(config.followRedirects,false);
      assert.strictEqual(config.validateHttpsCertificates,true);
      assert.strictEqual(config.method,'post');
      const request = JSON.parse(config.payload);
      assert.strictEqual(request.mailbox,EMAIL);
      assert.strictEqual(request.raw_b64url,raw);
      assert.strictEqual(request.gmail_message_id,MID);
      assert.strictEqual(request.version,'serviceit-appsscript-v1');
      const ts=config.headers['X-ServiceIT-Timestamp'];
      const expected=crypto.createHmac('sha256',SECRET)
        .update(ts+'.'+config.payload).digest('hex');
      assert.strictEqual(config.headers['X-ServiceIT-Signature'],'sha256='+expected);
      return {
        getResponseCode:()=>options.status || 202,
        getContentText:()=>JSON.stringify({
          status:options.ack || 'INGESTED',gmail_message_id:MID,crm_writes:0})
      };
    }}
  };
  vm.createContext(context);
  vm.runInContext(source,context,{timeout:5000});
  return {context,properties,calls,setAccount:v=>account=v};
}

function test(name,callback) {
  callback();
  process.stdout.write('PASS '+name+'\n');
}

test('readonly preview never sends data',()=>{
  const e=environment();
  e.context.checkB2BCenter();
  assert.strictEqual(e.calls.sent,0);
  assert(e.calls.log.some(x=>x.includes('EXTERNAL_TRANSMISSION=0')));
});

test('correct receiver signature and durable acknowledgement',()=>{
  const e=environment();
  e.context.pollAndForwardB2B();
  assert.strictEqual(e.calls.sent,1);
  assert.strictEqual(e.calls.got,1);
  assert.match(e.properties.SERVICEIT_LAST_RESULT,/OK total=1 new=1/);
  e.context.pollAndForwardB2B();
  assert.strictEqual(e.calls.sent,1);
  assert.strictEqual(e.calls.got,1);
});

test('no transmission without opt-in',()=>{
  const e=environment(); delete e.properties.SERVICEIT_SEND_ENABLED;
  assert.throws(()=>e.context.pollAndForwardB2B(),/SERVICEIT_POLL_BLOCKED/);
  assert.strictEqual(e.calls.sent,0);
});

test('wrong Gmail identity never sends vendor email',()=>{
  const e=environment();e.setAccount('wrong@example.org');
  assert.throws(()=>e.context.pollAndForwardB2B(),/SERVICEIT_POLL_BLOCKED/);
  assert.strictEqual(e.calls.sent,0);
  assert.strictEqual(e.calls.got,0);
});

test('no unsafe endpoint is allowed',()=>{
  const e=environment();
  for (const u of ['http://procurement.example.org/api/v2/gmail/ingest',
                   'https://localhost/api/v2/gmail/ingest',
                   'https://procurement.example.org.evil.net/api/v2/gmail/ingest?x=1']) {
    e.properties.SERVICEIT_RECEIVER_URL=u;
    assert.throws(()=>e.context.pollAndForwardB2B(),/SERVICEIT_POLL_BLOCKED/);
  }
  assert.strictEqual(e.calls.sent,0);
});

test('single five-minute trigger and explicit stop',()=>{
  const e=environment();
  e.context.installPollingTrigger();
  e.context.installPollingTrigger();
  assert.strictEqual(e.calls.triggers.length,1);
  e.context.stopPollingTrigger();
  assert.strictEqual(e.calls.triggers.length,0);
  assert.strictEqual(e.properties.SERVICEIT_SEND_ENABLED,'NO');
});

test('unconfirmed server acknowledgement never caches Gmail ID',()=>{
  const e=environment({ack:'UNCONFIRMED'});
  assert.throws(()=>e.context.pollAndForwardB2B(),/SERVICEIT_POLL_BLOCKED/);
  assert.strictEqual(e.calls.sent,1);
  assert.strictEqual(e.calls.got,1);
  assert.throws(()=>e.context.pollAndForwardB2B(),/SERVICEIT_POLL_BLOCKED/);
  assert.strictEqual(e.calls.sent,2);
  assert.strictEqual(e.calls.got,2);
});

test('server 503 never records successful delivery',()=>{
  const e=environment({status:503});
  assert.throws(()=>e.context.pollAndForwardB2B(),/SERVICEIT_POLL_BLOCKED/);
  assert.strictEqual(e.calls.sent,1);
  assert.match(e.properties.SERVICEIT_LAST_RESULT,/BLOCKED/);
});

test('unapproved private-host endpoint cannot transmit',()=>{
  const e=environment({properties:{
    SERVICEIT_RECEIVER_URL:'https://localhost/api/v2/gmail/ingest'}});
  assert.throws(()=>e.context.pollAndForwardB2B(),/SERVICEIT_POLL_BLOCKED/);
  assert.strictEqual(e.calls.sent,0);
});

test('invalid Gmail base64url is blocked without transmission',()=>{
  const e=environment({raw:'INVALID!'});
  assert.throws(()=>e.context.pollAndForwardB2B(),/SERVICEIT_POLL_BLOCKED/);
  assert.strictEqual(e.calls.sent,0);
});

test('preview does not send even when receiver is enabled',()=>{
  const e=environment();
  e.context.checkB2BCenter();
  assert.strictEqual(e.calls.sent,0);
});
