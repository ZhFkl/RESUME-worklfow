// Only the disposable loopback guest_ui_server.py; it uses no real SMS provider.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const target='http://127.0.0.1:18800';
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
 for(const [width,phone] of [[1440,'13800138144'],[390,'13800138390']]){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const name='sms'+width+Date.now();
  await page.goto(target+'/login.html');
  assert.equal(await page.locator('[data-mode]').count(),3);assert.equal(await page.locator('[data-mode=sms]').getAttribute('aria-pressed'),'true');
  await page.locator('#phone').fill(phone);
  await page.locator('#send-sms-code').click();await page.getByText('短信已提交，请查看手机验证码。',{exact:true}).waitFor();
  const code=async()=> (await (await page.request.get(target+'/test/sms-code?phone='+phone)).json()).code;
  await page.locator('#sms-code').fill(await code());
  assert(await page.locator('#send-sms-code').isDisabled());
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:'/tmp/resume-sms-register-'+width+'.png',fullPage:true});
  await page.locator('#auth-submit').click();await page.waitForURL('**/#workspace');
  const first=(await (await page.request.get(target+'/auth/me')).json()).account;assert.equal(first.has_password,false);
  await page.goto(target+'/#account');await page.locator('#logout-button').waitFor();assert(!await page.locator('#password-panel').isVisible());await page.locator('#logout-button').click();await page.waitForURL(target+'/');
  await page.request.get(target+'/test/sms-reset');await page.goto(target+'/login.html');
  assert(!await page.locator('#password').isVisible());await page.locator('#phone').fill(phone);await page.locator('#send-sms-code').click();await page.getByText('短信已提交，请查看手机验证码。',{exact:true}).waitFor();
  const actual=await code();await page.locator('#sms-code').fill(actual==='000000'?'111111':'000000');await page.locator('#auth-submit').click();await page.getByText('短信验证码错误或已过期',{exact:true}).waitFor();
  await page.locator('#sms-code').fill(actual);await page.locator('#auth-submit').click();await page.waitForURL('**/#workspace');
  await page.goto(target+'/#account');await page.waitForFunction(()=>document.querySelector('#bound-phone').textContent.includes('已绑定'));assert.match(await page.locator('#bound-phone').innerText(),new RegExp(phone));assert(!await page.locator('#bind-phone-form').isVisible());
  assert.equal((await (await page.request.get(target+'/auth/me')).json()).account.user_id,first.user_id);assert.deepEqual(errors,[]);await context.close();
 }
 // Existing email account binds a phone and keeps the same account after SMS login.
 const context=await browser.newContext(),page=await context.newPage();
 const name='bind'+Date.now(),email=name+'@example.com',phone='13900139000';
 await page.goto(target+'/password-login.html');await page.locator('[data-mode=register]').click();
 await page.locator('#new-username').fill(name);await page.locator('#email').fill(email);await page.locator('#password').fill('Testing password 123');await page.locator('#confirm-password').fill('Testing password 123');await page.locator('#send-email-code').click();await page.getByText('验证码已发送，请查看收件箱或垃圾邮件。',{exact:true}).waitFor();
 const mail=await (await page.request.get(target+'/test/email-code?email='+encodeURIComponent(email))).json();await page.locator('#email-code').fill(mail.code);await page.locator('#auth-submit').click();await page.waitForURL('**/#workspace');
 const uid=(await (await page.request.get(target+'/auth/me')).json()).account.user_id;
 await page.goto(target+'/#account');await page.locator('#bind-phone').fill(phone);await page.locator('#send-bind-code').click();await page.getByText('短信已提交，请查看手机验证码。',{exact:true}).waitFor();
 const sms=await (await page.request.get(target+'/test/sms-code?phone='+phone)).json();await page.locator('#bind-code').fill(sms.code);await page.locator('#bind-submit').click();await page.locator('#bind-phone-form').waitFor({state:'hidden'});
 await page.locator('#logout-button').click();await page.waitForURL(target+'/');await page.request.get(target+'/test/sms-reset');await page.goto(target+'/login.html');await page.locator('#phone').fill(phone);await page.locator('#send-sms-code').click();await page.getByText('短信已提交，请查看手机验证码。',{exact:true}).waitFor();
 const loginCode=await (await page.request.get(target+'/test/sms-code?phone='+phone)).json();await page.locator('#sms-code').fill(loginCode.code);await page.locator('#auth-submit').click();await page.waitForURL('**/#workspace');assert.equal((await (await page.request.get(target+'/auth/me')).json()).account.user_id,uid);
 await browser.close();console.log('PASS: desktop/mobile SMS registration and login, wrong-code rejection, cooldown, binding preserves existing account.');
})().catch(e=>{console.error(e);process.exit(1)});
