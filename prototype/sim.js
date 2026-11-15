/* 模拟模式：示例合同、模拟的宿主对话、场景和“原型”面板。界面本身在 ../mask/web/app.js。
   状态只在内存里；不运行模型，不读文件内容，不上传，不接宿主。 */
const CATEGORIES=['人名','公司','地址','邮箱','电话','身份证','银行账号','信用代码','税号','案号','内部编号','护照','通行证','不动产权证','车牌','微信','QQ','网址','域名','IP','支行','律所'];
const HOSTS={Codex:{entry:'$mask',auto:false},'Claude Code':{entry:'/mask',auto:true},WorkBuddy:{entry:'/mask',auto:false}};
const SESSION_URL='http://127.0.0.1:52417/s/demo';
const defaultTask='帮我对比这三份合同材料，找出付款条件和违约责任的差异，并整理一份风险清单。';
// candidates: [原文, 类别, 实体, 决定, 依据, 范围有误, 建议合并到的实体]
const templates=[
  {title:'技术服务合同',sections:[
    {title:'签约主体',paragraphs:['甲方：{{a}}。乙方：{{b}}。','甲方授权代表：{{c}}。双方就本次技术服务达成如下约定。']},
    {title:'第一条　服务与价款',paragraphs:['本合同总金额为人民币 100,000 元。服务期限自 2026年11月1日起至2027年4月30日止。']},
    {title:'第二条　付款安排',paragraphs:['合同签订后10个工作日内支付30%预付款；项目验收合格后30日内支付剩余70%。']},
    {title:'第三条　违约责任',paragraphs:['逾期付款的，按未付金额每日万分之三支付违约金。违约金累计不超过合同总金额的10%。','{{d}}负责甲方的日常联系与验收安排。']},
    {title:'第四条　争议解决',paragraphs:['争议由北京市朝阳区人民法院管辖。']},
    {title:'第五条　经办信息',paragraphs:['本合同由张三经办，内部项目编号 XH-2026-018。']}
  ],candidates:{a:['星河科技有限公司','公司','company1','mask','实际企业全称'],b:['海岚咨询有限公司','公司','company2','mask','实际企业全称'],c:['张三','人名','person1','mask','授权代表姓名'],d:['张三','人名','person1','mask','与前文同一人']}},
  {title:'技术服务补充协议',notice:'2 条批注未读取',sections:[
    {title:'协议主体',paragraphs:['甲方：{{a}}。甲方代表：{{b}}。','双方就原技术服务合同的付款安排作出以下补充约定。']},
    {title:'第一条　付款期限',paragraphs:['项目验收合格后，尾款付款期限由30日调整为60日。其他付款比例保持不变。']},
    {title:'第二条　违约责任',paragraphs:['逾期付款的违约金仍按未付金额每日万分之三计算。其他事项按原合同执行。']},
    {title:'第三条　联系信息',paragraphs:['{{c}}：{{d}}。联系电话：{{e}}。','本协议于2026年11月5日签署。']}
  ],candidates:{a:['星河科技有限公司','公司','company1','mask','与主合同甲方一致'],b:['张三','人名','person1','mask','与主合同代表一致'],c:['联系人','人名','role','pending','可能是字段名，不是姓名'],d:['李四','人名','person2','mask','字段后的实际姓名'],e:['13800001234','电话','phone1','mask','手机号码']}},
  {title:'付款安排确认函',sections:[
    {title:'致函主体',paragraphs:['致：{{a}}','本函所称“{{b}}”指{{c}}。']},
    {title:'一、付款确认',paragraphs:['尾款应于项目验收合格后45日内支付。该安排用于本次项目的结算。']},
    {title:'二、违约金',paragraphs:['逾期付款的，按未付金额每日万分之五计算违约金。本函未另行约定累计上限。']},
    {title:'三、生效与联系',paragraphs:['本函于2026年11月8日出具。其与原合同、补充协议之间的适用顺序，需由双方进一步明确。','落款：{{d}}章）。']}
  ],candidates:{a:['星河科技有限公司','公司','company1','mask','与前两份材料甲方一致'],b:['海岚','公司','alias','pending','正文定义的简称',false,'company2'],c:['海岚咨询有限公司','公司','company2','mask','与主合同乙方一致'],d:['海岚咨询有限公司（盖','公司','company2','pending','范围多了“（盖”，拖动手柄调整',true]}},
  {title:'逾期付款催告函',sections:[
    {title:'致函主体',paragraphs:['致：{{a}}','发函方：{{b}}。经办人：{{c}}。']},
    {title:'一、催告事项',paragraphs:['尾款已逾期，请于收到本函后7日内付清。']}
  ],candidates:{a:['星河科技有限公司','公司','company1','mask','沿用本项目代号'],b:['海岚咨询有限公司','公司','company2','mask','沿用本项目代号'],c:['王五','人名','person3','mask','经办人姓名']}}
];
const TERMS=[['验收后30日','每日万分之三，累计不超过合同总额10%'],['验收后60日','每日万分之三，其他按原合同'],['验收后45日','每日万分之五，本函未另列上限'],['催告后7日内','未另行约定']];
const COMPILED=templates.map(t=>{
  const spans={};
  const sections=t.sections.map((sec,si)=>({title:sec.title,paragraphs:sec.paragraphs.map((raw,pi)=>{
    let text='',last=0;
    for(const m of raw.matchAll(/\{\{(\w+)\}\}/g)){
      text+=raw.slice(last,m.index);
      const value=t.candidates[m[1]][0];
      spans[m[1]]={s:si,p:pi,start:text.length,end:text.length+value.length};
      text+=value;last=m.index+m[0].length;
    }
    return text+raw.slice(last);
  })}));
  return {sections,spans};
});
const seedEntities=()=>Object.fromEntries([['company1','公司',1],['company2','公司',2],['alias','公司',3],['person1','人名',1],['person2','人名',2],['role','人名',3],['person3','人名',4],['phone1','电话',1]].map(([id,type,no])=>[id,{id,type,no}]));
const workspaceFiles=()=>[
  {key:'f1',name:'技术服务合同.docx',format:'DOCX',template:0,checked:true},
  {key:'f2',name:'补充协议.docx',format:'DOCX',template:1,checked:true},
  {key:'f3',name:'付款确认函.pdf',format:'PDF',template:2,checked:true},
  {key:'f4',name:'扫描版授权书.pdf',format:'PDF',unsupported:'扫描件暂不支持'},
  {key:'f5',name:'报价明细.xlsx',format:'XLSX',unsupported:'格式暂不支持'}
];
const simState=(host='Codex')=>({...initialState(),view:'chat',phase:'idle',host,scenario:'full',draft:HOSTS[host].entry+' '+defaultTask,task:'',chat:[],categories:CATEGORIES,dir:workspaceFiles(),entities:seedEntities(),peek:null,setupDone:true,setupProgress:null,opened:false,nextId:1,reused:false});

