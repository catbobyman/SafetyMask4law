/* Mask 工作台界面：状态归约、增量渲染、核对页和还原页的共用部分。它不关心数据从哪来。
   真实应用（live.js）和模拟（prototype/sim.js）各自调用 start(mode) 登记自己的部分：
     pages                  核对页以外的页面 {import, done, restore, ...}，各返回 HTML
     sheets                 底部抽屉 {handoff, export, ...}，各返回 HTML
     actions                data-a 对应的动作，与 ACTIONS 同名时用这里的
     paper(d, hits)         核对页里一份材料的正文
     reviewGo()             核对页灵动岛的主按钮
     barLead()              顶栏左端
     restoreMap(s)          还原页用的 代号 → 原文
     addFiles(files)        入场页选中或拖入的文件
     render()               工作台以外的区域
     barEnd(island)         可选：顶栏右端
     islandExtras()         可选：灵动岛里“实体”之后的按钮
     menuExtras()           可选：“更多”菜单末尾追加的项
     reduce(s, a, prev)     可选：先于 reduce 收到动作，可改草稿 s；返回一个状态则到此为止
     allow(action)          可选：返回 false 时丢弃这个动作
     after(action, before)  可选：每次 dispatch 渲染之后
   材料正文在 state.documents[i].sections：[{title, paragraphs, breaks?}]；类别在 state.categories。 */
const initialState=()=>({tool:'pointer',penType:'auto',boxMode:'mask',toolsOpen:false,view:'bench',page:'import',categories:[],dir:[],documents:[],entities:{},current:0,selected:null,sel:null,selType:'',pick:'',preview:'original',inspector:false,menu:false,sheet:'',search:'',hit:-1,restoreInput:'',restoreFix:{},miss:null,restored:false,nextManual:1});

const paraText=(d,c)=>d.sections[c.s].paragraphs[c.p];
const scanned=s=>s.documents.filter(d=>d.scan>=100);
const allCands=s=>scanned(s).flatMap(d=>d.candidates.map(c=>({d,c})));
const maskedCands=s=>allCands(s).filter(x=>x.c.decision==='mask');
// 按原文分页：没有分页记录的节是一张纸；Word 正文按保存时的分页位置切开，候选不跨页拆开。
function sheetsOf(d){
  const sheets=[];
  d.sections.forEach((sec,si)=>{
    if(!sec.breaks?.length){sheets.push({label:sec.title,page:!!sec.breaks||d.format==='PDF',parts:sec.paragraphs.map((t,pi)=>[si,pi,0,t.length])});return;}
    const pages=[[]];
    sec.paragraphs.forEach((text,pi)=>{
      let from=0;
      const marks=d.candidates.filter(c=>c.s===si&&c.p===pi).sort((a,b)=>a.start-b.start);
      sec.breaks.filter(b=>b[0]===pi).map(b=>b[1]).sort((a,b)=>a-b).forEach(cut=>{
        marks.forEach(c=>{if(c.start<cut&&c.end>cut)cut=c.end;});
        if(cut>from){pages.at(-1).push([si,pi,from,cut]);from=cut;}
        if(pages.at(-1).length)pages.push([]);
      });
      if(from<text.length||from===0)pages.at(-1).push([si,pi,from,text.length]);
    });
    const full=pages.filter(p=>p.length);
    full.forEach((parts,i)=>sheets.push({label:`第 ${i+1} / ${full.length} 页`,page:true,parts}));
  });
  return sheets;
}
function sheetOf(sheets,s,p,offset){return sheets.findIndex(sh=>sh.parts.some(([si,pi,from,to])=>si===s&&pi===p&&offset>=from&&(offset<to||from===to)));}
const maskable=s=>allCands(s).filter(x=>x.c.decision==='pending'&&!x.c.bad).length;
const pendingOf=d=>d.scan>=100?d.candidates.filter(c=>c.decision==='pending').length:0;
const counts=s=>{const all=allCands(s);return {pending:all.filter(x=>x.c.decision==='pending').length,masked:all.filter(x=>x.c.decision==='mask').length,kept:all.filter(x=>x.c.decision==='keep').length,manual:all.filter(x=>x.c.manual).length,scanning:s.documents.filter(d=>d.scan<100).length};};
const canExport=s=>s.documents.length>0&&counts(s).scanning===0&&counts(s).pending===0;
// HaS 官方代号形状 <类型[编号].分类.属性>；同一主体的其他写法用 名称2、名称3。
const TAG_RE=/<[^<>\[\]]+\[\d+\]\.[^<>\[\].]+\.[^<>\[\].]+>/g,TAG_FULL=new RegExp('^'+TAG_RE.source+'$');
// Tag-like text an AI may have rewritten: full-width or escaped brackets, other dots, inserted spaces; or the angle brackets dropped. Same rule as mask/vault.py.
const LOOSE_TAG_RE=/(?:&lt;|\\?[<＜〈])[^<>＜＞〈〉]{1,80}?(?:&gt;|\\?[>＞〉])|([^\s<>＜＞〈〉\[\]［］【】.．。,，、:：;；()（）]{1,20}?)[\[［【]\d+[\]］】][.．。]\1[.．。]名称\d*/g,TAG_FOLD={'【':'[','】':']','〈':'<','〉':'>','。':'.'};
const tagShape=t=>{const s=t.replace(/&lt;/g,'<').replace(/&gt;/g,'>').normalize('NFKC').replace(/[\s\\]/g,'').replace(/[【】〈〉。]/g,c=>TAG_FOLD[c]);return s.startsWith('<')?s:`<${s}>`;};
const findTags=text=>[...text.matchAll(LOOSE_TAG_RE)].filter(m=>TAG_FULL.test(tagShape(m[0])));
// A rewritten tag is never replaced silently; its official shape is only offered as a suggestion.
const tagSuggestions=(tokens,map)=>Object.fromEntries(tokens.filter(t=>!(t in map)&&tagShape(t) in map).map(t=>[t,tagShape(t)]));
const tagFor=(e,i=0)=>`<${e.type}[${e.no}].${e.type}.名称${i>0?i+1:''}>`;
function token(s,c){
  // 服务端保存过的写法优先；还没保存的按当前遮住的写法现算。
  const e=s.entities[c.entity],saved=e.variants?.[c.original];
  if(saved)return saved;
  const variants=[...new Set(maskedCands(s).filter(x=>x.c.entity===c.entity).map(x=>x.c.original))];
  const i=variants.indexOf(c.original);
  return tagFor(e,i);
}
function restoreInfo(s){
  const map=mode.restoreMap(s),all=findTags(s.restoreInput).map(m=>m[0]),tokens=[...new Set(all)],fix=s.restoreFix;
  return {map,tokens,missing:tokens.filter(t=>!(t in map)&&!fix[t]),kept:tokens.filter(t=>fix[t]==='keep'),replaced:all.filter(t=>t in map||(fix[t]&&fix[t]!=='keep')).length,suggest:tagSuggestions(tokens,map)};
}
const overlaps=(d,si,pi,start,end,except)=>d.candidates.some(c=>c.id!==except&&c.decision!=='keep'&&c.s===si&&c.p===pi&&start<c.end&&end>c.start);
function newEntity(s,type){const no=Math.max(0,...Object.values(s.entities).filter(e=>e.type===type).map(e=>e.no))+1;const id='e'+type+no;s.entities[id]={id,type,no};return id;}
function entityFor(s,original,type){
  const same=allCands(s).find(x=>x.c.original===original&&s.entities[x.c.entity].type===type);
  if(same)return same.c.entity;
  // A company name written without its city prefix is the same subject as the masked full name.
  const outer=type==='公司'&&allCands(s).find(x=>x.c.decision==='mask'&&x.c.original.includes(original)&&s.entities[x.c.entity].type===type);
  return outer?outer.c.entity:newEntity(s,type);
}
function guessType(s,value){
  const same=allCands(s).find(x=>x.c.original===value);
  if(same)return s.entities[same.c.entity].type;
  if(/有限公司/.test(value))return '公司';
  if(/@/.test(value))return '邮箱';
  if(/^\d{11}$/.test(value))return '电话';
  if(/支行/.test(value))return '支行';
  if(/[A-Za-z]+-\d/.test(value))return '内部编号';
  return '人名';
}
// 工具改决定：保留时直接删掉补录的候选（它本来就是你加的）；范围过宽的不能遮住。
function applyTool(doc,targets,value){
  let changed=false;
  if(value==='keep'){
    const gone=new Set(targets.filter(x=>x.manual).map(x=>x.id));
    if(gone.size){doc.candidates=doc.candidates.filter(x=>!gone.has(x.id));changed=true;}
  }
  targets.forEach(x=>{if((value==='keep'&&x.manual)||x.decision===value||(value==='mask'&&x.bad))return;x.decision=value;changed=true;});
  if(changed)doc.handed=false;return changed;
}
// 撤销 / 重做：改动核对决定前记下候选和主体；一次拖动手柄只记一步。
const HISTORY=new Set(['decision','merge','sync','bulkEntity','maskAll','entity','type','range','addSel','addHits','pen','erase','boxApply']);
const undoStack=[],redoStack=[];let rangeLogged=false;
const snapshot=s=>({docs:s.documents.map(d=>({id:d.id,candidates:structuredClone(d.candidates)})),entities:structuredClone(s.entities)});
function addCandidate(s,docIndex,si,pi,start,end,type){
  const d=s.documents[docIndex],original=d.sections[si].paragraphs[pi].slice(start,end);
  const c={id:'m'+s.nextManual++,s:si,p:pi,start,end,original,type,entity:entityFor(s,original,type),decision:'mask',reason:'你补录的',bad:false,manual:true};
  d.candidates.push(c);d.handed=false;return c;
}
function searchHits(s){
  const q=s.search.trim();if(!q)return [];
  const hits=[];
  scanned(s).forEach(d=>d.sections.forEach((sec,si)=>sec.paragraphs.forEach((text,pi)=>{
    let i=-1;
    while((i=text.indexOf(q,i+1))>=0)hits.push({docIndex:s.documents.indexOf(d),s:si,p:pi,start:i,end:i+q.length,marked:overlaps(d,si,pi,i,i+q.length)});
  })));
  return hits;
}
function selectFirst(s){const d=s.documents[s.current];s.selected=d&&d.scan>=100?d.candidates.find(c=>c.decision==='pending')?.id||null:null;}
function setBulk(s,match,decision){allCands(s).filter(x=>match(x.c)&&!(decision==='mask'&&x.c.bad)).forEach(x=>{x.c.decision=decision;x.d.handed=false;});}

