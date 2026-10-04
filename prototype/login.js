const $=id=>document.getElementById(id);let mode='password',config={sms_enabled:false},busy=false,nextSend=0,legacy=null;
try{const original=JSON.parse(localStorage.getItem('resume-studio:backend-profile:v1')||'null');legacy=original?.token?original:JSON.parse(localStorage.getItem('resume-studio:legacy-profile:v1')||'null');}catch{}
function phoneMode(){return mode==='sms'||mode==='reset'||(mode==='register'&&$('registration-kind').value==='phone');}
function render(){
 const phone=phoneMode(),signup=mode==='register',reset=mode==='reset';
 for(const [id,visible] of Object.entries({'registration-kind-row':signup,'identifier-row':mode==='password','username-row':signup&&!phone,'phone-row':phone,'code-row':phone,'password-row':mode!=='sms','confirm-row':signup||reset,'legacy-row':signup&&!!legacy,'recovery-note':reset}))$(id).hidden=!visible;
 for(const [id,needed] of Object.entries({'identifier':mode==='password','new-username':signup&&!phone,'phone':phone,'sms-code':phone,'password':mode!=='sms','confirm-password':signup||reset})){$(id).required=needed;$(id).disabled=!needed||busy;}
 $('password').autocomplete=signup||reset?'new-password':'current-password';$('password').minLength=signup||reset?6:1;
 $('password-label').textContent=signup||reset?'设置密码（至少 6 个字符）':'密码';
 $('auth-submit').textContent=busy?'正在处理…':reset?'重设密码':signup?'注册并进入工作空间':'登录';$('auth-submit').disabled=busy||(phone&&!config.sms_enabled);
 $('sms-notice').hidden=!phone||config.sms_enabled;$('forgot-password').hidden=reset;$('back-login').hidden=!reset;
 document.querySelectorAll('[data-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mode===mode)));countdown();
}
function countdown(){const seconds=Math.max(0,Math.ceil((nextSend-Date.now())/1000));$('send-code').disabled=busy||seconds>0||!config.sms_enabled;$('send-code').textContent=seconds?seconds+' 秒后重发':'获取验证码';}
setInterval(countdown,500);
function switchMode(value){mode=value;$('auth-message').textContent='';$('sms-code').value='';render();}
for(const button of document.querySelectorAll('[data-mode]'))button.onclick=()=>switchMode(button.dataset.mode);
$('registration-kind').onchange=render;$('forgot-password').onclick=()=>switchMode('reset');$('back-login').onclick=()=>switchMode('password');
async function request(path,data){const result=await fetch(path,{method:data?'POST':'GET',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Resume-Client':'browser'},body:data?JSON.stringify(data):undefined,cache:'no-store'});const body=await result.json();if(!result.ok)throw new Error(body.error||'请求失败');return body;}
$('send-code').onclick=async()=>{if(!/^1[3-9]\d{9}$/.test($('phone').value.trim())){$('auth-message').textContent='请输入有效的 11 位手机号';return;}busy=true;render();try{await request('/auth/sms/send',{phone:$('phone').value.trim(),purpose:mode==='register'?'register':mode==='reset'?'reset':'login'});nextSend=Date.now()+60000;$('auth-message').textContent='验证码已发送，请查看手机短信。';}catch(error){$('auth-message').textContent=error.message;}finally{busy=false;render();}};
$('login-form').onsubmit=async event=>{
 event.preventDefault();if(busy)return;if((mode==='register'||mode==='reset')&&$('password').value!==$('confirm-password').value){$('auth-message').textContent='两次密码不一致';return;}
 let path,data;
 if(mode==='password'){path='/auth/login';data={identifier:$('identifier').value.trim(),password:$('password').value};}
 else if(mode==='reset'){path='/auth/password/reset';data={phone:$('phone').value.trim(),code:$('sms-code').value.trim(),password:$('password').value};}
 else if(mode==='register'&&!phoneMode()){path='/auth/register';data={username:$('new-username').value.trim(),password:$('password').value};}
 else{path='/auth/sms/login';data={mode:mode==='register'?'register':'login',phone:$('phone').value.trim(),code:$('sms-code').value.trim()};if(mode==='register')data.password=$('password').value;}
 if(mode==='register'&&legacy&&$('keep-legacy').checked)data.legacy_token=legacy.token;
 busy=true;render();$('auth-message').textContent='';
 try{const result=await request(path,data);if(mode==='reset'){switchMode('password');$('auth-message').textContent='密码已重设，请使用新密码登录。';$('password').value='';return;}
  if(legacy){if(result.account.user_id!==legacy.user_id)localStorage.setItem('resume-studio:legacy-profile:v1',JSON.stringify(legacy));else localStorage.removeItem('resume-studio:legacy-profile:v1');}
  localStorage.setItem('resume-studio:backend-profile:v1',JSON.stringify({user_id:result.account.user_id,session_id:result.account.user_id===legacy?.user_id?legacy.session_id:undefined}));
  location.replace('/'+(location.hash||'#workspace'));
 }catch(error){$('auth-message').textContent=error.message;}finally{busy=false;render();}
};
request('/auth/config').then(result=>{config=result;render();}).catch(error=>{$('auth-message').textContent='无法连接服务，请刷新重试。';});render();
