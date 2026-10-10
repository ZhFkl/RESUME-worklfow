// Disposable stream_ui_server.py only; no model or production data.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const url='http://127.0.0.1:18802';
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',args:['--no-sandbox']});try{
 for(const width of [1440,390]){
 const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();page.setDefaultTimeout(15000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await context.request.post(url+'/auth/login',{headers:{'X-Resume-Client':'browser'},data:{identifier:'stream_test',password:'Stream-test-2026!'}});
 await page.goto(url+'/#workspace');await page.waitForFunction(()=>!document.querySelector('#send-button').disabled);
 await page.locator('#message-input').fill('timeline');await page.locator('#send-button').click();
 await page.waitForFunction(()=>document.querySelector('.streaming .message-body')?.textContent.includes('先保留这一段说明。'));
 await page.waitForFunction(()=>document.querySelector('.streaming .message-body')?.textContent.includes('后续'));
 const text=await page.locator('.streaming .message-body').innerText();assert(text.indexOf('先保留')<text.indexOf('读取所选模板'));assert(text.indexOf('读取所选模板')<text.indexOf('后续'));
 assert(await page.locator('.streaming').evaluate(el=>el.querySelector('.message-body').compareDocumentPosition(el.querySelector('.reply-progress'))&Node.DOCUMENT_POSITION_FOLLOWING));
 assert(!await page.locator('.streaming').innerText().then(s=>s.includes('正在回复')));
 await page.reload();await page.waitForFunction(()=>document.querySelector('.conversation').textContent.includes('后续正文正在追加。'));
 await page.waitForFunction(()=>!document.querySelector('#send-button').disabled);
 assert.equal(await page.locator('.reply-progress').count(),0);
 let body=await page.locator('.assistant').last().innerText();assert(body.includes('先保留这一段说明。'));assert(body.includes('读取所选模板'));assert.equal(body.split('后续正文正在追加。').length-1,1);
 await page.reload();await page.waitForFunction(()=>!document.querySelector('#send-button').disabled);assert.equal(await page.locator('.assistant').last().innerText(),body);
 await page.locator('#message-input').fill('fail');await page.locator('#send-button').click();await page.locator('.reply-retry').waitFor();
 assert((await page.locator('#conversation').innerText()).includes('未完成，已保留'));
 await page.reload();await page.locator('.reply-retry').waitFor();assert((await page.locator('#conversation').innerText()).includes('逐步输出'));
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:'/tmp/reply-timeline-'+width+'.png'});assert.deepEqual(errors,[]);await context.close();
 }console.log('PASS ordered multi-round text and operations, bottom status, refresh during/after generation, failed partial persistence, desktop/mobile');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
