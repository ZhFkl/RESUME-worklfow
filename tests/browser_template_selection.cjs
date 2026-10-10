const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const url='http://127.0.0.1:18802';
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
 try{
  for(const width of [1440,390]){
   const context=await browser.newContext({viewport:{width,height:900},hasTouch:width===390});
   const login=await context.request.post(url+'/auth/login',{headers:{'X-Resume-Client':'browser'},data:{identifier:'stream_test',password:'Stream-test-2026!'}});
   const auth=await login.json();assert.equal(login.status(),200);
   const headers={'X-Resume-Client':'browser','X-CSRF-Token':auth.csrf,'X-Resume-Account':auth.account.user_id};
   const catalog=await (await context.request.get(url+'/templates')).json();
   const cn=catalog.templates.find(t=>t.name==='经典中文'),en=catalog.templates.find(t=>t.name==='Classic English');
   await context.request.put(url+'/templates/selection',{headers,data:{template_id:cn.id}});
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(url+'/#templates');await page.waitForFunction(()=>!document.getElementById('send-button').disabled);
   let release;const gate=new Promise(r=>release=r);
   await page.route('**/templates/selection',async route=>{await gate;await route.continue();});
   await page.locator('[data-template-id="'+en.id+'"] [data-template-select]').click();
   assert(await page.locator('#send-button').isDisabled());
   await page.evaluate(()=>location.hash='workspace');release();
   await page.waitForFunction(()=>!document.getElementById('send-button').disabled&&document.getElementById('selected-template-name').textContent==='Classic English');
   await page.unroute('**/templates/selection');
   // Another device changes the global choice after this page has displayed English.
   await context.request.put(url+'/templates/selection',{headers,data:{template_id:cn.id}});
   let sent;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/messages'))sent=r.postDataJSON();});
   await page.locator('#message-input').fill('verify selected template');await page.locator('#send-button').click();
   await page.waitForFunction(()=>document.getElementById('selected-template-name').textContent==='Classic English（本次已锁定）');
   assert.equal(sent.template_id,en.id);
   const active=(await (await context.request.get(url+'/tasks/active')).json()).task;
   assert.equal(active.selected_template.id,en.id);assert(active.selected_template.sha256);
   await page.waitForFunction(()=>!document.getElementById('send-button').disabled,{},{timeout:15000});
   assert.deepEqual(errors,[]);await context.close();
  }
  console.log('PASS: desktop/mobile selection save lock, explicit visible template ID, second-device changes cannot replace task template.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
