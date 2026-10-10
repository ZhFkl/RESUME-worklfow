const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const target='http://127.0.0.1:18801'; // Only editor_ui_server.py's disposable fixture.
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const response=await context.request.post(target+'/auth/login',{headers:{'X-Resume-Client':'browser'},data:{identifier:'editor_test',password:'Editor-test-2026!'}});
  assert.equal(response.status(),200,await response.text());
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(target+'/#versions');await page.locator('#pdf-canvas').waitFor({state:'visible'});
  const zipPromise=page.waitForEvent('download');await page.locator('#download-latex').click();const zip=await zipPromise;
  assert.equal(fs.readFileSync(await zip.path()).subarray(0,2).toString(),'PK');
  await page.locator('#edit-latex').click();await page.waitForFunction(()=>document.getElementById('latex-source').value.includes('Editor fixture'));
  await page.locator('#latex-canvas').waitFor({state:'visible'});
  await page.locator('#latex-code-mode').click();
  const initial=await page.locator('#latex-source').inputValue();
  await page.locator('#latex-source').fill(initial.replace('Editor fixture','Edited in browser'));
  assert((await page.locator('#latex-preview-label').textContent()).includes('尚未编译'));
  await page.locator('#close-latex').click();await page.locator('#edit-latex').click();
  await page.waitForFunction(()=>document.getElementById('latex-source').value.includes('Edited in browser'));
  await page.locator('#compile-latex').click();await page.waitForFunction(()=>document.getElementById('latex-result').textContent.includes('已保存新版本'));
  await page.locator('#latex-canvas').waitFor({state:'visible'});
  await page.waitForFunction(()=>document.querySelectorAll('.version-card').length===2);
  assert((await page.locator('#latex-save-state').textContent()).includes('一致'));
  const pdfPromise=page.waitForEvent('download');await page.locator('#latex-download-pdf').click();const pdf=await pdfPromise;
  assert.equal(fs.readFileSync(await pdf.path()).subarray(0,5).toString(),'%PDF-');
  const texPromise=page.waitForEvent('download');await page.locator('#download-latex-draft').click();const tex=await texPromise;
  assert(fs.readFileSync(await tex.path(),'utf8').includes('Edited in browser'));
  fs.mkdirSync('/tmp/resume-editor-qa',{recursive:true});
  await page.screenshot({path:'/tmp/resume-editor-qa/desktop.png'});
  await page.locator('#latex-code-mode').click();
  await page.locator('#latex-source').fill(String.raw`\documentclass{article}\begin{document}\invalidcommand\end{document}`);
  await page.locator('#compile-latex').click();await page.waitForFunction(()=>document.getElementById('latex-result').textContent.includes('编译失败'));
  assert((await page.locator('#latex-source').inputValue()).includes('invalidcommand'));
  assert.equal(await page.locator('.version-card').count(),2);
  for(const width of [390,320]) {
   await page.setViewportSize({width,height:844});await page.locator('#latex-source-tab').click();await page.locator('#latex-code-mode').click();
   assert(await page.locator('#latex-source').isVisible());assert(!await page.locator('#latex-canvas').isVisible());
   await page.locator('#latex-preview-tab').click();assert(await page.locator('#latex-canvas').isVisible());
   assert(await page.evaluate(()=>document.getElementById('latex-dialog').scrollWidth<=innerWidth));
   await page.screenshot({path:'/tmp/resume-editor-qa/mobile-'+width+'.png'});
  }
  await page.locator('#close-latex').click();await page.reload();await page.locator('#pdf-canvas').waitFor({state:'visible'});
  await page.locator('#edit-latex').click();await page.waitForFunction(()=>document.getElementById('latex-source').value.includes('invalidcommand'));
  assert.deepEqual(errors,[]);
  console.log('PASS: source/PDF downloads, browser draft recovery, real compile into new version, failure preserves draft and original, 1440/390/320 layouts; no page errors.');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
