import * as pdfjs from './vendor/pdf.min.mjs';
pdfjs.GlobalWorkerOptions.workerSrc = new URL('./vendor/pdf.worker.min.mjs', import.meta.url).href;
const pdfOptions = {cMapUrl: new URL('./vendor/cmaps/', import.meta.url).href, cMapPacked:true,
  standardFontDataUrl:new URL('./vendor/standard_fonts/',import.meta.url).href,
  wasmUrl:new URL('./vendor/wasm/',import.meta.url).href,isEvalSupported:false};
const $ = id => document.getElementById(id);
const profileKey='resume-studio:backend-profile:v1';
const oldKey='resume-studio:prototype:materials:v2';
const state={markdown:'',revision:'',editing:false,dirty:false,ready:false,busy:false,
  profile:null,attachments:[],selected:null,imported:'',versions:[],pdfURL:null,pdfGeneration:0,versionsGeneration:0,deleting:false,deleteTarget:null,task:null};
let toastTimer,saveTimer,saving=null,retryAction=null;
function toast(text){$('toast').textContent=text;$('toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('show'),4500);}
function stored(key){try{return localStorage.getItem(key);}catch{return null;}}
function remember(key,value){try{localStorage.setItem(key,value);return true;}catch{return false;}}
function forget(key){try{localStorage.removeItem(key);}catch{}}
function draftKey(){return 'resume-studio:unsaved:'+state.profile.user_id;}
async function api(path,options={}){
  const headers=new Headers(options.headers||{});
  headers.set('X-Resume-Client','browser');if(state.profile?.user_id)headers.set('X-Resume-Account',state.profile.user_id);if(state.csrf)headers.set('X-CSRF-Token',state.csrf);
  let body=options.body;
  if(body!==undefined&&!(body instanceof FormData)){headers.set('Content-Type','application/json');body=JSON.stringify(body);}
  let response;
  try{response=await fetch(path,{...options,body,headers,cache:'no-store'});}catch{throw new Error('无法连接后端，请确认本地服务仍在运行');}
  if(!response.ok){let detail;try{const problem=await response.json();detail=problem.error;if(problem.account_changed)location.reload();}catch{}const error=new Error(detail||'请求失败（HTTP '+response.status+'）');error.status=response.status;if(response.status===401&&state.ready){state.ready=false;$('app-shell').hidden=true;location.replace('/login.html'+location.hash);}throw error;}
  return response.json();
}
function controls(){
  const locked=!state.ready||state.busy||state.photoSaving;
  for(const id of ['upload-template-button','template-upload-submit','upload-profile-photo','remove-profile-photo','send-button','attach-button','new-chat','toggle-edit','import-md','restore-draft','save-materials']) $(id).disabled=locked;
  $('markdown-editor').disabled=locked;
  document.querySelectorAll('[data-template-select]').forEach(button=>button.disabled=locked);
  document.querySelectorAll('.attachment .remove').forEach(button=>button.disabled=state.busy);
}
function taskStatus(text,error=false,action=null){$('task-status').hidden=!text;$('task-label').textContent=text;$('task-status').classList.toggle('error',error);retryAction=action;$('retry-task').hidden=!action;if($('template-task-status')){$('template-task-status').hidden=!text;$('template-task-label').textContent=text;$('template-retry-task').hidden=!action;$('template-task-status').classList.toggle('error',error);}}
$('retry-task').addEventListener('click',()=>{const action=retryAction;if(action)void action();});
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
  const file=event.target.files[0];event.target.value='';if(!file||state.busy||state.photoSaving)return;
  if(file.size>5*1024*1024){toast('证件照不能超过 5 MB');return;}
  state.photoSaving=true;controls();
  try{const body=new FormData();body.append('photo',file);await api('/profile/photo',{method:'PUT',body});await refreshPhoto();toast('证件照已保存，下一次生成简历会自动使用。');}
  catch(error){toast(error.message);}finally{state.photoSaving=false;controls();}
});
$('remove-profile-photo').addEventListener('click',async()=>{
  if(state.busy||state.photoSaving)return;state.photoSaving=true;controls();
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
  state.markdown=material.markdown;state.revision=material.revision;
  $('markdown-editor').value=state.markdown;renderMarkdown();$('save-state').textContent='已保存到后端';
}
function editMaterials(text){
  state.markdown=text;state.dirty=true;
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
function setEditing(editing){state.editing=editing;$('markdown-editor').hidden=!editing;$('markdown-view').hidden=editing;$('save-materials').hidden=!editing;$('toggle-edit').setAttribute('aria-pressed',String(editing));$('toggle-edit').querySelector('span').textContent=editing?'阅读视图':'编辑文档';if(editing){$('markdown-editor').value=state.markdown;$('markdown-editor').focus();}else renderMarkdown();}
$('toggle-edit').addEventListener('click',()=>setEditing(!state.editing));
$('markdown-editor').addEventListener('input',event=>editMaterials(event.target.value));
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
function route(){
  const names={account:'账号设置',library:'我的素材库',versions:'简历版本',templates:'模板库',workspace:'简历工作台'};
  const selected=Object.hasOwn(names,location.hash.slice(1))?location.hash.slice(1):'workspace';
  for(const name of Object.keys(names))$(name+'-view').hidden=name!==selected;
  document.querySelectorAll('[data-route]').forEach(link=>{const active=link.dataset.route===selected;link.classList.toggle('active',active);link.setAttribute('aria-label',names[link.dataset.route]);if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
  $('page-title').textContent=names[selected];$('new-chat').hidden=selected!=='workspace';
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
  const body=document.createElement('div');body.className='message-body';body.textContent=text;message.append(body);$('conversation').append(message);$('welcome').hidden=true;$('chat-scroll').scrollTop=$('chat-scroll').scrollHeight;
}
async function loadHistory(){const result=await api('/sessions/'+state.profile.session_id+'/messages');$('conversation').replaceChildren();$('welcome').hidden=Boolean(result.messages.length);for(const message of result.messages)appendMessage(message.role,message.content,message.attachments||[]);}
function rememberProfile(){if(!remember(profileKey,JSON.stringify(state.profile)))toast('浏览器无法保存当前会话位置，登录身份仍由服务器维护');}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const stageLabels={queue:'排队',attachments:'附件解析与图片准备',model:'模型请求',tool:'工具',process:'子进程',save:'结果保存',quality:'内容与版面验收'};
const toolLabels={resume__read_file:'读取文件',resume__write_file:'写入文件',resume__compile_pdf:'编译与验收',resume__preview_pdf:'PDF 截图'};
let monitorResult=null;
const seconds=ms=>(ms/1000).toFixed(2)+' 秒';
function timingKey(id){return 'resume-studio:timing:'+state.profile.user_id+':'+id;}
function browserTiming(id,patch={}){
  let value={};try{value=JSON.parse(stored(timingKey(id))||'{}');}catch{}
  Object.assign(value,patch);remember(timingKey(id),JSON.stringify(value));return value;
}
function renderMonitor(result){
  if(!result?.performance)return;
  monitorResult=result;const p=result.performance;$('task-monitor').hidden=false;
  const current=p.current;const label=current?(toolLabels[current.tool]||current.command||stageLabels[current.stage]):null;
  $('monitor-summary').textContent='任务耗时 · '+seconds(p.server_total_ms)+(label?' · '+label+'已用 '+seconds(current.duration_ms):'');
  $('monitor-totals').textContent=(p.estimated?'历史日志估算':'实测')+' · 服务端 '+seconds(p.server_total_ms)+' · 模型 '+seconds(p.stage_ms.model||0)+' / '+p.model_calls+' 次'+(p.request_ms===null?' · 接收耗时未记录':' · 接收/保存上传 '+seconds(p.request_ms));
  const local=browserTiming(result.id);$('monitor-browser').textContent=[local.submit_ms!=null?'发送至受理 '+seconds(local.submit_ms):'',local.visible_ms!=null?'发送至结果显示 '+seconds(local.visible_ms):'',local.refresh_ms!=null?'结果刷新 '+seconds(local.refresh_ms):'',local.pdf_ms!=null?'最近 PDF 下载与渲染 '+seconds(local.pdf_ms):''].filter(Boolean).join(' · ')||'本浏览器没有记录此任务的端到端耗时。';
  $('monitor-rows').replaceChildren();
  for(const item of p.spans){
    const layout=item.tool==='resume__compile_pdf'?(p.layout_checks||[]).find(event=>event.time>=item.time&&(!p.spans.some(other=>other.tool==='resume__compile_pdf'&&other.started_at>item.started_at&&other.started_at<event.time))):null;
    const tr=document.createElement('tr');const status={ok:'完成',error:'异常',cancelled:'取消/超时',running:'执行中',estimated:'估算',interrupted:'中断'}[item.status]||item.status;
    const details=[status,layout?('第 '+layout.attempt+' 轮 · '+layout.pages+' 页 · 留白 '+layout.bottom_blank_percent+'% · 行距 '+(layout.parameters.line_spread??'模板默认')+' · 下边距 '+(layout.parameters.bottom_mm??'未识别')+' mm'):'',item.accepted===false?'验收未通过':'',item.error_type||'',item.next_tools?.length?'接下来：'+item.next_tools.map(name=>toolLabels[name]||name).join('、'):'',item.prompt_tokens!=null?'输入 '+item.prompt_tokens+' tokens':'',item.completion_tokens!=null?'输出 '+item.completion_tokens+' tokens':'',item.image_count!=null?'图片 '+item.image_count:'',item.returncode?('退出码 '+item.returncode):''].filter(Boolean).join(' · ');
    for(const value of [(item.parent_id?'↳ ':'')+(toolLabels[item.tool]||item.command||stageLabels[item.stage]||item.stage),seconds(item.duration_ms),details]){const td=document.createElement('td');td.textContent=value;tr.append(td);}
    $('monitor-rows').append(tr);
  }
}
async function refreshMonitor(selected){
  const sid=state.profile.session_id;const {tasks}=await api('/sessions/'+sid+'/tasks');
  if(sid!==state.profile.session_id)return;
  const select=$('monitor-task');const chosen=selected||select.value;select.replaceChildren();
  for(const task of tasks){const option=document.createElement('option');option.value=task.id;option.textContent=new Date(task.created_at).toLocaleTimeString('zh-CN')+' · '+task.id.slice(0,8)+' · '+task.status;select.append(option);}
  if(tasks.some(task=>task.id===chosen))select.value=chosen;
  const task=tasks.find(task=>task.id===select.value);if(task)renderMonitor(task);else{$('task-monitor').hidden=true;monitorResult=null;}
}
$('monitor-refresh').addEventListener('click',()=>void refreshMonitor().catch(error=>toast(error.message)));
$('monitor-task').addEventListener('change',()=>void refreshMonitor($('monitor-task').value).catch(error=>toast(error.message)));
const eventLabels={template_patching:'正在局部修复模板代码…',template_patch_applied:'局部修改已应用，正在重新验证…',template_patch_rejected:'局部修改未匹配，正在重新定位…',template_reviewing:'正在检查模板截图中的重叠、裁切和对齐…',template_converting:'正在识别版式并转换模板…',template_compiling:'正在编译模板并检查页数…',template_preview:'正在生成模板预览…',template_ready:'模板已转换完成',import_started:'正在整理简历信息…',model_started:'模型正在处理…',materials_saving:'正在保存素材库…',materials_saved:'素材已保存',tool_started:'正在执行简历工具…',tool_completed:'工具已完成，正在继续处理…',preview_started:'正在生成简历预览…',visual_review_started:'正在查看简历排版并检查效果…',attachment_processing:'正在读取附件内容…'};
async function watchTask(task){
  state.busy=true;state.task=task;controls();
  void refreshMonitor(task.task_id).catch(()=>{});
  remember('resume-studio:pending:'+state.profile.user_id,JSON.stringify(task));
  try{
    while(true){
      const result=await api('/sessions/'+task.session_id+'/tasks/'+task.task_id);
      const last=result.events.filter(event=>!event.type.startsWith('span_')).at(-1);
      renderMonitor(result);
      taskStatus(result.status==='queued'?'任务已提交，正在排队…':(eventLabels[last?.type]||(result.kind==='import'?'正在整理个人信息和项目经历…':'正在处理你的请求…')));
      if(result.status==='completed'||result.status==='failed'){
        state.busy=false;state.task=null;forget('resume-studio:pending:'+state.profile.user_id);controls();
        const refreshStart=performance.now();
        await loadHistory();await refreshMaterials();await refreshVersions();await refreshTemplates();
        if(!document.hidden)await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        const timing=browserTiming(result.id);browserTiming(result.id,{refresh_ms:performance.now()-refreshStart,...(timing.sent_at?{visible_ms:Math.max(0,Date.now()-timing.sent_at)}:{})});
        renderMonitor(result);void refreshMonitor(result.id).catch(()=>{});
        if(result.status==='failed'){
          if(result.kind!=='template'){appendMessage('user',result.message,result.attachments||[]);appendMessage('assistant',result.error);}
          taskStatus(result.error,true,()=>retryTask(task));
        }else taskStatus(result.kind==='template'?'模板已转换，请预览后选择使用。':result.kind==='import'?'已自动整理并保存到素材库。':'处理完成。');
        return result;
      }
      await wait(1000);
    }
  }catch(error){taskStatus(error.message+'；任务可能仍在运行。',true,()=>watchTask(task));throw error;}
}
async function retryTask(task){
  try{const result=await api('/sessions/'+task.session_id+'/tasks/'+task.task_id+'/retry',{method:'POST'});await watchTask({task_id:result.task_id,session_id:task.session_id});}
  catch(error){toast(error.message);}
}
async function bootstrap(){
  try{
    const health=await api('/health');if(health.app!=='resume-agent')throw new Error('当前是静态页面，请启动 api:app 后端服务');
    const auth=await api('/auth/me');state.csrf=auth.csrf;state.account=auth.account;
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
    state.ready=true;$('app-shell').hidden=false;controls();$('connection-status').textContent=health.restart_required?'后端需重启':(health.model_configured?'后端已连接':'模型未配置');
    const active=(await api('/tasks/active')).task;
    let pending=null;try{pending=JSON.parse(stored('resume-studio:pending:'+state.profile.user_id)||'null');}catch{}
    if(active||pending){
      const task=active?{task_id:active.id,session_id:active.session_id}:pending;
      state.profile.session_id=task.session_id;rememberProfile();await loadHistory();
      if(active&&active.kind!=='template')appendMessage('user',active.message,active.attachments||[]);
      void watchTask(task).catch(error=>toast(error.message));
    }else{
      const recent=(await api('/sessions/'+state.profile.session_id+'/tasks')).tasks[0];
      if(recent?.status==='failed'){if(recent.kind!=='template'){appendMessage('user',recent.message);appendMessage('assistant',recent.error);}taskStatus(recent.error,true,()=>retryTask({task_id:recent.id,session_id:recent.session_id}));}
    }
    await refreshMonitor();
    route();
  }catch(error){state.ready=false;controls();if(error.status===401){location.replace('/login.html'+location.hash);return;}$('app-shell').hidden=false;$('connection-status').textContent='连接失败';taskStatus(error.message,true,bootstrap);}
}
function clearPreview(){
  ++state.pdfGeneration;state.currentFile=null;
  if(state.pdfURL)URL.revokeObjectURL(state.pdfURL);state.pdfURL=null;
  $('pdf-panel').hidden=true;$('pdf-canvas').hidden=true;$('pdf-name').textContent='';$('pdf-pages').textContent='';
  $('download-pdf').removeAttribute('href');$('open-pdf').removeAttribute('href');$('delete-version').disabled=true;
}
async function refreshVersions(){
  const generation=++state.versionsGeneration;
  const {files}=await api('/versions');if(generation!==state.versionsGeneration)return;
  state.versions=files;$('versions-count').textContent=files.length+' 份';$('version-list').replaceChildren();
  if(!files.length){const empty=document.createElement('div');empty.className='empty-versions';empty.textContent='还没有生成的简历。可以在工作台告诉助手你的目标岗位并生成简历。';$('version-list').append(empty);clearPreview();return;}
  for(const file of files){const button=document.createElement('button');button.className='version-card';button.setAttribute('aria-pressed',String(state.currentFile===file.file_id));const icon=document.createElement('span');icon.className='pdf-icon';icon.textContent='PDF';const text=document.createElement('span');const title=document.createElement('strong');title.textContent=file.name;const small=document.createElement('small');small.textContent=new Date(file.created_at).toLocaleString('zh-CN');text.append(title,small);button.append(icon,text);button.dataset.fileId=file.file_id;button.addEventListener('click',()=>void previewFile(file).catch(error=>toast(error.message)));$('version-list').append(button);}
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
  const generation=++state.pdfGeneration;state.currentFile=file.file_id;$('delete-version').disabled=state.deleting;document.querySelectorAll('.version-card').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.fileId===file.file_id)));$('pdf-panel').hidden=false;$('pdf-canvas').hidden=true;$('pdf-status').hidden=false;$('pdf-status').textContent='正在加载预览…';$('pdf-name').textContent=file.name;
  $('download-pdf').removeAttribute('href');$('open-pdf').removeAttribute('href');
  let loading;const pdfStart=performance.now();
  try{
    const response=await fetch('/sessions/'+file.session_id+'/files/'+file.file_id,{credentials:'same-origin',headers:{'X-Resume-Account':state.profile.user_id},cache:'no-store'});
    if(!response.ok)throw new Error('无法读取该简历，请重试');
    const blob=await response.blob();if(generation!==state.pdfGeneration)return;
    if(state.pdfURL)URL.revokeObjectURL(state.pdfURL);state.pdfURL=URL.createObjectURL(blob);
    $('download-pdf').href=state.pdfURL;$('download-pdf').download=file.name;$('open-pdf').href=state.pdfURL;
    loading=pdfjs.getDocument({...pdfOptions,data:new Uint8Array(await blob.arrayBuffer())});const doc=await loading.promise;const page=await doc.getPage(1);const viewport=page.getViewport({scale:1.8});const canvas=document.createElement('canvas');canvas.width=viewport.width;canvas.height=viewport.height;await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise;
    if(generation!==state.pdfGeneration)return;
    const target=$('pdf-canvas');target.width=canvas.width;target.height=canvas.height;target.getContext('2d').drawImage(canvas,0,0);target.hidden=false;$('pdf-status').hidden=true;$('pdf-pages').textContent='第 1 页 / 共 '+doc.numPages+' 页';
    browserTiming(file.task_id,{pdf_ms:performance.now()-pdfStart});if(monitorResult?.id===file.task_id)renderMonitor(monitorResult);
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
  event.preventDefault();if(!state.ready||state.busy)return;
  const message=$('message-input').value.trim();const items=[...state.attachments];if(!message&&!items.length)return;
  try{
    await flushMaterials();state.busy=true;controls();taskStatus(items.length?'正在发送消息和附件…':'正在发送…');
    let body={message};
    if(items.length){body=new FormData();body.append('message',message);for(const item of items)body.append('files',item.file,item.name);}
    const sentAt=Date.now(),submitStart=performance.now();
    const result=await api('/sessions/'+state.profile.session_id+'/messages',{method:'POST',body});
    browserTiming(result.task_id,{sent_at:sentAt,submit_ms:performance.now()-submitStart});
    appendMessage('user',message||'请查看附件。',items);state.attachments=[];renderAttachments();$('message-input').value='';$('message-input').style.height='';
    await watchTask({task_id:result.task_id,session_id:state.profile.session_id});
  }catch(error){if(!state.task){state.busy=false;controls();taskStatus(error.message,true);}toast(error.message);}
});
$('message-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('chat-form').requestSubmit();}});
$('message-input').addEventListener('input',event=>{event.target.style.height='auto';event.target.style.height=Math.min(event.target.scrollHeight,200)+'px';});
$('new-chat').addEventListener('click',()=>$('new-chat-dialog').showModal());
$('confirm-new-chat').addEventListener('click',async()=>{
  if(state.busy)return;
  try{await flushMaterials();const result=await api('/sessions',{method:'POST'});state.profile.session_id=result.session_id;rememberProfile();state.attachments=[];renderAttachments();await loadHistory();$('message-input').value='';taskStatus('');await refreshMonitor();$('new-chat-dialog').close();}catch(error){toast(error.message);}
});
let templateFilter='all',templateGeneration=0,templateURLs=[],templatePreviewURL=null;
async function templateBlob(url){
  const response=await fetch(url,{credentials:'same-origin',headers:{'X-Resume-Account':state.profile.user_id},cache:'no-store'});
  if(!response.ok)throw new Error('无法读取模板文件，请刷新后重试');
  return response.blob();
}
async function refreshTemplates(){
  const generation=++templateGeneration;
  const data=await api('/templates');if(generation!==templateGeneration)return;
  state.templateCatalog=data;
  $('selected-template-name').textContent=data.templates.find(item=>item.id===data.selected_id)?.name||'未选择';
  renderTemplates();
}
function renderTemplates(){
  const generation=++templateGeneration;
  for(const url of templateURLs)URL.revokeObjectURL(url);templateURLs=[];
  const catalog=state.templateCatalog||{templates:[]};
  const items=catalog.templates.filter(item=>templateFilter==='all'||item.source===templateFilter);
  $('template-grid').replaceChildren();$('template-count').textContent=items.length+' 个模板';
  if(!items.length){const empty=document.createElement('p');empty.className='empty-versions';empty.textContent=templateFilter==='custom'?'还没有个人模板。上传喜欢的版式，制作你的第一份模板。':'暂无模板。';$('template-grid').append(empty);}
  const makeButton=(label,action,primary=false)=>{const button=document.createElement('button');button.type='button';button.className='button'+(primary?' primary':'');button.textContent=label;button.addEventListener('click',()=>void action().catch(error=>toast(error.message)));return button;};
  for(const item of items){
    const card=document.createElement('article');card.className='template-card'+(catalog.selected_id===item.id?' selected':'');card.dataset.templateId=item.id;
    const cover=document.createElement('div');cover.className='template-cover';const placeholder=document.createElement('span');placeholder.className='template-placeholder';placeholder.textContent=item.status==='ready'?'模板预览':item.status==='failed'?'转换未完成':'正在转换…';cover.append(placeholder);
    if(item.thumbnail_url){const img=document.createElement('img');img.alt=item.name+'模板预览';img.hidden=true;cover.append(img);void templateBlob(item.thumbnail_url).then(blob=>{if(generation!==templateGeneration)return;const url=URL.createObjectURL(blob);templateURLs.push(url);img.src=url;img.hidden=false;placeholder.hidden=true;}).catch(()=>{placeholder.textContent='预览暂不可用';});}
    const content=document.createElement('div');content.className='template-card-content';const heading=document.createElement('div');heading.className='template-card-heading';const title=document.createElement('h3');title.textContent=item.name;const badge=document.createElement('span');badge.className='template-source';badge.textContent=item.source==='system'?'系统模板':'我的模板';heading.append(title,badge);
    const description=document.createElement('p');description.textContent=item.error||item.description;
    const actions=document.createElement('div');actions.className='template-card-actions';
    if(item.status==='ready'){
      const select=makeButton(catalog.selected_id===item.id?'已选择':'使用此模板',async()=>{await api('/templates/selection',{method:'PUT',body:{template_id:item.id}});await refreshTemplates();toast('已选择「'+item.name+'」，下一次生成将使用此模板。');},catalog.selected_id!==item.id);select.dataset.templateSelect='';select.disabled=!state.ready||state.busy;actions.append(select);
      if(item.thumbnail_url)actions.append(makeButton('预览',()=>previewTemplate(item)));
      actions.append(makeButton('下载 LaTeX',()=>downloadTemplate(item.tex_url,item.name+'.tex')));
      if(item.pdf_url)actions.append(makeButton('示例 PDF',()=>downloadTemplate(item.pdf_url,item.name+'.pdf')));
    }else if(item.status==='failed')actions.append(makeButton('重试转换',()=>retryTask({task_id:item.task_id,session_id:item.session_id})));
    if(item.original_url)actions.append(makeButton('下载原稿',()=>downloadTemplate(item.original_url,item.original_name||item.name)));
    content.append(heading,description,actions);card.append(cover,content);$('template-grid').append(card);
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
$('template-upload-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!state.ready||state.busy)return;
  const file=$('template-file-input').files[0];if(!file)return;
  if(file.size>15*1024*1024){$('template-upload-error').textContent='文件不能超过 15 MB';return;}
  try{await flushMaterials();state.busy=true;controls();const body=new FormData();body.append('file',file);body.append('name',$('template-name-input').value.trim());taskStatus('正在上传模板…');
    const result=await api('/sessions/'+state.profile.session_id+'/templates',{method:'POST',body});$('template-upload-dialog').close();$('template-upload-form').reset();void refreshTemplates().catch(error=>toast(error.message));
    await watchTask({task_id:result.task_id,session_id:state.profile.session_id});
  }catch(error){if(!state.task){state.busy=false;controls();}if($('template-upload-dialog').open)$('template-upload-error').textContent=error.message;taskStatus(error.message,true);toast(error.message);}
});

let accountConfig=null,bindNextSend=0,resetUser=null;
async function refreshAccount(){
 const [auth,config]=await Promise.all([api('/auth/me'),api('/auth/config')]);state.account=auth.account;state.csrf=auth.csrf;accountConfig=config;
 $('account-identity').textContent=auth.account.display_name+' · '+(auth.account.role==='admin'?'管理员':'普通用户');
 $('bound-phone').textContent=auth.account.phone?'已绑定：'+auth.account.phone:'尚未绑定手机号';
 $('bind-phone-form').hidden=!!auth.account.phone||!config.sms_enabled;$('bind-unavailable').hidden=config.sms_enabled||!!auth.account.phone;
 $('admin-panel').hidden=auth.account.role!=='admin';if(auth.account.role==='admin')await refreshUsers();
}
$('logout-button').addEventListener('click',async()=>{try{await flushMaterials();await api('/auth/logout',{method:'POST'});location.replace('/login.html');}catch(error){toast(error.message);}});
$('password-form').addEventListener('submit',async event=>{
 event.preventDefault();if($('new-password').value!==$('new-password-confirm').value){$('password-message').textContent='两次密码不一致';return;}
 const button=event.target.querySelector('button');button.disabled=true;
 try{const result=await api('/auth/password',{method:'PUT',body:{old_password:$('current-password').value,password:$('new-password').value}});state.csrf=result.csrf;event.target.reset();$('password-message').textContent='密码已修改，其他设备的登录已失效。';}catch(error){$('password-message').textContent=error.message;}finally{button.disabled=false;}
});
setInterval(()=>{const seconds=Math.max(0,Math.ceil((bindNextSend-Date.now())/1000));$('send-bind-code').disabled=seconds>0||!accountConfig?.sms_enabled;$('send-bind-code').textContent=seconds?seconds+' 秒后重发':'获取验证码';},500);
$('send-bind-code').addEventListener('click',async()=>{
 $('send-bind-code').disabled=true;bindNextSend=Date.now()+60000;
 try{await api('/auth/sms/send',{method:'POST',body:{phone:$('bind-phone').value.trim(),purpose:'bind'}});$('bind-message').textContent='验证码已发送。';}catch(error){bindNextSend=0;$('bind-message').textContent=error.message;}
});
$('bind-phone-form').addEventListener('submit',async event=>{event.preventDefault();$('bind-submit').disabled=true;try{await api('/auth/phone',{method:'PUT',body:{phone:$('bind-phone').value.trim(),code:$('bind-code').value.trim()}});event.target.reset();await refreshAccount();toast('手机号已验证并绑定');}catch(error){$('bind-message').textContent=error.message;}finally{$('bind-submit').disabled=false;}});
async function refreshUsers(){
 const data=await api('/admin/users');$('admin-users').replaceChildren();
 for(const user of data.users){const row=document.createElement('div');row.className='admin-user';const info=document.createElement('p');info.textContent=user.display_name;const detail=document.createElement('small');detail.textContent=[user.username||user.phone,user.role==='admin'?'管理员':'普通用户',user.enabled?'已启用':'已停用'].join(' · ');info.append(detail);const actions=document.createElement('div');actions.className='admin-user-actions';
 const toggle=document.createElement('button');toggle.className='button';toggle.textContent=user.enabled?'停用账号':'恢复账号';toggle.disabled=user.user_id===state.profile.user_id;
 toggle.addEventListener('click',async()=>{if(user.enabled&&!confirm('停用「'+user.display_name+'」？该用户将退出登录，已有资料会保留。'))return;toggle.disabled=true;try{await api('/admin/users/'+user.user_id,{method:'PATCH',body:{enabled:!user.enabled}});await refreshUsers();}catch(error){$('admin-message').textContent=error.message;toggle.disabled=false;}});
 const reset=document.createElement('button');reset.className='button';reset.textContent='重置密码';reset.addEventListener('click',()=>{resetUser=user;$('reset-user-name').textContent='账号：'+user.display_name;$('reset-user-message').textContent='';$('reset-user-form').reset();$('reset-user-dialog').showModal();});actions.append(toggle,reset);row.append(info,actions);$('admin-users').append(row);}
}
$('refresh-users').addEventListener('click',()=>void refreshUsers().catch(error=>{$('admin-message').textContent=error.message;}));
$('reset-user-form').addEventListener('submit',async event=>{event.preventDefault();if(!resetUser)return;const button=event.target.querySelector('button[type=submit]');button.disabled=true;try{await api('/admin/users/'+resetUser.user_id+'/password',{method:'PUT',body:{password:$('reset-user-password').value}});$('reset-user-dialog').close();event.target.reset();toast('密码已重置，该账号需重新登录');if(resetUser.user_id===state.profile.user_id)location.replace('/login.html');}catch(error){$('reset-user-message').textContent=error.message;}finally{button.disabled=false;}});

window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
controls();route();void bootstrap();
