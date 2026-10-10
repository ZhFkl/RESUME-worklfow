import {createEditHistory} from './edit-history.js?v=20261010-1';
import {createReplyProgress,taskStage} from './reply-progress.js?v=20261010-timeline';
import {readTaskStream} from './task-stream.js?v=20261006-1';
import {createResumeEditor} from './resume-editor.js?v=20261006-3';
import * as pdfjs from './vendor/pdf.min.mjs';
pdfjs.GlobalWorkerOptions.workerSrc = new URL('./vendor/pdf.worker.min.mjs', import.meta.url).href;
const pdfOptions = {cMapUrl: new URL('./vendor/cmaps/', import.meta.url).href, cMapPacked:true,
  standardFontDataUrl:new URL('./vendor/standard_fonts/',import.meta.url).href,
  wasmUrl:new URL('./vendor/wasm/',import.meta.url).href,isEvalSupported:false};
const $ = id => document.getElementById(id);
const profileKey='resume-studio:backend-profile:v1';
const oldKey='resume-studio:prototype:materials:v2';
const state={markdown:'',revision:'',editing:false,dirty:false,ready:false,guest:false,busy:false,
  profile:null,attachments:[],imported:'',versions:[],pdfURL:null,pdfGeneration:0,versionsGeneration:0,deleting:false,deleteTarget:null,task:null};
