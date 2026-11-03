/* Offline sandboxed Apps Script bridge tests; no real external traffic. */
const fs = require("fs"), vm = require("vm"), crypto = require("crypto"), assert = require("assert");
const code = fs.readFileSync(__dirname + "/../Code.gs", "utf8");
const TOKEN = "SYNTHETIC-BITRIX-OUTGOING-APPLICATION-TOKEN-12345";
const PAT = "SYNTHETIC-GITHUB-TOKEN-NOT-REAL-1234";
const KEY = "SYNTHETIC-SERVICEIT-BRIDGE-HMAC-123456789012345678";
const P = "serviseit.bitrix24.ru";
function fixture(opts={}) {
  const state = {}, requestLog=[];
  let responses = opts.responses || [204], serial=0;
  const context = {
    SERVICEIT_BITRIX_BRIDGE_PRIVATE: {
      ownerApproved:"YES", enabled:"YES",
      outgoingApplicationToken:TOKEN,githubToken:PAT,hmacKey:KEY,
    },
    Utilities: {
      Charset:{UTF_8:"UTF-8"},DigestAlgorithm:{SHA_256:"SHA_256"},
      computeDigest:(_kind,text)=>[...crypto.createHash("sha256").update(text).digest()],
      computeHmacSha256Signature:(text,key)=>[...crypto.createHmac("sha256",key).update(text).digest()],
      getUuid:()=> "00000000-0000-4000-8000-"+String(++serial).padStart(12,"0")
    },
    PropertiesService:{getScriptProperties:()=>({
      getProperty:key=>state[key]||null,
      setProperty:(key,value)=>{state[key]=String(value);}
    })},
    LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},
    UrlFetchApp:{fetch:(url,args)=>{
      assert.strictEqual(url,"https://api.github.com/repos/Ramzes2023/Service-IT-LeadGen/dispatches");
      assert.strictEqual(args.method,"post");
      assert.strictEqual(args.followRedirects,false);
      assert.strictEqual(args.validateHttpsCertificates,true);
      const data=JSON.parse(args.payload);
      assert.strictEqual(data.event_type,"serviceit_bitrix_item_add_v1");
      const p=data.client_payload;
      assert.strictEqual(p.item_id,101);
      assert.strictEqual(p.entity_type_id,1062);
      assert.strictEqual(p.portal,P);
      const body="v1|"+p.portal+"|1062|"+p.item_id+"|"+p.timestamp+"|"+p.nonce;
      const digest="sha256="+crypto.createHmac("sha256",KEY).update(body).digest("hex");
      assert.strictEqual(p.signature,digest);
      assert(!args.payload.includes("ИНН"));
      assert(!args.payload.includes("Сигнал"));
      requestLog.push(p);
      return {getResponseCode:()=>responses.shift()||204};
    }},
    console:{log:()=>{},error:()=>{}},
    ContentService:{createTextOutput:s=>({body:s})}
  };
  vm.createContext(context);
  vm.runInContext(code,context,{timeout:4000});
  return {context,state,requests:requestLog};
}
function form(id=101, overrides={}) {
  const data={
    event:"ONCRMDYNAMICITEMADD",
    "auth[domain]":P,
    "auth[application_token]":TOKEN,
    "data[FIELDS][ID]":String(id),
    "data[FIELDS][ENTITY_TYPE_ID]":"1062",
    ...overrides
  };
  return {parameters:Object.fromEntries(Object.entries(data).map(([k,v])=>[k,[v]])),parameter:data};
}
function test(name,fn){fn();process.stdout.write("PASS "+name+"\n");}
test("one authenticated callback forwards only an exact signed item ID",()=>{
  const f=fixture();
  assert.strictEqual(f.context.processBitrixEvent_(form(101)),"DISPATCHED_ACKED");
  assert.strictEqual(f.requests.length,1);
  assert.strictEqual(f.state.SERVICEIT_BITRIX_ID_101,"DISPATCHED_ACKED");
});
test("duplicate callback never creates another GitHub run",()=>{
  const f=fixture();
  f.context.processBitrixEvent_(form(101));
  assert.strictEqual(f.context.processBitrixEvent_(form(101)),"DUPLICATE_NO_RETRY");
  assert.strictEqual(f.requests.length,1);
});
test("fake portal or outgoing application token rejected",()=>{
  const f=fixture();
  assert.throws(()=>f.context.processBitrixEvent_(form(101,{"auth[domain]":"evil.bitrix24.ru"})),/INVALID/);
  assert.throws(()=>f.context.processBitrixEvent_(form(101,{"auth[application_token]":"SYNTHETIC-WRONG-TOKEN-OF-SAME-LENGTH-12345"})),/INVALID/);
  assert.strictEqual(f.requests.length,0);
});
test("other processes ignored",()=>{
  const f=fixture();
  assert.strictEqual(f.context.processBitrixEvent_(form(101,{"data[FIELDS][ENTITY_TYPE_ID]":"1044"})),"IGNORED_OTHER_PROCESS");
  assert.strictEqual(f.requests.length,0);
});
test("historical cards 3 and 7 ignored",()=>{
  const f=fixture();
  assert.strictEqual(f.context.processBitrixEvent_(form(7)),"IGNORED_HISTORICAL_OR_INVALID");
  assert.strictEqual(f.requests.length,0);
});
test("ambiguous remote reply must fence event forever",()=>{
  const f=fixture({responses:[503,204]});
  assert.throws(()=>f.context.processBitrixEvent_(form(101)),/DISPATCH_UNCERTAIN/);
  assert.strictEqual(f.state.SERVICEIT_BITRIX_ID_101,"DISPATCH_UNCERTAIN_NO_RETRY");
  assert.strictEqual(f.context.processBitrixEvent_(form(101)),"DUPLICATE_NO_RETRY");
  assert.strictEqual(f.requests.length,1);
});
test("real JSON event and form event accepted",()=>{
  const f=fixture();
  const e={postData:{type:"application/json",contents:JSON.stringify({
    event:"ONCRMDYNAMICITEMADD",auth:{domain:P,application_token:TOKEN},
    data:{FIELDS:{ID:"101",ENTITY_TYPE_ID:"1062"}}
  })}};
  assert.strictEqual(f.context.processBitrixEvent_(e),"DISPATCHED_ACKED");
  assert.strictEqual(f.requests.length,1);
});
test("public endpoint returns generic result without leaking item or auth",()=>{
  const f=fixture();
  assert.strictEqual(f.context.doPost(form(101)).body,"SERVICEIT_EVENT_RECEIVED");
  assert.strictEqual(f.context.doGet({}).body,"SERVICEIT_BITRIX_BRIDGE");
  assert.strictEqual(f.requests.length,1);
});
test("source contains no CRM read/write or B2B/Gmail collectors",()=>{
  assert(!code.includes("crm.item.list"));
  assert(!code.includes("crm.item.update"));
  assert(!code.includes("Gmail.Users"));
});
process.stdout.write("ALL_SERVICEIT_BITRIX_BRIDGE_TESTS_PASS\n");
