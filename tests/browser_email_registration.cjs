// Run only against the disposable guest_ui_server.py with its fake mailbox.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const target='http://127.0.0.1:18800';
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
 for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:900}});const page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const name='email'+width+Date.now(),email=name+'@example.com';
  await page.goto(target+'/login.html');
  await page.locator('[data-mode=register]').click();
  await page.locator('#new-username').fill(name);await page.locator('#email').fill(email);
  await page.locator('#password').fill('Testing password 123');await page.locator('#confirm-password').fill('Testing password 123');
  await page.locator('#send-email-code').click();await page.getByText('验证码已发送，请查看收件箱或垃圾邮件。',{exact:true}).waitFor();
  assert(await page.locator('#send-email-code').isDisabled());
  const mailbox=await (await page.request.get(target+'/test/email-code?email='+encodeURIComponent(email))).json();
  const wrong=mailbox.code==='000000'?'111111':'000000';
  await page.locator('#email-code').fill(wrong);await page.locator('#auth-submit').click();await page.getByText('邮箱验证码错误、已过期或已使用',{exact:true}).waitFor();
  assert(await page.locator('#new-username').isVisible());
  await page.locator('#email-code').fill(mailbox.code);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:'/tmp/resume-email-register-'+width+'.png',fullPage:true});
  await page.locator('#auth-submit').click();await page.waitForURL('**/#workspace');
  await page.goto(target+'/#account');await page.locator('#logout-button').click();await page.waitForURL(target+'/');
  await page.goto(target+'/login.html');await page.locator('[data-mode=password]').click();await page.locator('#identifier').fill(email.toUpperCase());await page.locator('#password').fill('Testing password 123');await page.locator('#auth-submit').click();await page.waitForURL('**/#workspace');
  await page.goto(target+'/#account');await page.locator('#logout-button').click();await page.waitForURL(target+'/');
  await page.goto(target+'/login.html');await page.locator('[data-mode=password]').click();await page.locator('#identifier').fill(name);await page.locator('#password').fill('Testing password 123');await page.locator('#auth-submit').click();await page.waitForURL('**/#workspace');
  assert.deepEqual(errors,[]);await context.close();
 }
 await browser.close();console.log('PASS: desktop/mobile email verification, wrong-code rejection, resend cooldown, username/email login, and no overflow or JS errors.');
})().catch(e=>{console.error(e);process.exit(1)});
