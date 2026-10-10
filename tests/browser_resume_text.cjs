const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const target='http://127.0.0.1:18801'; // Disposable editor_ui_server.py only.
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  assert.equal((await context.request.post(target+'/auth/login',{headers:{'X-Resume-Client':'browser'},data:{identifier:'editor_test',password:'Editor-test-2026!'}})).status(),200);
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(target+'/#versions');await page.locator('.version-card').last().click();
  const count=await page.locator('.version-card').count();
  await page.locator('#edit-latex').click();await page.locator('.resume-text-input').first().waitFor();
  assert(!await page.locator('#latex-source').isVisible());
  assert.equal(await page.locator('#latex-text-mode').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('.resume-text-input').first().innerText(),'Editor fixture');
  await page.locator('#latex-code-mode').click();
  const original=await page.locator('#latex-source').inputValue();
  await page.locator('#latex-source').fill(original.replace('Editor fixture',String.raw`\textbf{Editor fixture}`));
  await page.locator('#latex-text-mode').click();
  assert.equal(await page.locator('.resume-text-input').first().locator('strong').innerText(),'Editor fixture');
  await page.locator('.resume-text-input').first().fill('Editor fixture & 5% C#');
  await page.locator('#close-latex').click();await page.locator('#edit-latex').click();
  await page.waitForFunction(()=>document.querySelector('.resume-text-input')?.innerText==='Editor fixture & 5% C#');
  await page.locator('#latex-code-mode').click();
  await page.locator('#latex-source').waitFor({state:'visible'});
  const revised=await page.locator('#latex-source').inputValue();
  assert(revised.includes(String.raw`\textbf{Editor fixture \& 5\% C\#}`));
  assert.equal(revised.split(String.raw`\begin{document}`)[0],original.split(String.raw`\begin{document}`)[0]);
  await page.locator('#latex-text-mode').click();
  await page.locator('#latex-source').waitFor({state:'hidden'});
  fs.mkdirSync('/tmp/resume-text-qa',{recursive:true});
  await page.screenshot({path:'/tmp/resume-text-qa/desktop.png'});
  for(const width of [390,320]) {
   await page.setViewportSize({width,height:844});await page.locator('#latex-source-tab').click();
   assert(await page.locator('.resume-text-input').first().isVisible());assert(!await page.locator('#latex-source').isVisible());
   assert(await page.evaluate(()=>document.getElementById('latex-dialog').scrollWidth<=innerWidth));
   await page.screenshot({path:'/tmp/resume-text-qa/mobile-'+width+'.png'});
   await page.locator('#latex-preview-tab').click();assert(await page.locator('#latex-canvas').isVisible());
  }
  await page.locator('#latex-source-tab').click();
  await page.locator('#compile-latex').click();await page.waitForFunction(()=>document.getElementById('latex-result').textContent.includes('已保存新版本'));
  await page.waitForFunction(n=>document.querySelectorAll('.version-card').length===n,count+1);
  await page.locator('#latex-source-tab').click();
  assert.equal(await page.locator('.resume-text-input').first().innerText(),'Editor fixture & 5% C#');
  assert.equal(await page.locator('.resume-text-input').first().locator('strong').innerText(),'Editor fixture & 5% C#');
  const texPromise=page.waitForEvent('download');await page.locator('#download-latex-draft').click();const tex=await texPromise;
  assert(fs.readFileSync(await tex.path(),'utf8').includes(String.raw`\& 5\% C\#`));
  assert.deepEqual(errors,[]);
  console.log('PASS: default prose, hidden commands, preserved bold/preamble, escaped symbols, mode switching, draft recovery, real PDF compile, desktop/390/320 layouts.');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
