const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const url='http://127.0.0.1:18802';
const reply='你好，这是一条逐步输出的回复😀。正在整理你的经历，请稍等。';
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
 try{
  for(const width of [1440,390]){
   const context=await browser.newContext({viewport:{width,height:900},hasTouch:width===390});
   assert.equal((await context.request.post(url+'/auth/login',{headers:{'X-Resume-Client':'browser'},data:{identifier:'stream_test',password:'Stream-test-2026!'}})).status(),200);
   const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(url+'/#workspace');await page.waitForFunction(()=>!document.getElementById('send-button').disabled);
   const message='stream '+width+' '+Date.now();
   await page.locator('#message-input').fill(message);await page.locator('#send-button').click();
   await page.waitForFunction(()=>document.querySelector('.streaming .message-body')?.textContent.length>2);
   const partial=await page.locator('.streaming .message-body').innerText();assert(reply.startsWith(partial));assert(partial.length<reply.length);assert(await page.locator('#send-button').isDisabled());
   fs.mkdirSync('/tmp/resume-stream-qa',{recursive:true});await page.screenshot({path:'/tmp/resume-stream-qa/'+width+'.png'});
   if(width===1440){
    await page.reload();await page.waitForFunction(()=>document.querySelector('.streaming .message-body')?.textContent.length>2);
   }else{
    await context.setOffline(true);await page.waitForTimeout(400);await context.setOffline(false);
   }
   await page.waitForFunction(()=>!document.getElementById('send-button').disabled,{},{timeout:15000});
   assert.equal(await page.locator('.assistant .message-body').last().innerText(),reply);
   assert.equal(await page.locator('.streaming').count(),0);
   assert.equal(await page.locator('.user .message-body').filter({hasText:message}).count(),1);
   assert.deepEqual(errors,[]);
   await context.close();
  }
  console.log('PASS: real SSE partial text before completion, desktop/mobile, reload replay, offline reconnect, no duplicate messages or JS errors.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