function reduce(state,a){
  const s=structuredClone(state),d=s.documents[s.current],c=d?.candidates.find(x=>x.id===s.selected);
  const own=mode.reduce?.(s,a,state);if(own)return own;
  switch(a.type){
    case 'addMore':s.page='import';s.menu=false;return s;
    case 'toggleFile':{const f=s.dir.find(x=>x.key===a.key);if(f&&!f.unsupported&&!f.docId)f.checked=!f.checked;return s;}
    case 'selectDoc':s.current=a.index;s.sel=null;s.pick='';selectFirst(s);return s;
    case 'select':s.selected=a.id;s.preview='original';s.sel=null;s.pick='';return s;
    case 'blur':s.selected=null;s.pick='';s.menu=false;s.miss=null;return s;
    case 'jump':s.current=a.index;s.selected=a.id;s.preview='original';s.sel=null;s.pick='';return s;
    case 'preview':s.preview=a.value;s.selected=null;s.sel=null;s.pick='';s.miss=null;return s;
    case 'inspector':s.inspector=!s.inspector;return s;
    case 'board':s.boardOpen=a.value;return s;
    case 'boardTab':s.boardTab=a.value;s.boardOpen=true;return s;
    case 'tool':s.tool=a.value;s.toolsOpen=true;s.pick='';s.sel=null;return s;
    case 'toolsOpen':s.toolsOpen=a.value;if(!a.value)s.tool='pointer';s.pick='';return s;
    case 'penType':s.penType=a.value;s.tool='pen';s.pick='';return s;
    case 'boxMode':s.boxMode=a.value;s.tool='box';s.pick='';return s;
    case 'pen':{
      // 涂黑笔：划过已识别的候选就遮住它；划在没有候选的文字上新建补录。
      const v=a.value,doc=s.documents[v.docIndex],hit=doc.candidates.filter(x=>x.s===v.s&&x.p===v.p&&x.start<v.end&&x.end>v.start);
      if(hit.length)return applyTool(doc,hit,'mask')?s:state;
      const original=doc.sections[v.s].paragraphs[v.p].slice(v.start,v.end);
      addCandidate(s,v.docIndex,v.s,v.p,v.start,v.end,s.penType==='auto'?guessType(s,original):s.penType);return s;
    }
    case 'erase':{
      const v=a.value,doc=s.documents[v.docIndex];
      const hit=v.ids?doc.candidates.filter(x=>v.ids.includes(x.id)):doc.candidates.filter(x=>x.s===v.s&&x.p===v.p&&x.start<v.end&&x.end>v.start);
      if(!applyTool(doc,hit,'keep'))return state;
      if(!d?.candidates.some(x=>x.id===s.selected))s.selected=null;return s;
    }
    case 'boxApply':{const hit=d.candidates.filter(x=>a.ids.includes(x.id));if(!applyTool(d,hit,a.value))return state;if(!d.candidates.some(x=>x.id===s.selected))s.selected=null;return s;}
    case 'restore':
      a.snap.docs.forEach(x=>{const doc=s.documents.find(y=>y.id===x.id);if(doc){doc.candidates=structuredClone(x.candidates);doc.handed=false;}});
      s.entities=structuredClone(a.snap.entities);s.selected=null;s.sel=null;s.pick='';return s;
    case 'menu':s.menu=!s.menu;return s;
    case 'pick':s.pick=s.pick===a.value?'':a.value;return s;
    case 'search':s.search=a.value;s.hit=-1;if(s.search)s.preview='original';return s;
    case 'hit':{
      const hits=searchHits(s);if(!hits.length)return state;
      s.hit=(s.hit+a.delta+hits.length)%hits.length;
      if(hits[s.hit].docIndex!==s.current){s.current=hits[s.hit].docIndex;s.selected=null;}
      return s;
    }
    case 'decision':if(!c||(a.value==='mask'&&c.bad))return state;c.decision=a.value;d.handed=false;return s;
    case 'merge':if(!c)return state;c.entity=c.suggest;c.decision='mask';d.handed=false;return s;
    case 'sync':if(!c||c.decision==='pending')return state;{const type=s.entities[c.entity].type,original=c.original,value=c.decision;setBulk(s,x=>x.original===original&&s.entities[x.entity].type===type,value);}return s;
    case 'bulkEntity':setBulk(s,x=>x.entity===a.id,a.value);return s;
    case 'maskAll':{
      // 只处理待确认。长的先遮；完全落在已遮范围里的已被盖住，标为保留，避免重叠挡住导出。
      // 范围过宽和只部分重叠的留给用户处理。
      allCands(s).filter(x=>x.c.decision==='pending'&&!x.c.bad).sort((x,y)=>(y.c.end-y.c.start)-(x.c.end-x.c.start)).forEach(({d,c})=>{
        const near=d.candidates.filter(o=>o!==c&&o.decision==='mask'&&o.s===c.s&&o.p===c.p&&c.start<o.end&&c.end>o.start);
        if(near.some(o=>o.start<=c.start&&c.end<=o.end))c.decision='keep';
        else if(!near.length){if(c.suggest&&c.entity!==c.suggest)c.entity=c.suggest;c.decision='mask';}
        else return;
        d.handed=false;
      });
      return s;
    }
    case 'entity':if(!c)return state;c.entity=a.value==='new'?newEntity(s,s.entities[c.entity].type):a.value;d.handed=false;s.pick='';return s;
    case 'type':if(!c)return state;c.entity=entityFor(s,c.original,a.value);d.handed=false;s.pick='';return s;
    case 'range':{
      if(!c)return state;
      const start=a.edge==='start'?a.offset:c.start,end=a.edge==='end'?a.offset:c.end,text=paraText(d,c);
      if(start<0||end>text.length||start>=end||overlaps(d,c.s,c.p,start,end,c.id)||(start===c.start&&end===c.end))return state;
      c.start=start;c.end=end;c.original=text.slice(start,end);c.bad=c.bad&&c.original.includes('（');d.handed=false;return s;
    }
    case 'sel':s.sel=a.value;s.selType=a.value?guessType(s,a.value.value):'';s.selected=null;s.pick='';return s;
    case 'clearSel':s.sel=null;s.pick='';return s;
    case 'selType':s.selType=a.value;s.pick='';return s;
    case 'addSel':{const m=s.sel,added=addCandidate(s,m.docIndex,m.s,m.p,m.start,m.end,s.selType);s.sel=null;s.pick='';s.selected=added.id;return s;}
    case 'addHits':{const type=guessType(s,s.search.trim());searchHits(s).filter(h=>!h.marked).forEach(h=>addCandidate(s,h.docIndex,h.s,h.p,h.start,h.end,type));return s;}
    case 'next':
      for(let offset=0;offset<s.documents.length;offset++){
        const index=(s.current+offset)%s.documents.length,hit=s.documents[index].scan>=100&&s.documents[index].candidates.find(x=>x.decision==='pending');
        if(hit){s.current=index;s.selected=hit.id;s.preview='original';s.sel=null;s.pick='';return s;}
      }
      s.selected=null;s.pick='';return s;
    case 'sheet':s.sheet=a.value;s.menu=false;s.selected=null;s.sel=null;return s;
    case 'restoreInput':s.restoreInput=a.value;s.restoreFix={};s.miss=null;s.restored=false;s.preview='original';s.page=a.value.trim()?'restore':'done';return s;
    case 'miss':s.miss=s.miss===a.value?null:a.value;return s;
    case 'fix':s.restoreFix[a.token]=a.value;s.miss=null;s.restored=false;return s;
    default:return state;
  }
}

