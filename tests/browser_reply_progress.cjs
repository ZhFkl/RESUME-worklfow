const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const url='http://127.0.0.1:18802';
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
 try{
  for(const width of [1440,390]){
   const context=await browser.newContext({viewport:{width,height:900},hasTouch:width===390});
   assert.equal((await context.request.post(url+'/auth/login',{headers:{'X-Resume-Client':'browser'},data:{identifier:'stream_test',password:'Stream-test-2026!'}})).status(),200);
   const page=await context.newPage(),errors=[];page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
   await page.goto(url+'/#workspace');await page.waitForFunction(()=>!document.getElementById('send-button').disabled);
   assert.equal(await page.locator('#task-monitor, .composer-area .task-status').count(),0);
   let release;const gate=new Promise(resolve=>release=resolve);
   await page.route('**/sessions/*/messages',async route=>{if(route.request().method()==='POST')await gate;await route.continue();});
   await page.locator('#message-input').fill('progress '+width);await page.locator('#send-button').click();
   await page.waitForFunction(()=>document.querySelector('.reply-progress-label')?.textContent==='正在发送…');
   assert.equal(await page.locator('.conversation > .message.user').last().innerText(),'progress '+width);
   assert(await page.locator('.conversation > .message').last().evaluate(e=>e.classList.contains('assistant')));
   release();
   await page.waitForFunction(()=>document.querySelector('.reply-progress-label')?.textContent==='正在思考…');
   assert.equal(await page.locator('.streaming .message-body').innerText(),'');
   assert.equal(await page.locator('.reply-progress').count(),1);
   assert(await page.locator('.conversation > .message').last().evaluate(e=>e.classList.contains('assistant')));
   fs.mkdirSync('/tmp/resume-reply-progress-qa',{recursive:true});await page.screenshot({path:'/tmp/resume-reply-progress-qa/thinking-'+width+'.png'});
   await page.waitForFunction(()=>document.querySelector('.reply-progress-label')?.textContent==='正在读取素材与模板…');
   await page.waitForFunction(()=>document.querySelector('.reply-progress-label')?.textContent==='正在生成 PDF 并检查版面…');
   await page.waitForFunction(()=>document.querySelector('.reply-progress-label')?.textContent==='正在检查排版效果…');
   await page.waitForFunction(()=>document.querySelector('.streaming .message-body')?.textContent.length>2);
   assert.equal(await page.locator('.reply-progress').count(),1);
   await page.waitForFunction(()=>!document.getElementById('send-button').disabled,{},{timeout:15000});
   assert.equal(await page.locator('.reply-progress, .streaming').count(),0);
   assert(!await page.locator('.composer-area').innerText().then(t=>t.includes('处理完成')||t.includes('任务耗时')));
   // Failure and retry stay in the reply and survive a refresh.
   await page.locator('#message-input').fill('fail');await page.locator('#send-button').click();
   await page.locator('.reply-retry').waitFor({state:'visible'});assert.equal(await page.locator('.reply-progress.error').count(),1);
   await page.reload();await page.locator('.reply-retry').waitFor({state:'visible'});
   const retried=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/retry'));
   await page.locator('.reply-retry').click();assert.equal((await retried).status(),202);
   await page.locator('.reply-retry').waitFor({state:'visible'});await page.waitForFunction(()=>!document.getElementById('send-button').disabled);
   // A new task clears its old error indicator; final Markdown is sanitized.
   await page.locator('#message-input').fill('markdown');await page.locator('#send-button').click();
   await page.waitForFunction(()=>!document.getElementById('send-button').disabled&&document.querySelector('.assistant:last-child strong')?.textContent==='整理完成');
   assert.equal(await page.locator('.assistant').last().locator('li').count(),2);
   assert.equal(await page.locator('.assistant').last().locator('script,img').count(),0);
   assert.equal(await page.evaluate(()=>window.unsafeReply),undefined);
   assert.equal(await page.locator('.reply-progress').count(),0);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:'/tmp/resume-reply-progress-qa/complete-'+width+'.png'});
   assert.deepEqual(errors,[]);await context.close();
  }
  console.log('PASS: desktop/mobile inline stages, streamed answer, no timing panel/completion footer, failure/retry/reload, sanitized formatted answer.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