const materialHistory=createEditHistory();
let beforeMaterialInput=null;
let toastTimer,saveTimer,saving=null,retryAction=null,authGeneration=0,taskStreamController=null,liveReply=null;
function toast(text){$('toast').textContent=text;$('toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('show'),4500);}
function stored(key){try{return localStorage.getItem(key);}catch{return null;}}
function remember(key,value){try{localStorage.setItem(key,value);return true;}catch{return false;}}
function forget(key){try{localStorage.removeItem(key);}catch{}}
function draftKey(){return 'resume-studio:unsaved:'+state.profile.user_id;}
async function api(path,options={},raw=false){
  const generation=authGeneration;
  const headers=new Headers(options.headers||{});
  headers.set('X-Resume-Client','browser');if(state.profile?.user_id)headers.set('X-Resume-Account',state.profile.user_id);if(state.csrf)headers.set('X-CSRF-Token',state.csrf);
  let body=options.body;
  if(body!==undefined&&!(body instanceof FormData)){headers.set('Content-Type','application/json');body=JSON.stringify(body);}
  let response;
  try{response=await fetch(path,{...options,body,headers,cache:'no-store'});}catch{throw new Error('无法连接后端，请确认本地服务仍在运行');}
  if(!response.ok){let detail;try{const problem=await response.json();detail=problem.error;if(problem.account_changed)location.reload();}catch{}const error=new Error(detail||'请求失败（HTTP '+response.status+'）');error.status=response.status;if(response.status===401&&state.ready){enterGuest();showLogin('登录已过期，请重新登录。已输入的消息会保留。');}throw error;}
  if(generation!==authGeneration){const error=new Error('登录状态已变化，请重试');error.status=401;throw error;}
  return raw?response:response.json();
}
function controls(){
  const locked=(!state.ready&&!state.guest)||state.busy||state.sending||state.templateSelecting||state.photoSaving;
  for(const id of ['upload-template-button','template-upload-submit','upload-profile-photo','remove-profile-photo','send-button','attach-button','new-chat','toggle-edit','import-md','restore-draft','save-materials']) $(id).disabled=locked;
  $('markdown-editor').disabled=locked||state.guest;updateHistoryControls();
  document.querySelectorAll('[data-template-select]').forEach(button=>button.disabled=locked);
  document.querySelectorAll('.attachment .remove').forEach(button=>button.disabled=state.busy);
}
function showLogin(message='登录后即可使用素材库、制作和保存简历。已输入的消息会保留。'){
  $('login-context').textContent=message;
  if(!$('login-frame').getAttribute('src'))$('login-frame').src='/login.html?embedded=1';
  if(!$('login-dialog').open)$('login-dialog').showModal();
}
function requireLogin(){
  if(state.ready)return true;
  if(state.guest)showLogin();else toast('正在连接服务，请稍等。');
  return false;
}
function guestMessage(title,description){
  const box=document.createElement('div');box.className='guest-message';
  const heading=document.createElement('h2');heading.textContent=title;
  const text=document.createElement('p');text.textContent=description;
  const button=document.createElement('button');button.type='button';button.className='button primary';button.textContent='登录 / 注册';button.addEventListener('click',()=>showLogin());
  box.append(heading,text,button);return box;
}
function renderGuestTemplates(){
  const grid=$('template-grid');grid.replaceChildren();
  if(templateFilter==='custom'){$('template-count').textContent='登录后查看个人模板';grid.append(guestMessage('收藏你喜欢的版式','登录后，可以上传自己的模板，并在之后的简历中重复使用。'));return;}
  const items=[{name:'简洁中文',image:'assets/template-cn.png',description:'清晰分区，适合中文求职简历。',categories:['engineering']},{name:'Classic English',image:'assets/template-en.png',description:'简洁的英文排版，让经历和成果更易阅读。',categories:['engineering']}].filter(matchesTemplateCategory);
  $('template-count').textContent=items.length+' 个系统模板';
  if(!items.length)renderTemplateEmpty();
  for(const item of items){
    const card=document.createElement('article');card.className='template-card';
    const cover=document.createElement('div');cover.className='template-cover';
    const img=document.createElement('img');img.src=item.image;img.alt=item.name+'系统模板示例（非个人资料）';cover.append(img);
    const content=document.createElement('div');content.className='template-card-content';
    const h=document.createElement('h3');h.textContent=item.name;
    const p=document.createElement('p');p.textContent=item.description;
    const select=document.createElement('button');select.className='button primary';select.textContent='使用此模板';select.addEventListener('click',()=>showLogin('登录后可以选择模板，开始制作你的简历。'));
    content.append(h,templateCategoryTags(item),p,select);card.append(cover,content);grid.append(card);
  }
}
function enterGuest(){
  materialHistory.reset();beforeMaterialInput=null;
  ++authGeneration;taskStreamController?.abort();clearTimeout(saveTimer);resumeEditor.reset();
  state.ready=false;state.guest=true;state.busy=false;state.profile=null;state.account=null;state.csrf=null;state.task=null;state.dirty=false;state.markdown='';state.revision='';state.versions=[];state.templateCatalog=null;state.templateSelecting=false;state.sending=false;
  document.body.classList.add('guest');
  $('conversation').replaceChildren();$('welcome').hidden=false;
  $('account-link').textContent='登录 / 注册';$('connection-status').textContent='访客浏览';$('workspace-mode').textContent='登录后保存你的创作';
  document.querySelectorAll('[data-login]').forEach(button=>button.textContent='登录 / 注册');
  $('selected-template-name').textContent='登录后选择';$('save-state').textContent='登录后查看';
  $('markdown-editor').value='';setEditing(false);
  // setEditing normally renders personal Markdown; restore the visitor explanation.
  $('markdown-view').replaceChildren(guestMessage('把经历整理成自己的素材库','教育背景、实习、项目和成果，集中保存在这里。登录后可上传或编辑。'));
  $('restore-draft').hidden=true;$('conflict-actions').hidden=true;
  $('profile-photo-preview').hidden=true;$('profile-photo-preview').removeAttribute('src');$('remove-profile-photo').hidden=true;
  if(state.photoURL){URL.revokeObjectURL(state.photoURL);state.photoURL=null;}
  $('profile-photo-status').textContent='登录后上传本人证件照，用于你的简历。';
  $('version-list').replaceChildren(guestMessage('每一版简历，都为你保存','登录后，在这里回看、预览和下载你生成的简历。'));$('versions-count').textContent='登录后查看';clearPreview();
  liveReply=null;taskStatus('');
  for(const node of $('account-view').children)node.hidden=true;
  $('account-guest')?.remove();const account=guestMessage('你的账号与资料','登录后管理账号、密码和个人资料。');account.id='account-guest';$('account-view').append(account);
  renderGuestTemplates();controls();route();
}
const protectedControls=new Set(['attach-button','new-chat','toggle-edit','import-md','export-md','restore-draft','save-materials','upload-profile-photo','remove-profile-photo','upload-template-button','account-link']);
document.addEventListener('click',event=>{
  const target=event.target.closest('button,a');if(!target)return;
  if(target.matches('[data-login]')){event.preventDefault();if(state.ready)location.hash='account';else showLogin();return;}
  if(!state.ready&&protectedControls.has(target.id)){event.preventDefault();event.stopImmediatePropagation();requireLogin();}
},true);
document.addEventListener('submit',event=>{
  if(!state.ready&&event.target.closest('#app-shell')){event.preventDefault();event.stopImmediatePropagation();requireLogin();}
},true);
$('close-login').addEventListener('click',()=>$('login-dialog').close());
$('login-dialog').addEventListener('click',event=>{if(event.target===$('login-dialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
$('login-dialog').addEventListener('close',()=>{if(state.guest&&location.hash==='#account')location.hash='workspace';});
let finishingLogin=false;
window.addEventListener('message',async event=>{
  if(event.origin!==location.origin||event.source!==$('login-frame').contentWindow)return;
  if(event.data?.type==='resume:login-size'){const height=event.data.height;if(Number.isFinite(height))$('login-frame').style.height=Math.max(280,Math.min(height,1200))+'px';return;}
  if(event.data?.type==='resume:close-login'){$('login-dialog').close();return;}
  if(event.data?.type!=='resume:authenticated'||finishingLogin)return;
  finishingLogin=true;$('login-dialog').close();
  try{await bootstrap();if(state.ready)toast('已登录，继续刚才的操作吧。');}finally{finishingLogin=false;$('login-frame').removeAttribute('src');}
});
function ensureLiveReply(){
  if(!liveReply?.element.isConnected){
    const scroll=$('chat-scroll'),top=scroll.scrollTop,follow=scroll.scrollHeight-top-scroll.clientHeight<80;
    liveReply=createReplyProgress(appendMessage('assistant',''),scroll);
    if(!follow)scroll.scrollTop=top;
  }
  return liveReply;
}
function templateStatus(text,error=false,action=null){
  retryAction=action;$('template-task-status').hidden=!text;$('template-task-label').textContent=text;
  $('template-retry-task').hidden=!action;$('template-task-status').classList.toggle('error',error);
}
function taskStatus(text,error=false,action=null){
  if(state.task?.kind==='template'){templateStatus(text,error,action);return;}
  if(state.task?.kind==='latex'){if(error)toast(text);return;}
  if(!text){liveReply?.clear();liveReply=null;return;}
  ensureLiveReply().status(text,error,action);
}
async function refreshPhoto(){
  const profile=await api('/profile');
  if(state.photoURL){URL.revokeObjectURL(state.photoURL);state.photoURL=null;}
  const image=$('profile-photo-preview');image.hidden=true;image.removeAttribute('src');
  $('remove-profile-photo').hidden=!profile.has_photo;
  $('profile-photo-status').textContent=profile.has_photo?'照片已保存，每次生成简历会自动使用；旧版本照片保持不变。':'尚未上传。上传后，每次生成简历都会使用这张照片。';
  if(profile.has_photo){
    const response=await fetch('/profile/photo',{credentials:'same-origin',headers:{'X-Resume-Account':state.profile.user_id},cache:'no-store'});
    if(!response.ok)throw new Error('证件照读取失败，请刷新重试');
    state.photoURL=URL.createObjectURL(await response.blob());image.src=state.photoURL;image.hidden=false;
  }
}
$('upload-profile-photo').addEventListener('click',()=>$('profile-photo-input').click());
$('profile-photo-input').addEventListener('change',async event=>{
  const file=event.target.files[0];event.target.value='';if(!file||state.busy||state.sending||state.templateSelecting||state.photoSaving)return;
  if(file.size>5*1024*1024){toast('证件照不能超过 5 MB');return;}
  state.photoSaving=true;controls();
  try{const body=new FormData();body.append('photo',file);await api('/profile/photo',{method:'PUT',body});await refreshPhoto();toast('证件照已保存，下一次生成简历会自动使用。');}
  catch(error){toast(error.message);}finally{state.photoSaving=false;controls();}
});
$('remove-profile-photo').addEventListener('click',async()=>{
  if(state.busy||state.sending||state.templateSelecting||state.photoSaving)return;state.photoSaving=true;controls();
  try{await api('/profile/photo',{method:'DELETE'});await refreshPhoto();toast('证件照已移除，旧简历保持不变。');}
  catch(error){toast(error.message);}finally{state.photoSaving=false;controls();}
});
function renderMarkdown(){
  $('markdown-view').innerHTML=DOMPurify.sanitize(marked.parse(state.markdown),{USE_PROFILES:{html:true},FORBID_TAGS:['img','video','audio','source','iframe','form','input','button','style'],FORBID_ATTR:['style']});
  $('markdown-view').querySelectorAll('a').forEach(link=>{link.target='_blank';link.rel='noopener noreferrer';});
}
async function refreshMaterials(){
  const material=await api('/materials');
  if(state.dirty)return;
  if(state.markdown!==material.markdown){materialHistory.reset();beforeMaterialInput=null;}
  state.markdown=material.markdown;state.revision=material.revision;updateHistoryControls();
  $('markdown-editor').value=state.markdown;renderMarkdown();$('save-state').textContent='已保存到后端';
}
function updateHistoryControls(){
  const blocked=!state.editing||!state.ready||$('markdown-editor').disabled;
  $('undo-materials').disabled=blocked||!materialHistory.canUndo;
  $('redo-materials').disabled=blocked||!materialHistory.canRedo;
}
function materialSnapshot(text=state.markdown){const editor=$('markdown-editor');return {text,start:editor.selectionStart,end:editor.selectionEnd};}
function editMaterials(text,{record=true,before=null,group=''}={}){
  if(record)materialHistory.record(before||materialSnapshot(),materialSnapshot(text),group);
  state.markdown=text;updateHistoryControls();state.dirty=true;
  const backed=remember(draftKey(),JSON.stringify({markdown:text,revision:state.revision}));
  $('save-state').textContent=backed?'有修改，待保存':'有修改，本机备份不可用';
  clearTimeout(saveTimer);saveTimer=setTimeout(()=>void flushMaterials().catch(error=>toast(error.message)),900);
}
async function flushMaterials(){
  if(saving)return saving;
  if(!state.dirty)return;
  if(!state.ready||state.busy)throw new Error('助手正在处理或后端未连接，草稿尚未保存');
  saving=(async()=>{
    while(state.dirty){
      const text=state.markdown;
      $('save-state').textContent='正在保存…';
      try{
        const result=await api('/materials',{method:'PUT',body:{markdown:text,revision:state.revision}});
        state.revision=result.revision;
        if(state.markdown===text){state.dirty=false;forget(draftKey());$('save-state').textContent='已保存到后端';}
      }catch(error){$('save-state').textContent='未保存：'+error.message;
        if(error.status===409){$('conflict-actions').hidden=false;}
        throw error;
      }
    }
  })();
  try{return await saving;}finally{saving=null;}
}
function setEditing(editing){state.editing=editing;$('markdown-editor').hidden=!editing;$('markdown-view').hidden=editing;$('save-materials').hidden=!editing;$('material-history-controls').hidden=!editing;updateHistoryControls();$('toggle-edit').setAttribute('aria-pressed',String(editing));$('toggle-edit').querySelector('span').textContent=editing?'阅读视图':'编辑文档';if(editing){$('markdown-editor').value=state.markdown;$('markdown-editor').focus();}else renderMarkdown();}
$('toggle-edit').addEventListener('click',()=>setEditing(!state.editing));
$('markdown-editor').addEventListener('beforeinput',()=>{beforeMaterialInput=materialSnapshot();});
$('markdown-editor').addEventListener('input',event=>{
  const group=['insertText','insertCompositionText','deleteContentBackward','deleteContentForward'].includes(event.inputType)?event.inputType:'';
  editMaterials(event.target.value,{before:beforeMaterialInput,group});beforeMaterialInput=null;
});
function restoreMaterialEdit(direction){
  if(!state.editing||!state.ready||$('markdown-editor').disabled)return;
  const snapshot=materialHistory[direction]();if(!snapshot)return;
  const editor=$('markdown-editor'),top=editor.scrollTop;
  editor.value=snapshot.text;editMaterials(snapshot.text,{record:false});
  editor.focus({preventScroll:true});editor.setSelectionRange(snapshot.start,snapshot.end);editor.scrollTop=top;beforeMaterialInput=null;
}
$('undo-materials').onclick=()=>restoreMaterialEdit('undo');
$('redo-materials').onclick=()=>restoreMaterialEdit('redo');
$('markdown-editor').addEventListener('keydown',event=>{
  if(event.isComposing||event.altKey||!(event.ctrlKey||event.metaKey))return;
  const key=event.key.toLowerCase();
  if(key==='z'||key==='y'){event.preventDefault();restoreMaterialEdit(key==='y'||event.shiftKey?'redo':'undo');}
});
$('save-materials').addEventListener('click',()=>void flushMaterials().catch(error=>toast(error.message)));
function download(text,name,type){const url=URL.createObjectURL(new Blob([text],{type}));const link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}
$('export-md').addEventListener('click',()=>download(state.markdown,'项目经历库.md','text/markdown;charset=utf-8'));
$('reload-materials').addEventListener('click',async()=>{
  download(state.markdown,'项目经历库-未合并草稿.md','text/markdown;charset=utf-8');
  const oldText=state.markdown,oldRevision=state.revision;
  try{state.dirty=false;await refreshMaterials();forget(draftKey());$('conflict-actions').hidden=true;toast('已下载草稿备份并读取最新素材，可导入草稿进行合并');}
  catch(error){state.markdown=oldText;state.revision=oldRevision;state.dirty=true;toast(error.message);}
});
$('restore-draft').addEventListener('click',()=>{state.imported=stored(oldKey)||'';$('import-description').textContent='发现旧版网页保留的本机素材。可追加到当前素材库或替换当前内容，原本机草稿仍会保留。';$('import-dialog').showModal();});
$('import-md').addEventListener('click',()=>$('md-input').click());
$('md-input').addEventListener('change',async event=>{
  const file=event.target.files[0];event.target.value='';if(!file)return;
  if(!/\.(md|markdown)$/i.test(file.name)||file.size>1024*1024){toast('请选择 1 MB 以内的 Markdown 文件');return;}
  try{state.imported=await file.text();$('import-description').textContent='已读取「'+file.name+'」，请选择追加或替换。替换前可先下载当前文档备份。';$('import-dialog').showModal();}catch{toast('无法读取文件');}
});
async function commitImport(replace){
  if(!state.ready||state.busy){toast('请等待当前任务完成');return;}
  editMaterials(replace?state.imported:state.markdown+'\n\n'+state.imported);$('markdown-editor').value=state.markdown;renderMarkdown();$('import-dialog').close();
  try{await flushMaterials();toast('素材已保存到后端');}catch(error){toast(error.message);}
}
$('import-append').addEventListener('click',()=>void commitImport(false));$('import-replace').addEventListener('click',()=>void commitImport(true));
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
const mobileLayout=window.matchMedia('(max-width: 600px)');
mobileLayout.addEventListener('change',route);
function route(){
  const names={menu:'工作空间',account:'账号设置',library:'我的素材库',versions:'简历版本',templates:'模板库',workspace:'简历工作台'};
  const requested=location.hash.slice(1);
  const hash=mobileLayout.matches&&!requested?'menu':requested;
  const home=!Object.hasOwn(names,hash)||(hash==='menu'&&!mobileLayout.matches);
  $('home-view').hidden=!home;$('app-shell').hidden=home;
  document.title=home?'Resume · 把经历写成机会':names[hash]+' · Resume';
  document.querySelectorAll('[data-route]').forEach(link=>{const active=link.dataset.route===(home?'home':hash);link.classList.toggle('active',active);link.setAttribute('aria-label',names[link.dataset.route]||'首页');if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
  if(home){if(hash==='how-it-works')requestAnimationFrame(()=>$('how-it-works').scrollIntoView({block:'start'}));return;}
  const selected=hash;
  for(const name of Object.keys(names))$(name+'-view').hidden=name!==selected;
  $('page-title').textContent=names[selected];$('new-chat').hidden=selected!=='workspace';
  if(state.guest&&selected==='account'){showLogin();return;}
  if(state.ready&&selected==='library'&&!state.dirty)void refreshMaterials().catch(error=>toast(error.message));
  if(state.ready&&selected==='versions')void refreshVersions().catch(error=>toast(error.message));
  if(state.ready&&selected==='account')void refreshAccount().catch(error=>toast(error.message));
  if(state.ready&&selected==='templates')void refreshTemplates().catch(error=>toast(error.message));
}
window.addEventListener('hashchange',route);
function appendMessage(role,text,attachments=[]){
  const message=document.createElement('div');message.className='message '+role;
  if(role==='assistant'){const label=document.createElement('span');label.className='message-label';label.textContent='简历助手';message.append(label);}
  if(attachments.length){const row=document.createElement('div');row.className='message-files';for(const item of attachments){const button=document.createElement('button');button.className='message-file';button.textContent=item.name;button.addEventListener('click',()=>showAttachment(item));row.append(button);}message.append(row);}
  const body=document.createElement('div');body.className='message-body';if(role==='assistant'&&text){body.classList.add('formatted');body.innerHTML=DOMPurify.sanitize(marked.parse(text),{ALLOWED_TAGS:['p','br','strong','em','del','ul','ol','li','blockquote','pre','code','h1','h2','h3','h4','h5','h6','hr','a','table','thead','tbody','tr','th','td'],ALLOWED_ATTR:['href','title','start'],ALLOW_DATA_ATTR:false});}else body.textContent=text;message.append(body);const scroll=$('chat-scroll'),follow=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<80;$('conversation').append(message);$('welcome').hidden=true;if(follow)scroll.scrollTop=scroll.scrollHeight;return message;
}
async function loadHistory({preserveScroll=false}={}){const result=await api('/sessions/'+state.profile.session_id+'/messages');const scroll=$('chat-scroll'),top=scroll.scrollTop,follow=scroll.scrollHeight-top-scroll.clientHeight<80;$('conversation').replaceChildren();liveReply=null;$('welcome').hidden=Boolean(result.messages.length);for(const message of result.messages){const element=appendMessage(message.role,message.timeline?'':message.content,message.attachments||[]);if(message.timeline){const saved=createReplyProgress(element,scroll);saved.timeline(message.timeline);saved.finish();if(message.incomplete){const note=document.createElement('div');note.className='reply-step';note.textContent='本轮未完成，已保留收到的内容。';element.append(note);}}}if(preserveScroll&&!follow)scroll.scrollTop=top;}
function rememberProfile(){if(!remember(profileKey,JSON.stringify(state.profile)))toast('浏览器无法保存当前会话位置，登录身份仍由服务器维护');}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function watchTask(task){
  const generation=authGeneration;
  taskStreamController?.abort();
  const controller=new AbortController();taskStreamController=controller;
  state.busy=true;state.task=task;controls();
  if(task.kind!=='template'&&task.kind!=='latex')taskStatus('正在思考…');
  let currentResult=null;
  const updateStatus=result=>{
    if(generation!==authGeneration||controller.signal.aborted)return;
    task.kind=result.kind;currentResult=result;
    if(result.selected_template){state.task.selected_template=result.selected_template;$('selected-template-name').textContent=result.selected_template.name+'（本次已锁定）';}
    if(result.timeline&&!['template','latex'].includes(result.kind))ensureLiveReply().timeline(result.timeline);
    if(['completed','failed'].includes(result.status))return;
    if(result.kind==='latex')return;
    taskStatus(taskStage(result,liveReply?.hasText()));
  };
  const updateText=payload=>{
    if(generation!==authGeneration||controller.signal.aborted||['template','latex'].includes(task.kind))return;
    const reply=ensureLiveReply();if(payload.timeline){reply.timeline(payload.timeline);return;}reply.text(payload);
    reply.status(currentResult?taskStage(currentResult,reply.hasText()):'正在思考…');
  };
  remember('resume-studio:pending:'+state.profile.user_id,JSON.stringify(task));
  try{
    while(true){
      if(generation!==authGeneration||controller.signal.aborted)return;
      const path='/sessions/'+task.session_id+'/tasks/'+task.task_id;
      let result;
      const connection=new AbortController();
      const stop=()=>connection.abort();controller.signal.addEventListener('abort',stop,{once:true});
      let idle;
      const heartbeat=()=>{clearTimeout(idle);idle=setTimeout(stop,15000);};
      try{
        heartbeat();
        const response=await api(path+'/stream',{signal:connection.signal},true);
        result=await readTaskStream(response,{signal:connection.signal,onText:payload=>{heartbeat();updateText(payload);},onTask:payload=>{heartbeat();updateStatus(payload);}});
      }catch(error){
        if(generation!==authGeneration||controller.signal.aborted)return;
        // A lost stream does not resubmit work. Recover state, then reconnect.
        try{result=await api(path);updateStatus(result);}
        catch(recoveryError){
          if(generation!==authGeneration||controller.signal.aborted)return;
          if(recoveryError.status&&recoveryError.status<500)throw recoveryError;
          taskStatus('连接暂时中断，正在自动重连；任务会继续处理…');
          await wait(1500);continue;
        }
      }finally{clearTimeout(idle);controller.signal.removeEventListener('abort',stop);connection.abort();}
      if(generation!==authGeneration||controller.signal.aborted)return;
      if(result.status==='completed'||result.status==='failed'){
        forget('resume-studio:pending:'+state.profile.user_id);
        await loadHistory({preserveScroll:true});
        if(result.status==='failed'){
          if(!result.timeline&&!['template','latex'].includes(result.kind))appendMessage('user',result.message,result.attachments||[]);
          taskStatus(result.error,true,result.kind==='latex'?null:()=>retryTask(task));
        }else if(result.kind==='template')templateStatus('模板已转换，请预览后选择使用。');
        else taskStatus('');
        state.busy=false;state.task=null;controls();
        const refreshed=await Promise.allSettled([refreshMaterials(),refreshVersions(),refreshTemplates()]);
        if(generation!==authGeneration||controller.signal.aborted)return;
        const failure=refreshed.find(item=>item.status==='rejected');if(failure)toast('回复已接收，资料列表刷新失败，请刷新页面。');
        return result;
      }
      await wait(1000);
    }
  }catch(error){if(generation!==authGeneration||controller.signal.aborted)return;taskStatus(error.message+'；任务可能仍在运行。',true,()=>watchTask(task));throw error;}
  finally{controller.abort();if(taskStreamController===controller)taskStreamController=null;}
}
async function retryTask(task){
  try{const result=await api('/sessions/'+task.session_id+'/tasks/'+task.task_id+'/retry',{method:'POST'});await watchTask({task_id:result.task_id,session_id:task.session_id,kind:task.kind});}
  catch(error){toast(error.message);}
}
async function bootstrap(){
  try{
    const health=await api('/health');if(health.app!=='resume-agent')throw new Error('当前是静态页面，请启动 api:app 后端服务');
    const auth=await api('/auth/me');state.csrf=auth.csrf;state.account=auth.account;state.guest=false;document.body.classList.remove('guest');$('account-view').querySelectorAll(':scope > :not(.guest-message)').forEach(node=>node.hidden=false);$('account-guest')?.remove();$('admin-panel').hidden=true;
    let cached=null;try{cached=JSON.parse(stored(profileKey)||'null');}catch{}
    if(cached?.token)remember('resume-studio:legacy-profile:v1',JSON.stringify(cached));
    state.profile={user_id:auth.account.user_id,session_id:cached?.user_id===auth.account.user_id?cached.session_id:null};
    const sessions=(await api('/sessions')).sessions;
    if(!sessions.some(item=>item.session_id===state.profile.session_id))state.profile.session_id=sessions[0]?.session_id;
    if(!state.profile.session_id){const session=await api('/sessions',{method:'POST'});state.profile.session_id=session.session_id;}
    rememberProfile();$('account-link').textContent=auth.account.display_name;
    await refreshMaterials();await loadHistory();await refreshPhoto();await refreshTemplates();
    const draft=stored(draftKey());if(draft){try{const data=JSON.parse(draft);state.markdown=data.markdown;state.revision=data.revision;state.dirty=true;$('markdown-editor').value=state.markdown;renderMarkdown();$('save-state').textContent='已恢复未保存草稿，请核对后保存';}catch{}}
    $('restore-draft').hidden=!stored(oldKey);
    state.ready=true;controls();$('workspace-mode').textContent='已登录 · 资料独立保存';document.querySelectorAll('[data-login]').forEach(button=>button.textContent='我的账号');$('connection-status').textContent=health.restart_required?'后端需重启':(health.model_configured?'后端已连接':'模型未配置');
    const active=(await api('/tasks/active')).task;
    let pending=null;try{pending=JSON.parse(stored('resume-studio:pending:'+state.profile.user_id)||'null');}catch{}
    if(active||pending){
      const task=active?{task_id:active.id,session_id:active.session_id,kind:active.kind}:pending;
      state.profile.session_id=task.session_id;rememberProfile();await loadHistory();
      if(active&&!['template','latex'].includes(active.kind))appendMessage('user',active.message,active.attachments||[]);
      void watchTask(task).catch(error=>toast(error.message));
    }else{
      const recent=(await api('/sessions/'+state.profile.session_id+'/tasks')).tasks[0];
      if(recent?.status==='failed'){const retry=recent.kind==='latex'?null:()=>retryTask({task_id:recent.id,session_id:recent.session_id,kind:recent.kind});if(recent.kind==='template')templateStatus(recent.error,true,retry);else if(recent.kind!=='latex'){const detail=await api('/sessions/'+recent.session_id+'/tasks/'+recent.id);if(!detail.timeline)appendMessage('user',recent.message);taskStatus(recent.error,true,retry);}}
    }
    route();
  }catch(error){state.ready=false;controls();if(error.status===401){enterGuest();return;}$('connection-status').textContent='连接失败';taskStatus(error.message,true,bootstrap);}
}
function clearPreview(){
  ++state.pdfGeneration;state.currentFile=null;
  if(state.pdfURL)URL.revokeObjectURL(state.pdfURL);state.pdfURL=null;
  $('pdf-panel').hidden=true;$('pdf-canvas').hidden=true;$('pdf-name').textContent='';$('pdf-pages').textContent='';
  $('download-pdf').removeAttribute('href');$('open-pdf').removeAttribute('href');$('delete-version').disabled=true;$('edit-latex').disabled=true;$('download-latex').disabled=true;
}
async function refreshVersions(){
  const generation=++state.versionsGeneration;
  const {files}=await api('/versions');if(generation!==state.versionsGeneration)return;
  state.versions=files;$('versions-count').textContent=files.length+' 份';$('version-list').replaceChildren();
  if(!files.length){const empty=document.createElement('div');empty.className='empty-versions';empty.textContent='还没有生成的简历。可以在工作台告诉助手你的目标岗位并生成简历。';$('version-list').append(empty);clearPreview();return;}
  for(const file of files){const button=document.createElement('button');button.className='version-card';button.setAttribute('aria-pressed',String(state.currentFile===file.file_id));const icon=document.createElement('span');icon.className='pdf-icon';icon.textContent='PDF';const text=document.createElement('span');const title=document.createElement('strong');title.textContent=file.name;const small=document.createElement('small');small.textContent=(file.kind==='latex'?'手动微调 · ':'')+(file.selected_template?file.selected_template.name+' · ':'')+new Date(file.created_at).toLocaleString('zh-CN');text.append(title,small);button.append(icon,text);button.dataset.fileId=file.file_id;button.addEventListener('click',()=>void previewFile(file).catch(error=>toast(error.message)));$('version-list').append(button);}
  if(!files.some(file=>file.file_id===state.currentFile)){clearPreview();if(location.hash==='#versions')await previewFile(files[0]);}
}
$('delete-version').addEventListener('click',()=>{
  const file=state.versions.find(file=>file.file_id===state.currentFile);if(!file||state.deleting)return;
  state.deleteTarget=file;$('delete-version-name').textContent=file.name;$('delete-version-error').textContent='';$('delete-version-dialog').showModal();
});
$('delete-version-dialog').addEventListener('cancel',event=>{if(state.deleting)event.preventDefault();});
$('confirm-delete-version').addEventListener('click',async()=>{
  if(!state.deleteTarget||state.deleting)return;
  const file=state.deleteTarget;state.deleting=true;++state.versionsGeneration;
  $('confirm-delete-version').disabled=true;$('cancel-delete-version').disabled=true;$('delete-version').disabled=true;$('delete-version-error').textContent='';
  try{
    await api('/versions/'+file.file_id,{method:'DELETE'});
    if(state.currentFile===file.file_id)clearPreview();
    state.deleteTarget=null;$('delete-version-dialog').close();toast('已删除「'+file.name+'」');
    await refreshVersions();
  }catch(error){
    if(error.status===404){if(state.currentFile===file.file_id)clearPreview();$('delete-version-dialog').close();await refreshVersions().catch(e=>toast(e.message));toast('该版本已不存在，列表已更新');}
    else if($('delete-version-dialog').open)$('delete-version-error').textContent=error.message;
    else toast('版本已删除，列表刷新失败：'+error.message);
  }finally{
    state.deleting=false;$('confirm-delete-version').disabled=false;$('cancel-delete-version').disabled=false;
    $('delete-version').disabled=!state.versions.some(file=>file.file_id===state.currentFile);
  }
});
async function previewFile(file){
  const generation=++state.pdfGeneration;state.currentFile=file.file_id;$('edit-latex').disabled=false;$('download-latex').disabled=false;$('delete-version').disabled=state.deleting;document.querySelectorAll('.version-card').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.fileId===file.file_id)));$('pdf-panel').hidden=false;$('pdf-canvas').hidden=true;$('pdf-status').hidden=false;$('pdf-status').textContent='正在加载预览…';$('pdf-name').textContent=file.name;
  $('download-pdf').removeAttribute('href');$('open-pdf').removeAttribute('href');
  let loading;
  try{
    const response=await fetch('/sessions/'+file.session_id+'/files/'+file.file_id,{credentials:'same-origin',headers:{'X-Resume-Account':state.profile.user_id},cache:'no-store'});
    if(!response.ok)throw new Error('无法读取该简历，请重试');
    const blob=await response.blob();if(generation!==state.pdfGeneration)return;
    if(state.pdfURL)URL.revokeObjectURL(state.pdfURL);state.pdfURL=URL.createObjectURL(blob);
    $('download-pdf').href=state.pdfURL;$('download-pdf').download=file.name;$('open-pdf').href=state.pdfURL;
    loading=pdfjs.getDocument({...pdfOptions,data:new Uint8Array(await blob.arrayBuffer())});const doc=await loading.promise;const page=await doc.getPage(1);const viewport=page.getViewport({scale:1.8});const canvas=document.createElement('canvas');canvas.width=viewport.width;canvas.height=viewport.height;await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise;
    if(generation!==state.pdfGeneration)return;
    const target=$('pdf-canvas');target.width=canvas.width;target.height=canvas.height;target.getContext('2d').drawImage(canvas,0,0);target.hidden=false;$('pdf-status').hidden=true;$('pdf-pages').textContent='第 1 页 / 共 '+doc.numPages+' 页';
  }catch(error){if(generation===state.pdfGeneration){$('pdf-status').textContent=error.message+'，可下载原文件查看。';}}
  finally{if(loading)await loading.destroy();}
}
let attachmentURL=null;
async function showAttachment(item){
  try{
    let file=item.file;
    if(!file){
      const response=await fetch('/sessions/'+state.profile.session_id+'/uploads/'+item.id,{credentials:'same-origin',headers:{'X-Resume-Account':state.profile.user_id},cache:'no-store'});
      if(!response.ok)throw new Error('无法读取附件');file=await response.blob();
    }
    if(attachmentURL)URL.revokeObjectURL(attachmentURL);attachmentURL=URL.createObjectURL(file);
    $('attachment-title').textContent=item.name;
    $('attachment-description').textContent=item.id?'已保存的原始附件':'待发送附件。发送后由助手读取并处理。';
    const image=$('attachment-image');image.hidden=!/\.(png|jpe?g|webp)$/i.test(item.name);
    image.removeAttribute('src');if(!image.hidden)image.src=attachmentURL;
    $('attachment-download').href=attachmentURL;$('attachment-download').download=item.name;
    $('attachment-dialog').showModal();
  }catch(error){toast(error.message);}
}
$('attachment-dialog').addEventListener('close',()=>{if(attachmentURL)URL.revokeObjectURL(attachmentURL);attachmentURL=null;$('attachment-image').removeAttribute('src');});
function renderAttachments(){
  $('attachments').replaceChildren();
  for(const item of state.attachments){
    const card=document.createElement('div');card.className='attachment ready';const button=document.createElement('button');button.type='button';
    const title=document.createElement('strong');title.textContent=item.name;const caption=document.createElement('small');caption.textContent='待发送 · 点击查看';
    button.append(title,caption);button.addEventListener('click',()=>void showAttachment(item));
    const remove=document.createElement('button');remove.type='button';remove.className='remove';remove.textContent='×';remove.disabled=state.busy;remove.setAttribute('aria-label','移除 '+item.name);
    remove.addEventListener('click',()=>{state.attachments=state.attachments.filter(entry=>entry!==item);renderAttachments();});
    card.append(button,remove);$('attachments').append(card);
  }
  controls();
}
function addAttachments(files){
  if(!requireLogin())return;
  if(!state.ready||state.busy){toast('请等待当前任务完成');return;}
  for(const file of files){
    if(state.attachments.length>=3){toast('每条消息最多添加 3 份附件');break;}
    if(!/\.(pdf|docx|png|jpe?g|webp|txt|md)$/i.test(file.name)||file.size===0||file.size>15*1024*1024){toast('请选择 15 MB 以内的 PDF、DOCX、图片或文本文件');continue;}
    state.attachments.push({name:file.name,file});
  }
  renderAttachments();
}
$('attach-button').addEventListener('click',()=>$('file-input').click());
$('file-input').addEventListener('change',event=>{addAttachments([...event.target.files]);event.target.value='';});
$('message-input').addEventListener('paste',event=>{
  const files=[...event.clipboardData.files];if(files.length){event.preventDefault();addAttachments(files);}
});
$('chat-form').addEventListener('dragover',event=>event.preventDefault());
$('chat-form').addEventListener('drop',event=>{event.preventDefault();addAttachments([...event.dataTransfer.files]);});
$('chat-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!requireLogin())return;if(state.busy||state.sending)return;if(state.templateSelecting){toast('正在保存模板选择，请稍等。');return;}
  const message=$('message-input').value.trim();const items=[...state.attachments];if(!message&&!items.length)return;
  const generation=authGeneration;
  let pendingMessage=null,accepted=false;
  state.sending=true;controls();
  try{
    if(!state.templateCatalog?.selected_id)await refreshTemplates();
    const template_id=state.templateCatalog?.selected_id;
    if(!template_id)throw new Error('请先在模板库选择本次使用的模板');
    await flushMaterials();taskStatus('');pendingMessage=appendMessage('user',message||'请查看附件。',items);
    taskStatus(items.length?'正在发送消息和附件…':'正在发送…');
    let body={message,template_id};
    if(items.length){body=new FormData();body.append('message',message);body.append('template_id',template_id);for(const item of items)body.append('files',item.file,item.name);}
    const result=await api('/sessions/'+state.profile.session_id+'/messages',{method:'POST',body});
    accepted=true;state.attachments=[];renderAttachments();if($('message-input').value.trim()===message){$('message-input').value='';$('message-input').style.height='';}
    state.sending=false;
    await watchTask({task_id:result.task_id,session_id:state.profile.session_id,kind:'chat'});
  }catch(error){if(generation!==authGeneration)return;if(!accepted)pendingMessage?.remove();if(!state.task){state.busy=false;controls();taskStatus(error.message,true);}toast(error.message);}
  finally{state.sending=false;controls();}
});
$('message-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('chat-form').requestSubmit();}});
$('message-input').addEventListener('input',event=>{event.target.style.height='auto';event.target.style.height=Math.min(event.target.scrollHeight,200)+'px';});
$('new-chat').addEventListener('click',()=>$('new-chat-dialog').showModal());
$('confirm-new-chat').addEventListener('click',async()=>{
  if(state.busy)return;
  try{await flushMaterials();const result=await api('/sessions',{method:'POST'});state.profile.session_id=result.session_id;rememberProfile();state.attachments=[];renderAttachments();await loadHistory();$('message-input').value='';taskStatus('');$('new-chat-dialog').close();}catch(error){toast(error.message);}
});
const templateCategoryNames={engineering:'技术研发',product:'产品运营',design:'设计创意',marketing:'市场销售',business:'综合职能'};
let templateFilter='all',templateCategory='all',templateGeneration=0,templateRefreshGeneration=0,templateURLs=[],templatePreviewURL=null;
function matchesTemplateCategory(item){return templateCategory==='all'||(Array.isArray(item.categories)&&item.categories.includes(templateCategory));}
function templateCategoryTags(item){
  const tags=document.createElement('div');tags.className='template-category-tags';
  const categories=Array.isArray(item.categories)?item.categories.filter(value=>Object.hasOwn(templateCategoryNames,value)):[];
  for(const category of categories.length?categories:[null]){const tag=document.createElement('span');tag.className='template-category-tag';tag.textContent=category?templateCategoryNames[category]:'未分类';tags.append(tag);}
  return tags;
}
function renderTemplateEmpty(){
  const box=document.createElement('div');box.className='template-empty';
  const title=document.createElement('h3');title.textContent=templateCategory==='all'?'暂无模板':'暂无'+templateCategoryNames[templateCategory]+'模板';
  const description=document.createElement('p');description.textContent=templateCategory==='all'&&templateFilter==='custom'?'上传喜欢的版式，制作你的第一份个人模板。':'当前筛选条件下还没有模板，可以切换岗位方向或查看全部模板。';
  const reset=document.createElement('button');reset.className='button';reset.type='button';reset.textContent='查看全部模板';reset.addEventListener('click',()=>{
    templateCategory='all';templateFilter='all';
    document.querySelectorAll('[data-template-category]').forEach(item=>item.setAttribute('aria-pressed',String(item.dataset.templateCategory==='all')));
    document.querySelectorAll('[data-template-filter]').forEach(item=>item.setAttribute('aria-pressed',String(item.dataset.templateFilter==='all')));
    renderTemplates();
  });
  box.append(title,description,reset);$('template-grid').append(box);
}
async function templateBlob(url){
  const response=await fetch(url,{credentials:'same-origin',headers:{'X-Resume-Account':state.profile.user_id},cache:'no-store'});
  if(!response.ok)throw new Error('无法读取模板文件，请刷新后重试');
  return response.blob();
}
async function refreshTemplates(){
  const generation=++templateRefreshGeneration;
  const data=await api('/templates');if(generation!==templateRefreshGeneration)return;
  state.templateCatalog=data;
  const locked=state.busy&&state.task?.selected_template;
  $('selected-template-name').textContent=locked?locked.name+'（本次已锁定）':data.templates.find(item=>item.id===data.selected_id)?.name||'未选择';
  renderTemplates();
}
function renderTemplates(){
  if(state.guest){renderGuestTemplates();return;}
  const generation=++templateGeneration;
  for(const url of templateURLs)URL.revokeObjectURL(url);templateURLs=[];
  const catalog=state.templateCatalog||{templates:[]};
  const items=catalog.templates.filter(item=>(templateFilter==='all'||item.source===templateFilter)&&matchesTemplateCategory(item));
  $('template-grid').replaceChildren();$('template-count').textContent=items.length+' 个模板';
  if(!items.length)renderTemplateEmpty();
  const makeButton=(label,action,primary=false)=>{const button=document.createElement('button');button.type='button';button.className='button'+(primary?' primary':'');button.textContent=label;button.addEventListener('click',()=>void action().catch(error=>toast(error.message)));return button;};
  for(const item of items){
    const card=document.createElement('article');card.className='template-card'+(catalog.selected_id===item.id?' selected':'');card.dataset.templateId=item.id;
    const cover=document.createElement('div');cover.className='template-cover';const placeholder=document.createElement('span');placeholder.className='template-placeholder';placeholder.textContent=item.status==='ready'?'模板预览':item.status==='failed'?'转换未完成':'正在转换…';cover.append(placeholder);
    if(item.thumbnail_url){const img=document.createElement('img');img.alt=item.name+'模板预览';img.hidden=true;cover.append(img);void templateBlob(item.thumbnail_url).then(blob=>{if(generation!==templateGeneration)return;const url=URL.createObjectURL(blob);templateURLs.push(url);img.src=url;img.hidden=false;placeholder.hidden=true;}).catch(()=>{placeholder.textContent='预览暂不可用';});}
    const content=document.createElement('div');content.className='template-card-content';const heading=document.createElement('div');heading.className='template-card-heading';const title=document.createElement('h3');title.textContent=item.name;const badge=document.createElement('span');badge.className='template-source';badge.textContent=item.source==='system'?'系统模板':'我的模板';heading.append(title,badge);
    const description=document.createElement('p');description.textContent=item.error||item.description;
    const actions=document.createElement('div');actions.className='template-card-actions';
    if(item.status==='ready'){
      const select=makeButton(catalog.selected_id===item.id?'已选择':'使用此模板',async()=>{
        if(state.busy||state.sending||state.templateSelecting)return;
        state.templateSelecting=true;++templateRefreshGeneration;controls();
        try{await api('/templates/selection',{method:'PUT',body:{template_id:item.id}});
          state.templateCatalog={...state.templateCatalog,selected_id:item.id};
          $('selected-template-name').textContent=item.name;
          await refreshTemplates();toast('已选择「'+item.name+'」，下一次生成将使用此模板。');
        }finally{state.templateSelecting=false;controls();}
      },catalog.selected_id!==item.id);select.dataset.templateSelect='';select.disabled=!state.ready||state.busy||state.sending||state.templateSelecting;actions.append(select);
      if(item.thumbnail_url)actions.append(makeButton('预览',()=>previewTemplate(item)));
      actions.append(makeButton('下载 LaTeX',()=>downloadTemplate(item.tex_url,item.name+'.tex')));
      if(item.pdf_url)actions.append(makeButton('示例 PDF',()=>downloadTemplate(item.pdf_url,item.name+'.pdf')));
    }else if(item.status==='failed')actions.append(makeButton('重试转换',()=>retryTask({task_id:item.task_id,session_id:item.session_id,kind:'template'})));
    if(item.original_url)actions.append(makeButton('下载原稿',()=>downloadTemplate(item.original_url,item.original_name||item.name)));
    content.append(heading,templateCategoryTags(item),description,actions);card.append(cover,content);$('template-grid').append(card);
  }
}
async function downloadTemplate(url,name){const blob=await templateBlob(url);const href=URL.createObjectURL(blob);const link=document.createElement('a');link.href=href;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(href),10000);}
async function previewTemplate(item){
  const dialog=$('template-preview-dialog');$('template-preview-title').textContent=item.name;$('template-preview-description').textContent=item.description;$('template-preview-error').textContent='正在加载预览…';$('template-preview-image').hidden=true;dialog.showModal();
  try{const blob=await templateBlob(item.thumbnail_url);if(!dialog.open)return;if(templatePreviewURL)URL.revokeObjectURL(templatePreviewURL);templatePreviewURL=URL.createObjectURL(blob);$('template-preview-image').src=templatePreviewURL;$('template-preview-image').hidden=false;$('template-preview-error').textContent='';}
  catch(error){$('template-preview-error').textContent=error.message;}
}
$('close-template-preview').addEventListener('click',()=>$('template-preview-dialog').close());
$('upload-template-button').addEventListener('click',()=>{$('template-upload-error').textContent='';$('template-upload-dialog').showModal();});
$('close-template-upload').addEventListener('click',()=>$('template-upload-dialog').close());
$('template-file-input').addEventListener('change',event=>{const file=event.target.files[0];if(file&&!$('template-name-input').value)$('template-name-input').value=file.name.replace(/\.[^.]+$/,'').slice(0,100);});
$('template-retry-task').addEventListener('click',()=>{if(retryAction)void retryAction();});
for(const button of document.querySelectorAll('[data-template-filter]'))button.addEventListener('click',()=>{templateFilter=button.dataset.templateFilter;document.querySelectorAll('[data-template-filter]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));renderTemplates();});
for(const button of document.querySelectorAll('[data-template-category]'))button.addEventListener('click',()=>{templateCategory=button.dataset.templateCategory;document.querySelectorAll('[data-template-category]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));renderTemplates();});
$('template-upload-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!state.ready||state.busy)return;
  const file=$('template-file-input').files[0];if(!file)return;
  if(file.size>15*1024*1024){$('template-upload-error').textContent='文件不能超过 15 MB';return;}
  try{await flushMaterials();state.busy=true;controls();const body=new FormData();body.append('file',file);body.append('name',$('template-name-input').value.trim());templateStatus('正在上传模板…');
    const result=await api('/sessions/'+state.profile.session_id+'/templates',{method:'POST',body});$('template-upload-dialog').close();$('template-upload-form').reset();void refreshTemplates().catch(error=>toast(error.message));
    await watchTask({task_id:result.task_id,session_id:state.profile.session_id,kind:'template'});
  }catch(error){if(!state.task){state.busy=false;controls();}if($('template-upload-dialog').open)$('template-upload-error').textContent=error.message;templateStatus(error.message,true);toast(error.message);}
});

let resetUser=null,bindChallenge=null,bindNextSend=0,bindBusy=false,accountSmsEnabled=false;
async function refreshAccount(){
 const [auth,config]=await Promise.all([api('/auth/me'),api('/auth/config')]);state.account=auth.account;state.csrf=auth.csrf;
 $('password-panel').hidden=!auth.account.has_password;accountSmsEnabled=!!config.sms_enabled;$('phone-panel').hidden=!accountSmsEnabled;
 $('bound-phone').textContent=auth.account.phone?'已绑定：'+auth.account.phone:'绑定手机号后，可用短信验证码登录。';$('bind-phone-form').hidden=!!auth.account.phone;bindChallenge=null;updateBindControls();
 $('account-identity').textContent=auth.account.display_name+' · '+(auth.account.role==='admin'?'管理员':'普通用户');
 $('admin-panel').hidden=auth.account.role!=='admin';if(auth.account.role==='admin')await refreshUsers();
}
$('logout-button').addEventListener('click',async()=>{try{await flushMaterials();await api('/auth/logout',{method:'POST'});location.replace('/');}catch(error){toast(error.message);}});
$('password-form').addEventListener('submit',async event=>{
 event.preventDefault();if($('new-password').value!==$('new-password-confirm').value){$('password-message').textContent='两次密码不一致';return;}
 const button=event.target.querySelector('button');button.disabled=true;
 try{const result=await api('/auth/password',{method:'PUT',body:{old_password:$('current-password').value,password:$('new-password').value}});state.csrf=result.csrf;event.target.reset();$('password-message').textContent='密码已修改，其他设备的登录已失效。';}catch(error){$('password-message').textContent=error.message;}finally{button.disabled=false;}
});
function updateBindControls(){const seconds=Math.max(0,Math.ceil((bindNextSend-Date.now())/1000));$('send-bind-code').disabled=bindBusy||seconds>0||!accountSmsEnabled;$('send-bind-code').textContent=seconds?seconds+' 秒后重发':'发送验证码';$('bind-submit').disabled=bindBusy||!bindChallenge;$('bind-phone').disabled=bindBusy;$('bind-code').disabled=bindBusy;}
setInterval(updateBindControls,500);
$('bind-phone').addEventListener('input',()=>{bindChallenge=null;$('bind-code').value='';$('bind-message').textContent='';updateBindControls();});
$('send-bind-code').addEventListener('click',async()=>{
 if(bindBusy||Date.now()<bindNextSend||!$('bind-phone').reportValidity())return;
 bindBusy=true;bindChallenge=null;updateBindControls();$('bind-message').textContent='';
 try{const result=await api('/auth/sms/send',{method:'POST',body:{phone:$('bind-phone').value.trim(),purpose:'bind'}});bindChallenge=result.challenge;bindNextSend=Date.now()+result.retry_after*1000;$('bind-message').textContent='短信已提交，请查看手机验证码。';}
 catch(error){$('bind-message').textContent=error.message;}finally{bindBusy=false;updateBindControls();}
});
$('bind-phone-form').addEventListener('submit',async event=>{
 event.preventDefault();if(bindBusy||!bindChallenge)return;bindBusy=true;updateBindControls();
 try{await api('/auth/phone',{method:'PUT',body:{phone:$('bind-phone').value.trim(),code:$('bind-code').value.trim(),challenge:bindChallenge}});event.target.reset();await refreshAccount();toast('手机号已绑定，可用短信登录');}
 catch(error){$('bind-message').textContent=error.message;}finally{bindBusy=false;updateBindControls();}
});
async function refreshUsers(){
 const data=await api('/admin/users');$('admin-users').replaceChildren();
 for(const user of data.users){const row=document.createElement('div');row.className='admin-user';const info=document.createElement('p');info.textContent=user.display_name;const detail=document.createElement('small');detail.textContent=[user.username||user.phone,user.role==='admin'?'管理员':'普通用户',user.enabled?'已启用':'已停用'].join(' · ');info.append(detail);const actions=document.createElement('div');actions.className='admin-user-actions';
 const toggle=document.createElement('button');toggle.className='button';toggle.textContent=user.enabled?'停用账号':'恢复账号';toggle.disabled=user.user_id===state.profile.user_id;
 toggle.addEventListener('click',async()=>{if(user.enabled&&!confirm('停用「'+user.display_name+'」？该用户将退出登录，已有资料会保留。'))return;toggle.disabled=true;try{await api('/admin/users/'+user.user_id,{method:'PATCH',body:{enabled:!user.enabled}});await refreshUsers();}catch(error){$('admin-message').textContent=error.message;toggle.disabled=false;}});
 const reset=document.createElement('button');reset.className='button';reset.textContent='重置密码';reset.addEventListener('click',()=>{resetUser=user;$('reset-user-name').textContent='账号：'+user.display_name;$('reset-user-message').textContent='';$('reset-user-form').reset();$('reset-user-dialog').showModal();});actions.append(toggle,reset);row.append(info,actions);$('admin-users').append(row);}
}
$('refresh-users').addEventListener('click',()=>void refreshUsers().catch(error=>{$('admin-message').textContent=error.message;}));
$('reset-user-form').addEventListener('submit',async event=>{event.preventDefault();if(!resetUser)return;const button=event.target.querySelector('button[type=submit]');button.disabled=true;try{await api('/admin/users/'+resetUser.user_id+'/password',{method:'PUT',body:{password:$('reset-user-password').value}});$('reset-user-dialog').close();event.target.reset();toast('密码已重置，该账号需重新登录');if(resetUser.user_id===state.profile.user_id)location.replace('/');}catch(error){$('reset-user-message').textContent=error.message;}finally{button.disabled=false;}});

window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
const resumeEditor=createResumeEditor({api,state,pdfjs,pdfOptions,refreshVersions,previewFile,download});
$('edit-latex').addEventListener('click',()=>resumeEditor.open(state.versions.find(file=>file.file_id===state.currentFile)));
$('download-latex').addEventListener('click',()=>{const file=state.versions.find(file=>file.file_id===state.currentFile);if(file)void downloadTemplate('/versions/'+file.file_id+'/source?download=1',file.name.replace(/\.pdf$/i,'-latex.zip')).catch(error=>toast(error.message));});
controls();route();void bootstrap();