let mode,state=initialState(),hud='',hudTimer,composing=false,drag=null;
function start(m){mode=m;}
const $=id=>document.getElementById(id);
const esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const motionOK=()=>!matchMedia('(prefers-reduced-motion: reduce)').matches;
const ICONS={
  eye:'<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff:'<path d="M4 4l16 16"/><path d="M9.9 5.7a9.4 9.4 0 0 1 2.1-.2c6 0 9.5 6.5 9.5 6.5a16.7 16.7 0 0 1-3.3 4"/><path d="M6.3 7.6A16.6 16.6 0 0 0 2.5 12S6 18.5 12 18.5a9.5 9.5 0 0 0 4.4-1.1"/><path d="M10 10.2a3 3 0 0 0 4 4.2"/>',
  search:'<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  more:'<circle cx="5.5" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none"/><circle cx="18.5" cy="12" r="1.2" fill="currentColor" stroke="none"/>',
  up:'<path d="M12 19V5M6 11l6-6 6 6"/>',
  right:'<path d="M5 12h14M13 6l6 6-6 6"/>',
  down:'<path d="M12 5v14M6 13l6 6 6-6"/>',
  back:'<path d="m15 5-7 7 7 7"/>',
  next:'<path d="m9 5 7 7-7 7"/>',
  chev:'<path d="m7 10 5 5 5-5"/>',
  check:'<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x:'<path d="M6 6l12 12M18 6 6 18"/>',
  panel:'<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="M15 5v14"/>',
  spark:'<path d="M12 3.5c.9 4.6 3 6.7 7.5 7.5v1c-4.5.8-6.6 2.9-7.5 7.5-.9-4.6-3-6.7-7.5-7.5v-1C9 10.2 11.1 8.1 12 3.5Z"/>',
  doc:'<path d="M7 3.5h7l4.5 4.5V19a1.5 1.5 0 0 1-1.5 1.5H7A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5Z"/><path d="M14 3.5V8h4.5"/>',
  lock:'<rect x="5.5" y="10.5" width="13" height="9.5" rx="2"/><path d="M8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3"/>',
  stack:'<rect x="8" y="8" width="11.5" height="11.5" rx="2"/><path d="M16 8V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8.5A1.5 1.5 0 0 0 6 16h2"/>',
  info:'<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8v.01"/>',
  share:'<path d="M12 15V4M8 7.5l4-4 4 4"/><path d="M6 11v7.5A1.5 1.5 0 0 0 7.5 20h9a1.5 1.5 0 0 0 1.5-1.5V11"/>',
  pause:'<path d="M9 6v12M15 6v12"/>',
  pointer:'<path d="M6.5 4.5 18 11l-5 1.5 3 5.5-2.2 1.2-3-5.5L7.2 17Z"/>',
  marker:'<path d="M15 4l5 5-9 9H6v-5Z"/><path d="m12.5 6.5 5 5"/><path d="M4 21h9"/>',
  eraser:'<path d="M5.5 14.5 14 6a2 2 0 0 1 2.8 0l1.7 1.7a2 2 0 0 1 0 2.8L10 19H8.5Z"/><path d="m9.5 10.5 4.5 4.5"/><path d="M13 19h6"/>',
  lasso:'<rect x="4.5" y="4.5" width="15" height="15" rx="2" stroke-dasharray="3.2 2.6"/>',
  undo:'<path d="M9 13 4.5 8.5 9 4"/><path d="M4.5 8.5H15a5 5 0 0 1 0 10h-4"/>',
  redo:'<path d="m15 13 4.5-4.5L15 4"/><path d="M19.5 8.5H9a5 5 0 0 0 0 10h4"/>',
  note:'<path d="M4 20l1-4.5L16 4.5a1.8 1.8 0 0 1 2.5 0l1 1a1.8 1.8 0 0 1 0 2.5L8.500 19Z"/>'
};
const icon=(name,size=18)=>`<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

// 把新 HTML 合入现有 DOM：节点保留，CSS 过渡、焦点、滚动和划选都不被重绘打断。
function syncNode(a,b){
  if(a.nodeType===3){if(a.nodeValue!==b.nodeValue)a.nodeValue=b.nodeValue;return;}
  const float=a.hasAttribute('data-float');
  for(const {name} of [...a.attributes])if(!b.hasAttribute(name)&&!(float&&(name==='style'||name==='data-side')))a.removeAttribute(name);
  for(const {name,value} of [...b.attributes])if(a.getAttribute(name)!==value)a.setAttribute(name,value);
  if(a.tagName==='INPUT'||a.tagName==='TEXTAREA'){if(!composing&&a.value!==b.value)a.value=b.value;if(a.tagName==='TEXTAREA')return;}
  syncKids(a,b);
}
const keyOf=n=>n.dataset?.k||'';
const alike=(x,y)=>x&&x.nodeType===y.nodeType&&x.nodeName===y.nodeName&&keyOf(x)===keyOf(y);
function syncKids(a,b){
  const keyed=new Map();a.childNodes.forEach(n=>{if(keyOf(n))keyed.set(keyOf(n),n);});
  let cur=a.firstChild;
  [...b.childNodes].forEach(nb=>{
    const m=keyOf(nb)?keyed.get(keyOf(nb)):(alike(cur,nb)?cur:null);
    if(!m||m.nodeName!==nb.nodeName){a.insertBefore(nb,cur);return;}
    if(m===cur)cur=cur.nextSibling;else a.insertBefore(m,cur);
    syncNode(m,nb);
  });
  while(cur){const n=cur.nextSibling;cur.remove();cur=n;}
}
function patch(root,html){const t=document.createElement('template');t.innerHTML=html;syncKids(root,t.content);}

// 缩略纸
const COLS=26,MAX_LINES=17;
function miniLines(d){
  const lines=[{w:44,center:true}];
  d.sections.forEach((sec,si)=>{
    lines.push({w:Math.min(50,sec.title.length*6)});
    sec.paragraphs.forEach((text,pi)=>{
      const n=Math.max(1,Math.ceil(text.length/COLS));
      for(let i=0;i<n;i++)lines.push({w:i===n-1?Math.max(14,((text.length-i*COLS)/COLS)*100):100,s:si,p:pi,from:i*COLS});
    });
  });
  return lines.slice(0,MAX_LINES);
}
const reached=(d,si,pi)=>{
  if(d.scan>=100)return true;
  const secs=d.sections,total=secs.reduce((n,x)=>n+x.paragraphs.length,0);
  const before=secs.slice(0,si).reduce((n,x)=>n+x.paragraphs.length,0)+pi;
  return (before+1)/total*100<=d.scan;
};
function mini(d,scale=1){
  const lines=miniLines(d).map(l=>{
    const segs=d.candidates.filter(c=>c.s===l.s&&c.p===l.p&&c.decision!=='keep'&&reached(d,c.s,c.p)&&c.end>l.from&&c.start<l.from+COLS).map(c=>{
      const a=Math.max(c.start,l.from),b=Math.min(c.end,l.from+COLS);
      return `<b class="${c.decision==='pending'?'q':''}" style="left:${(a-l.from)/COLS*100}%;width:${(b-a)/COLS*100}%"></b>`;
    }).join('');
    return `<i ${l.center?'class="c"':''} style="width:${l.w}%">${segs}</i>`;
  }).join('');
  return `<span class="mini" style="--u:${scale}">${lines}</span>`;
}

// 主按钮：有待办时带你去下一处，清零后才变成发送。方向表示数据去向：→ 留在本机，↑ 发给 AI，↓ 存到本地。
function go({cls,a='',label,inner,pct=0,disabled=false,key}){
  return `<button class="go ${cls}" ${a?`data-a="${a}"`:''} aria-label="${esc(label)}" title="${esc(label)}" ${disabled?'disabled':''}><svg class="ring" viewBox="0 0 44 44" aria-hidden="true"><circle class="tr" cx="22" cy="22" r="19"/><circle class="va" cx="22" cy="22" r="19" style="stroke-dashoffset:${(119.38*(1-pct)).toFixed(2)}"/></svg><span class="go-in" data-k="${esc(key)}">${inner}</span></button>`;
}
// 灵动岛里的主操作：始终写明下一步做什么。
function act({cls,a='',title,inner,disabled=false,key}){
  return `<button class="act ${cls}" ${a?`data-a="${a}"`:''} title="${esc(title)}" aria-label="${esc(title)}" ${disabled?'disabled':''}><span class="act-in" data-k="${esc(key)}">${inner}</span></button>`;
}
const dock=(page,inner)=>`<footer class="dock" data-k="dock" data-page="${page}">${inner}</footer>`;
function bar({title='',island=''}={}){
  return `<header class="bar" data-k="bar">${mode.barLead()}${title?`<div class="bar-title">${esc(title)}</div>`:''}${island}<div class="bar-tools">${mode.barEnd?.(island)||''}</div></header>`;
}
function viewSeg(first,second,tips=[first,second]){
  const ai=state.preview==='masked';
  return `<div class="seg" role="group" aria-label="查看方式"><button class="${ai?'':'on'}" data-a="preview" data-v="original" title="${tips[0]}" aria-pressed="${!ai}">${first}</button><button class="${ai?'on':''}" data-a="preview" data-v="masked" title="${tips[1]}" aria-pressed="${ai}">${second}</button></div>`;
}
function moreMenu(items){
  return `<div class="more"><button class="icon" data-a="menu" aria-label="更多" title="更多" aria-expanded="${state.menu}">${icon('more',20)}</button>${state.menu?`<div class="menu" role="menu">${items.concat(mode.menuExtras?.()||[]).map(([a,ic,label,off])=>`<button role="menuitem" data-a="${a}" ${off?'disabled':''}>${icon(ic,16)}${label}</button>`).join('')}</div>`:''}</div>`;
}
// 入场页的一行：格式标记或真实缩略图、文件名、例外说明，右侧是勾选或去向。
function fileRow({f,i,a='',on=false,off=false,locked=false,sub='',hot=false,doc=null,end=''}){
  const lead=doc?mini(doc,.73):`<span class="fmt">${esc(f.format||f.name.split('.').pop().toUpperCase())}</span>`;
  return `<button class="file ${on?'on':''} ${off?'off':''} ${locked?'locked':''}" style="--i:${i}" data-k="${f.key}" ${a?`data-a="${a}" data-v="${f.key}"`:''} title="${esc(f.name)}" ${a?'':'disabled'} ${a==='toggle'?`aria-pressed="${on}"`:''}>${lead}<span class="f-main"><span class="f-name">${esc(f.name)}</span>${sub?`<span class="f-sub ${hot?'hot':''}">${esc(sub)}</span>`:''}</span>${end||(off?'':`<span class="chk">${icon('check',14)}</span>`)}</button>`;
}
const addRow=(i,sub)=>`<button class="file add" style="--i:${i}" data-k="add" data-a="pick-file" title="添加文件，或把文件拖到这里"><span class="fmt">${icon('plus',18)}</span><span class="f-main"><span class="f-name">添加文件，或拖到这里</span><span class="f-sub">${sub}</span></span></button>`;
const countNum=n=>`<b class="num" data-k="c${n}">${n}</b>`;
const fileOrder=dir=>dir.slice().sort((a,b)=>!!a.unsupported-!!b.unsupported);
function markHtml(c,here,cur){
  const ai=state.preview==='masked',on=state.selected===c.id;
  const label=ai&&c.decision==='mask'?token(state,c):c.original;
  const hit=here.some(h=>h.start<c.end&&h.end>c.start),isCur=cur&&cur.start<c.end&&cur.end>c.start;
  const cls=['m',{mask:'x',keep:'k',pending:'q'}[c.decision],on&&'on',c.bad&&'bad',hit&&'hitting',isCur&&'cur'].filter(Boolean).join(' ');
  const grips=on&&!ai?`<i class="h s" data-edge="start" tabindex="0" role="slider" aria-label="调整起点" aria-valuenow="${c.start}"></i><i class="h e" data-edge="end" tabindex="0" role="slider" aria-label="调整终点" aria-valuenow="${c.end}"></i>`:'';
  const tip=c.decision==='pending'?'待确认：点开选择遮住或保留':c.decision==='mask'?(ai?'AI 收到的代号':'已遮住：AI 看不到原文'):'已保留：AI 能看到原文';
  return `<span class="${cls}" data-k="${c.id}" data-a="mark" data-v="${c.id}" role="button" tabindex="0" title="${tip}" aria-label="${esc(state.entities[c.entity].type+' '+c.original)}">${esc(label)}${grips}</span>`;
}
function calloutFor(d){
  const ai=state.preview==='masked';
  if(state.sel&&!ai){
    const v=state.sel;
    if(v.conflict)return `<div class="callout" data-k="co" data-float><div class="why hot" style="padding-bottom:3px">与已有标注重叠</div></div>`;
    return `<div class="callout" data-k="co" data-float role="dialog" aria-label="补录"><div class="co-row"><span class="sel-val">${esc(v.value)}</span><button class="chip" data-a="pick" data-v="sel-type">${esc(state.selType)}${icon('chev',14)}</button><span class="grow"></span><div class="pick"><button class="on" data-a="add-sel">${icon('eyeOff',18)}遮住</button></div></div>${state.pick==='sel-type'?`<div class="choices">${state.categories.map(x=>`<button class="${x===state.selType?'on':''}" data-a="sel-type" data-v="${x}">${x}</button>`).join('')}</div>`:''}</div>`;
  }
  const c=d.candidates.find(x=>x.id===state.selected);
  if(!c||ai||d.scan<100)return '';
  const type=state.entities[c.entity].type,e=state.entities[c.entity];
  const same=allCands(state).filter(x=>x.c.original===c.original&&state.entities[x.c.entity].type===type);
  const peers=Object.values(state.entities).filter(x=>x.type===type&&(x.id===c.entity||allCands(state).some(y=>y.c.entity===x.id&&y.c.decision==='mask')));
  const peerName=x=>maskedCands(state).find(y=>y.c.entity===x.id)?.c.original||c.original;
  const suggest=c.suggest&&c.decision==='pending'&&c.entity!==c.suggest?state.entities[c.suggest]:null;
  const code=c.decision==='mask'?token(state,c):tagFor(e);
  return `<div class="callout" data-k="co" data-float role="dialog" aria-label="${esc(c.original)}">${c.reason?`<p class="why ${c.decision==='pending'?'hot':''}">${esc(c.reason)}</p>`:''}<div class="co-row"><button class="chip" data-a="pick" data-v="type" title="类别" aria-expanded="${state.pick==='type'}">${esc(type)}${icon('chev',14)}</button>${suggest?`<button class="chip plain merge" data-a="merge" title="并入这个主体并遮住">= ${esc(allCands(state).find(x=>x.c.entity===suggest.id)?.c.original||peerName(suggest))}</button>`:c.decision==='keep'?'':`<button class="chip" data-a="pick" data-v="entity" title="代号" aria-expanded="${state.pick==='entity'}">${esc(code)}${icon('chev',14)}</button>`}<span class="grow"></span>${same.length>1?`<button class="chip plain ${c.decision!=='pending'&&samePending(c)?'hot':''}" data-a="sync" title="同步到全部 ${same.length} 处（S）" aria-keyshortcuts="S" aria-label="同步到全部 ${same.length} 处" ${c.decision==='pending'?'disabled':''}>${icon('stack',15)}&nbsp;${c.decision!=='pending'&&samePending(c)?'同步 ':''}${same.length}</button>`:''}<div class="pick" role="group" aria-label="决定"><button class="keep ${c.decision==='keep'?'on':''}" data-a="keep" title="保留（K）" aria-keyshortcuts="K">${icon('eye',18)}保留</button><button class="${c.decision==='mask'?'on':''} ${c.bad?'off':''}" data-a="mask" title="${c.bad?'先调整范围':'遮住（M）'}" aria-keyshortcuts="M">${icon('eyeOff',18)}遮住</button></div></div>${state.pick==='type'?`<div class="choices">${state.categories.map(x=>`<button class="${x===type?'on':''}" data-a="type" data-v="${x}">${x}</button>`).join('')}</div>`:''}${state.pick==='entity'?`<div class="choices list">${peers.map(x=>`<button class="${x.id===c.entity?'on':''}" data-a="entity" data-v="${x.id}">${esc(peerName(x))}</button>`).join('')}<button data-a="entity" data-v="new">${icon('plus',14)} 新主体</button></div>`:''}</div>`;
}
function rail(hits){
  const thumb=(d,i)=>{
    const n=pendingOf(d),active=state.documents.find(x=>x.scan<100)===d,missed=hits.some(h=>h.docIndex===i&&!h.marked);
    const status=d.scan<100?(active?`<svg class="dial" viewBox="0 0 24 24" aria-hidden="true"><circle class="tr" cx="12" cy="12" r="9"/><circle class="va" cx="12" cy="12" r="9" style="stroke:var(--zhu);stroke-dashoffset:${(56.5*(1-d.scan/100)).toFixed(1)}"/></svg>`:''):n?`<span class="badge">${n}</span>`:missed?'<span class="badge dot"></span>':d.handed?`<span class="badge ok">${icon('check',11)}</span>`:'';
    const label=d.scan<100?'识别中':n?`${n} 处待确认`:d.handed?'已交接':'无待确认';
    return `<button class="thumb ${i===state.current?'on':''} ${d.scan<100&&!active?'wait':''}" data-k="t${d.id}" data-a="doc" data-v="${i}" title="${esc(d.name)}：${label}" aria-label="${esc(d.name)}，${label}">${mini(d)}${status}</button>`;
  };
  return `<nav class="rail" data-k="rail" aria-label="材料">${state.documents.map(thumb).join('')}<button class="icon" data-a="add-more" title="添加材料" aria-label="添加材料">${icon('plus',20)}</button></nav>`;
}
function conflicts(){
  const result=[];
  state.documents.forEach(d=>{
    const active=d.candidates.filter(c=>c.decision==='mask').sort((a,b)=>a.s-b.s||a.p-b.p||a.start-b.start);
    active.forEach((c,i)=>{const p=active[i-1];if(p&&p.s===c.s&&p.p===c.p&&p.end>c.start)result.push({d,c,with:p});});
  });return result;
}
// 待处理看板：把主按钮背后的清单摊开，一行一处，点行跳到原文。
function todoHint(d,c){
  const outer=d.candidates.find(o=>o!==c&&o.decision!=='keep'&&o.s===c.s&&o.p===c.p&&o.start<=c.start&&o.end>=c.end&&o.end-o.start>c.end-c.start);
  if(outer)return {text:`在「${outer.original}」里`};
  const full=c.suggest&&c.entity!==c.suggest&&allCands(state).find(x=>x.c.entity===c.suggest);
  if(full)return {text:'= '+full.c.original};
  if(c.bad)return {text:'范围过宽，先拖手柄'};
  return null;
}
function todoRow(x){
  const ai=state.preview==='masked';
  if(!x.c)return `<button class="brow" data-k="bd${x.d.id}" data-a="todo" data-i="${x.i}"><span class="brow-state ${x.hot?'hot':''}">${x.label}</span></button>`;
  const c=x.c,hint=x.with?{text:`与「${x.with.original}」重叠`,hot:true}:todoHint(x.d,c);
  return `<button class="brow ${x.i===state.current&&state.selected===c.id?'on':''}" data-k="b${c.id}${x.with?'x':''}" data-a="todo" data-i="${x.i}" data-v="${c.id}"><span class="brow-main"><span class="m ${x.with?'x':'q'}">${esc(ai&&x.with?token(state,c):c.original)}</span><span class="brow-type">${esc(state.entities[c.entity].type)}</span></span>${hint?`<span class="brow-hint ${hint.hot?'hot':''}">${esc(hint.text)}</span>`:''}</button>`;
}
// 左侧悬浮看板：待处理只列需要你的；全部列出每一处候选，含被长候选盖住的重叠项，点行跳到原文。
function allRow(x,pages){
  const c=x.c,ai=state.preview==='masked',page=pages.length>1?pages[sheetOf(pages,c.s,c.p,c.start)]?.label.replace(/ \/ \d+ 页$/,' 页'):'';
  return `<button class="brow ${x.i===state.current&&state.selected===c.id?'on':''}" data-k="a${c.id}" data-a="todo" data-i="${x.i}" data-v="${c.id}"><span class="brow-main"><span class="m ${{mask:'x',keep:'k',pending:'q'}[c.decision]}">${esc(ai&&c.decision==='mask'?token(state,c):c.original)}</span><span class="brow-type">${esc(state.entities[c.entity].type)}</span></span>${page?`<span class="brow-hint">${esc(page)}</span>`:''}</button>`;
}
function board(){
  const clash=conflicts(),groups=state.documents.map((d,i)=>{
    const rows=d.status==='failed'?[{d,i,label:'识别失败',hot:true}]:d.scan<100?[{d,i,label:d.status==='queued'?'待识别':'识别中'}]
      :d.candidates.filter(c=>c.decision==='pending').sort((a,b)=>a.s-b.s||a.p-b.p||a.start-b.start).map(c=>({d,i,c}));
    return {d,i,rows:rows.concat(clash.filter(x=>x.d===d).map(x=>({d,i,c:x.c,with:x.with})))};
  }).filter(g=>g.rows.length);
  const total=groups.reduce((t,g)=>t+g.rows.length,0),every=allCands(state).length;
  if(!total&&!every)return '';
  const open=state.boardOpen??!matchMedia('(max-width:1060px)').matches,n=counts(state),tab=total&&state.boardTab!=='all'?'todo':'all';
  // 文件行兼作缩略图栏：看板展开时不再单独显示左侧缩略图。
  const docName=(d,i)=>{
    const p=pendingOf(d),st=d.status==='failed'?'<span class="bd-n">识别失败</span>':d.scan<100?`<span class="bd-n wait">${d.status==='queued'?'待识别':'识别中'}</span>`:p?`<span class="bd-n">${p}</span>`:`<span class="bd-n ok" title="无待确认">${icon('check',13)}</span>`;
    return `<button class="board-doc ${i===state.current?'on':''}" data-k="bh${d.id}" data-a="todo" data-i="${i}" title="${esc(d.name)}">${mini(d,.5)}<span class="bd-name">${esc(state.preview==='masked'?'材料 '+(d.no??i+1):d.name)}</span>${st}</button>`;
  };
  const byDoc=new Map(groups.map(g=>[g.d,g.rows]));
  const list=tab==='todo'?state.documents.map((d,i)=>docName(d,i)+(byDoc.get(d)||[]).filter(x=>x.c).map(todoRow).join('')).join('')+(maskable(state)?`<button class="board-all" data-a="mask-all" title="遮住全部待确认；已决定的不变，范围过宽的仍需手动处理">${icon('lock',14)}其余 ${maskable(state)} 处全部遮住</button>`:'')
    :state.documents.map((d,i)=>{
      const rows=scanned(state).includes(d)?d.candidates.slice().sort((a,b)=>a.s-b.s||a.p-b.p||a.start-b.start):[];
      const pages=sheetsOf(d);
      return docName(d,i)+rows.map(c=>allRow({d,i,c},pages)).join('');
    }).join('');
  const head=`<div class="board-head" role="tablist">${total?`<button class="bt todo ${tab==='todo'?'on':''}" role="tab" aria-selected="${tab==='todo'}" data-a="board-tab" data-v="todo">待处理<b>${total}</b></button>`:''}<button class="bt ${tab==='all'?'on':''}" role="tab" aria-selected="${tab==='all'}" data-a="board-tab" data-v="all" title="每一处候选，含重叠项">全部候选<b>${every}</b></button><button class="icon" data-a="board" aria-expanded="${open}" aria-label="${open?'收起':'展开'}看板" title="${open?'收起':'展开'}">${icon('chev',15)}</button></div>`;
  return `<aside class="board ${open?'open':''}" data-k="board" aria-label="候选看板">${head}${open?`<div class="legend"><span><span class="m q">待确认</span>${n.pending}</span><span><span class="m x">遮住</span>${n.masked}</span><span><span class="m k">保留</span>${n.kept}</span></div><div class="board-list">${list}</div>`:''}</aside>`;
}
function inspector(){
  const groups={},byType={};
  allCands(state).forEach(x=>{(groups[x.c.entity]??=[]).push(x);});
  Object.entries(groups).forEach(([id,list])=>{(byType[state.entities[id].type]??=[]).push([id,list]);});
  const all=allCands(state),insp={size:Object.keys(groups).length,total:all.length,pending:all.filter(x=>x.c.decision==='pending').length};
  const bulk=(id,v)=>`<button class="icon" data-a="bulk-entity" data-i="${id}" data-v="${v}" title="全部${v==='keep'?'保留':'遮住'}" aria-label="全部${v==='keep'?'保留':'遮住'}">${icon(v==='keep'?'eye':'eyeOff',17)}</button>`;
  const row=([id,list])=>{
    const values=[...new Set(list.map(x=>x.c.original))],tokens=[...new Set(list.filter(x=>x.c.decision==='mask').map(x=>token(state,x.c)))];
    const pending=list.some(x=>x.c.decision==='pending'),first=list.find(x=>x.c.decision==='pending')||list[0],hidden=tokens.length>0&&!pending;
    return `<div class="ent" data-k="e${id}"><button class="ent-main" data-a="jump" data-i="${state.documents.indexOf(first.d)}" data-v="${first.c.id}"><span class="tok ${pending?'q':hidden?'':'k'}">${esc(tokens[0]||(pending?'待定':'保留'))}</span><span class="ent-name">${esc(values.join('、'))}</span><span class="ent-n">${list.length}</span></button>${pending||hidden?bulk(id,'keep'):''}${pending||!hidden?bulk(id,'mask'):''}</div>`;
  };
  return `<aside class="inspector ${state.inspector?'open':''}" data-k="insp" aria-label="实体与代号" ${state.inspector?'':'inert'}><div class="insp-head">${icon('lock',13)}仅在本机<span class="grow"></span>${insp.size} 个主体 · ${insp.pending?insp.pending+' 处待定':insp.total+' 处'}</div>${state.categories.filter(t=>byType[t]).map(t=>`<h3>${t}</h3>${byType[t].map(row).join('')}`).join('')}</aside>`;
}
function toolsBar(){
  if(state.preview==='masked')return '';
  if(!state.toolsOpen)return '';
  const t=state.tool,tool=(id,ic,label,key)=>`<button class="tl ${t===id?'on':''}" data-a="tool" data-v="${id}" title="${label}（${key}）" aria-label="${label}" aria-pressed="${t===id}">${icon(ic,19)}</button>`;
  const choose=(pick,action,list,cur,cls='')=>state.pick===pick?`<div class="menu ${cls}" role="menu">${list.map(([v,l])=>`<button role="menuitemradio" aria-checked="${v===cur}" class="${v===cur?'on':''}" data-a="${action}" data-v="${v}">${esc(l)}</button>`).join('')}</div>`:'';
  const dd=(pick,label)=>`<button class="tl-dd" data-a="pick" data-v="${pick}" title="${label}" aria-label="${label}" aria-expanded="${state.pick===pick}">${icon('chev',14)}</button>`;
  const penLabel=state.penType==='auto'?'自动':state.penType,boxLabel=state.boxMode==='mask'?'遮住':'保留';
  return `<div class="tools" data-k="tools" role="toolbar" aria-label="编辑工具">${tool('pointer','pointer','指针','V')}`
    +`<div class="tl-group">${tool('pen','marker','涂黑笔：划过即遮住','B')}${dd('pen-type','涂黑笔类别：'+penLabel)}${t==='pen'?`<span class="tl-tag">${esc(penLabel)}</span>`:''}${choose('pen-type','pen-type',[['auto','自动'],...state.categories.map(x=>[x,x])],state.penType,'cats')}</div>`
    +tool('eraser','eraser','橡皮：点或划过改为保留','E')
    +`<div class="tl-group">${tool('box','lasso','框选：框内全部'+boxLabel,'R')}${dd('box-mode','框选方式：'+boxLabel)}${t==='box'?`<span class="tl-tag">${boxLabel}</span>`:''}${choose('box-mode','box-mode',[['mask','框内全部遮住'],['keep','框内全部保留']],state.boxMode)}</div>`
    +`<span class="sep"></span><button class="tl" data-a="undo" title="撤销（Ctrl+Z）" aria-label="撤销" ${undoStack.length?'':'disabled'}>${icon('undo',19)}</button><button class="tl" data-a="redo" title="重做（Ctrl+Shift+Z）" aria-label="重做" ${redoStack.length?'':'disabled'}>${icon('redo',19)}</button>`
    +`<span class="sep"></span><button class="tl" data-a="tools-close" title="收起标注工具，回到指针（Esc）" aria-label="收起标注工具">${icon('x',18)}</button></div>`;
}
function reviewPage(){
  const d=state.documents[state.current],hits=searchHits(state),q=state.search.trim(),missed=hits.filter(h=>!h.marked).length,ai=state.preview==='masked';
  const find=q?`<span class="isl-n">${hits.length?(state.hit>=0?`${state.hit+1}/${hits.length}`:`${hits.length} 处`):'无结果'}</span>${hits.length?`<button class="icon" data-a="hit" data-v="-1" aria-label="上一处" title="上一处">${icon('back',14)}</button><button class="icon" data-a="hit" data-v="1" aria-label="下一处" title="下一处（回车）">${icon('next',14)}</button>`:''}${missed?`<button class="isl-add" data-a="add-hits" title="把 ${missed} 处未遮住的结果全部遮住">遮住 ${missed}</button>`:''}`:'';
  const island=`<div class="island" data-k="island" role="toolbar" aria-label="核对工具">${viewSeg('原文','脱敏后',['原文：核对真名和标注','脱敏后：AI 收到的内容'])}<span class="sep"></span><div class="isl-find ${q?'has':''}"><label>${icon('search',15)}<input id="search" type="search" aria-label="查找全部材料" placeholder="查找" value="${esc(state.search)}"></label>${find}</div><span class="sep"></span>${ai?'':`<button class="isl-btn ${state.toolsOpen?'on':''}" data-a="${state.toolsOpen?'tools-close':'tools-open'}" title="标注工具：涂黑笔、橡皮、框选" aria-pressed="${state.toolsOpen}">${icon('marker',16)}<span class="lbl">标注</span></button>`}<button class="isl-btn ${state.inspector?'on':''}" data-a="inspector" title="实体与代号：按主体批量处理" aria-pressed="${state.inspector}">${icon('panel',16)}<span class="lbl">实体</span></button>${mode.islandExtras?.()||''}${moreMenu([['mask-all','lock','全部遮住待确认',!maskable(state)],['export','share','导出原格式',!canExport(state)],['add-more','plus','添加材料'],['pause','pause','稍后继续']])}${mode.reviewGo()}</div>`;
  return bar({title:ai?`材料 ${d.no??state.current+1}`:d.name,island})
    +board()+`<main class="canvas" id="canvas" data-k="p-review">${mode.paper(d,hits)}</main>`+rail(hits)+inspector()+toolsBar();
}
function restoreInline(line,r){
  const code=state.preview==='masked';let out='',last=0;
  for(const m of findTags(line)){
    const t=m[0],fix=state.restoreFix[t],real=r.map[t]??(fix&&fix!=='keep'?r.map[fix]:null);
    out+=esc(line.slice(last,m.index));last=m.index+t.length;
    if(real)out+=`<span class="r">${esc(code?t:real)}</span>`;
    else if(fix==='keep')out+=`<span class="m k">${esc(t)}</span>`;
    else out+=`<span class="m q ${state.miss===t?'on':''}" data-a="miss" data-v="${esc(t)}" role="button" tabindex="0" aria-label="没有映射：${esc(t)}">${esc(t)}</span>`;
  }
  return out+esc(line.slice(last));
}
function restorePaper(r){
  let html='',table=[];
  const flush=()=>{if(!table.length)return;html+=`<table><thead><tr>${table[0].map(x=>`<th>${restoreInline(x,r)}</th>`).join('')}</tr></thead><tbody>${table.slice(1).map(row=>`<tr>${row.map(x=>`<td>${restoreInline(x,r)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;table=[];};
  state.restoreInput.split('\n').forEach(line=>{
    if(line.startsWith('|')){if(!/^\|\s*-/.test(line))table.push(line.split('|').slice(1,-1).map(x=>x.trim()));return;}
    flush();
    if(line.startsWith('# '))html+=`<h1>${restoreInline(line.slice(2),r)}</h1>`;
    else if(line.trim())html+=`<p>${restoreInline(line,r)}</p>`;
  });
  flush();
  const sug=r.suggest[state.miss],choices=Object.entries(r.map).sort(([a],[b])=>(b===sug)-(a===sug));
  const co=state.miss?`<div class="callout" data-k="co" data-float role="dialog" aria-label="${esc(state.miss)}"><p class="why hot">${sug?'代号形状被改动，第一项是格式还原后的代号':'映射中没有这个代号'}</p><div class="choices list" style="margin:0;padding:0;border:0">${choices.map(([t,v])=>`<button data-a="fix" data-v="${esc(t)}">${esc(t)}<span style="color:var(--ink3)">${esc(v)}</span></button>`).join('')}<button data-a="fix" data-v="keep">${icon('eye',14)} 保留代号</button></div></div>`:'';
  return `<article class="page" data-k="restored">${html}${co}</article>`;
}
function restoreIsland(status,primary){
  return `<div class="island" data-k="island" role="toolbar" aria-label="还原工具">${viewSeg('真名','代号',['还原后的真名版','AI 结果原样'])}${status?`<span class="isl-status" role="status">${esc(status)}</span>`:''}<span class="sep"></span><button class="isl-btn" data-a="clear-restore" title="换一份 AI 结果">${icon('x',15)}<span class="lbl">换一份</span></button>${primary}</div>`;
}
function benchView(){
  const pages={review:reviewPage,...mode.pages};
  return pages[state.page]()+`<div class="hud ${hud?'on':''}" data-k="hud" role="status">${esc(hud)}</div>`;
}
function noticeHtml(){
  const notices=state.documents.filter(d=>d.notice);
  return notices.length?`<div class="warn">${icon('info',16)}<span>${notices.map(d=>`${esc(d.name)}：${esc(d.notice)}`).join('<br>')}，按部分处理记录</span></div>`:'';
}

function placeCallout(){
  const co=document.querySelector('.callout');if(!co)return;
  const page=co.parentElement,P=page.getBoundingClientRect(),box=$('canvas').getBoundingClientRect();
  const mark=page.querySelector('.m.on'),v=state.sel;
  const A=mark?(r=>({left:r.left-P.left,top:r.top-P.top,width:r.width,height:r.height}))(mark.getBoundingClientRect()):v?.rect;
  if(!A)return;
  // 宽屏把卡片停在文档右侧空白处，不盖正文；放不下时回到词的上方或下方。
  const gap=24,room=innerWidth-P.right-gap-16-(state.inspector?352:0);
  co.style.maxWidth='';
  if(room>=280&&innerWidth>760){
    co.style.maxWidth=Math.min(372,room)+'px';
    // 连线走在该词下方的行距里，不压字，从词一直连到卡片。
    const y=A.top+A.height+4,top=Math.max(0,y-24);
    co.style.left=P.width+gap+'px';co.style.top=top+'px';co.dataset.side='side';
    co.style.setProperty('--ly',(y-top)+'px');co.style.setProperty('--lead',(P.width+gap-A.left-3)+'px');
    return;
  }
  const w=co.offsetWidth,h=co.offsetHeight,cx=A.left+A.width/2,left=Math.max(8,Math.min(P.width-w-8,cx-w/2));
  const above=A.top+P.top-box.top-56>h+18;
  co.style.left=left+'px';co.style.top=(above?A.top-h-16:A.top+A.height+16)+'px';
  co.dataset.side=above?'top':'bottom';co.style.setProperty('--tail',Math.max(20,Math.min(w-20,cx-left))+'px');
}
function render(){
  const b=document.body;b.dataset.view=state.view;b.dataset.sheet=state.sheet?'1':'';
  b.dataset.tool=state.view==='bench'&&state.page==='review'&&state.preview==='original'?state.tool:'';
  patch($('bench'),benchView());patch($('overlay'),mode.sheets[state.sheet]?.()||'');
  $('bench').inert=state.view!=='bench'||!!state.sheet;
  mode.render();
  placeCallout();
}
function reveal(selector='.m.on'){
  requestAnimationFrame(()=>{
    const box=$('canvas'),el=box?.querySelector(selector);if(!el)return;
    const b=box.getBoundingClientRect(),r=el.getBoundingClientRect();
    box.scrollTo({top:box.scrollTop+r.top-b.top-box.clientHeight*.42,behavior:motionOK()?'smooth':'auto'});
  });
}
function notify(text){hud=text;render();clearTimeout(hudTimer);hudTimer=setTimeout(()=>{hud='';render();},2600);}
function dispatch(action){
  if(mode.allow?.(action)===false)return state;
  const before=state;state=reduce(state,action);
  if(state!==before&&HISTORY.has(action.type)&&!(action.type==='range'&&rangeLogged)){
    undoStack.push(snapshot(before));if(undoStack.length>200)undoStack.shift();redoStack.length=0;
    if(action.type==='range'&&drag)rangeLogged=true;
  }
  render();
  mode.after?.(action,before);
  return state;
}
function stepHistory(from,to,label){
  const snap=from.at(-1);if(!snap)return;
  const current=snapshot(state),before=state;dispatch({type:'restore',snap});
  if(state===before)return;
  from.pop();to.push(current);notify(label);
}
function decide(value){
  const d=state.documents[state.current],c=d?.candidates.find(x=>x.id===state.selected);if(!c)return;
  if(value==='mask'&&c.bad)return;
  const wasPending=c.decision==='pending';
  dispatch({type:'decision',value});
  // Identical pending occurrences keep the callout in place so the sync chip can be used.
  if(wasPending&&!samePending(c))advance(c.id);
}
function samePending(c){
  const type=state.entities[c.entity].type;
  return allCands(state).some(x=>x.c.id!==c.id&&x.c.original===c.original&&state.entities[x.c.entity].type===type&&x.c.decision==='pending');
}
function advance(id){setTimeout(()=>{if(state.selected===id){dispatch({type:'next'});reveal();}},motionOK()?340:0);}
const ACTIONS={
  'pick-file':()=>$('picker').click(),
  toggle:t=>dispatch({type:'toggleFile',key:t.dataset.v}),
  doc:t=>dispatch({type:'selectDoc',index:+t.dataset.v}),
  mark:t=>{
    const tool=state.page==='review'&&state.preview==='original'?state.tool:'pointer';
    if(tool==='pen')dispatch({type:'boxApply',ids:[t.dataset.v],value:'mask'});
    else if(tool==='eraser')dispatch({type:'erase',value:{docIndex:state.current,ids:[t.dataset.v]}});
    else if(tool==='pointer')dispatch({type:'select',id:t.dataset.v});
  },
  tool:t=>dispatch({type:'tool',value:t.dataset.v}),
  'pen-type':t=>dispatch({type:'penType',value:t.dataset.v}),
  'box-mode':t=>dispatch({type:'boxMode',value:t.dataset.v}),
  'tools-open':()=>dispatch({type:'toolsOpen',value:true}),
  'tools-close':()=>dispatch({type:'toolsOpen',value:false}),
  undo:()=>stepHistory(undoStack,redoStack,'已撤销'),
  redo:()=>stepHistory(redoStack,undoStack,'已重做'),
  jump:t=>{
    dispatch({type:'jump',index:+t.dataset.i,id:t.dataset.v});
    // 窄屏上实体面板盖住正文，跳过去后收起。
    if(state.inspector&&matchMedia('(max-width:1060px)').matches)dispatch({type:'inspector'});
    reveal();
  },
  preview:t=>dispatch({type:'preview',value:t.dataset.v}),
  inspector:()=>dispatch({type:'inspector'}),
  board:t=>dispatch({type:'board',value:t.getAttribute('aria-expanded')!=='true'}),
  'board-tab':t=>dispatch({type:'boardTab',value:t.dataset.v}),
  todo:t=>{
    dispatch(t.dataset.v?{type:'jump',index:+t.dataset.i,id:t.dataset.v}:{type:'selectDoc',index:+t.dataset.i});
    // 窄屏上看板盖住正文，跳过去后收起。
    if(matchMedia('(max-width:1060px)').matches)dispatch({type:'board',value:false});
    reveal();
  },
  menu:()=>dispatch({type:'menu'}),
  pick:t=>dispatch({type:'pick',value:t.dataset.v}),
  type:t=>dispatch({type:'type',value:t.dataset.v}),
  entity:t=>dispatch({type:'entity',value:t.dataset.v}),
  'sel-type':t=>dispatch({type:'selType',value:t.dataset.v}),
  mask:()=>decide('mask'),
  keep:()=>decide('keep'),
  merge:()=>{const id=state.selected;dispatch({type:'merge'});advance(id);},
  sync:()=>{const id=state.selected;dispatch({type:'sync'});notify('已同步');advance(id);},
  'bulk-entity':t=>dispatch({type:'bulkEntity',id:t.dataset.i,value:t.dataset.v}),
  'mask-all':()=>{
    const before=counts(state);state.menu=false;dispatch({type:'maskAll'});const after=counts(state);
    if(after.masked===before.masked&&after.kept===before.kept)return;
    notify(`已遮住 ${after.masked-before.masked} 处${after.kept>before.kept?`，${after.kept-before.kept} 处已被长候选盖住`:''}${after.pending?`，还剩 ${after.pending} 处需手动处理`:''}`);
  },
  'add-sel':()=>{dispatch({type:'addSel'});getSelection().removeAllRanges();},
  'add-hits':()=>dispatch({type:'addHits'}),
  hit:t=>{dispatch({type:'hit',delta:+t.dataset.v});reveal('.cur');},
  next:()=>{dispatch({type:'next'});reveal();},
  'add-more':()=>dispatch({type:'addMore'}),
  release:()=>{dispatch({type:'sheet',value:'handoff'});$('confirm')?.focus();},
  export:()=>{dispatch({type:'sheet',value:'export'});$('confirm')?.focus();},
  'close-sheet':()=>dispatch({type:'sheet',value:''}),
  miss:t=>dispatch({type:'miss',value:t.dataset.v}),
  'next-miss':()=>{dispatch({type:'miss',value:restoreInfo(state).missing[0]});reveal();},
  fix:t=>dispatch({type:'fix',token:state.miss,value:t.dataset.v}),
};
document.addEventListener('click',e=>{
  const t=e.target.closest('[data-a]');
  if(t){if(!t.disabled)(mode.actions[t.dataset.a]||ACTIONS[t.dataset.a])?.(t);return;}
  if(state.view==='bench'&&!e.target.closest('.callout,.menu,.inspector,.board,.tools,.corner')&&(state.selected||state.menu||state.miss||state.pick))dispatch({type:'blur'});
});
function onInput(e){
  const id=e.target.id,v=e.target.value;
  if(id==='search')dispatch({type:'search',value:v});
  if(id==='paste'&&v.trim())dispatch({type:'restoreInput',value:v});
}
document.addEventListener('input',e=>{if(!e.isComposing)onInput(e);});
document.addEventListener('compositionstart',()=>{composing=true;});
document.addEventListener('compositionend',e=>{composing=false;onInput(e);});
document.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&e.target.id==='search'){dispatch({type:'hit',delta:1});reveal('.cur');return;}
  if((e.key==='Enter'||e.key===' ')&&e.target.matches('[role=button],[role=link]')){e.preventDefault();e.target.click();return;}
  if((e.key==='ArrowLeft'||e.key==='ArrowRight')&&e.target.matches('.h')){
    e.preventDefault();e.stopPropagation();
    const d=state.documents[state.current],c=d.candidates.find(x=>x.id===state.selected),delta=e.key==='ArrowLeft'?-1:1;
    dispatch({type:'range',edge:e.target.dataset.edge,offset:(e.target.dataset.edge==='start'?c.start:c.end)+delta});
    return;
  }
  // Tools: V pointer, B pen, E eraser, R box; Ctrl+Z undo, Ctrl+Shift+Z / Ctrl+Y redo.
  if(state.view==='bench'&&state.page==='review'&&!state.sheet&&!e.target.closest('input,textarea,[contenteditable]')&&!e.isComposing){
    const k=e.key.toLowerCase();
    if((e.ctrlKey||e.metaKey)&&!e.altKey&&(k==='z'||k==='y')){e.preventDefault();ACTIONS[k==='y'||e.shiftKey?'redo':'undo']();return;}
    const tool=!e.ctrlKey&&!e.metaKey&&!e.altKey&&state.preview==='original'&&{v:'pointer',b:'pen',e:'eraser',r:'box'}[k];
    if(tool){e.preventDefault();dispatch({type:'tool',value:tool});return;}
  }
  // Review shortcuts: K keep, M mask, S sync identical occurrences, J next pending.
  const key=!e.ctrlKey&&!e.metaKey&&!e.altKey&&!e.isComposing&&state.view==='bench'&&state.page==='review'&&!state.sheet
    &&!e.target.closest('input,textarea,[contenteditable]')&&{k:'keep',m:'mask',s:'sync',j:'next'}[e.key.toLowerCase()];
  if(key){
    const button=key==='next'?document.querySelector('.island [data-a=next]'):document.querySelector(`.callout [data-a=${key}]`);
    if(button&&!button.disabled){e.preventDefault();ACTIONS[key]();}
    return;
  }
  if(e.key==='Escape'){
    if(state.sheet)dispatch({type:'sheet',value:''});
    else if(state.sel)dispatch({type:'clearSel'});
    else if(state.selected||state.menu||state.miss||state.pick)dispatch({type:'blur'});
    else if(state.tool!=='pointer'&&state.page==='review')dispatch({type:'tool',value:'pointer'});
    else if(state.inspector)dispatch({type:'inspector'});
  }
});
$('picker').addEventListener('change',e=>{mode.addFiles(e.target.files);e.target.value='';});
const canDrop=()=>state.view==='bench'&&state.page==='import';
document.addEventListener('dragover',e=>{if(canDrop()){e.preventDefault();document.body.classList.add('dropping');}});
document.addEventListener('dragleave',e=>{if(!e.relatedTarget)document.body.classList.remove('dropping');});
document.addEventListener('drop',e=>{document.body.classList.remove('dropping');if(canDrop()){e.preventDefault();mode.addFiles(e.dataTransfer.files);}});
addEventListener('resize',placeCallout);
matchMedia('(max-width:1060px)').addEventListener('change',()=>render());

