const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
async function boot(path='/setup', response={ok:true,status:201}, hash='#ephemeral-bootstrap-fixture') {
  const elements = Object.fromEntries(['auth-form','username','password','auth-error','auth-submit','auth-title','auth-description'].map(id=>[id,{value:id==='password'?'ephemeral fixture passphrase':'owner',textContent:'',hidden:false,addEventListener(name,fn){this[name]=fn;}}]));
  const calls=[], redirects=[];
  const location={pathname:path,hash,replace:v=>redirects.push(v)};
  const history={replaceState(...args){calls.push(['history',...args]);}};
  vm.runInNewContext(fs.readFileSync(require('node:path').resolve(__dirname, '../../web')+'/auth.js','utf8'), {document:{getElementById:id=>elements[id]}, location, history, fetch:async(url,options)=>{calls.push([url,options]); return response;}});
  await elements['auth-form'].submit({preventDefault(){}});
  return {elements,calls,redirects};
}
test('setup consumes fragment in memory, clears URL, and posts JSON without persistent storage', async()=>{
  const app=await boot();
  assert.equal(app.calls[0][0],'history');
  const [url, options]=app.calls.find(c=>c[0]==='/api/setup');
  assert.equal(url,'/api/setup'); assert.equal(options.method,'POST');
  assert.equal(JSON.parse(options.body).token,'ephemeral-bootstrap-fixture');
  assert.equal(app.redirects[0],'/login');
});
test('setup accepts the named token fragment used in delivered setup links', async()=>{
  const app=await boot('/setup',{ok:true,status:201},'#token=ephemeral-bootstrap-fixture');
  const options=app.calls.find(c=>c[0]==='/api/setup')[1];
  assert.equal(JSON.parse(options.body).token,'ephemeral-bootstrap-fixture');
});
test('login errors use text content and never redirect failed authentication',async()=>{
  const app=await boot('/login',{ok:false,status:401});
  assert.equal(app.redirects.length,0);
  assert.match(app.elements['auth-error'].textContent,/tidak cocok/);
  assert.equal(JSON.parse(app.calls.find(c=>c[0]==='/api/login')[1].body).token,undefined);
});
