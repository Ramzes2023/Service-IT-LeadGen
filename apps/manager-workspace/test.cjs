const assert=require('node:assert/strict');const {cents,splitMoney,timelineLayout}=require('./app.js');
assert.equal(cents(''),null);assert.equal(cents('0'),0);assert.equal(cents('1,25'),125);
for(const bad of ['-1','1e4','NaN','1.234','10000000000'])assert.throws(()=>cents(bad));
assert.equal(splitMoney(null,0),null);
assert.deepEqual(splitMoney(10000000,4000000),{profit:6000000,developer:900000,manager:600000,company:4500000});
assert.deepEqual(splitMoney(0,100),{profit:-100,developer:0,manager:0,company:-100});
for(let p=0;p<10000;p++){let x=splitMoney(p,0);assert.equal(x.profit,x.developer+x.manager+x.company);assert.ok(x.company>=0);}
const l=timelineLayout({id:39,UF_CRM_13_SVCIT_FIN_REVENUE:'100',UF_CRM_13_SVCIT_FIN_COSTS:'0'},'finance');assert.equal(l.footer.buttons.edit.action.type,'openRestApp');assert.equal(l.footer.buttons.edit.action.actionParams.cardId,'39');assert.equal(Object.keys(l.body.blocks).length,6);
console.log('APP_CALCULATION_AND_LAYOUT_TESTS=PASS');
