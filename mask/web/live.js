/* The local application: connects the workbench in app.js to the local service through /api. */
let liveRevision=0,liveMapping={},liveFiles=[],liveBusy=false,liveError='',liveRetry=false,liveProgress='',liveOutputs=[],liveDone={title:'',sub:''},liveCopied=false;
let editVersion=0,savedVersion=0,saveTimer=null,savingPromise=null;
// An imported mapping file replaces the project mapping for restore only; it is never merged into the project.
let liveRestoreMapping=null;
// Set when the page could not join the workbench at startup (no session, or the project failed to load).
let liveUnopened='';
// A Word AI result is restored into a Word file; it is kept until the text on the restore page is replaced.
let liveResultWord=null;
// Workbench opened by a host skill: exporting Markdown hands it to the workspace.
let liveHost=false,liveCatalog=[],liveScope=null,liveDefaultScope=[],liveHosts=[];
const restoreMapping=()=>liveRestoreMapping?Object.fromEntries(Object.entries(liveRestoreMapping.mapping).map(([t,v])=>[t,v[0]])):liveMapping;
const pendingUploads=new Map();
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
  liveHost=project.host;liveCatalog=project.catalog;liveDefaultScope=project.default_scope;liveHosts=project.hosts;
  state.categories=project.categories;state.entities=project.entities;
  const selectedId=state.documents[state.current]?.id;
  state.documents=project.documents.map(d=>{
    d.sections.forEach(sec=>{if(sec.breaks)sec.breaks=sec.breaks.map(([p,o])=>[p,cpToJs(sec.paragraphs[p],o)]);});
    d.candidates=d.candidates.map(c=>{const text=d.sections[c.s].paragraphs[c.p];return {...c,start:cpToJs(text,c.start),end:cpToJs(text,c.end)};});
    return {...d,scan:d.status==='review'?100:0,notice:d.issues.join('；')};
  });
  state.dir=state.dir.filter(f=>!f.docId&&!project.documents.some(d=>d.name===f.name)).concat(state.documents.map(d=>({key:d.id,name:d.name,format:d.format,docId:d.id,checked:true})));
  state.current=Math.max(0,state.documents.findIndex(d=>d.id===selectedId));
  state.nextManual=Math.max(state.nextManual,Date.now());
  if(!state.documents[state.current]?.candidates.some(c=>c.id===state.selected))state.selected=null;
}
function reviewPayload(){
  return {revision:liveRevision,entities:state.entities,documents:state.documents.map(d=>({id:d.id,candidates:d.candidates.map(c=>{
    const text=d.sections[c.s].paragraphs[c.p];
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
// No review edits while a request is running; a range handle never stops inside a surrogate pair.
function allow(action){
  if(mutationTypes.has(action.type)&&liveBusy)return false;
  if(action.type==='range'){
    const d=state.documents[state.current],c=d.candidates.find(c=>c.id===state.selected),text=paraText(d,c);
    const n=action.offset;
    if(n>0&&n<text.length&&/[\uD800-\uDBFF]/.test(text[n-1])&&/[\uDC00-\uDFFF]/.test(text[n]))return false;
  }
}
// Review edits are saved shortly after the last one.
function after(action){
  if(!mutationTypes.has(action.type))return;
  editVersion++;clearTimeout(saveTimer);
  saveTimer=setTimeout(()=>persist().catch(fail),220);
}
function statusView(){return `<div class="live-status">${liveBusy?esc(liveProgress||'处理中…'):savedVersion!==editVersion?'正在保存核对…':'本地工作台 · 核对自动保存'}</div>${liveError?`<aside class="live-error" role="alert"><b>未完成</b><p>${esc(liveError)}</p>${liveRetry?'<button data-a="retry-save">重试保存</button>':''}<button data-a="dismiss-error">关闭提示</button></aside>`:''}`;}
const barLead=()=>state.page==='import'?'<div class="brand"><i class="brand-mark"></i>Mask</div>':`<button class="crumb glass" data-a="home" aria-label="材料列表">${icon('back',15)}<span>Mask</span></button>`;
// 核对页的还原入口在灵动岛里；其他页面放在右上角。
const barEnd=island=>island?'':`<button class="crumb glass" data-a="restore-open" title="还原 AI 结果中的代号">${icon('down',15)}<span>还原 AI 结果</span></button>`;
const islandExtras=()=>`<button class="isl-btn" data-a="restore-open" title="还原 AI 结果中的代号">${icon('down',16)}<span class="lbl">还原</span></button>`;
function tile(f,i){
  if(f.unsupported)return fileRow({f,i,off:true,sub:'先另存为 .docx'});
  const doc=state.documents.find(d=>d.id===f.docId);
  if(!doc)return fileRow({f,i,a:'toggle',on:!!f.checked,sub:f.local?'':'待导入'});
  // 已识别的材料：点行直接回到核对。
  const ready=doc.status==='review',n=pendingOf(doc),sub=doc.status==='failed'?'识别失败':!ready?'待识别':n?`${n} 处待确认`:'已核对';
  return fileRow({f,i,a:ready?'open-existing':'',doc:ready?doc:null,sub,hot:doc.status==='failed'||n>0,on:!ready,end:ready?`<span class="f-go">${n?'核对':''}${icon('next',14)}</span>`:''});
}
function selectPage(){
  // Without the project nothing on this page can work, so the startup failure replaces it.
  if(liveUnopened)return `<main class="center" data-k="p-unopened"><div class="brand"><i class="brand-mark"></i>Mask</div><h1 class="ask">工作台没能打开</h1><p class="quiet">${esc(liveUnopened)}</p></main>`;
  const n=state.dir.filter(f=>f.checked&&!f.docId).length+state.documents.filter(d=>d.status!=='review').length;
  const start=n?`<button class="cta hot" data-a="start-scan" ${liveBusy?'disabled':''}>开始识别 ${countNum(n)} 份${icon('right',18)}</button>`:'';
  const resume=state.documents.some(d=>d.status==='review')?`<button class="cta ${n?'soft':'hot'}" data-a="review-open">继续核对</button>`:'';
  // Opened from an agent or in a material folder, the files are already here: materials first, then the one
  // action, then the two rarely changed settings as quiet chips.
  const local=state.dir.filter(f=>f.local&&!f.unsupported).length;
  return bar()+`<main class="center" data-k="p-import"><h1 class="ask">${!state.dir.length?'选择需要脱敏的材料':!n&&resume?'继续上次的核对':'确认需要脱敏的材料'}</h1><p class="quiet">${icon('lock',13)}${local?`已带入工作目录的 ${local} 份，`:''}原文与映射只在本机处理</p><div class="files">${fileOrder(state.dir).map(tile).join('')}${addRow(state.dir.length,'TXT · Markdown · Word（.docx）· 原生 PDF')}</div>${start||resume?`<div class="ctas wide">${start}${resume}</div>`:'<p class="quiet">勾选或添加材料后开始识别</p>'}<div class="opts">${scopeLine()}${hostsLine()}</div></main>`;
}
function reviewGo(){
  const c=counts(state),bad=state.documents.filter(d=>d.status==='failed'),clash=conflicts().length;
  if(liveBusy)return act({cls:'busy',title:'本地处理中',inner:'<i class="spin"></i><span class="lbl">处理中</span>',key:'busy'});
  if(bad.length)return act({cls:'todo',a:'start-scan',title:'重试失败的识别',inner:`${icon('right',15)}<span class="lbl">重试识别</span>`,key:'retry'});
  if(c.scanning){const again=state.documents.some(d=>d.status!=='review'&&d.candidates.length)?'重新识别':'识别剩余材料';return act({cls:'next',a:'start-scan',title:again+'，已做的决定保留',inner:`${icon('right',15)}<span class="lbl">${again}</span>`,key:'scan'});}
  if(c.pending)return act({cls:'todo',a:'next',title:`下一处待确认，还剩 ${c.pending} 处（J）`,inner:`<b>${c.pending}</b><span class="lbl">处待确认</span>${icon('next',14)}`,key:'n'+c.pending});
  if(clash)return act({cls:'todo',a:'conflict',title:'处理重叠范围',inner:`<b>${clash}</b><span class="lbl">处范围重叠</span>${icon('next',14)}`,key:'conflict'+clash});
  return act({cls:'ready',a:'release',title:'把脱敏稿'+handName(),inner:`${icon('up',15)}<span class="lbl">${handName()}</span>`,key:'export'});
}
function paraHtml(d,si,pi,hits,cur,from=0,to){
  const text=d.sections[si].paragraphs[pi],here=hits.filter(h=>h.s===si&&h.p===pi);
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
}
function paper(d,hits){
  const here=hits.filter(h=>h.docIndex===state.current),cur=hits[state.hit],ai=state.preview==='masked';
  const sheets=sheetsOf(d),c=d.candidates.find(c=>c.id===state.selected),target=state.sel||c;
  const coSheet=target?Math.max(0,sheetOf(sheets,target.s,target.p,target.start)):-1;
  const flags=`${d.notice?`<div class="flag">${esc(d.notice)}；按部分处理导出</div>`:''}${d.error?`<div class="flag" role="alert">识别失败：${esc(d.error)}</div>`:''}${d.status!=='review'?`<div class="flag">${d.status==='failed'?'请修复原因后重试':'尚未完成识别'}</div>`:''}`;
  const name=ai?'材料 '+d.no:d.name;
  return sheets.map((sh,i)=>`<div class="sheet-no" data-k="n${d.id}-${i}">${i?'':esc(name)+' · '}${esc(sh.label)}</div><article class="page ${ai?'ai':''} ${sh.page?'pg':''}" data-k="doc${d.id}-${i}">${i?'':flags}${sh.parts.map(([si,pi,from,to])=>`<p data-s="${si}" data-p="${pi}" data-o="${from}" style="white-space:pre-wrap;overflow-wrap:anywhere">${paraHtml(d,si,pi,here,cur,from,to)}</p>`).join('')}${i===coSheet?calloutFor(d):''}</article>`).join('');
}
function menuExtras(){
  if(state.page!=='review'&&state.page!=='done')return [];
  const extra=[['scope-open','search','脱敏范围'],['hosts-open','lock','接入 Agent'],['export-mapping','lock','导出映射文件',!Object.keys(liveMapping).length]];
  if(state.page==='review')extra.push(['remove-document','x','移除当前材料']);
  return extra;
}
function downloadLink(f){return `<a class="download" href="/api/download/${encodeURIComponent(f.id)}" download>${icon('down',16)} ${esc(f.name)}</a>`;}
function donePage(){
  return bar()+`<main class="center" data-k="p-done"><h1 class="ask">${esc(liveDone.title)}</h1><p class="quiet">${esc(liveDone.sub)}</p>${liveDone.brief?`<button class="cta hot" data-a="copy-brief">${icon(liveCopied?'check':'doc',18)}${liveCopied?'已复制，去 Agent 里粘贴':'复制给 Agent 的指令'}</button>`:''}<div class="downloads">${liveFiles.map(downloadLink).join('')}</div><button class="cta ${liveDone.brief?'soft':''}" data-a="review-open">返回核对</button></main>`+dock('done',`${moreMenu([['export','share','导出原格式',!canExport(state)],['restore-file','doc','打开结果文件'],['add-more','plus','补充材料']])}<label class="field"><textarea id="paste" rows="1" aria-label="AI 结果" placeholder="粘贴 AI 结果，还原真名"></textarea></label>${go({cls:'next',a:'restore-open',label:'还原结果',inner:icon('down'),key:'restore'})}`);
}
function restorePage(){
  if(state.restoreInput.trim()){
    const r=restoreInfo(state),partial=r.kept.length>0;
    const save=partial?'保存部分还原版':'保存真名版',status=r.missing.length?'':partial?`部分还原，${r.kept.length} 个代号保留`:`已还原 ${r.replaced} 处`;
    const fixable=r.missing.filter(t=>r.suggest[t]).length;
    const source=liveRestoreMapping?`<button class="isl-btn" data-a="clear-mapping" title="正在使用 ${esc(liveRestoreMapping.name)}；点此改用本项目映射">${icon('x',15)}<span class="lbl">已用映射文件</span></button>`
      :`<button class="isl-btn" data-a="mapping-file" title="用导出的 mapping.json 还原，不并入本项目">${icon('lock',15)}<span class="lbl">映射文件</span></button>`;
    const primary=source+(fixable?`<button class="isl-btn" data-a="accept-suggest" title="把形状被改动的代号按格式还原后的代号处理">${icon('check',15)}<span class="lbl">采用 ${fixable} 个格式建议</span></button>`:'')+(r.missing.length?act({cls:'todo',a:'next-miss',title:'下一处没有映射的代号',inner:`<b>${r.missing.length}</b><span class="lbl">个代号没有映射</span>${icon('next',14)}`,key:'missing'+r.missing.length}):act({cls:'save',a:'save-restored',title:save,inner:`${icon('down',15)}<span class="lbl">${save}</span>`,disabled:liveBusy,key:'save'}));
    return bar({title:'AI 结果',island:restoreIsland(status,primary)})+`<main class="canvas" id="canvas" data-k="p-restore">${restorePaper(r)}</main>`;
  }
  return bar({title:'还原结果'})+`<main class="center" data-k="p-restore-input"><h1 class="ask">还原 AI 结果中的代号</h1><p class="quiet">${liveRestoreMapping?`使用映射文件 ${esc(liveRestoreMapping.name)}（${Object.keys(liveRestoreMapping.mapping).length} 个代号），不并入本项目`:'使用本项目已保存的映射'}，真名版只保存到本机</p><button class="cta" data-a="restore-file">打开 TXT / Markdown / Word 结果文件</button>${liveRestoreMapping?'<button class="alt" data-a="clear-mapping">改用本项目映射</button>':'<button class="alt" data-a="mapping-file">改用映射文件（mapping.json）</button>'}</main>`+dock('restore',`<label class="field"><textarea id="paste" rows="1" aria-label="AI 结果" placeholder="粘贴含代号的文本"></textarea></label>`);
}
const handName=()=>liveHost?'交给 AI':'交给 Agent';
function exportDialog(kind){
  const partial=state.documents.some(d=>d.issues.length),c=counts(state);
  return `<div class="scrim" data-a="close-sheet"></div><section class="modal" role="dialog" aria-modal="true" aria-label="确认导出"><button class="x" data-a="close-sheet" aria-label="关闭">${icon('x',15)}</button><h2>${kind==='original'?'导出原格式文件':handName()}</h2><div class="tally"><span>脱敏 ${c.masked} 处</span><span>保留 ${c.kept} 处</span><span>补录 ${c.manual} 处</span></div>${noticeHtml()}${partial?'<label class="partial-ack"><input type="checkbox" id="partial-ack"> 我已核对以上未处理内容，按部分处理导出</label>':''}<p class="quiet">${kind==='markdown'?'脱敏稿写入工作目录的 mask-handoff 文件夹，Agent 只读这里；原件和映射不会写入':'原文件不会覆盖，映射不包含在下载文件中'}</p><button class="cta hot" id="confirm" data-a="${kind==='original'?'confirm-export':'confirm-handoff'}" ${liveBusy?'disabled':''}>${kind==='markdown'?icon('up',18)+handName():icon('down',18)+'生成文件'}</button></section>`;
}
// 脱敏范围：标准类别默认全开；按需类别默认保留，开启后才识别。
function scopeLine(){
  const extra=liveCatalog.filter(g=>g.optional).flatMap(g=>g.categories).filter(c=>state.categories.includes(c)).length;
  return `<button class="opt" data-a="scope-open"><span>脱敏范围</span><b>${state.categories.length-extra} 类${extra?` + 按需 ${extra} 类`:''}</b>${icon('next',12)}</button>`;
}
// 接入 Agent：一份面板两页（Agent / 记录）。只读写全机状态，对本机所有案件生效，不依赖当前打开的案件。
const AGENTS=[['dsh','DeepSeek Harness','D'],['codex','Codex','C'],['claude-code','Claude Code','C']];
// 每个 Agent 的说明：part 是 Mask 在它的配置文件里写的东西；scope 是哪些请求经过 Mask；adds 是接入加了什么、哪些不动；removes 是断开时一并移除的。
const AGENT_NOTES={
  dsh:{part:'一块',scope:'同时装入 /mask 技能和拦截插件。只有 DeepSeek 官方路由（API Key）的模型经过 Mask，在它里面选了其他提供方的模型时不经过。',adds:'只加 Mask 自己的这一块：拦截插件，以及 DeepSeek 官方路由的地址。<b>API Key 和其他设置不动。</b>断开时就是删掉这一块。',removes:'/mask 技能一并移除'},
  codex:{part:'一行',scope:'同时装入 $mask 技能。用 ChatGPT 账号登录、走内置 OpenAI 通道的请求经过 Mask；把 model_provider 换成别的提供方后不经过。Codex 不会自己启动网关，重启电脑后要先在这里点“启动”。',adds:'只加 Mask 自己的这一行：内置 OpenAI 通道的地址。<b>登录凭据和其他设置不动。</b>断开时就是删掉这一行。',removes:'$mask 技能一并移除'}};
// gw：GET /api/hosts 的结果。gwView：''、'off'（断开确认）、'diff'（看改动）。gwUndo：刚接入、还能撤销的 Agent。
let gw=null,gwTab='agents',gwOpen='',gwView='',gwDiff=null,gwLog=null,gwLogBy='req',gwOpenLog=-1,gwUndo='',gwBusy='',gwPoll=0,gwUndoTimer=0;
const hostOn=id=>!!liveHosts.find(h=>h.id===id)?.enabled;
const agentName=id=>AGENTS.find(a=>a[0]===id)?.[1]||id;
const sumTags=r=>Object.values(r.tags||{}).reduce((a,b)=>a+b,0);
async function gwRefresh(){
  gw=await api('hosts',undefined,'GET');
  liveHosts=gw.agents.map(a=>({id:a.id,enabled:a.state!=='off'}));
  if(gwTab==='log')gwLog=await api('gateway/log',undefined,'GET');
}
function hostsLine(){
  const on=AGENTS.filter(([id])=>hostOn(id)).map(([,name])=>name),todo=gw?.agents.some(a=>a.state==='down');
  return `<button class="opt" data-a="hosts-open"><i class="dot ${todo?'todo':on.length?'on':''}"></i><span>Agent</span><b>${on.length?esc(on.join('、')):'未接入'}</b>${icon('next',12)}</button>`;
}
function gwLine(){
  const g=gw.gateway;
  if(g.problem)return `<div class="gw down"><span><b>网关无法启动</b> · ${esc(g.problem)}</span></div>`;
  if(gw.agents.some(a=>a.state==='down'))return `<div class="gw down"><span><b>网关没有运行</b> · 已接入的 Agent 现在发不出请求</span><button data-a="gw-start" ${liveBusy?'disabled':''}>启动</button></div>`;
  if(!gw.agents.some(a=>a.state!=='off'))return `<div class="gw"><i class="gdot"></i><span>还没有 Agent 经过 Mask。打开开关后，它发给模型的内容里已确认的真名会换成代号。</span></div>`;
  return `<div class="gw"><i class="gdot on"></i><span><b>网关运行中</b></span><span class="nums">今天 ${g.requests} 条请求 · 换了 ${g.replaced} 处${g.refused?` · 拦下 ${g.refused} 条`:''}</span></div>`;
}
function agentRow([id,name,glyph]){
  const a=gw.agents.find(a=>a.id===id);
  if(!a)return `<div class="ag na"><div class="ag-ic">${glyph}</div><div class="ag-main"><b>${esc(name)}</b><div class="ag-st">暂未支持</div></div></div>`;
  const on=a.state!=='off',open=gwOpen===id,busy=gwBusy===id,host=new URL(a.upstream).host;
  const [dot,text]=busy?['wait',on?'正在断开…':'正在启动网关并写入配置…']:a.blocked?['','不能接入']:{
    off:['','未接入 · 直连 '+host],pending:['wait',`已接入 · 重启 ${name} 后生效`],live:['on','已接入 · 生效中'],down:['todo','已接入 · 网关没有运行，请求发不出去']}[a.state];
  const right=a.blocked?'':`<button class="switch ${on?'on':''}" role="switch" aria-checked="${on}" aria-label="${esc(name)}" data-a="host-toggle" data-v="${id}" ${liveBusy?'disabled':''}><i></i></button>`;
  const route=`<div class="kv"><span>请求去向</span><code>${esc(name)} → Mask → ${esc(host)}</code></div>`;
  const note=AGENT_NOTES[id],scope=`<div>${esc(note.scope)}</div>`;
  const detail=!open?'':a.blocked?`<div class="ag-detail"><div>${esc(a.blocked)}</div></div>`
    :!on?`<div class="ag-detail"><div class="kv"><span>会改</span><code>${esc(a.file)}（加 Mask 的${note.part}）</code></div>${route}${scope}<div class="links"><button class="lk" data-a="gw-diff" data-v="${id}">看将要改的内容</button></div></div>`
    :`<div class="ag-detail"><div class="kv"><span>改动位置</span><code>${esc(a.file)}（Mask 的${note.part}）</code></div>${route}${a.state==='pending'?`<div>已打开的 ${esc(name)} 还在用旧设置。重启后第一条请求到达，这里会变成“生效中”。</div>`:''}${scope}<div class="links"><button class="lk" data-a="gw-diff" data-v="${id}">看改了什么</button><button class="lk" data-a="gw-tab" data-v="log">看它的记录</button><button class="lk red" data-a="gw-ask-off" data-v="${id}">断开并还原</button></div></div>`;
  return `<div class="ag"><div class="ag-ic">${glyph}</div><div class="ag-main"><b>${esc(name)}</b><div class="ag-st ${dot==='todo'?'todo':''}">${dot?`<i class="gdot ${dot}"></i>`:''}${esc(text)}</div></div><button class="ag-more ${open?'open':''}" data-a="gw-open" data-v="${id}" aria-expanded="${open}">${a.blocked?'原因':'详情'}${icon('next',13)}</button>${right}</div>${detail}`;
}
function gwAgents(){
  const undo=gwUndo?`<div class="gw note"><span>已接入 ${esc(agentName(gwUndo))}，重启它后生效</span><button data-a="gw-undo" ${liveBusy?'disabled':''}>撤销</button></div>`:'';
  return undo+gwLine()+`<div class="ags">${AGENTS.map(agentRow).join('')}</div><p class="quiet">开关对本机所有案件生效。原件和映射始终只在本机</p>`;
}
const REFUSED={strict:r=>`含未脱敏的${(r.strict||[]).join('、')}号码，没有发出。请先在工作台处理那份材料`,path:()=>'案件文件夹的路径里含已脱敏的名称，没有发出。请把材料放到路径里不含该名称的文件夹',
  'not-json':()=>'请求不是 JSON（如上传图片），无法检查，没有发出',encoding:()=>'请求体经过压缩，无法检查，没有发出',error:()=>'检查请求时出错，没有发出'};
function gwRecords(){
  if(!gwLog)return '<div class="empty">正在读取…</div>';
  if(!gwLog.records.length)return '<div class="empty">今天还没有记录。<br>接入的 Agent 每发一条请求，换了什么会列在这里，不记原文。</div>';
  const by=`<div class="gseg sm"><button class="${gwLogBy==='req'?'on':''}" data-a="gw-logby" data-v="req">按请求</button><button class="${gwLogBy==='tag'?'on':''}" data-a="gw-logby" data-v="tag">按代号</button></div>`;
  if(gwLogBy==='tag'){
    const sum={};gwLog.records.forEach(r=>Object.entries(r.tags||{}).forEach(([t,n])=>sum[t]=(sum[t]||0)+n));
    const rows=Object.entries(sum).sort((x,y)=>y[1]-x[1]);
    return by+(rows.length?`<div class="logs">${rows.map(([t,n])=>`<div class="lg tagrow"><span><span class="tag">${esc(t)}</span></span><em>今天换了 ${n} 处</em></div>`).join('')}</div><p class="quiet">代号对应的原文在对应案件的“实体”面板里看</p>`:'<div class="empty">今天的请求里没有已确认的真名。</div>');
  }
  return by+`<div class="logs">${gwLog.records.map((r,i)=>{
    const n=sumTags(r),open=gwOpenLog===i,bad=r.refused||r.status>=400;
    const where=r.project===gwLog.here?'本案件':r.project==='*'?'未说明工作目录':r.project?'其他案件':'没有 Mask 项目的目录';
    const right=r.refused?'未发送':r.status===502?'没连上模型服务':r.status>=400?`模型服务返回 ${r.status}`:n?`换了 ${n} 处`:'没有要换的';
    const x=!open?'':`<div class="lg-x">${r.refused?`<p>${esc(REFUSED[r.refused](r))}</p>`:n?Object.entries(r.tags).map(([t,c])=>`<span class="tag">${esc(t)}<b>×${c}</b></span>`).join(''):`<small>${r.project?'这条请求里没有已确认的真名。':'这个目录没有 Mask 项目，只检查了身份证号和信用代码。'}</small>`}</div>`;
    return `<button class="lg ${bad?'blk':''}" data-a="gw-openlog" data-v="${i}" aria-expanded="${open}"><time>${esc(r.t.slice(11,16))}</time><span>${esc(agentName(r.agent))}<small> · ${where}</small></span><em>${right}</em></button>${x}`;
  }).join('')}</div>`;
}
function gwDiffBox(){
  return `<div class="diff"><div class="diff-h">${esc(gwDiff.file)}</div>${gwDiff.lines.length?gwDiff.lines.map(([k,t])=>`<div class="dl ${k==='+'?'add':k==='-'?'del':'ctx'}" title="${esc(t)}">${esc(k+' '+t)}</div>`).join(''):'<div class="dl ctx">没有要改的内容</div>'}</div>`;
}
function gwConfirm(){
  const name=agentName(gwDiff.id),note=AGENT_NOTES[gwDiff.id],back=`<button class="bk" data-a="gw-back" aria-label="返回">${icon('back',15)}</button>`;
  if(gwView==='off')return back+`<h2>断开 ${esc(name)} 和 Mask？</h2><p class="say">${esc(name)} 将恢复接入前的设置。要删掉的只有 Mask 自己写的这${note.part}，别的不动；${note.removes}。</p>${gwDiffBox()}<div class="warn">${icon('info',16)}<span>断开后，它发给模型的内容不再经过 Mask，真名会原样发出。重启 ${esc(name)} 后生效。</span></div><div class="btns"><button class="cta soft" data-a="gw-back">取消</button><button class="cta hot" data-a="gw-off" ${liveBusy?'disabled':''}>断开并还原</button></div>`;
  return back+`<h2>${hostOn(gwDiff.id)?'接入时改了什么':'接入会改什么'}</h2><p class="say">${note.adds}</p>${gwDiffBox()}<div class="btns"><button class="cta soft" data-a="gw-back">知道了</button></div>`;
}
function hostsSheet(){
  const head=`<div class="scrim" data-a="close-sheet"></div><section class="modal gwp" role="dialog" aria-modal="true" aria-label="接入 Agent"><button class="x" data-a="close-sheet" aria-label="关闭">${icon('x',15)}</button>`;
  if(!gw)return head+'<h2>接入 Agent</h2><div class="empty">正在读取…</div></section>';
  if(gwView)return head+gwConfirm()+'</section>';
  return head+`<div class="gseg"><button class="${gwTab==='agents'?'on':''}" data-a="gw-tab" data-v="agents">Agent</button><button class="${gwTab==='log'?'on':''}" data-a="gw-tab" data-v="log">记录</button></div>`+(gwTab==='agents'?gwAgents():gwRecords())+'</section>';
}
async function gwSwitch(id,on){
  gwBusy=id;render();
  try{await api('hosts/'+id,{enabled:on},'PUT');}finally{gwBusy='';await gwRefresh();}
  clearTimeout(gwUndoTimer);gwUndo='';gwView='';
  if(on){gwOpen=id;gwUndo=id;gwUndoTimer=setTimeout(()=>{gwUndo='';render();},6000);}
}
function scopeSheet(){
  const rescan=state.documents.some(d=>d.status==='review');
  const isDefault=liveScope.size===liveDefaultScope.length&&liveDefaultScope.every(c=>liveScope.has(c));
  return `<div class="scrim" data-a="close-sheet"></div><section class="modal" role="dialog" aria-modal="true" aria-label="脱敏范围"><button class="x" data-a="close-sheet" aria-label="关闭">${icon('x',15)}</button><h2>脱敏范围</h2>${liveCatalog.map(g=>{
    const on=g.categories.filter(c=>liveScope.has(c)).length;
    return `<div class="scope-group"><div class="scope-head"><b>${esc(g.name)}</b><span>${g.optional?'按需：默认保留，分析案情常要用到':''}</span><button data-a="scope-group" data-v="${esc(g.name)}">${on===g.categories.length?'全不选':'全选'}</button></div><div class="scope-chips">${g.categories.map(c=>`<button class="scope-chip ${liveScope.has(c)?'on':''}" data-a="scope-toggle" data-v="${esc(c)}" aria-pressed="${liveScope.has(c)}">${esc(c)}</button>`).join('')}</div></div>`;
  }).join('')}<p class="quiet">金额、日期、法院和政府机关始终保留${rescan?'。改动后已识别的材料需要重新识别，已做的决定会保留':''}</p><button class="cta hot" data-a="scope-save" ${liveBusy||!liveScope.size?'disabled':''}>${icon('check',18)}确定</button><button class="alt" data-a="scope-default" ${isDefault||liveBusy||!liveScope.size?'disabled':''}>${isDefault?'已是新项目的默认范围':'设为新项目的默认范围'}</button></section>`;
}
function addFiles(files){
  for(const file of files){
    if(!/\.(txt|md|docx|pdf)$/i.test(file.name)){fail(new Error(`${file.name}：支持 TXT、Markdown、DOCX 和原生 PDF；.doc 请另存为 .docx`));continue;}
    const key=crypto.randomUUID();pendingUploads.set(key,file);
    state.dir.push({key,name:file.name,format:file.name.split('.').pop().toUpperCase(),checked:true});
  }render();
}
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
  state.sheet='';state.page='done';state.restored=false;
  liveCopied=false;
  // Opened by the user rather than a host skill: the agent is told where the masked drafts are by a pasted brief.
  liveDone=liveHost&&result.handoff?{title:'已交给 AI',sub:'回到对话，发送“继续”。脱敏稿在 '+result.handoff}:result.handoff?{title:'已交给 Agent',sub:'在 Agent 里粘贴下面的指令，再写你的需求。脱敏稿在 '+result.handoff,brief:`请只读取 ${result.handoff} 里的脱敏稿来完成我的任务，不要读取、搜索或列出这个文件夹以外的材料。文件开头有代号说明：回答时原样使用代号，不要猜测代号背后的真实信息。

我的任务：`}:{title:'脱敏文件已生成',sub:kind==='markdown'?'下载后交给 AI；文件开头附有代号说明':'原文件未改动'};
}
const liveActions={
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
    await persist();const mapping=liveRestoreMapping?.mapping??null;let result;
    if(liveResultWord?.text===state.restoreInput){
      const form=new FormData();form.append('file',liveResultWord.file);form.append('choices',JSON.stringify({fixes:state.restoreFix,mapping}));
      result=await api('restore-word',form);
    }else result=await api('restore',{text:state.restoreInput,fixes:state.restoreFix,save:true,mapping});
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
  'scope-open':()=>{liveScope=new Set(state.categories);state.menu=false;dispatch({type:'sheet',value:'scope'});render();},
  'scope-toggle':t=>{const c=t.dataset.v;liveScope.has(c)?liveScope.delete(c):liveScope.add(c);render();},
  'scope-group':t=>{const g=liveCatalog.find(g=>g.name===t.dataset.v),all=g.categories.every(c=>liveScope.has(c));g.categories.forEach(c=>all?liveScope.delete(c):liveScope.add(c));render();},
  'scope-save':()=>task(async()=>{
    await persist();
    hydrate(await api('scope',{categories:[...liveScope],revision:liveRevision},'PUT'));
    state.sheet='';
  }),
  'hosts-open':()=>{
    state.menu=false;gwView='';dispatch({type:'sheet',value:'hosts'});render();
    const refresh=()=>gwRefresh().then(render,fail);
    refresh();clearInterval(gwPoll);
    // The state changes outside this page: the agent restarts, sends a request, the gateway stops.
    gwPoll=setInterval(()=>{if(state.sheet!=='hosts')clearInterval(gwPoll);else if(!liveBusy&&!gwView)refresh();},4000);
  },
  // Connecting is one click and can be undone; disconnecting shows what is removed and asks first.
  'host-toggle':t=>hostOn(t.dataset.v)?liveActions['gw-ask-off'](t):task(()=>gwSwitch(t.dataset.v,true)),
  'gw-ask-off':t=>task(async()=>{gwDiff={id:t.dataset.v,...await api('hosts/'+t.dataset.v+'/preview?enabled=false',undefined,'GET')};gwView='off';}),
  'gw-diff':t=>task(async()=>{gwDiff={id:t.dataset.v,...await api('hosts/'+t.dataset.v+'/preview?enabled=true',undefined,'GET')};gwView='diff';}),
  'gw-off':()=>task(()=>gwSwitch(gwDiff.id,false)),
  'gw-undo':()=>task(()=>gwSwitch(gwUndo,false)),
  'gw-back':()=>{gwView='';render();},
  'gw-open':t=>{gwOpen=gwOpen===t.dataset.v?'':t.dataset.v;render();},
  'gw-tab':t=>task(async()=>{gwTab=t.dataset.v;gwOpenLog=-1;await gwRefresh();}),
  'gw-logby':t=>{gwLogBy=t.dataset.v;render();},
  'gw-openlog':t=>{gwOpenLog=gwOpenLog===+t.dataset.v?-1:+t.dataset.v;render();},
  'gw-start':()=>task(async()=>{gw=await api('gateway/start',{});}),
  'copy-brief':()=>task(async()=>{await navigator.clipboard.writeText(liveDone.brief);liveCopied=true;}),
  'scope-default':()=>task(async()=>{liveDefaultScope=(await api('default-scope',{categories:[...liveScope]},'PUT')).default_scope;})
};
const resultPicker=document.createElement('input');resultPicker.type='file';resultPicker.accept='.txt,.md,.docx';resultPicker.id='result-picker';resultPicker.hidden=true;document.body.append(resultPicker);
resultPicker.onchange=()=>task(async()=>{
  if(!resultPicker.files.length)return;
  const file=resultPicker.files[0],form=new FormData();form.append('file',file);
  const result=await api('read-result',form);liveResultWord=/\.docx$/i.test(file.name)?{file,text:result.text}:null;state.restoreInput=result.text;state.restoreFix={};state.page='restore';state.restored=false;resultPicker.value='';
});
const mappingPicker=document.createElement('input');mappingPicker.type='file';mappingPicker.accept='.json';mappingPicker.hidden=true;document.body.append(mappingPicker);
mappingPicker.onchange=()=>task(async()=>{
  if(!mappingPicker.files.length)return;
  const form=new FormData();form.append('file',mappingPicker.files[0]);mappingPicker.value='';
  liveRestoreMapping=await api('read-mapping',form);state.restoreFix={};state.miss=null;state.page='restore';state.restored=false;
});
addEventListener('beforeunload',event=>{if(savedVersion!==editVersion||liveBusy){event.preventDefault();event.returnValue='';}});
start({
  pages:{import:selectPage,done:donePage,restore:restorePage},
  sheets:{handoff:()=>exportDialog('markdown'),export:()=>exportDialog('original'),hosts:hostsSheet,scope:scopeSheet},
  actions:liveActions,paper,reviewGo,addFiles,barLead,barEnd,islandExtras,menuExtras,allow,after,
  restoreMap:restoreMapping,
  render(){patch($('status'),statusView());}
});
(async()=>{
  try{
    try{
      const launch=location.hash.slice(1);
      if(launch){
        const response=await fetch('/session',{method:'POST',headers:{'X-Launch-Token':launch}});
        if(!response.ok)throw new Error((await response.json()).error);
        history.replaceState(null,'','/');
      }
      hydrate(await api('project',undefined,'GET'));
    }catch(error){liveUnopened=error.message;render();return;}
    const local=await api('workspace',undefined,'GET');
    gwRefresh().then(render,fail);
    state.dir.push(...local.filter(f=>!state.dir.some(x=>x.name===f.name)).map(f=>({key:crypto.randomUUID(),name:f.name,local:true,checked:f.supported,unsupported:!f.supported,format:f.name.split('.').pop().toUpperCase()})));
    if(state.documents.length){state.page='review';selectFirst(state);}
    render();
  }catch(error){fail(error);}
})();
