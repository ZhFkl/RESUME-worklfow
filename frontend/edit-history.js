// Session-local undo history; saving does not discard edits.
export function createEditHistory({limit=100,maxChars=8_000_000}={}){
  let undo=[],redo=[],last=null;
  const cost=entry=>entry.before.text.length+entry.after.text.length;
  return {
    reset(){undo=[];redo=[];last=null;},
    record(before,after,group=''){
      if(before.text===after.text)return;
      const now=Date.now(),previous=undo.at(-1);
      if(group&&last?.group===group&&now-last.time<750&&previous&&previous.after.text===before.text&&previous.after.start===before.start&&previous.after.end===before.end){previous.after=after;}
      else undo.push({before,after});
      redo=[];last={group,time:now};
      let size=undo.reduce((sum,item)=>sum+cost(item),0);
      while(undo.length>1&&(undo.length>limit||size>maxChars))size-=cost(undo.shift());
    },
    undo(){const entry=undo.pop();last=null;if(!entry)return null;redo.push(entry);return entry.before;},
    redo(){const entry=redo.pop();last=null;if(!entry)return null;undo.push(entry);return entry.after;},
    get canUndo(){return undo.length>0;},get canRedo(){return redo.length>0;}
  };
}
