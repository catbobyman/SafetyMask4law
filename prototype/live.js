/* Served only by the local application. Reuse the prototype's review gestures and layout. */
let liveRevision=0,liveMapping={},liveFiles=[],liveBusy=false,liveError='',liveRetry=false,liveProgress='',liveOutputs=[],liveDone={title:'',sub:''};
let editVersion=0,savedVersion=0,saveTimer=null,savingPromise=null;
const pendingUploads=new Map();
const protoDispatch=dispatch,protoToken=token,protoMoreMenu=moreMenu;
const mutationTypes=new Set(['decision','merge','sync','bulkEntity','maskAll','entity','type','range','addSel','addHits','pen','erase','boxApply','restore']);
const cpToJs=(text,n)=>[...text].slice(0,n).join('').length;
const jsToCp=(text,n)=>[...text.slice(0,n)].length;

async function api(path,body,method='POST'){
  const options={method,headers:{'X-Mask':'1'}};
  if(body instanceof FormData)options.body=body;
  else if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
  const response=await fetch('/api/'+path,options);
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||JSON.stringify(result.detail)||'请求失败');
  return result;
}
function fail(error){liveError=error.message||String(error);liveRetry=!!error.saveFailed;render();}
async function task(work){
  if(liveBusy)return;
  liveBusy=true;liveError='';render();
  try{await work();}catch(error){fail(error);}finally{liveBusy=false;liveProgress='';render();}
}
function hydrate(project){
  // Imports, scans and removals replace documents wholesale; earlier snapshots no longer apply.
  undoStack.length=0;redoStack.length=0;
  liveRevision=project.revision;liveMapping=project.mapping;liveOutputs=project.outputs;
  state.entities=project.entities;
  const selectedId=state.documents[state.current]?.id;
  templates.length=0;COMPILED.length=0;
  state.documents=project.documents.map((d,i)=>{
    d.sections.forEach(sec=>{if(sec.breaks)sec.breaks=sec.breaks.map(([p,o])=>[p,cpToJs(sec.paragraphs[p],o)]);});
    templates.push({title:`材料 ${i+1}`});COMPILED.push({sections:d.sections});
    d.candidates=d.candidates.map(c=>{const text=d.sections[c.s].paragraphs[c.p];return {...c,start:cpToJs(text,c.start),end:cpToJs(text,c.end)};});
    return {...d,template:i,scan:d.status==='review'?100:0,notice:d.issues.join('；'),custom:false};
  });
  state.dir=state.dir.filter(f=>!f.docId&&!project.documents.some(d=>d.name===f.name)).concat(state.documents.map(d=>({key:d.id,name:d.name,format:d.format,docId:d.id,template:d.template,checked:true})));
  state.current=Math.max(0,state.documents.findIndex(d=>d.id===selectedId));
  state.nextManual=Math.max(state.nextManual,Date.now());
  if(!state.documents[state.current]?.candidates.some(c=>c.id===state.selected))state.selected=null;
}
function reviewPayload(){
  return {revision:liveRevision,entities:state.entities,documents:state.documents.map(d=>({id:d.id,candidates:d.candidates.map(c=>{
    const text=COMPILED[d.template].sections[c.s].paragraphs[c.p];
    return {...c,start:jsToCp(text,c.start),end:jsToCp(text,c.end)};
  })}))};
}
async function persist(){
  clearTimeout(saveTimer);
  if(savingPromise){await savingPromise;if(savedVersion!==editVersion)return persist();return;}
  if(savedVersion===editVersion)return;
  savingPromise=(async()=>{
    while(savedVersion!==editVersion){
      const version=editVersion,result=await api('review',reviewPayload(),'PUT').catch(error=>{error.saveFailed=true;throw error;});
      liveRevision=result.revision;liveMapping=result.mapping;
      Object.entries(result.entities).forEach(([id,e])=>{
        if(state.entities[id])Object.assign(state.entities[id],{no:e.no,variants:e.variants,values:e.values});
      });
      savedVersion=version;
    }
  })();
  try{await savingPromise;}finally{savingPromise=null;render();}
}
dispatch=function(action){
  if(mutationTypes.has(action.type)&&liveBusy)return state;
  if(action.type==='range'){
    const d=state.documents[state.current],c=d.candidates.find(c=>c.id===state.selected),text=paraText(d,c);
    const n=action.offset;
    if(n>0&&n<text.length&&/[\uD800-\uDBFF]/.test(text[n-1])&&/[\uDC00-\uDFFF]/.test(text[n]))return state;
  }
  const result=protoDispatch(action);
  if(mutationTypes.has(action.type)){
    editVersion++;clearTimeout(saveTimer);
    saveTimer=setTimeout(()=>persist().catch(fail),220);
  }
  return result;
};
token=function(s,c){return s.entities[c.entity]?.variants?.[c.original]||protoToken(s,c);};
restoreInfo=function(s){
  const all=s.restoreInput.match(TAG_RE)||[],tokens=[...new Set(all)],fix=s.restoreFix;
  return {map:liveMapping,tokens,missing:tokens.filter(t=>!(t in liveMapping)&&!fix[t]),kept:tokens.filter(t=>fix[t]==='keep'),
    replaced:all.filter(t=>t in liveMapping||(fix[t]&&fix[t]!=='keep')).length};
};
// Disable the demonstration's synthetic host, installation, and scan timers in live mode.
ensureTicker=function(){};
chatView=function(){return '';};
protoView=function(){return `<div class="live-status">${liveBusy?esc(liveProgress||'处理中…'):savedVersion!==editVersion?'正在保存核对…':'本地工作台 · 核对自动保存'}</div>${liveError?`<aside class="live-error" role="alert"><b>未完成</b><p>${esc(liveError)}</p>${liveRetry?'<button data-a="retry-save">重试保存</button>':''}<button data-a="dismiss-error">关闭提示</button></aside>`:''}`;};
// 核对页的还原入口在灵动岛里；其他页面放在右上角。
bar=function({title='',tools='',island=''}={}){
  return `<header class="bar" data-k="bar"><button class="crumb glass" data-a="home" aria-label="材料列表">${icon('back',15)}<span>Mask</span></button>${title?`<div class="bar-title">${esc(title)}</div>`:''}${island}<div class="bar-tools">${tools}${island?'':`<button class="crumb glass" data-a="restore-open" title="还原 AI 结果中的代号">${icon('down',15)}<span>还原</span></button>`}</div></header>`;
};
islandExtras=function(){return `<button class="isl-btn" data-a="restore-open" title="还原 AI 结果中的代号">${icon('down',16)}<span class="lbl">还原</span></button>`;};
tile=function(f){
  const doc=state.documents.find(d=>d.id===f.docId);
  return `<button class="tile ${f.checked?'on':''}" data-k="${f.key}" data-a="${doc?'open-existing':'toggle'}" data-v="${f.key}" title="${esc(f.name)}" aria-pressed="${!!f.checked}"><span class="slot">${mini(doc?{template:doc.template,doc}:null,2.08)}<span class="tick">${icon('check',13)}</span></span><span class="nm">${esc(f.name)}</span><span class="sub">${doc?doc.status==='failed'?'识别失败':doc.status==='review'?'继续核对':'待识别':f.local?'工作目录':'待导入'}</span></button>`;
};
selectPage=function(){
  const fresh=state.dir.filter(f=>f.checked&&!f.docId).length+Number(!!state.note.trim()),queued=state.documents.filter(d=>d.status!=='review').length;
  return bar()+`<main class="center" data-k="p-import"><h1 class="ask">选择需要脱敏的材料</h1><p class="quiet">TXT · Word（.docx）· 原生 PDF<br>原文与映射只在本机处理</p><div class="tiles">${state.dir.map(tile).join('')}<button class="tile add" data-a="pick-file" aria-label="添加文件"><span class="slot">${icon('plus',24)}</span></button></div>${state.documents.length?'<button class="cta" data-a="review-open">继续核对已有材料</button>':''}</main>`+dock('import',`<label class="field">${icon('note',18)}<input id="note" aria-label="补充说明" placeholder="补充说明，可写真名，会一起识别" value="${esc(state.note)}"></label>${go({cls:'next',a:'start-scan',label:`开始识别 ${fresh+queued} 份`,inner:icon('right',20),disabled:liveBusy||!(fresh+queued+Number(!!state.note.trim())),key:'start'})}`);
};
reviewGo=function(){
  const c=counts(state),bad=state.documents.filter(d=>d.status==='failed'),clash=conflicts().length;
  if(liveBusy)return act({cls:'busy',title:'本地处理中',inner:'<i class="spin"></i><span class="lbl">处理中</span>',key:'busy'});
  if(bad.length)return act({cls:'todo',a:'start-scan',title:'重试失败的识别',inner:`${icon('right',15)}<span class="lbl">重试识别</span>`,key:'retry'});
  if(c.scanning)return act({cls:'next',a:'start-scan',title:'识别剩余材料',inner:`${icon('right',15)}<span class="lbl">识别剩余材料</span>`,key:'scan'});
  if(c.pending)return act({cls:'todo',a:'next',title:`下一处待确认，还剩 ${c.pending} 处（J）`,inner:`<b>${c.pending}</b><span class="lbl">处待确认</span>${icon('next',14)}`,key:'n'+c.pending});
  if(clash)return act({cls:'todo',a:'conflict',title:'处理重叠范围',inner:`<b>${clash}</b><span class="lbl">处范围重叠</span>${icon('next',14)}`,key:'conflict'+clash});
  return act({cls:'ready',a:'release',title:'导出脱敏 Markdown',inner:`${icon('up',15)}<span class="lbl">导出脱敏稿</span>`,key:'export'});
};
paraHtml=function(d,si,pi,hits,cur,from=0,to){
  const text=COMPILED[d.template].sections[si].paragraphs[pi],here=hits.filter(h=>h.s===si&&h.p===pi);
  to??=text.length;
  let last=from,out='';
  // Overlapping alternatives remain in the candidate list; this only chooses how to draw text.
  const candidates=d.candidates.filter(c=>c.s===si&&c.p===pi&&c.start>=from&&c.start<to).sort((a,b)=>a.start-b.start||b.end-a.end);
  const selected=candidates.find(c=>c.id===state.selected);
  const visible=candidates.filter(c=>!selected||c===selected||c.end<=selected.start||c.start>=selected.end);
  const plain=(from,to)=>{
    let result='',pos=from;
    for(const h of here.filter(h=>!h.marked&&h.start>=from&&h.end<=to)){
      if(h.start<pos)continue;
      result+=esc(text.slice(pos,h.start))+`<span class="hit ${cur===h?'cur':''}">${esc(text.slice(h.start,h.end))}</span>`;pos=h.end;
    }
    return result+esc(text.slice(pos,to));
  };
  for(const c of visible){if(c.start<last)continue;out+=plain(last,c.start)+markHtml(c,here,cur);last=c.end;}
  return out+plain(last,to);
};
paper=function(d,hits){
  const here=hits.filter(h=>h.docIndex===state.current),cur=hits[state.hit],ai=state.preview==='masked';
  const sheets=sheetsOf(d),c=d.candidates.find(c=>c.id===state.selected),target=state.sel||c;
  const coSheet=target?Math.max(0,sheetOf(sheets,target.s,target.p,target.start)):-1;
  const flags=`${d.notice?`<div class="flag">${esc(d.notice)}；按部分处理导出</div>`:''}${d.error?`<div class="flag" role="alert">识别失败：${esc(d.error)}</div>`:''}${d.status!=='review'?`<div class="flag">${d.status==='failed'?'请修复原因后重试':'尚未完成识别'}</div>`:''}`;
  const name=ai?'材料 '+(state.current+1):d.name;
  return sheets.map((sh,i)=>`<div class="sheet-no" data-k="n${d.id}-${i}">${i?'':esc(name)+' · '}${esc(sh.label)}</div><article class="page ${ai?'ai':''} ${sh.page?'pg':''}" data-k="doc${d.id}-${i}">${i?'':flags}${sh.parts.map(([si,pi,from,to])=>`<p data-s="${si}" data-p="${pi}" data-o="${from}" style="white-space:pre-wrap;overflow-wrap:anywhere">${paraHtml(d,si,pi,here,cur,from,to)}</p>`).join('')}${i===coSheet?calloutFor(d):''}</article>`).join('');
};
moreMenu=function(items){
  if(state.page!=='review'&&state.page!=='done')return protoMoreMenu(items);
  const extra=[['export-mapping','lock','导出映射文件',!Object.keys(liveMapping).length]];
  if(state.page==='review')extra.push(['remove-document','x','移除当前材料']);
  return protoMoreMenu([...items,...extra]);
};
function downloadLink(f){return `<a class="download" href="/api/download/${encodeURIComponent(f.id)}" download>${icon('down',16)} ${esc(f.name)}</a>`;}
donePage=function(){
  return bar()+`<main class="center" data-k="p-done"><h1 class="ask">${esc(liveDone.title)}</h1><p class="quiet">${esc(liveDone.sub)}</p><div class="downloads">${liveFiles.map(downloadLink).join('')}</div><button class="cta" data-a="review-open">返回核对</button></main>`+dock('done',`${moreMenu([['export','share','导出原格式',!canExport(state)],['restore-file','doc','打开结果文件'],['add-more','plus','补充材料']])}<label class="field"><textarea id="paste" rows="1" aria-label="AI 结果" placeholder="粘贴 AI 结果，还原真名"></textarea></label>${go({cls:'next',a:'restore-open',label:'还原结果',inner:icon('down'),key:'restore'})}`);
};
restorePage=function(){
  if(state.restoreInput.trim()){
    const r=restoreInfo(state),partial=r.kept.length>0;
    const save=partial?'保存部分还原版':'保存真名版',status=r.missing.length?'':partial?`部分还原，${r.kept.length} 个代号保留`:`已还原 ${r.replaced} 处`;
    const primary=r.missing.length?act({cls:'todo',a:'next-miss',title:'下一处没有映射的代号',inner:`<b>${r.missing.length}</b><span class="lbl">个代号没有映射</span>${icon('next',14)}`,key:'missing'+r.missing.length}):act({cls:'save',a:'save-restored',title:save,inner:`${icon('down',15)}<span class="lbl">${save}</span>`,disabled:liveBusy,key:'save'});
    return bar({title:'AI 结果',island:restoreIsland(status,primary)})+`<main class="canvas" id="canvas" data-k="p-restore">${restorePaper(r)}</main>`;
  }
  return bar({title:'还原结果'})+`<main class="center" data-k="p-restore-input"><h1 class="ask">还原 AI 结果中的代号</h1><p class="quiet">使用本项目已保存的映射，真名版只保存到本机</p><button class="cta" data-a="restore-file">打开 TXT / Markdown 结果文件</button></main>`+dock('restore',`<label class="field"><textarea id="paste" rows="1" aria-label="AI 结果" placeholder="粘贴含代号的文本"></textarea></label>`);
};
function exportDialog(kind){
  const partial=state.documents.some(d=>d.issues.length),c=counts(state);
  return `<div class="scrim" data-a="close-sheet"></div><section class="modal" role="dialog" aria-modal="true" aria-label="确认导出"><button class="x" data-a="close-sheet" aria-label="关闭">${icon('x',15)}</button><h2>导出${kind==='original'?'原格式文件':'脱敏 Markdown'}</h2><div class="tally"><span>脱敏 ${c.masked} 处</span><span>保留 ${c.kept} 处</span><span>补录 ${c.manual} 处</span></div>${noticeHtml()}${partial?'<label class="partial-ack"><input type="checkbox" id="partial-ack"> 我已核对以上未处理内容，按部分处理导出</label>':''}<p class="quiet">原文件不会覆盖，映射不包含在下载文件中</p><button class="cta hot" id="confirm" data-a="${kind==='original'?'confirm-export':'confirm-handoff'}" ${liveBusy?'disabled':''}>${icon('down',18)}生成文件</button></section>`;
}
handoffSheet=function(){return exportDialog('markdown');};
exportSheet=function(){return exportDialog('original');};
addFiles=function(files){
  for(const file of files){
    if(!/\.(txt|docx|pdf)$/i.test(file.name)){fail(new Error(`${file.name}：支持 TXT、DOCX 和原生 PDF；.doc 请另存为 .docx`));continue;}
    const key=crypto.randomUUID();pendingUploads.set(key,file);
    state.dir.push({key,name:file.name,format:file.name.split('.').pop().toUpperCase(),checked:true});
  }render();
};
async function scanAll(){
  await persist();
  const fresh=state.dir.filter(f=>f.checked&&!f.docId),errors=[];
  for(const file of fresh){
    liveProgress='正在读取 '+file.name;render();
    // Materials are independent: an unreadable file is reported by name and left unchecked.
    try{
      let result;
      if(file.local)result=await api('import-local',{name:file.name});
      else{const form=new FormData();form.append('file',pendingUploads.get(file.key));result=await api('upload',form);pendingUploads.delete(file.key);}
      hydrate(result);
    }catch(error){errors.push(`${file.name}：${error.message}`);file.checked=false;}
  }
  if(state.note.trim()){hydrate(await api('note',{text:state.note}));state.note='';}
  const queued=state.documents.filter(d=>d.status!=='review').map(d=>d.id);
  if(state.documents.length){state.page='review';state.view='bench';}
  for(const id of queued){
    const d=state.documents.find(d=>d.id===id);d.status='scanning';liveProgress='HaS 正在识别 '+d.name;render();
    hydrate(await api('scan/'+id,{}));
    const updated=state.documents.find(d=>d.id===id);
    if(updated.status==='failed')errors.push(`${updated.name}：${updated.error}`);
    render();
  }
  selectFirst(state);
  // Deciding per subject first settles most occurrences; the document view is for exceptions.
  if(state.documents.some(d=>d.candidates.some(c=>c.decision==='pending')))state.inspector=true;
  if(errors.length){liveError=errors.join('\n');liveRetry=false;}
}
async function doExport(kind){
  const partial=state.documents.some(d=>d.issues.length),ack=!!document.getElementById('partial-ack')?.checked;
  if(partial&&!ack)throw new Error('请先确认未处理内容');
  await persist();liveProgress='正在生成并校验文件';render();
  const result=await api('export',{kind,acknowledge:ack,revision:liveRevision});
  liveRevision=result.revision;liveFiles=result.files;
  hydrate(await api('project',undefined,'GET'));
  state.sheet='';state.page='done';state.phase='handed';state.restored=false;
  liveDone={title:'脱敏文件已生成',sub:'可下载后交给 AI；宿主自动接入尚未连接'};
}
Object.assign(ACTIONS,{
  home:()=>{state.page='import';state.view='bench';render();},
  'open-existing':t=>{state.current=state.documents.findIndex(d=>d.id===t.dataset.v);state.page='review';selectFirst(state);render();},
  'review-open':()=>{if(state.documents.length){state.page='review';selectFirst(state);render();}},
  'start-scan':()=>task(scanAll),
  'confirm-export':()=>task(()=>doExport('original')),
  'confirm-handoff':()=>task(()=>doExport('markdown')),
  'restore-open':()=>{state.page='restore';state.view='bench';render();},
  'clear-restore':()=>{state.restoreInput='';state.restoreFix={};state.page='restore';state.restored=false;render();},
  'restore-file':()=>document.getElementById('result-picker').click(),
  'save-restored':()=>task(async()=>{
    await persist();const result=await api('restore',{text:state.restoreInput,fixes:state.restoreFix,save:true});
    hydrate(await api('project',undefined,'GET'));
    liveFiles=[result.file];state.restored=true;state.page='done';
    liveDone={title:'还原结果已保存到本机',sub:'真名版仅供本地使用；文件名标明是否部分还原'};
  }),
  'export-mapping':()=>task(async()=>{
    await persist();const result=await api('export-mapping',{revision:liveRevision});
    liveRevision=result.revision;hydrate(await api('project',undefined,'GET'));
    liveFiles=[result.file];state.menu=false;state.restored=false;state.page='done';
    liveDone={title:'映射文件已生成',sub:'官方 mapping.json 格式，可用于 has.sh text restore；它能还原全部脱敏内容，不要和脱敏稿一起发出'};
  }),
  pause:()=>task(async()=>{await persist();state.page='import';}),
  conflict:()=>{const {d,c}=conflicts()[0];state.current=state.documents.indexOf(d);state.selected=c.id;render();reveal();},
  'retry-save':()=>task(async()=>{await persist();}),
  'remove-document':()=>task(async()=>{
    await persist();const id=state.documents[state.current].id;
    hydrate(await api('documents/'+id+'?revision='+liveRevision,undefined,'DELETE'));
    state.menu=false;state.selected=null;state.page=state.documents.length?'review':'import';
  }),
  'dismiss-error':()=>{liveError='';render();}
});
const resultPicker=document.createElement('input');resultPicker.type='file';resultPicker.accept='.txt,.md';resultPicker.id='result-picker';resultPicker.hidden=true;document.body.append(resultPicker);
resultPicker.onchange=()=>task(async()=>{
  if(!resultPicker.files.length)return;
  const form=new FormData();form.append('file',resultPicker.files[0]);
  const result=await api('read-result',form);state.restoreInput=result.text;state.restoreFix={};state.page='restore';state.restored=false;resultPicker.value='';
});
addEventListener('beforeunload',event=>{if(savedVersion!==editVersion||liveBusy){event.preventDefault();event.returnValue='';}});
const liveStyle=document.createElement('style');liveStyle.textContent=`
  body[data-view=bench] #proto{display:block}
  .live-status{position:fixed;bottom:6px;left:16px;font-size:11px;color:var(--ink2);z-index:50}
  .live-error{position:fixed;right:18px;top:64px;z-index:100;width:min(420px,90vw);padding:18px;border-radius:16px;background:var(--elev);box-shadow:var(--shadow-2);border:1px solid var(--zhu);overflow-wrap:anywhere}
  .live-error p{white-space:pre-line;margin:8px 0;max-height:35vh;overflow:auto}.live-error button{padding:6px 12px;border:1px solid var(--line);border-radius:8px;margin:6px 6px 0 0}
  .downloads{display:grid;gap:12px;max-height:40vh;overflow:auto;margin:24px auto}.download{color:var(--ink);display:flex;align-items:center;gap:6px;padding:12px;border:1px solid var(--line);border-radius:12px;text-decoration:none}
  .partial-ack{display:flex;gap:8px;margin:16px 0;align-items:flex-start}.modal h2{margin:22px 0 14px}.quiet{line-height:1.7}.tiles{max-height:45vh;overflow:auto}
`;document.head.append(liveStyle);
(async()=>{
  state=initialState();state.view='bench';state.phase='working';state.page='import';state.dir=[];state.documents=[];state.entities={};
  try{
    const launch=location.hash.slice(1);
    if(launch){
      const response=await fetch('/session',{method:'POST',headers:{'X-Launch-Token':launch}});
      if(!response.ok)throw new Error((await response.json()).error);
      history.replaceState(null,'','/');
    }
    hydrate(await api('project',undefined,'GET'));
    const local=await api('workspace',undefined,'GET');
    state.dir.push(...local.filter(f=>!state.dir.some(x=>x.name===f.name)).map(f=>({key:crypto.randomUUID(),name:f.name,local:true,checked:true,format:f.name.split('.').pop().toUpperCase()})));
    if(state.documents.length){state.page='review';selectFirst(state);}
    render();
  }catch(error){fail(error);}
})();
