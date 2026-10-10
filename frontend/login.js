const embedded=new URLSearchParams(location.search).get('embedded')==='1'&&window.parent!==window;
if(embedded)document.body.classList.add('embedded-login');
const $=id=>document.getElementById(id);let mode=document.body.dataset.initialMode==='password'?'password':'sms',busy=false,sending=false,nextSend=0,emailEnabled=false,smsEnabled=false,smsNextSend=0,smsChallenge=null,legacy=null;
try{const original=JSON.parse(localStorage.getItem('resume-studio:backend-profile:v1')||'null');legacy=original?.token?original:JSON.parse(localStorage.getItem('resume-studio:legacy-profile:v1')||'null');}catch{}
function phoneMode(){return mode==='sms'||(mode==='register'&&$('registration-kind').value==='phone');}
function render(){
 const signup=mode==='register',phone=phoneMode();
 for(const [id,visible] of Object.entries({'registration-kind-row':signup,'identifier-row':mode==='password','username-row':signup,'confirm-row':signup,'password-row':mode!=='sms','email-row':signup&&!phone,'email-code-row':signup&&!phone,'phone-row':phone,'sms-code-row':phone,'legacy-row':(signup||mode==='sms')&&!!legacy}))$(id).hidden=!visible;
 for(const [id,needed] of Object.entries({'identifier':mode==='password','new-username':signup,'password':mode!=='sms','confirm-password':signup,'email':signup&&!phone,'email-code':signup&&!phone,'phone':phone,'sms-code':phone})){$(id).required=needed;$(id).disabled=!needed||busy||sending;}
 $('forgot-password').hidden=mode!=='password';
 $('password').autocomplete=signup?'new-password':'current-password';$('password').minLength=signup?6:1;
 $('password-label').textContent=signup?'设置密码（至少 6 个字符）':'密码';
 $('auth-submit').textContent=busy?'正在处理…':signup?'注册并进入工作空间':'登录';$('auth-submit').disabled=busy||sending||(signup&&!phone&&!emailEnabled)||(phone&&(!smsEnabled||!smsChallenge));
 $('email-unavailable').hidden=!signup||phone||emailEnabled;
 $('sms-unavailable').hidden=!phone||smsEnabled;$('registration-kind').disabled=busy||sending;countdown();
 document.querySelectorAll('[data-mode]').forEach(button=>{button.disabled=busy||sending;button.setAttribute('aria-pressed',String(button.dataset.mode===mode));});
}
function countdown(){const smsSeconds=Math.max(0,Math.ceil((smsNextSend-Date.now())/1000));$('send-sms-code').disabled=busy||sending||smsSeconds>0||!smsEnabled;$('send-sms-code').textContent=sending&&phoneMode()?'正在发送…':smsSeconds?smsSeconds+' 秒后重发':'发送验证码';const seconds=Math.max(0,Math.ceil((nextSend-Date.now())/1000));$('send-email-code').disabled=busy||sending||seconds>0||!emailEnabled;$('send-email-code').textContent=sending?'正在发送…':seconds?seconds+' 秒后重发':'发送验证码';}
setInterval(countdown,500);
$('email').addEventListener('input',()=>{$('email-code').value='';$('email-status').textContent='';});
$('send-email-code').onclick=async()=>{
 if(busy||sending||Date.now()<nextSend||!emailEnabled)return;
 if(!$('email').value.trim()||!$('email').reportValidity())return;
 const address=$('email').value.trim();sending=true;render();$('email-status').classList.remove('success');$('email-status').textContent='';
 try{const result=await request('/auth/email/send',{email:address});nextSend=Date.now()+result.retry_after*1000;$('email-status').classList.add('success');$('email-status').textContent='验证码已发送，请查看收件箱或垃圾邮件。';}
 catch(error){$('email-status').textContent=error.message;}
 finally{sending=false;render();}
};
function clearSms(){smsChallenge=null;$('sms-code').value='';$('sms-status').textContent='';}
$('phone').addEventListener('input',()=>{clearSms();render();});
$('registration-kind').addEventListener('change',()=>{clearSms();$('auth-message').textContent='';render();});
$('send-sms-code').onclick=async()=>{
 if(busy||sending||Date.now()<smsNextSend||!smsEnabled)return;
 if(!$('phone').reportValidity()||!$('phone').value.trim())return;
 sending=true;smsChallenge=null;render();$('sms-status').classList.remove('success');$('sms-status').textContent='';
 try{const result=await request('/auth/sms/send',{phone:$('phone').value.trim(),purpose:mode==='register'?'register':'login'});smsChallenge=result.challenge;smsNextSend=Date.now()+result.retry_after*1000;$('sms-status').classList.add('success');$('sms-status').textContent='短信已提交，请查看手机验证码。';}
 catch(error){$('sms-status').textContent=error.message;}
 finally{sending=false;render();}
};
function switchMode(value){mode=value;clearSms();$('auth-message').textContent='';$('recovery-note').hidden=true;render();}
for(const button of document.querySelectorAll('[data-mode]'))button.onclick=()=>switchMode(button.dataset.mode);
$('forgot-password').onclick=()=>{$('recovery-note').hidden=false;};
async function request(path,data){const result=await fetch(path,{method:data?'POST':'GET',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Resume-Client':'browser'},body:data?JSON.stringify(data):undefined,cache:'no-store'});const body=await result.json();if(!result.ok)throw new Error(body.error||'请求失败');return body;}
$('login-form').onsubmit=async event=>{
 event.preventDefault();if(busy||sending)return;if(mode==='register'&&$('password').value!==$('confirm-password').value){$('auth-message').textContent='两次密码不一致';return;}
 let path,data;
 if(mode==='password'){path='/auth/login';data={identifier:$('identifier').value.trim(),password:$('password').value};}
 else if(phoneMode()){path='/auth/sms/login';data={mode:mode==='register'?'register':'login',phone:$('phone').value.trim(),code:$('sms-code').value.trim(),challenge:smsChallenge};if(mode==='register'){data.username=$('new-username').value.trim();data.password=$('password').value;}}
 else{path='/auth/register';data={username:$('new-username').value.trim(),password:$('password').value,email:$('email').value.trim(),code:$('email-code').value.trim()};}
 if((mode==='register'||mode==='sms')&&legacy&&$('keep-legacy').checked)data.legacy_token=legacy.token;
 busy=true;render();$('auth-message').textContent='';
 try{const result=await request(path,data);
  if(legacy){if(result.account.user_id!==legacy.user_id)localStorage.setItem('resume-studio:legacy-profile:v1',JSON.stringify(legacy));else localStorage.removeItem('resume-studio:legacy-profile:v1');}
  localStorage.setItem('resume-studio:backend-profile:v1',JSON.stringify({user_id:result.account.user_id,session_id:result.account.user_id===legacy?.user_id?legacy.session_id:undefined}));
  if(embedded){window.parent.postMessage({type:'resume:authenticated'},location.origin);return;}
  location.replace('/'+(location.hash||'#workspace'));
 }catch(error){$('auth-message').textContent=error.message;}finally{busy=false;render();}
};
request('/auth/config').then(config=>{emailEnabled=!!config.email_enabled;smsEnabled=!!config.sms_enabled;if(!emailEnabled&&smsEnabled)$('registration-kind').value='phone';render();}).catch(()=>{emailEnabled=false;render();});render();

window.addEventListener('keydown',event=>{if(embedded&&event.key==='Escape')window.parent.postMessage({type:'resume:close-login'},location.origin);});

if(embedded)new ResizeObserver(()=>window.parent.postMessage({type:'resume:login-size',height:Math.ceil(document.querySelector('.login-card').getBoundingClientRect().height)+8},location.origin)).observe(document.querySelector('.login-card'));