// 拖手柄改范围
function offsetAt(x,y){
  const p=document.querySelector('.m.on')?.closest('p');if(!p)return null;
  const pos=document.caretPositionFromPoint?.(x,y),range=pos?{node:pos.offsetNode,offset:pos.offset}:(r=>r&&{node:r.startContainer,offset:r.startOffset})(document.caretRangeFromPoint?.(x,y));
  if(!range||!p.contains(range.node))return null;
  const before=document.createRange();before.selectNodeContents(p);before.setEnd(range.node,range.offset);
  return before.toString().length+(+p.dataset.o||0);
}
document.addEventListener('pointerdown',e=>{
  const h=e.target.closest('.h');if(!h)return;
  e.preventDefault();drag=h.dataset.edge;document.body.classList.add('dragging');
});
document.addEventListener('pointermove',e=>{
  if(!drag)return;
  const offset=offsetAt(e.clientX,e.clientY);
  if(offset!=null)dispatch({type:'range',edge:drag,offset});
});
document.addEventListener('pointerup',()=>{drag=null;rangeLogged=false;document.body.classList.remove('dragging');});

// 划选补录
document.addEventListener('mousedown',e=>{if(state.sel&&!e.target.closest('.callout'))dispatch({type:'clearSel'});});
document.addEventListener('mouseup',e=>{
  if(drag||e.target.closest('.callout,.dock,.bar,.rail,.inspector,.tools,.board'))return;
  setTimeout(()=>{
    const sel=getSelection();
    if(!sel||sel.isCollapsed||state.preview!=='original'||state.page!=='review'||state.view!=='bench')return;
    const el=n=>n.nodeType===1?n:n.parentElement,p=el(sel.anchorNode).closest('.page p[data-s]');
    if(!p||p!==el(sel.focusNode).closest('.page p[data-s]'))return;
    const range=sel.getRangeAt(0),before=document.createRange();
    before.selectNodeContents(p);before.setEnd(range.startContainer,range.startOffset);
    const d=state.documents[state.current],si=+p.dataset.s,pi=+p.dataset.p,text=d.sections[si].paragraphs[pi];
    let start=before.toString().length+(+p.dataset.o||0),end=start+range.toString().length;
    while(start<end&&/\s/.test(text[start]))start++;
    while(end>start&&/\s/.test(text[end-1]))end--;
    if(start>=end)return;
    if(state.tool==='pen'||state.tool==='eraser'){
      sel.removeAllRanges();
      dispatch({type:state.tool==='pen'?'pen':'erase',value:{docIndex:state.current,s:si,p:pi,start,end}});
      return;
    }
    const r=range.getBoundingClientRect(),P=p.closest('.page').getBoundingClientRect();
    dispatch({type:'sel',value:{docIndex:state.current,s:si,p:pi,start,end,value:text.slice(start,end),conflict:overlaps(d,si,pi,start,end),rect:{left:r.left-P.left,top:r.top-P.top,width:r.width,height:r.height}}});
  });
});
// 框选：拖一个框，框内的候选一次遮住或保留。
let lasso=null;
document.body.insertAdjacentHTML('beforeend','<div id="lasso" hidden></div>');
const lassoRect=e=>({left:Math.min(lasso.x,e.clientX),top:Math.min(lasso.y,e.clientY),right:Math.max(lasso.x,e.clientX),bottom:Math.max(lasso.y,e.clientY)});
function drawLasso(e){const r=lassoRect(e);Object.assign($('lasso').style,{left:r.left+'px',top:r.top+'px',width:r.right-r.left+'px',height:r.bottom-r.top+'px'});$('lasso').hidden=false;}
document.addEventListener('pointerdown',e=>{
  if(state.tool!=='box'||state.view!=='bench'||state.page!=='review'||state.preview!=='original'||state.sheet||e.button!==0)return;
  if(!e.target.closest('#canvas')||e.target.closest('.callout,.h'))return;
  e.preventDefault();lasso={x:e.clientX,y:e.clientY};drawLasso(e);
});
document.addEventListener('pointermove',e=>{if(lasso)drawLasso(e);});
document.addEventListener('pointerup',e=>{
  if(!lasso)return;
  const r=lassoRect(e);lasso=null;$('lasso').hidden=true;
  const ids=[...document.querySelectorAll('#canvas .page .m[data-v]')].filter(m=>[...m.getClientRects()].some(q=>q.left<=r.right&&q.right>=r.left&&q.top<=r.bottom&&q.bottom>=r.top)).map(m=>m.dataset.v);
  if(!ids.length){notify('框里没有候选');return;}
  const d=state.documents[state.current],was=new Map(d.candidates.map(c=>[c.id,c.decision])),value=state.boxMode;
  dispatch({type:'boxApply',ids,value});
  const now=new Map(state.documents[state.current].candidates.map(c=>[c.id,c.decision])),n=ids.filter(id=>now.get(id)!==was.get(id)).length;
  notify(n?`已${value==='mask'?'遮住':'保留'} ${n} 处`:`框里的候选已经是${value==='mask'?'遮住':'保留'}`);
});
