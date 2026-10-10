// Uses only the disposable guest_ui_server.py on port 18800.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const target='http://127.0.0.1:18800';
const fs=require('node:fs');
const output=process.env.RESUME_UI_ARTIFACTS||'/tmp/resume-template-categories';
fs.mkdirSync(output,{recursive:true});
(async()=>{
  const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
  try {
    for(const width of [1440,390,320]){
      const page=await browser.newPage({viewport:{width,height:900},isMobile:width<600,hasTouch:width<600});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      const selectionWrites=[];page.on('request',r=>{if(r.method()==='PUT'&&r.url().endsWith('/templates/selection'))selectionWrites.push(r.url());});
      const category=id=>page.locator('[data-template-category="'+id+'"]');
      const source=id=>page.locator('[data-template-filter="'+id+'"]');
      const cardCount=n=>page.waitForFunction(n=>document.querySelectorAll('#template-grid .template-card').length===n,n);
      await page.goto(target+'/#templates');
      await page.waitForFunction(()=>document.body.classList.contains('guest'));
      assert.equal(await page.locator('[data-template-category]').count(),6);
      await category('engineering').click();await cardCount(2);
      assert.equal(await category('engineering').getAttribute('aria-pressed'),'true');
      await page.screenshot({path:output+'/templates-'+width+'.png',fullPage:true});
      for(const id of ['product','design','marketing','business']){
        await category(id).click();await cardCount(0);
        assert(await page.locator('.template-empty').isVisible());
      }
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await page.getByRole('button',{name:'查看全部模板',exact:true}).click();await cardCount(2);
      await source('custom').click();assert(await page.locator('#template-grid .guest-message').isVisible());
      await source('all').click();
      await page.locator('#upload-template-button').click();
      await page.locator('#login-dialog').waitFor({state:'visible'});
      const frame=page.frameLocator('#login-frame');
      const phone='136'+String(width).padStart(8,'0');
      await frame.locator('#phone').fill(phone);await frame.locator('#send-sms-code').click();await frame.getByText('短信已提交，请查看手机验证码。',{exact:true}).waitFor();
      const sms=await (await page.request.get(target+'/test/sms-code?phone='+phone)).json();await frame.locator('#sms-code').fill(sms.code);
      await frame.locator('#auth-submit').click();
      await page.waitForFunction(()=>!document.body.classList.contains('guest')&&!document.querySelector('#upload-template-button').disabled);
      await cardCount(2);
      const real=await page.evaluate(async()=>{const r=await fetch('/templates');return r.json();});
      assert(real.templates.every(item=>item.categories.includes('engineering')));
      const extra=(id,name,categories)=>({id,name,categories,description:'Test layout',source:'custom',status:'ready',preview:false,tex_url:null});
      const catalog={...real,templates:[...real.templates,extra('fixture-multi','运营版式',['product','marketing']),extra('fixture-unknown','未分类版式',[])]};
      await page.route('**/templates',route=>route.fulfill({json:catalog}));
      await page.evaluate(()=>{location.hash='workspace';});await page.locator('#workspace-view').waitFor({state:'visible'});
      await page.evaluate(()=>{location.hash='templates';});await cardCount(4);
      await category('product').click();await cardCount(1);
      assert.equal(await page.locator('#template-grid h3').textContent(),'运营版式');
      await source('system').click();await cardCount(0);
      await source('custom').click();await cardCount(1);
      await category('marketing').click();await cardCount(1);
      await category('all').click();await cardCount(2);
      assert(await page.getByText('未分类版式',{exact:true}).isVisible());
      await category('engineering').click();await cardCount(0);
      await page.getByRole('button',{name:'查看全部模板',exact:true}).click();await cardCount(4);
      assert.equal(await page.locator('.template-card.selected').getAttribute('data-template-id'),real.selected_id);

      // A filter click must not invalidate an in-flight catalog response.
      await page.unroute('**/templates');
      let release,seen;
      const gate=new Promise(resolve=>{release=resolve;});const requested=new Promise(resolve=>{seen=resolve;});
      await page.route('**/templates',async route=>{seen();await gate;await route.fulfill({json:{...catalog,templates:[...catalog.templates,extra('fixture-new','新运营版式',['product'])]}});});
      await page.evaluate(()=>{location.hash='workspace';});await page.locator('#workspace-view').waitFor({state:'visible'});
      await page.evaluate(()=>{location.hash='templates';});await requested;
      await category('product').click();release();await cardCount(2);
      assert.equal(await category('product').getAttribute('aria-pressed'),'true');
      assert.deepEqual(selectionWrites,[]);assert.deepEqual(errors,[]);
      await page.close();console.log('PASS '+width+'px: guest/auth categories, combined sources, multi-role/unknown metadata, empty reset, request race, selection unchanged');
    }
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