const canRelease=s=>canExport(s)&&s.documents.some(d=>!d.handed);
const mapping=s=>Object.fromEntries(maskedCands(s).map(({c})=>[token(s,c),c.original]));
const realNames=s=>[...new Set(maskedCands(s).map(x=>x.c.original))].sort((a,b)=>b.length-a.length);
function maskText(s,text){
  const byName=Object.fromEntries(maskedCands(s).map(({c})=>[c.original,token(s,c)]));
  return realNames(s).reduce((t,n)=>t.split(n).join(byName[n]),text);
}
function paragraphOut(s,d,si,pi){
  const text=d.sections[si].paragraphs[pi];
  let out='',pos=0;
  d.candidates.filter(c=>c.s===si&&c.p===pi&&c.decision==='mask').sort((a,b)=>a.start-b.start).forEach(c=>{out+=text.slice(pos,c.start)+token(s,c);pos=c.end;});
  return out+text.slice(pos);
}
function docMarkdown(s,d){
  return `# ${d.title}\n\n`+d.sections.map((sec,si)=>`## ${sec.title}\n\n`+sec.paragraphs.map((_,pi)=>paragraphOut(s,d,si,pi)).join('\n\n')).join('\n\n');
}
function nameFor(s,entity,fallback){const hit=maskedCands(s).find(x=>x.c.entity===entity);return hit?token(s,hit.c):fallback;}
function reportData(s){
  const a=nameFor(s,'company1','甲方'),b=nameFor(s,'company2','乙方'),p=nameFor(s,'person1','甲方代表');
  return {rows:s.documents.filter(d=>d.handed).map((d,i)=>[`材料 ${i+1}`,d.title,...TERMS[d.template]]),notes:[`材料 3 把尾款期限写成 45 日，与材料 2 的 60 日不一致，需 ${a} 与 ${b} 书面确认适用顺序。`,`材料 3 的违约金改为每日万分之五且未列上限，建议由 ${p} 在验收前核对。`,'另需确认 <公司[9].公司.名称> 的授权文件。']};
}
function reportMarkdown(s){
  const r=reportData(s);
  return ['# 付款与违约责任风险清单','','| 材料 | 文件 | 尾款期限 | 逾期违约金 |','| --- | --- | --- | --- |',...r.rows.map(x=>`| ${x.join(' | ')} |`),'',...r.notes.map((n,i)=>`${i+1}. ${n}`)].join('\n');
}
function makeDocument(id,f){
  const t=templates[f.template];
  return {id,name:f.name,format:f.format,template:f.template,title:t.title,sections:COMPILED[f.template].sections,custom:!!f.custom,notice:t.notice||'',scan:0,handed:false,candidates:Object.entries(t.candidates).map(([key,[original,type,entity,decision,reason,bad,suggest]])=>({id:id+'-'+key,...COMPILED[f.template].spans[key],original,type,entity,decision,reason,bad:!!bad,suggest,manual:false}))};
}
const say=(s,text)=>s.chat.push({k:'text',text});
function startFlow(s,body){
  s.task=body;s.phase='working';s.page=s.setupDone?'import':'setup';
  if(HOSTS[s.host].auto){s.chat.push({k:'tool',text:'mask_start 运行中，已转后台'});say(s,`工作台已打开：${SESSION_URL}\n我没有读取任何文件。核对完成后自动继续。`);}
  else{s.chat.push({k:'tool',text:'mask_start 已返回'});say(s,`工作台已打开：${SESSION_URL}\n我没有读取任何文件。核对完成后回到这里发送“继续”。`);}
}
function deliver(s){s.chat.push({k:'tool',text:`mask_status 已交接 ${s.documents.filter(d=>d.handed).length} 份，读取 mask-handoff/`});s.chat.push({k:'report'});s.phase='analyzed';}

