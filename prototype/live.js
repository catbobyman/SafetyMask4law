/* Served only by the local application. Reuse the prototype's review gestures and layout. */
let liveRevision=0,liveMapping={},liveFiles=[],liveBusy=false,liveError='',liveRetry=false,liveProgress='',liveOutputs=[],liveDone={title:'',sub:''},liveCopied=false;
let editVersion=0,savedVersion=0,saveTimer=null,savingPromise=null;
// An imported mapping file replaces the project mapping for restore only; it is never merged into the project.
let liveRestoreMapping=null;
// Workbench opened by a host skill: exporting Markdown hands it to the workspace.
let liveHost=false,liveCatalog=[],liveScope=null,liveDefaultScope=[],liveHosts=[];
const restoreMapping=()=>liveRestoreMapping?Object.fromEntries(Object.entries(liveRestoreMapping.mapping).map(([t,v])=>[t,v[0]])):liveMapping;
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
  liveHost=project.host;liveCatalog=project.catalog;liveDefaultScope=project.default_scope;liveHosts=project.hosts;CATEGORIES.length=0;CATEGORIES.push(...project.categories);
  state.entities=project.entities;
  const selectedId=state.documents[state.current]?.id;
  templates.length=0;COMPILED.length=0;
  state.documents=project.documents.map((d,i)=>{
    d.sections.forEach(sec=>{if(sec.breaks)sec.breaks=sec.breaks.map(([p,o])=>[p,cpToJs(sec.paragraphs[p],o)]);});
    templates.push({title:`材料 ${d.no}`});COMPILED.push({sections:d.sections});
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
  const map=restoreMapping(),all=findTags(s.restoreInput).map(m=>m[0]),tokens=[...new Set(all)],fix=s.restoreFix;
  return {map,tokens,missing:tokens.filter(t=>!(t in map)&&!fix[t]),kept:tokens.filter(t=>fix[t]==='keep'),
    replaced:all.filter(t=>t in map||(fix[t]&&fix[t]!=='keep')).length,suggest:tagSuggestions(tokens,map)};
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
  if(f.unsupported)return `<button class="tile off" data-k="${f.key}" disabled title="${esc(f.name)}"><span class="slot">${mini(null,2.08)}</span><span class="nm">${esc(f.name)}</span><span class="sub">先另存为 .docx</span></button>`;
  const doc=state.documents.find(d=>d.id===f.docId);
  return `<button class="tile ${f.checked?'on':''}" data-k="${f.key}" data-a="${doc?'open-existing':'toggle'}" data-v="${f.key}" title="${esc(f.name)}" aria-pressed="${!!f.checked}"><span class="slot">${mini(doc?{template:doc.template,doc}:null,2.08)}<span class="tick">${icon('check',13)}</span></span><span class="nm">${esc(f.name)}</span><span class="sub">${doc?doc.status==='failed'?'识别失败':doc.status==='review'?'继续核对':'待识别':f.local?'工作目录':'待导入'}</span></button>`;
};
selectPage=function(){
  const n=state.dir.filter(f=>f.checked&&!f.docId).length+state.documents.filter(d=>d.status!=='review').length;
  const start=n?`<button class="cta hot" data-a="start-scan" ${liveBusy?'disabled':''}>${icon('right',18)}开始识别 ${n} 份</button>`:'';
  const resume=state.documents.length?`<button class="cta ${n?'soft':''}" data-a="review-open">继续核对已有材料</button>`:'';
  // Opened from an agent or in a material folder, the files are already here: materials first, then the one
  // action, then the two rarely changed settings as quiet chips.
  const local=state.dir.filter(f=>f.local&&!f.unsupported).length;
  return bar()+`<main class="center" data-k="p-import"><h1 class="ask">${state.dir.length?'确认需要脱敏的材料':'选择需要脱敏的材料'}</h1><p class="quiet">${local?`已带入工作目录的 ${local} 份`:'TXT · Word（.docx）· 原生 PDF'} · 原文与映射只在本机处理</p><div class="tiles">${state.dir.map(tile).join('')}<button class="tile add" data-a="pick-file" aria-label="添加文件"><span class="slot">${icon('plus',24)}</span></button></div>${start||resume?`<div class="ctas">${start}${resume}</div>`:'<p class="quiet">勾选或添加材料后开始识别</p>'}<div class="opts">${scopeLine()}${hostsLine()}</div></main>`;
};
reviewGo=function(){
  const c=counts(state),bad=state.documents.filter(d=>d.status==='failed'),clash=conflicts().length;
  if(liveBusy)return act({cls:'busy',title:'本地处理中',inner:'<i class="spin"></i><span class="lbl">处理中</span>',key:'busy'});
  if(bad.length)return act({cls:'todo',a:'start-scan',title:'重试失败的识别',inner:`${icon('right',15)}<span class="lbl">重试识别</span>`,key:'retry'});
  if(c.scanning){const again=state.documents.some(d=>d.status!=='review'&&d.candidates.length)?'重新识别':'识别剩余材料';return act({cls:'next',a:'start-scan',title:again+'，已做的决定保留',inner:`${icon('right',15)}<span class="lbl">${again}</span>`,key:'scan'});}
  if(c.pending)return act({cls:'todo',a:'next',title:`下一处待确认，还剩 ${c.pending} 处（J）`,inner:`<b>${c.pending}</b><span class="lbl">处待确认</span>${icon('next',14)}`,key:'n'+c.pending});
  if(clash)return act({cls:'todo',a:'conflict',title:'处理重叠范围',inner:`<b>${clash}</b><span class="lbl">处范围重叠</span>${icon('next',14)}`,key:'conflict'+clash});
  return act({cls:'ready',a:'release',title:'把脱敏稿'+handName(),inner:`${icon('up',15)}<span class="lbl">${handName()}</span>`,key:'export'});
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
  const name=ai?'材料 '+d.no:d.name;
  return sheets.map((sh,i)=>`<div class="sheet-no" data-k="n${d.id}-${i}">${i?'':esc(name)+' · '}${esc(sh.label)}</div><article class="page ${ai?'ai':''} ${sh.page?'pg':''}" data-k="doc${d.id}-${i}">${i?'':flags}${sh.parts.map(([si,pi,from,to])=>`<p data-s="${si}" data-p="${pi}" data-o="${from}" style="white-space:pre-wrap;overflow-wrap:anywhere">${paraHtml(d,si,pi,here,cur,from,to)}</p>`).join('')}${i===coSheet?calloutFor(d):''}</article>`).join('');
};
moreMenu=function(items){
  if(state.page!=='review'&&state.page!=='done')return protoMoreMenu(items);
  const extra=[['scope-open','search','脱敏范围'],['hosts-open','lock','接入 Agent'],['export-mapping','lock','导出映射文件',!Object.keys(liveMapping).length]];
  if(state.page==='review')extra.push(['remove-document','x','移除当前材料']);
  return protoMoreMenu([...items,...extra]);
};
function downloadLink(f){return `<a class="download" href="/api/download/${encodeURIComponent(f.id)}" download>${icon('down',16)} ${esc(f.name)}</a>`;}
donePage=function(){
  return bar()+`<main class="center" data-k="p-done"><h1 class="ask">${esc(liveDone.title)}</h1><p class="quiet">${esc(liveDone.sub)}</p>${liveDone.brief?`<button class="cta hot" data-a="copy-brief">${icon(liveCopied?'check':'doc',18)}${liveCopied?'已复制，去 Agent 里粘贴':'复制给 Agent 的指令'}</button>`:''}<div class="downloads">${liveFiles.map(downloadLink).join('')}</div><button class="cta ${liveDone.brief?'soft':''}" data-a="review-open">返回核对</button></main>`+dock('done',`${moreMenu([['export','share','导出原格式',!canExport(state)],['restore-file','doc','打开结果文件'],['add-more','plus','补充材料']])}<label class="field"><textarea id="paste" rows="1" aria-label="AI 结果" placeholder="粘贴 AI 结果，还原真名"></textarea></label>${go({cls:'next',a:'restore-open',label:'还原结果',inner:icon('down'),key:'restore'})}`);
};
restorePage=function(){
  if(state.restoreInput.trim()){
    const r=restoreInfo(state),partial=r.kept.length>0;
    const save=partial?'保存部分还原版':'保存真名版',status=r.missing.length?'':partial?`部分还原，${r.kept.length} 个代号保留`:`已还原 ${r.replaced} 处`;
    const fixable=r.missing.filter(t=>r.suggest[t]).length;
    const source=liveRestoreMapping?`<button class="isl-btn" data-a="clear-mapping" title="正在使用 ${esc(liveRestoreMapping.name)}；点此改用本项目映射">${icon('x',15)}<span class="lbl">已用映射文件</span></button>`
      :`<button class="isl-btn" data-a="mapping-file" title="用导出的 mapping.json 还原，不并入本项目">${icon('lock',15)}<span class="lbl">映射文件</span></button>`;
    const primary=source+(fixable?`<button class="isl-btn" data-a="accept-suggest" title="把形状被改动的代号按格式还原后的代号处理">${icon('check',15)}<span class="lbl">采用 ${fixable} 个格式建议</span></button>`:'')+(r.missing.length?act({cls:'todo',a:'next-miss',title:'下一处没有映射的代号',inner:`<b>${r.missing.length}</b><span class="lbl">个代号没有映射</span>${icon('next',14)}`,key:'missing'+r.missing.length}):act({cls:'save',a:'save-restored',title:save,inner:`${icon('down',15)}<span class="lbl">${save}</span>`,disabled:liveBusy,key:'save'}));
    return bar({title:'AI 结果',island:restoreIsland(status,primary)})+`<main class="canvas" id="canvas" data-k="p-restore">${restorePaper(r)}</main>`;
  }
  return bar({title:'还原结果'})+`<main class="center" data-k="p-restore-input"><h1 class="ask">还原 AI 结果中的代号</h1><p class="quiet">${liveRestoreMapping?`使用映射文件 ${esc(liveRestoreMapping.name)}（${Object.keys(liveRestoreMapping.mapping).length} 个代号），不并入本项目`:'使用本项目已保存的映射'}，真名版只保存到本机</p><button class="cta" data-a="restore-file">打开 TXT / Markdown 结果文件</button>${liveRestoreMapping?'<button class="alt" data-a="clear-mapping">改用本项目映射</button>':'<button class="alt" data-a="mapping-file">改用映射文件（mapping.json）</button>'}</main>`+dock('restore',`<label class="field"><textarea id="paste" rows="1" aria-label="AI 结果" placeholder="粘贴含代号的文本"></textarea></label>`);
};
const handName=()=>liveHost?'交给 AI':'交给 Agent';
function exportDialog(kind){
  const partial=state.documents.some(d=>d.issues.length),c=counts(state);
  return `<div class="scrim" data-a="close-sheet"></div><section class="modal" role="dialog" aria-modal="true" aria-label="确认导出"><button class="x" data-a="close-sheet" aria-label="关闭">${icon('x',15)}</button><h2>${kind==='original'?'导出原格式文件':handName()}</h2><div class="tally"><span>脱敏 ${c.masked} 处</span><span>保留 ${c.kept} 处</span><span>补录 ${c.manual} 处</span></div>${noticeHtml()}${partial?'<label class="partial-ack"><input type="checkbox" id="partial-ack"> 我已核对以上未处理内容，按部分处理导出</label>':''}<p class="quiet">${kind==='markdown'?'脱敏稿写入工作目录的 mask-handoff 文件夹，Agent 只读这里；原件和映射不会写入':'原文件不会覆盖，映射不包含在下载文件中'}</p><button class="cta hot" id="confirm" data-a="${kind==='original'?'confirm-export':'confirm-handoff'}" ${liveBusy?'disabled':''}>${kind==='markdown'?icon('up',18)+handName():icon('down',18)+'生成文件'}</button></section>`;
}
handoffSheet=function(){return exportDialog('markdown');};
// 脱敏范围：标准类别默认全开；按需类别默认保留，开启后才识别。
function scopeLine(){
  const extra=liveCatalog.filter(g=>g.optional).flatMap(g=>g.categories).filter(c=>CATEGORIES.includes(c)).length;
  return `<button class="opt" data-a="scope-open"><span>脱敏范围</span><b>${CATEGORIES.length-extra} 类${extra?` + 按需 ${extra} 类`:''}</b>${icon('next',12)}</button>`;
}
// 接入 Agent：开关打开即把技能和拦截装入该 Agent，关闭即移除。对本机所有项目生效。
const AGENTS=[['dsh','DeepSeek Harness'],['codex','Codex'],['claude-code','Claude Code']];
const hostOn=id=>!!liveHosts.find(h=>h.id===id)?.enabled;
function hostsLine(){
  const on=AGENTS.filter(([id])=>hostOn(id)).map(([,name])=>name);
  return `<button class="opt" data-a="hosts-open"><i class="dot ${on.length?'on':''}"></i><span>Agent</span><b>${on.length?esc(on.join('、')):'未接入'}</b>${icon('next',12)}</button>`;
}
SHEETS.hosts=function(){
  return `<div class="scrim" data-a="close-sheet"></div><section class="modal" role="dialog" aria-modal="true" aria-label="接入 Agent"><button class="x" data-a="close-sheet" aria-label="关闭">${icon('x',15)}</button><h2>接入 Agent</h2>${AGENTS.map(([id,name])=>{
    const ready=liveHosts.some(h=>h.id===id),on=hostOn(id);
    return `<div class="host-row"><div><b>${esc(name)}</b><span>${!ready?'暂未支持':on?'已开启：/mask 技能可用，含真名的提问和读取原件会被拦下':'已关闭：这个 Agent 不受 Mask 约束'}</span></div><button class="switch ${on?'on':''}" role="switch" aria-checked="${on}" aria-label="${esc(name)}" data-a="host-toggle" data-v="${id}" ${!ready||liveBusy?'disabled':''}><i></i></button></div>`;
  }).join('')}<p class="quiet">开关对本机所有项目生效，切换后重启该 Agent 才起效。拦截只在有 Mask 项目的工作目录里起作用</p></section>`;
};
SHEETS.scope=function(){
  const rescan=state.documents.some(d=>d.status==='review');
  const isDefault=liveScope.size===liveDefaultScope.length&&liveDefaultScope.every(c=>liveScope.has(c));
  return `<div class="scrim" data-a="close-sheet"></div><section class="modal" role="dialog" aria-modal="true" aria-label="脱敏范围"><button class="x" data-a="close-sheet" aria-label="关闭">${icon('x',15)}</button><h2>脱敏范围</h2>${liveCatalog.map(g=>{
    const on=g.categories.filter(c=>liveScope.has(c)).length;
    return `<div class="scope-group"><div class="scope-head"><b>${esc(g.name)}</b><span>${g.optional?'按需：默认保留，分析案情常要用到':''}</span><button data-a="scope-group" data-v="${esc(g.name)}">${on===g.categories.length?'全不选':'全选'}</button></div><div class="scope-chips">${g.categories.map(c=>`<button class="scope-chip ${liveScope.has(c)?'on':''}" data-a="scope-toggle" data-v="${esc(c)}" aria-pressed="${liveScope.has(c)}">${esc(c)}</button>`).join('')}</div></div>`;
  }).join('')}<p class="quiet">金额、日期、法院和政府机关始终保留${rescan?'。改动后已识别的材料需要重新识别，已做的决定会保留':''}</p><button class="cta hot" data-a="scope-save" ${liveBusy||!liveScope.size?'disabled':''}>${icon('check',18)}确定</button><button class="alt" data-a="scope-default" ${isDefault||liveBusy||!liveScope.size?'disabled':''}>${isDefault?'已是新项目的默认范围':'设为新项目的默认范围'}</button></section>`;
};
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
  liveRevision=result.revision;liveFiles=liveHost?[]:result.files;
  hydrate(await api('project',undefined,'GET'));
  state.sheet='';state.page='done';state.phase='handed';state.restored=false;
  liveCopied=false;
  // Opened by the user rather than a host skill: the agent is told where the masked drafts are by a pasted brief.
  liveDone=liveHost&&result.handoff?{title:'已交给 AI',sub:'回到对话，发送“继续”。脱敏稿在 '+result.handoff}:result.handoff?{title:'已交给 Agent',sub:'在 Agent 里粘贴下面的指令，再写你的需求。脱敏稿在 '+result.handoff,brief:`请只读取 ${result.handoff} 里的脱敏稿来完成我的任务，不要读取、搜索或列出这个文件夹以外的材料。文件开头有代号说明：回答时原样使用代号，不要猜测代号背后的真实信息。

我的任务：`}:{title:'脱敏文件已生成',sub:kind==='markdown'?'下载后交给 AI；文件开头附有代号说明':'原文件未改动'};
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
  'mapping-file':()=>mappingPicker.click(),
  // Fixes name tags of the previous mapping, so switching the mapping starts the choices over.
  'clear-mapping':()=>{liveRestoreMapping=null;state.restoreFix={};state.miss=null;state.restored=false;render();},
  'accept-suggest':()=>{const r=restoreInfo(state);r.missing.forEach(t=>{if(r.suggest[t])state.restoreFix[t]=r.suggest[t];});state.miss=null;state.restored=false;render();},
  'save-restored':()=>task(async()=>{
    await persist();const result=await api('restore',{text:state.restoreInput,fixes:state.restoreFix,save:true,mapping:liveRestoreMapping?.mapping??null});
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
  'dismiss-error':()=>{liveError='';render();},
  'scope-open':()=>{liveScope=new Set(CATEGORIES);state.menu=false;dispatch({type:'sheet',value:'scope'});render();},
  'scope-toggle':t=>{const c=t.dataset.v;liveScope.has(c)?liveScope.delete(c):liveScope.add(c);render();},
  'scope-group':t=>{const g=liveCatalog.find(g=>g.name===t.dataset.v),all=g.categories.every(c=>liveScope.has(c));g.categories.forEach(c=>all?liveScope.delete(c):liveScope.add(c));render();},
  'scope-save':()=>task(async()=>{
    await persist();
    hydrate(await api('scope',{categories:[...liveScope],revision:liveRevision},'PUT'));
    state.sheet='';
  }),
  'hosts-open':()=>{state.menu=false;dispatch({type:'sheet',value:'hosts'});render();},
  'host-toggle':t=>task(async()=>{liveHosts=(await api('hosts/'+t.dataset.v,{enabled:!hostOn(t.dataset.v)},'PUT')).hosts;}),
  'copy-brief':()=>task(async()=>{await navigator.clipboard.writeText(liveDone.brief);liveCopied=true;}),
  'scope-default':()=>task(async()=>{liveDefaultScope=(await api('default-scope',{categories:[...liveScope]},'PUT')).default_scope;})
});
const resultPicker=document.createElement('input');resultPicker.type='file';resultPicker.accept='.txt,.md';resultPicker.id='result-picker';resultPicker.hidden=true;document.body.append(resultPicker);
resultPicker.onchange=()=>task(async()=>{
  if(!resultPicker.files.length)return;
  const form=new FormData();form.append('file',resultPicker.files[0]);
  const result=await api('read-result',form);state.restoreInput=result.text;state.restoreFix={};state.page='restore';state.restored=false;resultPicker.value='';
});
const mappingPicker=document.createElement('input');mappingPicker.type='file';mappingPicker.accept='.json';mappingPicker.hidden=true;document.body.append(mappingPicker);
mappingPicker.onchange=()=>task(async()=>{
  if(!mappingPicker.files.length)return;
  const form=new FormData();form.append('file',mappingPicker.files[0]);mappingPicker.value='';
  liveRestoreMapping=await api('read-mapping',form);state.restoreFix={};state.miss=null;state.page='restore';state.restored=false;
});
addEventListener('beforeunload',event=>{if(savedVersion!==editVersion||liveBusy){event.preventDefault();event.returnValue='';}});
const liveStyle=document.createElement('style');liveStyle.textContent=`
  body[data-view=bench] #proto{display:block}
  .live-status{position:fixed;bottom:6px;left:16px;font-size:11px;color:var(--ink2);z-index:50}
  .live-error{position:fixed;right:18px;top:64px;z-index:100;width:min(420px,90vw);padding:18px;border-radius:16px;background:var(--elev);box-shadow:var(--shadow-2);border:1px solid var(--zhu);overflow-wrap:anywhere}
  .live-error p{white-space:pre-line;margin:8px 0;max-height:35vh;overflow:auto}.live-error button{padding:6px 12px;border:1px solid var(--line);border-radius:8px;margin:6px 6px 0 0}
  .downloads{display:grid;gap:12px;max-height:40vh;overflow:auto;margin:24px auto}.download{color:var(--ink);display:flex;align-items:center;gap:6px;padding:12px;border:1px solid var(--line);border-radius:12px;text-decoration:none}
  .alt{display:block;margin:16px auto 0;color:var(--ink2);font-size:13px;text-decoration:underline;text-underline-offset:3px}
  .tile.off{opacity:.45;cursor:default}
  .scope-group{display:grid;gap:8px}.scope-head{display:flex;align-items:baseline;gap:8px;font-size:13px}.scope-head b{font-weight:600}.scope-head span{flex:1;color:var(--ink2);font-size:12px}.scope-head button{color:var(--ink2);font-size:12px;text-decoration:underline;text-underline-offset:3px}
  .scope-chips{display:flex;flex-wrap:wrap;gap:6px}.scope-chip{padding:5px 12px;border-radius:10px;background:var(--fill);color:var(--ink2);font-size:13px;transition:background-color .15s,color .15s}.scope-chip.on{background:var(--pill);color:var(--pill-fg)}
  .opts{display:flex;flex-wrap:wrap;justify-content:center;gap:8px}
  .opt{display:flex;align-items:center;gap:8px;height:34px;padding:0 12px 0 14px;border-radius:17px;background:var(--fill);color:var(--ink2);font-size:13px;transition:background-color .15s}.opt:hover{background:var(--fill2)}.opt b{font-weight:500;color:var(--ink)}.opt svg{color:var(--ink3)}
  .opt .dot{width:7px;height:7px;border-radius:50%;background:var(--ink3)}.opt .dot.on{background:#4a7de0}
  .host-row{display:flex;align-items:center;gap:16px}.host-row>div{flex:1;display:grid;gap:3px}.host-row b{font-weight:600;font-size:14px}.host-row span{color:var(--ink2);font-size:12px}
  .switch{flex:none;width:46px;height:26px;border-radius:13px;background:var(--fill2);padding:3px;transition:background-color .2s}.switch i{display:block;width:20px;height:20px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:transform .2s var(--spring)}
  .switch.on{background:#4a7de0}.switch.on i{transform:translateX(20px)}.switch:disabled{opacity:.4}
  .modal .quiet,.modal .alt{margin-top:0}.alt:disabled{text-decoration:none}
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
    state.dir.push(...local.filter(f=>!state.dir.some(x=>x.name===f.name)).map(f=>({key:crypto.randomUUID(),name:f.name,local:true,checked:f.supported,unsupported:!f.supported,format:f.name.split('.').pop().toUpperCase()})));
    if(state.documents.length){state.page='review';selectFirst(state);}
    render();
  }catch(error){fail(error);}
})();
