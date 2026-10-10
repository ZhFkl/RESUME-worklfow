// Run against a local preview; only browses and edits an unsent chat draft.
const {chromium,webkit}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const target=process.env.RESUME_UI_TEST_URL||'http://127.0.0.1:18800';
if(!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(target))throw new Error('Use a local preview');
(async()=>{
  const browser=process.env.RESUME_UI_BROWSER==='webkit'
    ? await webkit.launch({headless:true})
    : await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox','--no-proxy-server']});
  try {
    for(const width of [320,360,390,430]){
      const page=await browser.newPage({viewport:{width,height:844},isMobile:true,hasTouch:true});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.goto(target);
      await page.waitForFunction(()=>document.body.classList.contains('guest'));
      assert(await page.locator('#menu-view').isVisible());
      assert.deepEqual(await page.locator('.mobile-entry strong').allTextContents(),['首页','素材库','模板库','简历版本','工作台']);
      assert(!await page.locator('.sidebar').isVisible());
      for(const route of ['home','library','templates','versions','workspace']){
        await page.locator('#menu-view a[href="#'+route+'"]').click();
        await page.locator('#'+route+'-view').waitFor({state:'visible'});
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),route+' overflows at '+width);
        await page.locator('[data-mobile-menu]:visible').tap();
        const drawer=page.locator('#mobile-nav-dialog');
        await drawer.waitFor({state:'visible'});
        assert.equal(await drawer.locator('[aria-current="page"]').getAttribute('href'),'#'+route);
        assert.equal(await page.locator('[data-mobile-menu]:visible').getAttribute('aria-expanded'),'true');
        await drawer.locator('a[href="#menu"]').click();
        await page.locator('#menu-view').waitFor({state:'visible'});
        assert(!await drawer.isVisible());
      }
      await page.locator('#menu-view a[href="#workspace"]').click();
      await page.locator('#message-input').fill('切换页面后保留这条草稿');
      await page.locator('[data-mobile-menu]:visible').click();
      await page.locator('#mobile-nav-dialog a[href="#templates"]').click();
      await page.locator('#templates-view').waitFor({state:'visible'});
      await page.goBack();
      await page.locator('#workspace-view').waitFor({state:'visible'});
      assert.equal(await page.locator('#message-input').inputValue(),'切换页面后保留这条草稿');
      await page.locator('[data-mobile-menu]:visible').click();
      await page.keyboard.press('Escape');
      await page.locator('#mobile-nav-dialog').waitFor({state:'hidden'});
      await page.waitForFunction(()=>[...document.querySelectorAll('[data-mobile-menu]')].every(el=>el.getAttribute('aria-expanded')==='false'));
      assert.equal(await page.locator('[data-mobile-menu]:visible').getAttribute('aria-expanded'),'false');
      assert(await page.locator('[data-mobile-menu]:visible').evaluate(el=>el===document.activeElement));
      await page.locator('[data-mobile-menu]:visible').click();
      await page.mouse.click(width-4,200);
      await page.locator('#mobile-nav-dialog').waitFor({state:'hidden'});
      await page.locator('[data-mobile-menu]:visible').click();
      await page.getByRole('button',{name:'关闭导航菜单'}).click();
      await page.locator('#mobile-nav-dialog').waitFor({state:'hidden'});
      await page.locator('[data-mobile-menu]:visible').click();
      await page.setViewportSize({width:1440,height:900});
      await page.locator('#mobile-nav-dialog').waitFor({state:'hidden'});
      assert(await page.locator('.sidebar').isVisible());
      assert.equal(await page.locator('[data-mobile-menu]:visible').count(),0);
      await page.goto(target);
      await page.locator('#home-view').waitFor({state:'visible'});
      assert(!await page.locator('#menu-view').isVisible());
      assert.deepEqual(errors,[]);
      await page.close();
      console.log('PASS '+width+'px: five entries, drawer navigation/closing, history, draft, desktop resize');
    }
    // Navigation must not depend on the app/PDF module finishing its download.
    const independent=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    await independent.route('**/app.js*',route=>route.abort());
    await independent.goto(target+'/#home');
    const trigger=independent.locator('[data-mobile-menu]:visible');
    assert.equal((await trigger.textContent()).trim(),'');
    assert.equal(await trigger.locator('use').getAttribute('href'),'#menu-icon');
    await trigger.tap();
    await independent.locator('#mobile-nav-dialog').waitFor({state:'visible'});
    assert.equal(await independent.locator('.mobile-nav-links a:visible').count(),5);
    await independent.getByRole('button',{name:'关闭导航菜单'}).tap();
    await independent.locator('#mobile-nav-dialog').waitFor({state:'hidden'});
    await independent.close();
    console.log('PASS: hamburger opens navigation even when the app module cannot load');

    const loading=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    let release;
    const gate=new Promise(resolve=>{release=resolve;});
    await loading.route('**/health',async route=>{await gate;await route.continue();});
    await loading.goto(target+'/#home');
    await loading.locator('[data-mobile-menu]:visible').tap();
    await loading.locator('#mobile-nav-dialog').waitFor({state:'visible'});
    release();
    await loading.waitForFunction(()=>document.body.classList.contains('guest'));
    assert(await loading.locator('#mobile-nav-dialog').isVisible());
    await loading.close();
    console.log('PASS: completing startup does not dismiss an open drawer');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
