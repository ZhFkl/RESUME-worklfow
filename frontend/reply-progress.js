// Public task stages live inside the assistant reply, alongside streamed text.
export function createReplyProgress(message, scroll) {
  message.classList.add('streaming');
  const body=message.querySelector('.message-body');
  const progress=document.createElement('div');progress.className='reply-progress';progress.setAttribute('role','status');
  const dots=document.createElement('span');dots.className='reply-progress-dots';dots.setAttribute('aria-hidden','true');
  for(let i=0;i<3;i++)dots.append(document.createElement('i'));
  const label=document.createElement('span');label.className='reply-progress-label';
  const retry=document.createElement('button');retry.type='button';retry.className='button reply-retry';retry.textContent='重试';retry.hidden=true;
  progress.append(dots,label,retry);body.after(progress);
  let action=null, timelineMode=false, nodes=[];
  retry.addEventListener('click',async()=>{
    if(!action||retry.disabled)return;
    retry.disabled=true;
    try{await action();}finally{retry.disabled=false;}
  });
  function change(fn){
    const follow=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<80;
    fn();if(follow)scroll.scrollTop=scroll.scrollHeight;
  }
  return {
    element:message,
    hasText:()=>Boolean(body.textContent),
    status(text,error=false,retryAction=null){
      change(()=>{
        progress.hidden=!text;label.textContent=text;progress.classList.toggle('error',error);
        dots.hidden=error||Boolean(retryAction);retry.hidden=!retryAction;action=retryAction;
        message.classList.toggle('reply-failed',error);
      });
    },
    timeline(items){
      timelineMode=true;
      change(()=>{
        items.forEach((item,index)=>{
          let node=nodes[index];
          if(!node){node=document.createElement('div');nodes.push(node);body.append(node);}
          const text=item.type==='text'?item.text:operationLabel(item);
          if(node.dataset.value!==text){
            node.dataset.value=text;
            node.className=item.type==='text'?'reply-segment formatted':'reply-step';
            if(item.type==='text')node.innerHTML=DOMPurify.sanitize(marked.parse(text),{ALLOWED_TAGS:['p','br','strong','em','del','ul','ol','li','blockquote','pre','code','h1','h2','h3','h4','h5','h6','hr','a','table','thead','tbody','tr','th','td'],ALLOWED_ATTR:['href','title','start'],ALLOW_DATA_ATTR:false});
            else node.textContent=text;
          }
          node.hidden=!text;
        });
      });
    },
    finish(){progress.remove();message.classList.remove('streaming');},
    text({text,reset}){
      if(timelineMode)return;
      change(()=>{if(reset)body.textContent=text;else body.append(document.createTextNode(text));});
    },
    clear(){if(body.textContent){progress.remove();message.classList.remove('streaming');}else message.remove();}
  };
}

export function taskStage(result, hasText=false) {
  if(result.status==='queued')return '正在排队…';
  const event=result.events.filter(event=>!event.type.startsWith('span_')).at(-1);
  const tools={resume__read_file:'正在读取素材与模板…',resume__read_selected_template:'正在读取所选模板…',resume__write_file:'正在整理简历内容…',resume__edit_file:'正在调整简历内容…',resume__compile_pdf:'正在生成 PDF 并检查版面…',resume__preview_pdf:'正在查看简历排版…'};
  const stages={template_selected:'正在核对所选模板…',model_started:'正在思考…',tool_completed:'正在整理下一步…',preview_started:'正在查看简历排版…',visual_review_started:'正在检查排版效果…',attachment_processing:'正在读取附件…',import_started:'正在整理你的经历…',materials_saving:'正在保存素材…',materials_saved:'素材已保存，正在整理回复…',layout_checked:'正在核对简历内容与版面…',latex_compiling:'正在生成修改后的 PDF…',template_patching:'正在调整模板…',template_patch_applied:'正在重新检查模板…',template_patch_rejected:'正在检查模板…',template_reviewing:'正在检查模板排版…',template_converting:'正在识别模板版式…',template_compiling:'正在生成模板预览…',template_preview:'正在生成模板预览…',template_ready:'模板已准备好'};
  return event?.type==='tool_started'?(tools[event.tool]||'正在处理你的请求…'):(stages[event?.type]||'正在处理你的请求…');
}

export function operationLabel(event){
  if(['model_started','tool_completed'].includes(event.type))return '';
  return taskStage({status:'running',events:[event]}).replace(/^正在/, '').replace(/…$/, '');
}
