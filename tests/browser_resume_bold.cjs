const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const target='http://127.0.0.1:18801';
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
 try {
  const context=await browser.newContext({viewport:{width:1440,height:1000},hasTouch:true});
  assert.equal((await context.request.post(target+'/auth/login',{headers:{'X-Resume-Client':'browser'},data:{identifier:'editor_test',password:'Editor-test-2026!'}})).status(),200);
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(target+'/#versions');await page.locator('.version-card').last().click();await page.locator('#edit-latex').click();
  await page.locator('.resume-text-input[contenteditable="plaintext-only"]').first().waitFor();
  assert(await page.locator('#latex-bold').isDisabled());
  async function select(){await page.locator('.resume-text-input').first().evaluate(el=>{el.focus();const walk=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);while(walk.nextNode()&&!walk.currentNode.textContent.length){}const range=document.createRange();range.setStart(walk.currentNode,0);range.setEnd(walk.currentNode,6);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);});await page.waitForFunction(()=>!document.getElementById('latex-bold').disabled);}
  await select();await page.locator('#latex-bold').click();
  await page.waitForFunction(()=>document.getElementById('latex-bold').getAttribute('aria-pressed')==='true');
  assert.equal(await page.locator('.resume-text-input').first().locator('strong').innerText(),'Editor');
  await page.locator('#latex-bold').click();await page.waitForFunction(()=>document.getElementById('latex-bold').getAttribute('aria-pressed')==='false'&&!document.getElementById('latex-bold').disabled);
  assert.equal(await page.locator('.resume-text-input').first().locator('strong').count(),0);
  await page.locator('.resume-text-input').nth(1).fill('Changed C# & 5%');
  await select();await page.keyboard.press('Control+b');
  await page.waitForFunction(()=>document.getElementById('latex-bold').getAttribute('aria-pressed')==='true');
  await page.locator('#close-latex').click();await page.locator('#edit-latex').click();
  await page.locator('.resume-text-input strong').first().waitFor();
  assert.equal(await page.locator('.resume-text-input').nth(1).innerText(),'Changed C# & 5%');
  fs.mkdirSync('/tmp/resume-bold-qa',{recursive:true});await page.screenshot({path:'/tmp/resume-bold-qa/desktop.png'});
  await page.setViewportSize({width:390,height:844});await select();await page.locator('#latex-bold').tap();
  await page.waitForFunction(()=>document.getElementById('latex-bold').getAttribute('aria-pressed')==='false'&&!document.getElementById('latex-bold').disabled);
  await page.locator('#latex-bold').tap();await page.waitForFunction(()=>document.getElementById('latex-bold').getAttribute('aria-pressed')==='true');
  await page.screenshot({path:'/tmp/resume-bold-qa/mobile.png'});
  assert(await page.evaluate(()=>document.getElementById('latex-dialog').scrollWidth<=innerWidth));
  await page.locator('#compile-latex').click();await page.waitForFunction(()=>document.getElementById('latex-result').textContent.includes('已保存新版本'));
  await page.locator('#latex-source-tab').click();assert.equal(await page.locator('.resume-text-input').first().locator('strong').innerText(),'Editor');
  assert.deepEqual(errors,[]);console.log('PASS: selected bold/unbold, keyboard shortcut, pending edits, draft restore, touch button, real compile and preview.');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
