const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const url='http://127.0.0.1:18800';
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',args:['--no-sandbox']});try{
 for(const width of [1440,390]){
 const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();page.setDefaultTimeout(15000);
 const name='undo'+width+Date.now(),email=name+'@example.com',headers={'X-Resume-Client':'browser'};
 await context.request.post(url+'/auth/email/send',{headers,data:{email}});
 const code=(await(await context.request.get(url+'/test/email-code?email='+email)).json()).code;
 const signup=await context.request.post(url+'/auth/register',{headers,data:{username:name,email,password:'Testing123!',code}});assert.equal(signup.status(),200);
 const csrf=(await signup.json()).csrf;
 await page.goto(url+'/#library');await page.waitForFunction(()=>!document.querySelector('#toggle-edit').disabled);
 await page.locator('#toggle-edit').click();const editor=page.locator('#markdown-editor');const original=await editor.inputValue();
 assert(await page.locator('#undo-materials').isDisabled());
 await editor.fill(original+'\n第一处修改');await page.waitForFunction(()=>document.querySelector('#save-state').textContent==='已保存到后端');
 await editor.fill(original+'\n第二处修改');await page.waitForFunction(()=>document.querySelector('#save-state').textContent==='已保存到后端');
 await page.locator('#undo-materials').click();assert.equal(await editor.inputValue(),original+'\n第一处修改');
 await editor.press('Control+z');assert.equal(await editor.inputValue(),original);
 await editor.press('Control+Shift+z');assert.equal(await editor.inputValue(),original+'\n第一处修改');
 await page.locator('#redo-materials').click();assert.equal(await editor.inputValue(),original+'\n第二处修改');
 await page.locator('#undo-materials').click();await editor.fill(original+'\n新的修改');assert(await page.locator('#redo-materials').isDisabled());
 await page.waitForFunction(()=>document.querySelector('#save-state').textContent==='已保存到后端');
 assert.equal((await(await context.request.get(url+'/materials')).json()).markdown,original+'\n新的修改');
 await page.locator('#toggle-edit').click();await page.locator('#toggle-edit').click();assert(!await page.locator('#undo-materials').isDisabled());
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'/tmp/material-undo-'+width+'.png'});
 // A newly loaded server version starts a fresh undo boundary.
 const material=await(await context.request.get(url+'/materials')).json();
 assert.equal((await context.request.put(url+'/materials',{headers:{...headers,'X-CSRF-Token':csrf},data:{markdown:original+'\n外部更新',revision:material.revision}})).status(),200);
 await page.goto(url+'/#workspace');await page.goto(url+'/#library');await page.waitForFunction(()=>document.querySelector('#markdown-editor').value.endsWith('外部更新'));
 assert(await page.locator('#undo-materials').isDisabled());await context.close();
 }console.log('PASS desktop/mobile undo/redo, keyboard, autosave retention, redo branching, external update reset');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