// 模拟独有的动作；addMore、sheet 只补上模拟的字段，其余交回共用的 reduce。
function simReduce(s,a,state){
  switch(a.type){
    case 'draft':s.draft=a.value;return s;
    case 'host':if(s.phase==='idle')s.draft=s.draft.replace(/^[/$]mask/,HOSTS[a.value].entry);s.host=a.value;return s;
    case 'win':s.view=a.value;s.menu=false;return s;
    case 'open':if(s.phase==='working'&&s.view==='chat'&&!s.opened){s.opened=true;s.view='bench';}return s;
    case 'send':{
      const raw=s.draft.trim();if(!raw)return state;
      const body=raw.replace(/^[/$]mask(?::\S+)?\s*/i,''),entry=body!==raw,h=HOSTS[s.host];
      if(/@\S+\.(docx?|pdf|txt)/i.test(raw)){s.chat.push({k:'hook',text:`未发送：@ 会把原件直接交给模型。\n去掉文件引用，用 ${h.entry} 在工作台选择。`});return s;}
      const names=['handed','analyzed'].includes(s.phase)?realNames(s).filter(n=>raw.includes(n)):[];
      if(names.length){s.chat.push({k:'hook',text:`未发送：含真名“${names[0]}”。\n可改写为：${maskText(s,raw)}`});return s;}
      s.chat.push({k:'user',text:raw});s.draft='';
      if(s.phase==='idle'){if(!entry||!body){say(s,`这条消息没有经过 Mask。处理文档请以 ${h.entry} 开头并写明需求。`);return s;}startFlow(s,body);return s;}
      if(s.phase==='paused'&&/继续/.test(body)){s.phase='working';s.view='bench';say(s,`工作台已恢复：${SESSION_URL}`);return s;}
      if(s.phase==='working'||s.phase==='paused'){const n=counts(s);say(s,`材料还没有交接。${n.pending?`工作台还有 ${n.pending} 处待确认。`:'请在工作台确认交接。'}\n${SESSION_URL}`);return s;}
      if(/还原/.test(body)){s.page='restore';s.view='bench';if(!s.restoreInput)s.restoreInput=reportMarkdown(s);say(s,`还原页已打开：${SESSION_URL}\n真名版只保存在本地，不回传对话。`);return s;}
      if(/补充/.test(body)){s.page='import';s.view='bench';s.phase='working';say(s,`材料列表已打开：${SESSION_URL}\n新材料沿用本项目代号。`);return s;}
      if(s.phase==='handed'){if(/继续/.test(body))deliver(s);else say(s,'脱敏稿已就绪。发送“继续”后我开始读取。');return s;}
      say(s,'继续使用 mask-handoff/ 中的脱敏稿，没有重新打开工作台。\n按严重程度排序：① 文件适用顺序与付款期限；② 违约金标准及上限；③ 签署与确认手续。');return s;
    }
    case 'pause':s.phase='paused';s.view='chat';s.menu=false;s.chat.push({k:'tool',text:'mask_start 已返回，进度已存在本地'});say(s,`核对已暂存。回来时发送 ${HOSTS[s.host].entry} 继续核对。`);return s;
    case 'addFiles':a.files.forEach(f=>s.dir.push({key:'c'+s.dir.length,name:f.name,format:f.format,template:s.dir.length%3,custom:true,checked:true}));return s;
    case 'startScan':{
      const fresh=s.dir.filter(f=>f.checked&&!f.docId);if(!fresh.length)return state;
      fresh.forEach(f=>{const doc=makeDocument(s.nextId++,f);f.docId=doc.id;s.documents.push(doc);});
      s.page='review';s.current=s.documents.findIndex(x=>x.scan<100);s.selected=null;return s;
    }
    case 'installStart':s.setupProgress=0;return s;
    case 'tick':{
      if(s.setupProgress!==null&&!s.setupDone){s.setupProgress=Math.min(100,s.setupProgress+4);if(s.setupProgress===100){s.setupDone=true;s.page='import';}return s;}
      const doc=s.documents.find(x=>x.scan<100);if(!doc)return state;
      doc.scan=Math.min(100,doc.scan+5);
      if(doc.scan===100&&s.documents[s.current]===doc)selectFirst(s);
      return s;
    }
    case 'peek':s.peek=s.peek===a.value?null:a.value;return s;
    case 'handoff':
      if(!canRelease(s))return state;
      s.documents.forEach(x=>{x.handed=true;});s.task=s.task&&maskText(s,s.task);s.page='done';s.restoreInput='';s.preview='original';s.search='';s.inspector=false;
      if(HOSTS[s.host].auto)deliver(s);else s.phase='handed';
      return s;
    case 'toReview':s.page='review';s.current=a.index??s.current;s.selected=null;return s;
    case 'toDone':s.page='done';return s;
    case 'restored':s.restored=true;return s;
    case 'addMore':s.phase='working';return;
    case 'sheet':s.peek=null;return;
  }
}
function fastForward(s){
  s.draft='';s.task=defaultTask;s.chat=[{k:'user',text:HOSTS[s.host].entry+' '+defaultTask}];
  startFlow(s,defaultTask);s.opened=true;s.view='bench';s.page='review';
  s.dir.filter(f=>f.checked).forEach(f=>{const doc=makeDocument(s.nextId++,f);f.docId=doc.id;doc.scan=100;s.documents.push(doc);});
  s.current=0;selectFirst(s);return s;
}
function resolveDemo(s){
  allCands(s).forEach(({c})=>{
    if(c.original==='联系人')c.decision='keep';
    if(c.original==='海岚'){c.entity='company2';c.decision='mask';}
    if(c.bad){c.end=c.start+8;c.original=c.original.slice(0,8);c.bad=false;c.decision='mask';}
  });
  s.documents.forEach(x=>{x.handed=true;});deliver(s);s.page='done';s.view='bench';return s;
}
function scenarioState(value,host){
  const s=simState(host);s.scenario=value;
  if(value==='setup')s.setupDone=false;
  if(value==='review')fastForward(s);
  if(value==='project'){
    resolveDemo(fastForward(s));
    s.dir.splice(3,0,{key:'f6',name:'逾期付款催告函.docx',format:'DOCX',template:3,checked:true});
    s.page='import';s.phase='working';s.reused=true;s.task='补充一份催告函';
    s.chat.push({k:'user',text:HOSTS[host].entry+' 补充一份催告函'});say(s,`材料列表已打开：${SESSION_URL}\n新材料沿用本项目代号。`);
  }
  if(value==='restore'){resolveDemo(fastForward(s));s.page='restore';s.restoreInput=reportMarkdown(s);}
  return s;
}

