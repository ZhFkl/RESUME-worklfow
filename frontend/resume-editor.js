export function createResumeEditor({api, state, pdfjs, pdfOptions, refreshVersions, previewFile, download}) {
  const $ = id => document.getElementById(id);
  let source=null, generation=0, renderGeneration=0, pdfURL=null, task=null, busy=false;
  let mode='text',textFields=[],textEdits={};
  let textSelection=null;
  function captureSelection() {
    const button=$('latex-bold');
    if(busy||!source||mode!=='text'){button.disabled=true;return;}
    const selection=window.getSelection();
    if(selection?.rangeCount&&!selection.isCollapsed){
      const range=selection.getRangeAt(0);
      const parent=node=>node.nodeType===Node.ELEMENT_NODE?node:node.parentElement;
      const input=parent(range.startContainer)?.closest('.resume-text-input');
      if(input&&input===parent(range.endContainer)?.closest('.resume-text-input')){
        const prefix=range.cloneRange();prefix.selectNodeContents(input);prefix.setEnd(range.startContainer,range.startOffset);
        const start=Array.from(prefix.toString()).length,end=start+Array.from(range.toString()).length;
        let offset=0,allBold=true;
        const walker=document.createTreeWalker(input,NodeFilter.SHOW_TEXT);
        while(walker.nextNode()){const node=walker.currentNode,length=Array.from(node.textContent).length;
          if(offset<end&&offset+length>start&&!node.parentElement.closest('strong,b'))allBold=false;offset+=length;
        }
        textSelection={field_id:input.dataset.fieldId,start,end,bold:!allBold};
        button.disabled=false;button.setAttribute('aria-pressed',String(allBold));button.querySelector('span').textContent=allBold?'取消加粗':'加粗';return;
      }
    }
    if(document.activeElement===button&&textSelection)return;
    textSelection=null;button.disabled=true;button.setAttribute('aria-pressed','false');button.querySelector('span').textContent='加粗';
  }
  function restoreSelection(selection) {
    const input=document.querySelector('.resume-text-input[data-field-id="'+selection.field_id+'"]');if(!input)return;
    const walker=document.createTreeWalker(input,NodeFilter.SHOW_TEXT);let offset=0,start=null,end=null;
    while(walker.nextNode()){const node=walker.currentNode,letters=Array.from(node.textContent),limit=offset+letters.length;
      if(!start&&selection.start>=offset&&selection.start<=limit)start=[node,letters.slice(0,selection.start-offset).join('').length];
      if(selection.end>=offset&&selection.end<=limit){end=[node,letters.slice(0,selection.end-offset).join('').length];break;}
      offset=limit;
    }
    if(start&&end){input.focus({preventScroll:true});const range=document.createRange();range.setStart(...start);range.setEnd(...end);const active=window.getSelection();active.removeAllRanges();active.addRange(range);captureSelection();}
  }
  async function toggleBold() {
    if(busy||!source||!textSelection)return;
    const formatting={...textSelection},run=generation;lock(true);
    try {
      const data=await api('/versions/'+source.file_id+'/text',{method:'POST',body:{content:$('latex-source').value,edits:textEdits,formatting}});
      if(run!==generation)return;
      $('latex-source').value=data.content;textEdits={};renderFields(data);persist();lock(false);restoreSelection(data.selection);
    } catch(error){if(run===generation){lock(false);result(error.message,true);}}
  }
  document.addEventListener('selectionchange',captureSelection);
  $('latex-bold').addEventListener('pointerdown',event=>{captureSelection();event.preventDefault();});
  $('latex-bold').addEventListener('click',()=>void toggleBold());
  const key=()=>source&&state.profile ? 'resume-latex-draft:'+state.profile.user_id+':'+source.file_id : null;
  const dirty=()=>source&&($('latex-source').value!==source.content||Object.keys(textEdits).length>0);
  function persist() {
    const id=key();if(!id)return;
    try {
      if(dirty()||task)localStorage.setItem(id,JSON.stringify({revision:source.revision,content:$('latex-source').value,textEdits,task}));
      else localStorage.removeItem(id);
      $('latex-save-state').textContent=dirty()?'草稿已保存在此浏览器':'内容与 PDF 一致';
    } catch {$('latex-save-state').textContent='草稿未能保存，请下载 .tex 备份';}
    $('latex-preview-label').textContent=dirty()?'上次成功编译的 PDF（当前修改尚未编译）':'对应的 PDF';
  }
  function lock(value,caption='请稍候…') {
    busy=value;$('compile-latex').disabled=value||!source;$('latex-source').readOnly=value;
    $('compile-latex').textContent=value?caption:'编译并保存新版本';
    $('download-latex-draft').disabled=value||!source;
    $('latex-text-mode').disabled=value||!source;$('latex-code-mode').disabled=value||!source;
    document.querySelectorAll('.resume-text-input').forEach(input=>{input.contentEditable=value?'false':'plaintext-only';input.setAttribute('aria-readonly',String(value));});
    captureSelection();
  }
  function showMode(next) {
    mode=next;
    $('latex-text-mode').setAttribute('aria-pressed',String(next==='text'));
    $('latex-code-mode').setAttribute('aria-pressed',String(next==='code'));
    $('latex-source').hidden=next!=='code';$('latex-code-label').hidden=next!=='code';
    $('latex-text-fields').hidden=next!=='text';$('latex-text-hint').hidden=next!=='text';
    $('latex-format-toolbar').hidden=next!=='text';captureSelection();
  }
  function renderFields(data) {
    textFields=data.fields;$('latex-text-fields').replaceChildren();let section=null;
    $('latex-text-hint').textContent=data.partial?'已显示可识别的正文。特殊排版内容可在高级源码中修改。':'直接修改文字，原有加粗、字体和排版会保留。';
    for(const field of textFields) {
      if(section!==field.section){section=field.section;const title=document.createElement('h3');title.className='resume-text-section';title.textContent=section;$('latex-text-fields').append(title);}
      const row=document.createElement('div');row.className='resume-text-field';
      const label=document.createElement('label');label.id='resume-field-label-'+field.id;label.textContent=field.label;
      const input=document.createElement('div');input.className='resume-text-input';input.dataset.fieldId=field.id;
      input.contentEditable=busy?'false':'plaintext-only';input.setAttribute('role','textbox');input.setAttribute('aria-multiline','true');input.setAttribute('aria-labelledby',label.id);input.spellcheck=false;
      const value=textEdits[field.id]??field.text;
      if(value===field.text){const letters=Array.from(value);let end=0;for(const [a,b] of field.bold){input.append(document.createTextNode(letters.slice(end,a).join('')));const strong=document.createElement('strong');strong.textContent=letters.slice(a,b).join('');input.append(strong);end=b;}input.append(document.createTextNode(letters.slice(end).join('')));}
      else input.textContent=value;
      input.addEventListener('input',()=>{const value=input.innerText.replace(/\r/g,'');if(value===field.text)delete textEdits[field.id];else textEdits[field.id]=value;persist();});
      input.addEventListener('beforeinput',event=>{if(event.inputType?.startsWith('format'))event.preventDefault();});
      input.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='b'){event.preventDefault();captureSelection();void toggleBold();}});
      row.append(label,input);$('latex-text-fields').append(row);
    }
    if(!textFields.length){const empty=document.createElement('p');empty.className='resume-text-empty';empty.textContent='这份模板暂时无法提取可编辑正文，请切换到高级源码。';$('latex-text-fields').append(empty);}
  }
  async function readFields(run=generation) {
    const data=await api('/versions/'+source.file_id+'/text',{method:'POST',body:{content:$('latex-source').value}});
    if(run!==generation)return false;
    renderFields(data);return true;
  }
  async function syncText(run=generation) {
    if(!Object.keys(textEdits).length)return true;
    const data=await api('/versions/'+source.file_id+'/text',{method:'POST',body:{content:$('latex-source').value,edits:textEdits}});
    if(run!==generation)return false;
    $('latex-source').value=data.content;textEdits={};renderFields(data);persist();return true;
  }
  async function switchMode(next) {
    if(busy||!source||next===mode)return;
    const run=generation;lock(true);
    try {if(next==='code'){if(!await syncText(run))return;}else if(!await readFields(run))return;showMode(next);}
    catch(error){if(run===generation)result(error.message,true);}
    finally{if(run===generation)lock(false);}
  }
  $('latex-text-mode').addEventListener('click',()=>void switchMode('text'));
  $('latex-code-mode').addEventListener('click',()=>void switchMode('code'));
  function result(text,error=false) {$('latex-result').textContent=text;$('latex-result').dataset.error=String(error);}
  function tab(view) {
    $('latex-workspace').dataset.view=view;
    $('latex-source-tab').setAttribute('aria-pressed',String(view==='source'));
    $('latex-preview-tab').setAttribute('aria-pressed',String(view==='preview'));
  }
  async function preview(item) {
    const run=++renderGeneration;
    $('latex-canvas').hidden=true;$('latex-preview-status').hidden=false;$('latex-preview-status').textContent='正在加载 PDF…';
    $('latex-download-pdf').hidden=true;
    if(pdfURL)URL.revokeObjectURL(pdfURL);pdfURL=null;
    let loading;
    try {
      const response=await fetch('/sessions/'+item.session_id+'/files/'+item.file_id,{cache:'no-store',headers:{'X-Resume-Account':state.profile.user_id}});
      if(!response.ok)throw new Error('PDF 读取失败，请重新打开编辑器');
      const blob=await response.blob();if(run!==renderGeneration)return;
      pdfURL=URL.createObjectURL(blob);$('latex-download-pdf').href=pdfURL;$('latex-download-pdf').download=item.name;$('latex-download-pdf').hidden=false;
      loading=pdfjs.getDocument({...pdfOptions,data:new Uint8Array(await blob.arrayBuffer())});
      const doc=await loading.promise,page=await doc.getPage(1),viewport=page.getViewport({scale:1.6});
      const canvas=document.createElement('canvas');canvas.width=viewport.width;canvas.height=viewport.height;
      await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise;
      if(run!==renderGeneration)return;
      const target=$('latex-canvas');target.width=canvas.width;target.height=canvas.height;target.getContext('2d').drawImage(canvas,0,0);
      target.hidden=false;$('latex-preview-status').hidden=true;
    } catch(error) {if(run===renderGeneration)$('latex-preview-status').textContent=error.message;}
    finally {if(loading)await loading.destroy();}
  }
  async function poll(run) {
    const pending=task;
    try {
      while(run===generation&&task===pending) {
        const status=await api('/sessions/'+pending.session_id+'/tasks/'+pending.task_id);
        if(run!==generation)return;
        if(status.status==='failed'){task=null;throw new Error(status.error||'编译失败，请检查源码');}
        if(status.status==='completed') {
          const pdf=status.files.find(item=>item.name.endsWith('.pdf'));
          if(!pdf)throw new Error('编译任务未返回 PDF');
          const next={...pdf,session_id:pending.session_id,task_id:pending.task_id};
          const updated=await api('/versions/'+pdf.file_id+'/source');
          if(run!==generation)return;
          try {localStorage.removeItem(key());}catch{}
          source=updated;task=null;textEdits={};$('latex-name').textContent=source.name;$('latex-source').value=source.content;
          if(!await readFields(run))return;
          persist();lock(false);
          const layout=status.events.findLast(event=>event.type==='layout_checked');
          result('已保存新版本，原版本保留。'+(layout?'共 '+layout.pages+' 页，页底留白 '+layout.bottom_blank_percent+'%。':'')+' 请检查 PDF 预览。');
          await preview(next);tab('preview');
          await refreshVersions();await previewFile(next);
          return;
        }
        result(status.status==='queued'?'正在排队编译…':'正在编译并检查页数、留白和内容…');
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
    } catch(error) {
      if(run!==generation)return;
      // A network interruption does not mean the server stopped compiling. Keep the task for reopening.
      if(error.status===404)task=null;
      persist();lock(false);result(error.message,true);
    }
  }
  async function open(item) {
    if(!item)return;
    const run=++generation;source=null;task=null;textEdits={};textFields=[];lock(true);tab('source');showMode('text');$('latex-text-fields').replaceChildren();
    $('latex-source').value='';$('latex-name').textContent=item.name;$('latex-save-state').textContent='正在读取正文…';
    result('修改后点击编译，检查通过将保存为新版本。原版本会保留。');
    if(!$('latex-dialog').open)$('latex-dialog').showModal();
    void preview(item);
    try {
      const loaded=await api('/versions/'+item.file_id+'/source');if(run!==generation)return;
      source=loaded;$('latex-source').value=loaded.content;
      try {const draft=JSON.parse(localStorage.getItem(key()));if(draft?.revision===source.revision&&typeof draft.content==='string'){$('latex-source').value=draft.content;task=draft.task||null;textEdits=draft.textEdits&&typeof draft.textEdits==='object'?draft.textEdits:{};}}catch{}
      if(!await readFields(run))return;
      persist();lock(Boolean(task),'正在编译…');if(task)void poll(run);
    } catch(error) {if(run===generation){lock(false);result(error.message,true);$('latex-save-state').textContent='正文读取失败，可使用高级源码';}}
  }
  function close() {persist();++generation;++renderGeneration;task=null;lock(false);$('latex-dialog').close();if(pdfURL)URL.revokeObjectURL(pdfURL);pdfURL=null;}
  $('close-latex').addEventListener('click',close);
  $('latex-dialog').addEventListener('cancel',event=>{event.preventDefault();close();});
  $('latex-source').addEventListener('input',persist);
  $('latex-source-tab').addEventListener('click',()=>tab('source'));
  $('latex-preview-tab').addEventListener('click',()=>tab('preview'));
  $('download-latex-draft').addEventListener('click',async()=>{if(!source||busy)return;const run=generation;lock(true);try{if(await syncText(run))download($('latex-source').value,source.name,'application/x-tex;charset=utf-8');}catch(error){if(run===generation)result(error.message,true);}finally{if(run===generation)lock(false);}});
  $('compile-latex').addEventListener('click',async()=>{
    if(!source||busy)return;
    const run=generation;lock(true,'正在编译…');result('正在提交编译…');
    try {
      if(!task){
        if(!await syncText(run))return;
        const draftId=key(),revision=source.revision,content=$('latex-source').value;
        const submitted=await api('/versions/'+source.file_id+'/compile',{method:'POST',body:{content,revision}});
        if(run!==generation){try{localStorage.setItem(draftId,JSON.stringify({revision,content,task:submitted}));}catch{}return;}
        task=submitted;
      }
      if(run!==generation)return;
      persist();await poll(run);
    } catch(error) {if(run===generation){lock(false);result(error.message,true);}}
  });
  window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});
  return {open,reset(){++generation;++renderGeneration;source=null;task=null;textEdits={};textFields=[];$('latex-text-fields').replaceChildren();$('latex-source').value='';$('latex-dialog').close();if(pdfURL)URL.revokeObjectURL(pdfURL);pdfURL=null;}};
}