let ticker=null,protoOpen=false,lastChatLength=0;
const hostOf=()=>HOSTS[state.host];
function reviewGo(){
  const c=counts(state);
  if(c.scanning)return act({cls:'busy',a:c.pending?'next':'',title:'本地识别中',inner:`<i class="spin"></i><span class="lbl">识别中</span>${c.pending?`<b>${c.pending}</b>`:''}`,key:'busy'+c.pending});
  if(c.pending)return act({cls:'todo',a:'next',title:`下一处待确认，还剩 ${c.pending} 处（J）`,inner:`<b>${c.pending}</b><span class="lbl">处待确认</span>${icon('next',14)}`,key:'n'+c.pending});
  if(canRelease(state))return act({cls:'ready',a:'release',title:`交给 ${state.host}`,inner:`${icon('up',15)}<span class="lbl">交给 ${esc(state.host)}</span>`,key:'send'});
  return act({cls:'quiet',a:'to-done',title:'已交接',inner:`${icon('check',15)}<span class="lbl">已交接</span>`,key:'done'});
}

// 宿主对话（模拟）
function reportHtml(){
  const r=reportData(state);
  return `<div class="reply report"><h2>付款安排存在差异，适用顺序需确认。</h2><table><thead><tr><th>材料</th><th>文件</th><th>尾款期限</th><th>逾期违约金</th></tr></thead><tbody>${r.rows.map(x=>`<tr>${x.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table>${r.notes.map((n,i)=>`<p>${i+1}. ${esc(n)}</p>`).join('')}<p style="margin-top:10px">已写入 mask-handoff/风险清单.md。需要真名版时发送 ${esc(hostOf().entry)} 还原。</p></div>`;
}
function message(m,i){
  const k=`data-k="msg${i}"`;
  if(m.k==='user')return `<div class="bubble" ${k}>${esc(m.text)}</div>`;
  if(m.k==='hook')return `<div class="hook" ${k}>${icon('lock',15)}<span>${esc(m.text)}</span></div>`;
  if(m.k==='tool')return `<div class="tool" ${k}>${icon('check',14)}${esc(m.text)}</div>`;
  if(m.k==='text')return `<div class="reply" ${k}>${esc(m.text).replace(esc(SESSION_URL),'<a data-a="win" data-v="bench" role="link" tabindex="0"><u>打开工作台</u></a>')}</div>`;
  return `<div ${k}>${reportHtml()}</div>`;
}
function aidButtons(){
  const e=hostOf().entry;
  const sets={idle:[['用 @ 引用原件',`${e} @技术服务合同.docx 帮我审一下`]],working:[['继续','继续']],paused:[['继续核对',`${e} 继续核对`]],handed:[['继续','继续'],['写了真名的追问','星河科技有限公司的付款风险大吗？']],analyzed:[['追问','把风险按严重程度排序'],['写了真名的追问','星河科技有限公司的付款风险大吗？'],['请求还原',`${e} 还原`],['补充材料',`${e} 补充材料`]]};
  return (sets[state.phase]||[]).map(([label,value])=>`<button data-a="fill" data-v="${esc(value)}">${esc(label)}</button>`).join('');
}
function chatView(){
  const empty=!state.chat.length;
  return `<div class="chat ${empty?'is-empty':''}"><div class="chat-host">${esc(state.host)}</div><div class="thread" id="thread"><div class="thread-in">${state.chat.map(message).join('')}</div></div><div class="composer-box"><div class="composer"><textarea id="draft" rows="1" aria-label="消息" placeholder="回复">${esc(state.draft)}</textarea><button class="send" data-a="send" aria-label="发送" title="发送" ${state.draft.trim()?'':'disabled'}>${icon('up',18)}</button></div><div class="aid">${aidButtons()}</div></div></div>`;
}

function setupPage(){
  const installing=state.setupProgress!==null,p=state.setupProgress||0;
  const rows=[['本地服务',1,''],['llama.cpp',Math.min(1,p/45),''],['HaS 模型',Math.max(0,(p-45)/55),'约 640 MB']];
  return bar()+`<main class="center" data-k="p-setup"><h1 class="ask">准备本地环境</h1><div class="steps">${rows.map(([name,v,note])=>`<div class="stp">${v>=1?`<span class="tick-on">${icon('check',13)}</span>`:`<svg class="dial" viewBox="0 0 24 24" aria-hidden="true"><circle class="tr" cx="12" cy="12" r="9"/><circle class="va" cx="12" cy="12" r="9" style="stroke-dashoffset:${(56.5*(1-v)).toFixed(1)}"/></svg>`}${name}<small>${note}</small></div>`).join('')}</div></main>`+dock('setup',`<div class="field"><span class="field-text">只下载运行环境，不读取文档</span></div>${go({cls:'next',a:'install',label:'安装',inner:icon('down',20),disabled:installing,key:'install'})}`);
}
function tile(f,i){
  const doc=state.documents.find(d=>d.id===f.docId),off=!!f.unsupported;
  return fileRow({f,i,a:off||doc?'':'toggle',on:f.checked&&!off&&!doc,off,locked:!!doc,doc,sub:f.unsupported||(doc?.handed?'已交接':doc?'已加入':f.custom?'示例正文':'')});
}
function selectPage(){
  const fresh=state.dir.filter(f=>f.checked&&!f.docId).length;
  return bar()+`<main class="center" data-k="p-import"><h1 class="ask">${esc(state.task)}</h1>${state.reused?`<p class="quiet">${icon('check',14)}沿用本项目代号</p>`:''}<div class="files">${fileOrder(state.dir).map(tile).join('')}${addRow(state.dir.length,'TXT · Markdown · Word（.docx）· 原生 PDF')}</div>${fresh?`<div class="ctas wide"><button class="cta hot" data-a="start-scan">开始识别 ${countNum(fresh)} 份${icon('right',18)}</button></div>`:'<p class="quiet">勾选或添加材料后开始识别</p>'}</main>`;
}
function paraHtml(d,si,pi,hits,cur){
  const text=d.sections[si].paragraphs[pi],here=hits.filter(h=>h.s===si&&h.p===pi);
  const plain=(from,to)=>{
    let out='',pos=from;
    here.filter(h=>!h.marked&&h.start>=from&&h.end<=to).forEach(h=>{out+=esc(text.slice(pos,h.start))+`<span class="hit ${cur===h?'cur':''}">${esc(text.slice(h.start,h.end))}</span>`;pos=h.end;});
    return out+esc(text.slice(pos,to));
  };
  let out='',last=0;
  if(reached(d,si,pi))d.candidates.filter(c=>c.s===si&&c.p===pi).sort((a,b)=>a.start-b.start).forEach(c=>{out+=plain(last,c.start)+markHtml(c,here,cur?.s===si&&cur?.p===pi?cur:null);last=c.end;});
  return out+plain(last,text.length);
}
function paper(d,hits){
  const ai=state.preview==='masked';
  const here=hits.filter(h=>h.docIndex===state.documents.indexOf(d)),cur=state.hit>=0?hits[state.hit]:null,curHere=cur&&here.find(h=>h.s===cur.s&&h.p===cur.p&&h.start===cur.start);
  const body=d.sections.map((sec,si)=>`<h3 class="${reached(d,si,0)?'':'dim'}">${sec.title}</h3>${sec.paragraphs.map((_,pi)=>`<p class="${reached(d,si,pi)?'':'dim'}" data-s="${si}" data-p="${pi}">${paraHtml(d,si,pi,here,curHere)}</p>`).join('')}`).join('');
  return `<article class="page ${ai?'ai':''}" data-k="doc${d.id}">${d.notice?`<div class="flag">${icon('info',14)}${esc(d.notice)}</div>`:''}<h1>${d.title}</h1>${body}<p class="fine">示例正文${d.custom?'，非所选文件内容':''}</p>${calloutFor(d)}</article>`;
}
function donePage(){
  const handed=state.documents.filter(d=>d.handed),waiting=state.phase==='handed',h=esc(state.host);
  return bar()+`<main class="center" data-k="p-done"><div class="papers">${handed.map((d,i)=>`<button data-k="p${d.id}" data-a="open-doc" data-v="${state.documents.indexOf(d)}" title="mask-handoff/材料${i+1}.md">${mini(d,1.5)}材料 ${i+1}</button>`).join('')}</div><h1 class="ask">${waiting?'脱敏稿已交接':`${h} 已继续分析`}</h1><button class="cta" data-a="back-host">${waiting?`回到 ${h}，发送“继续”`:`回到 ${h}`}</button></main>`
    +dock('done',`${moreMenu([['export','share','导出原格式',!canExport(state)],['add-more','plus','补充材料']])}<label class="field"><textarea id="paste" rows="1" aria-label="AI 结果" placeholder="粘贴 AI 结果，还原真名">${esc(state.restoreInput)}</textarea></label>${go({cls:'next',label:'保存真名版',inner:icon('down',20),disabled:true,key:'save-off'})}`);
}
function restorePage(){
  const r=restoreInfo(state),partial=r.kept.length>0,save=partial?'保存部分还原版':'保存真名版';
  const status=!r.tokens.length?'没有检测到代号':r.missing.length?'':state.restored?'已保存到本地':partial?`部分还原，${r.kept.length} 个代号未映射`:`完整还原 ${r.replaced} 处`;
  const primary=r.missing.length?act({cls:'todo',a:'next-miss',title:'下一处没有映射的代号',inner:`<b>${r.missing.length}</b><span class="lbl">个代号没有映射</span>${icon('next',14)}`,key:'m'+r.missing.length}):state.restored?act({cls:'quiet',title:'已保存',inner:`${icon('check',15)}<span class="lbl">已保存</span>`,disabled:true,key:'saved'}):act({cls:'save',a:'save-restored',title:save,inner:`${icon('down',15)}<span class="lbl">${save}</span>`,disabled:!r.replaced,key:'save'});
  return bar({title:'AI 结果',island:restoreIsland(status,primary)})+`<main class="canvas" id="canvas" data-k="p-restore">${restorePaper(r)}</main>`;
}
function handoffSheet(){
  const byType={},c=counts(state),docs=state.documents.filter(d=>!d.handed),h=esc(state.host);
  maskedCands(state).forEach(x=>{const t=state.entities[x.c.entity].type;byType[t]=(byType[t]||0)+1;});
  const peek=state.peek!=null?docMarkdown(state,docs[state.peek]):'';
  return `<div class="scrim" data-a="close-sheet"></div><section class="modal" role="dialog" aria-modal="true" aria-label="确认交接"><button class="x" data-a="close-sheet" aria-label="关闭">${icon('x',15)}</button><div class="papers" style="padding-top:6px">${docs.map((d,i)=>`<button data-a="peek" data-v="${i}" aria-pressed="${state.peek===i}" title="预览材料 ${i+1}">${mini(d,1.15)}材料 ${i+1}</button>`).join('')}</div>${peek?`<pre tabindex="0">${esc(peek)}</pre>`:''}<div class="tally">${Object.entries(byType).map(([t,n])=>`<span class="tag">${t}<b>${n}</b></span>`).join('')}<span class="tag k">保留<b>${c.kept}</b></span><span class="tag k">补录<b>${c.manual}</b></span></div>${noticeHtml()}<div class="split"><div><span>${icon('up',15)}发给 ${h}</span><small>${docs.length} 份脱敏稿</small></div><div><span>${icon('lock',15)}留在本机</span><small>原件、文件名、代号表</small></div></div><button class="cta hot" id="confirm" data-a="confirm-handoff">${icon('up',18)}交给 ${h}</button></section>`;
}
function exportSheet(){
  return `<div class="scrim" data-a="close-sheet"></div><section class="modal" role="dialog" aria-modal="true" aria-label="导出原格式"><button class="x" data-a="close-sheet" aria-label="关闭">${icon('x',15)}</button><div class="rows" style="padding-top:22px">${state.documents.map(d=>`<div><span>${esc(d.name.replace(/\.(\w+)$/,'.脱敏.$1'))}</span><small>${esc(d.format)}</small></div>`).join('')}</div>${noticeHtml()}<button class="cta" id="confirm" data-a="confirm-export">${icon('down',18)}导出到 mask-export/</button></section>`;
}
function protoView(){
  const hints={setup:'',review:'试试：查找“张三”；划选 XH-2026-018',project:'新材料沿用原代号',restore:'<公司[9].公司.名称> 没有映射',full:''};
  const scenes=[['full','完整流程'],['setup','首次使用'],['review','核对查漏'],['project','项目续接'],['restore','结果还原']];
  return `<button class="proto-btn" data-a="proto" aria-expanded="${protoOpen}">原型</button>${protoOpen?`<div class="proto-panel"><div class="proto-group">${Object.keys(HOSTS).map(x=>`<button class="${x===state.host?'on':''}" data-a="host" data-v="${x}">${x}</button>`).join('')}</div><div class="proto-group">${scenes.map(([v,l])=>`<button class="${v===state.scenario?'on':''}" data-a="scenario" data-v="${v}">${l}</button>`).join('')}</div>${hints[state.scenario]?`<p>${hints[state.scenario]}</p>`:''}</div>`:''}`;
}

function ensureTicker(){
  if(ticker)return;
  ticker=setInterval(()=>{
    const busy=(state.setupProgress!==null&&!state.setupDone)||state.documents.some(d=>d.scan<100);
    if(!busy){clearInterval(ticker);ticker=null;return;}
    const before=state.selected;
    state=reduce(state,{type:'tick'});render();
    if(state.selected&&state.selected!==before)reveal();
  },90);
}
function addFiles(files){
  const selected=[...files],accepted=selected.filter(f=>/\.(txt|md|docx|pdf)$/i.test(f.name));
  if(!accepted.length){notify('只支持 TXT、Markdown、DOCX、PDF');return;}
  dispatch({type:'addFiles',files:accepted.map(f=>({name:f.name,format:f.name.split('.').pop().toUpperCase()}))});
  if(accepted.length<selected.length)notify('其余格式未加入');
}
function scenario(value){state=scenarioState(value,state.host);lastChatLength=-1;render();ensureTicker();}
const SIM_ACTIONS={
  win:t=>dispatch({type:'win',value:t.dataset.v}),
  fill:t=>{dispatch({type:'draft',value:t.dataset.v});$('draft').focus();},
  send:()=>dispatch({type:'send'}),
  install:()=>{dispatch({type:'installStart'});ensureTicker();},
  'start-scan':()=>{dispatch({type:'startScan'});ensureTicker();},
  pause:()=>dispatch({type:'pause'}),
  peek:t=>dispatch({type:'peek',value:+t.dataset.v}),
  'confirm-handoff':()=>{
    dispatch({type:'sheet',value:''});document.body.classList.add('flying');
    setTimeout(()=>{document.body.classList.remove('flying');dispatch({type:'handoff'});},motionOK()?520:0);
  },
  'confirm-export':()=>{dispatch({type:'sheet',value:''});notify('示例：已导出到 mask-export/，未生成真实文件');},
  'to-done':()=>dispatch({type:'toDone'}),
  'open-doc':t=>dispatch({type:'toReview',index:+t.dataset.v}),
  'back-host':()=>{if(state.phase==='handed')state=reduce(state,{type:'draft',value:'继续'});dispatch({type:'win',value:'chat'});},
  'clear-restore':()=>dispatch({type:'restoreInput',value:''}),
  'save-restored':()=>{dispatch({type:'restored'});notify('示例：已存到 mask-restored/，未回传对话');},
  proto:()=>{protoOpen=!protoOpen;render();},
  host:t=>dispatch({type:'host',value:t.dataset.v}),
  scenario:t=>scenario(t.dataset.v)
};
// 只有模拟有对话输入框。
document.addEventListener('input',e=>{if(!e.isComposing&&e.target.id==='draft')dispatch({type:'draft',value:e.target.value});});
document.addEventListener('compositionend',e=>{if(e.target.id==='draft')dispatch({type:'draft',value:e.target.value});});
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&e.target.id==='draft'&&!e.isComposing){e.preventDefault();dispatch({type:'send'});}});

start({
  pages:{setup:setupPage,import:selectPage,done:donePage,restore:restorePage},
  sheets:{handoff:handoffSheet,export:exportSheet},
  actions:SIM_ACTIONS,paper,reviewGo,addFiles,
  barLead:()=>`<button class="crumb glass" data-a="win" data-v="chat" aria-label="回到 ${esc(state.host)}">${icon('back',15)}<span>${esc(state.host)}</span></button>`,
  restoreMap:mapping,
  reduce:simReduce,
  after(action,before){if(before.phase==='idle'&&state.phase==='working'&&state.view==='chat')setTimeout(()=>dispatch({type:'open'}),900);},
  render(){
    patch($('chat'),chatView());patch($('proto'),protoView());
    $('chat').inert=state.view!=='chat';
    if(state.chat.length!==lastChatLength){lastChatLength=state.chat.length;const th=$('thread');if(th)th.scrollTop=th.scrollHeight;}
  }
});
state=simState();render();
