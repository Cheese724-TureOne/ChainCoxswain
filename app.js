/* ChainCoxswain v2 interface: original 4 modules + Qwen agent panel + QwenWork
   collaboration + comparison experiments + A.zip real-data workspace.
   All inventory numbers remain computed by engine.js in the browser; the
   optional backend adds Qwen API proxying, A.zip parsing, MCP bridge and
   Office report generation. Offline demo mode keeps working without it. */
(function(){
'use strict';
const E=window.ChainEngine;
const API_BASE=window.CHAINCOXSWAIN_API_BASE||'http://127.0.0.1:8765';
const DEMO=E.demoData();
const state={materials:DEMO.materials,orders:DEMO.orders,source:'演示数据',dataKind:'demo',horizon:14,section:'overview',
  selectedSku:'M-004',auto:null,autoSku:null,manualSku:'M-001',manualType:'deliveryDelay',manualValue:3,
  upSku:null,upPolicy:null,upProb:null,upTier:null,upRecovery:null,upLoading:false,
  manualSupplier:'',manualResult:null,reviewed:new Set(),importMessages:[],lastImport:'',working:false,
  today:null,backend:null,qwenStatus:null,agent:null,agentRunning:false,
  agentGoal:'分析当前库存风险，找出最脆弱的物料，自动运行压力测试，推荐应对方案。先用工具获取风险清单，再对重点物料搜索临界点并比较干预方案；所有数字必须来自工具结果。',
  agentParams:{maxRounds:8,maxToolCalls:30,timeoutSec:240,budgetTokens:60000},
  collab:{toolResults:{},mcpChecked:false,mcpOnline:false},
  compare:null,compareRunning:false,datasetMeta:null,aZipInfo:null,backtest:null,backtestLoading:false,
  relInfo:null,censored:null,censoredLoading:false,wizard:null};
const labels={overview:'运营总览',data:'数据工作台',auto:'自主压力测试',manual:'手动情景模拟',agent:'千问 Agent',collab:'千问办公协作',compare:'对照实验',report:'风险报告'};
const nav=[['overview','◫','运营总览'],['data','⇧','数据工作台'],['auto','◎','自主压力测试'],['manual','⚑','手动情景模拟'],['agent','✺','千问 Agent'],['collab','⬡','千问办公协作'],['compare','≣','对照实验'],['report','▤','风险报告']];
const riskLabels={critical:'缺货风险',watch:'低于安全库存',excess:'库存覆盖偏高',normal:'状态正常'};
const scenarioLabels={deliveryDelay:'采购到货延迟',purchaseDelay:'补货下单推迟',demandIncrease:'需求突然上涨',supplierOutage:'供应商断供'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=n=> n==null||!Number.isFinite(Number(n)) ? '—' : Number(n).toLocaleString('zh-CN',{maximumFractionDigits:2});
const dateShort=s=>s?String(s).slice(5):'—';
const today=()=>state.today||E.toDayString();
const mat=sku=>state.materials.find(m=>m.sku===sku)||state.materials[0];
const allRisks=()=>E.rankRisks(state.materials,state.orders,state.horizon,today());
const risk=sku=>E.baselineRisk(mat(sku),state.orders,state.horizon,today());
const nonempty=s=>String(s??'').trim();
function getStatus(level){return `<span class="status ${level}">${riskLabels[level]}</span>`;}
function btn(label,action,cls='btn-outline',extra=''){return `<button class="btn ${cls}" data-action="${action}" ${extra}>${label}</button>`;}
function toast(message,error=false){
 const area=document.querySelector('#toast-area');if(!area)return;
 const el=document.createElement('div');el.className='toast'+(error?' error':'');el.textContent=message;
 area.appendChild(el);setTimeout(()=>el.remove(),4900);
}
// ================= backend bridge (optional; offline-safe) =================
async function api(path,opts={}){
 const ctrl=new AbortController();const timer=setTimeout(()=>ctrl.abort(),opts.timeout||60000);
 try{
  const resp=await fetch(API_BASE+path,{method:opts.method||'GET',
   headers:{'Content-Type':'application/json'},body:opts.body?JSON.stringify(opts.body):undefined,
   signal:ctrl.signal});
  clearTimeout(timer);
  const data=await resp.json().catch(()=>({}));
  return {ok:resp.ok&&data.ok!==false,status:resp.status,data};
 }catch(e){clearTimeout(timer);return {ok:false,status:0,data:{error:'无法连接后端（'+API_BASE+'）：'+e.message}};}
}
async function checkBackend(){
 const r=await api('/api/health',{timeout:4000});
 state.backend=r.ok?r.data:null;
 if(r.ok){state.qwenStatus=r.data.qwen;state.collab.mcpOnline=true;}
 render();
}
// ============================ navigation shell ============================
function navHtml(){return `<aside class="sidebar"><div class="brand"><div class="brand-icon">⟠</div><div><div class="brand-title">链舵 <span style="font-size:12px;font-weight:500;color:#6acabc">ChainCoxswain</span></div><div class="brand-sub">SUPPLY CHAIN INTELLIGENCE</div></div></div>
  <div class="nav-caption">工作空间</div><div class="side-nav">${nav.map(([k,ic,title])=>`<button class="nav-btn ${state.section===k?'active':''}" data-action="nav" data-page="${k}"><span class="nav-icon">${ic}</span>${title}</button>`).join('')}</div>
  <div class="nav-divider"></div><div class="nav-caption">风险推演引擎</div>
  <div class="nav-note"><div class="title"><span style="color:#45ddc6">●</span>计算引擎就绪 · V2.0</div><p>自主选择物料 → 生成扰动 → 迭代搜索临界点 → 比较处置预案。所有库存数量由计算引擎生成。</p></div>
  <div class="nav-meta">${state.backend?('后端在线 · 端口 '+(API_BASE.split(':').pop()||'8765')):'纯本地模式 · 后端未启动'}<br>${state.qwenStatus?('千问模式：'+state.qwenStatus.modeLabel):'千问状态未知'} · ${today()}</div></aside>`;}
function topHtml(){return `<header class="topbar"><div class="breadcrumb"><span>CHAINCOXSWAIN</span><span> / </span><strong>${labels[state.section]}</strong></div>
<div class="top-actions"><span class="source-pill ${state.dataKind}"><span class="pulse"></span>${esc(state.source)}</span>${btn('↻ 载入示例','sample','btn-outline btn-sm')}${btn('⇧ 导入数据','to-data','btn-primary btn-sm')}</div></header>`;}
function header(title,subtitle,eyebrow,actions=''){return `<div class="hero"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${subtitle}</p></div>${actions?`<div class="hero-tools">${actions}</div>`:''}</div>`;}
function horizonHtml(){return `<div class="seg-control"><button class="${state.horizon===14?'active':''}" data-action="horizon" data-value="14">14 天</button><button class="${state.horizon===30?'active':''}" data-action="horizon" data-value="30">30 天</button></div>`;}
function metricHtml(){const ranked=allRisks(),critical=ranked.filter(r=>r.level==='critical').length,watch=ranked.filter(r=>r.level==='watch').length;
 const delayed=state.auto?state.auto.tested.filter(t=>Object.values(t.thresholds).some(v=>v.status==='found')).length:'—';
 const metrics=[['监控物料',state.materials.length,'已纳入当前测试范围','◫','teal'],['基准缺货风险',critical,`未来 ${state.horizon} 天逐日测算`,'!','red'],['安全库存预警',watch,'不包含已缺货的物料','△','orange'],['已找到临界情景',delayed,state.auto?`累计 ${state.auto.totalRounds} 次压力计算`:'运行自主测试后更新','◎','blue']];
 return `<div class="metric-grid">${metrics.map(([title,value,note,ic,color])=>`<div class="metric"><div class="metric-top">${title}<span class="metric-symbol ${color}">${ic}</span></div><div class="metric-value">${value}</div><div class="metric-note">${note}</div></div>`).join('')}</div>`;}
function chartSVG(base,comparison=null,title2='扰动情景',third=null,thirdTitle='缓解后',safety=0){
 if(!base||!base.points)return '<div class="empty">暂无库存仿真结果</div>';
 const sets=[{r:base,color:'#17aca0',label:'基准'}];
 if(comparison)sets.push({r:comparison,color:'#efa34a',label:title2});
 if(third)sets.push({r:third,color:'#7783de',label:thirdTitle});
 const values=sets.flatMap(o=>o.r.points.map(p=>p.stock));
 const ceiling=Math.max(10,safety,...values)*1.14;const width=780,height=273,L=48,R=17,T=16,B=35;
 const cw=width-L-R,ch=height-T-B;
 const x=i=>L+(base.points.length===1?0:i/(base.points.length-1))*cw;
 const y=v=>T+ch-Math.max(0,v)/ceiling*ch;
 const pts=series=>series.map((p,i)=>`${i===0?'M':'L'}${x(i).toFixed(1)} ${y(p.stock).toFixed(1)}`).join(' ');
 const lines=[];for(let i=0;i<=4;i++){
  const v=ceiling*i/4;lines.push(`<line x1="${L}" y1="${y(v)}" x2="${width-R}" y2="${y(v)}" stroke="#eaf0f5" stroke-dasharray="${i===0?'0':'4 4'}"/><text x="${L-11}" y="${y(v)+3}" font-size="10" text-anchor="end" fill="#9cacb9">${num(Math.round(v))}</text>`);
 }
 const step=Math.max(1,Math.ceil(base.points.length/7));
 const xl=base.points.map((p,i)=>i%step===0||i===base.points.length-1?`<text x="${x(i)}" y="${height-10}" text-anchor="middle" fill="#9aaaB7" font-size="10">${dateShort(p.date)}</text>`:'').join('');
 const safeLine=safety>0?`<line x1="${L}" y1="${y(safety)}" x2="${width-R}" y2="${y(safety)}" stroke="#aab9c9" stroke-dasharray="5 5"/><text x="${width-R-5}" y="${y(safety)-5}" text-anchor="end" font-size="10" fill="#98a8b6">安全库存 ${num(safety)}</text>`:'';
 return `<div class="chart-shell"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="每日期末库存折线图"><rect x="${L}" y="${T}" width="${cw}" height="${ch}" fill="#fbfdff"/>${lines.join('')}${safeLine}${sets.map(s=>`<path d="${pts(s.r.points)}" fill="none" stroke="${s.color}" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/>${s.r.points.filter((_,i)=>i===0||i===s.r.points.length-1).map(p=>{const i=s.r.points.indexOf(p);return `<circle cx="${x(i)}" cy="${y(p.stock)}" r="3.4" fill="${s.color}" stroke="white" stroke-width="1.5"/>`;}).join('')}`).join('')}${xl}</svg></div>
 <div class="chart-key">${sets.map(s=>`<span><i class="line-dot" style="background:${s.color}"></i>${s.label}</span>`).join('')}${safety>0?'<span><i class="line-dot" style="background:#acbbc9"></i>安全库存</span>':''}</div>`;
}
function thresholdBars(tested){
 // Bar visualisation of found thresholds across materials (mixed units -> per-type rows)
 const types=[['deliveryDelay','到货延期 (天)'],['purchaseDelay','下单推迟 (天)'],['demandIncrease','需求上涨 (%)']];
 const maxV={deliveryDelay:30,purchaseDelay:30,demandIncrease:200};
 return `<div class="threshold-bars">${types.map(([k,label])=>{
  const rows=tested.map(t=>{const th=t.thresholds[k];let v=null,note='';
   if(th.status==='found'){v=th.threshold;note=th.threshold+(k==='demandIncrease'?'%':'天');}
   else if(th.status==='already'){note='基线已缺货';}
   else if(th.status==='not-applicable'){note='不适用';}
   else if(th.status==='not-found'){note='未触发';}
   return {sku:t.risk.material.sku,v,note};}).filter(r=>r.note!=='不适用');
  const shown=rows.filter(r=>r.v!==null);
  return `<div class="tb-row"><div class="tb-label">${label}</div><div class="tb-bars">${rows.map(r=>`
   <div class="tb-item"><div class="tb-track"><div class="tb-fill ${r.v!==null?'has':''}" style="width:${r.v!==null?Math.max(3,Math.min(100,r.v/maxV[k]*100)):0}%"></div></div>
   <div class="tb-sku">${esc(r.sku)}</div><div class="tb-val">${r.v!==null?esc(r.note):`<span class="muted">${esc(r.note)}</span>`}</div></div>`).join('')}</div></div>`;}).join('')}
 <div class="tiny-footer">柱长按类型上限归一（延期/推迟 30 天，需求 200%）；“不适用”表示无对应采购数据，不画出柱形。</div></div>`;
}
function coverageValue(c){return c===null?'无日均消耗':num(c)+' 天';}
function materialTable(risks,limit=100){return `<div class="table-wrap"><table class="data-table"><thead><tr><th>物料名称 / 编码</th><th>当前库存</th><th>日均消耗</th><th>覆盖天数</th><th>未来预警</th></tr></thead>
<tbody>${risks.slice(0,limit).map(r=>`<tr data-action="select-sku" data-sku="${esc(r.material.sku)}" class="${state.selectedSku===r.material.sku?'chosen':''}">
<td><div class="sku-name">${esc(r.material.name)}</div><div class="sku-code">${esc(r.material.sku)}</div></td><td>${num(r.material.stock)} 件</td><td>${num(r.material.usage)} 件</td><td>${coverageValue(r.coverage)}</td><td>${getStatus(r.level)}</td></tr>`).join('')}</tbody></table></div>`;}
function detail(r){const m=r.material,first=r.firstPO;
 return `<div class="detail-header"><div class="detail-icon">◈</div><div><div class="detail-title">${esc(m.name)}</div><div class="detail-sub">${esc(m.sku)}　·　${esc(m.supplier||'供应商未提供')}</div></div></div>
 <div class="mb16">${getStatus(r.level)}</div><div class="insight"><strong>诊断解释</strong><div style="margin-top:7px">${esc(r.reason)}</div></div>
 <div class="key-value"><div><div class="k">库存覆盖天数</div><div class="v">${coverageValue(r.coverage)}</div></div><div><div class="k">建议补货点（估算）</div><div class="v">${num(r.replenishmentPoint)} <span style="font-size:11px;font-weight:500">件</span></div></div>
 <div><div class="k">下一笔预计到货</div><div class="v" style="font-size:15px">${first?esc(first.arrival):'暂无记录'}</div></div><div><div class="k">安全库存</div><div class="v">${num(m.safety)} <span style="font-size:11px;font-weight:500">件</span></div></div></div>
 <div class="small-muted">补货点 = 日均使用量 × 供应交期 + 安全库存；非自动采购指令。未确认收货的逾期采购单不计入可用库存。</div>
 <div class="row-actions">${btn('◎ 自主探测风险','analyze-selected','btn-primary')}${btn('⚑ 手动设置情景','manual-selected','btn-outline')}${btn('✺ 交给千问 Agent','agent-selected','btn-outline')}</div>
 ${upgradePanel(r)}`;}
function upgradePanel(r){
 const sku=r.material.sku;
 if(state.upSku!==sku||!state.upPolicy){
  return `<div class="insight" style="margin-top:14px"><strong>升级算法（后端）</strong><div style="margin-top:7px" class="small-muted">Monte Carlo 缺货概率、动态安全库存/再订货点、六维风险与优先级由后端计算。</div>
  <div class="row-actions" style="margin-top:8px">${btn('◎ 加载概率与政策','fetch-upgrade','btn-outline')}${state.upLoading?'<span class="filehint">计算中…</span>':''}</div></div>`;
 }
 const pol=state.upPolicy||{},prob=state.upProb||{},tier=state.upTier||{};
 const h=(prob.horizons||{});
 const bar=(p)=>p===null||p===undefined?'—':(p*100).toFixed(1)+'%';
 return `<div class="insight" style="margin-top:14px"><strong>升级算法（后端 · 数据版本 ${esc(String(pol.dataVersion??state.upVersion??'—'))}）</strong>
 <div class="key-value" style="margin-top:8px">
 <div><div class="k">优先级</div><div class="v">${esc(tier.tier||'—')}</div></div>
 <div><div class="k">推荐安全库存</div><div class="v">${pol.available?num(pol.recommendedSafety)+' 件':'回退'}</div></div>
 <div><div class="k">推荐再订货点</div><div class="v">${pol.available?num(pol.recommendedROP)+' 件':'—'}</div></div>
 <div><div class="k">库存位置</div><div class="v">${num(pol.inventoryPosition)} 件</div></div>
 <div><div class="k">7/14/30 天缺货概率</div><div class="v">${prob.available?`${bar((h['7']||{}).stockoutProbability)} / ${bar((h['14']||{}).stockoutProbability)} / ${bar((h['30']||{}).stockoutProbability)}`:'历史不足'}</div></div>
 <div><div class="k">14 天预计未满足 / P95</div><div class="v">${prob.available?`${num((h['14']||{}).expectedUnmet)} / ${num((h['14']||{}).p95Unmet)} 件`:'—'}</div></div></div>
 ${prob.available?'':`<div class="small-muted" style="margin-top:6px">${esc(prob.reason||'')}</div>`}
 ${(tier.rules||[]).length?`<div class="small-muted" style="margin-top:6px">优先级规则：${tier.rules.map(esc).join('；')}</div>`:''}
 <div class="row-actions" style="margin-top:8px">${btn('⟲ 反向干预优化','fetch-recovery','btn-outline')}${btn('◎ 重新加载','fetch-upgrade','btn-outline btn-sm')}</div>
 ${state.upRecovery?recoveryHtml(state.upRecovery):''}</div>`;
}
function recoveryHtml(rec){
 const f=rec.feasibleOptions||[];
 return `<div class="insight" style="margin-top:10px"><strong>解除缺货的最小干预（仿真验证）</strong>
 <div class="small-muted" style="margin-top:6px">可行方案：${f.length?f.join('、'):'无（约束内不可行）'}；推荐：${esc(rec.recommended||'—')}</div>
 <div class="small-muted">补货：${rec.replenishment&&rec.replenishment.feasible?`最小 ${num(rec.replenishment.quantity)} 件 @ ${esc(rec.replenishment.arrival)}`:(rec.replenishment?esc(rec.replenishment.reason||'不可行'):'—')}；
 加急：${rec.expedite&&rec.expedite.feasible?`${num(rec.expedite.days)} 天`:(rec.expedite?esc(rec.expedite.reason||'不可行'):'—')}；
 需求削减：${rec.demandReduction&&rec.demandReduction.feasible?`${num(rec.demandReduction.percent)}%`:'—'}</div></div>`;
}
async function fetchUpgrade(sku){
 state.upLoading=true;render();
 const [p,q,d]=await Promise.all([api('/api/policy',{method:'POST',body:{sku,serviceLevel:0.95}}),
  api('/api/probability',{method:'POST',body:{sku,n:500,seed:42}}),
  api('/api/dimensions',{method:'POST',body:{sku}})]);
 state.upLoading=false;
 if(!p.ok&&!q.ok&&!d.ok){toast('后端不可用：升级算法需启动后端服务',true);state.upSku=null;render();return;}
 state.upSku=sku;state.upPolicy=p.ok?p.data:null;state.upProb=q.ok?q.data:null;state.upTier=d.ok?(d.data.priority||{}):null;
 state.upVersion=(p.ok&&p.data.dataVersion)||(q.ok&&q.data.dataVersion)||null;state.upRecovery=null;render();
}
async function fetchRecovery(sku){
 const r=await api('/api/recovery',{method:'POST',body:{sku,scenarioType:'baseline',value:0,horizon:state.horizon}});
 if(!r.ok){toast('反向干预优化失败：'+(r.data.error||'后端不可用'),true);return;}
 state.upRecovery=r.data;render();
}
function resetUpgrade(){state.upSku=null;state.upPolicy=null;state.upProb=null;state.upTier=null;state.upRecovery=null;state.upVersion=null;}
function dataQualityCard(){
 const meta=state.datasetMeta;
 if(!meta)return '';
 const flags=meta.flags||[],missing=meta.missingFields||{};
 return `<div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">数据质量与来源标识</div><div class="card-sub">真实数据 / 历史数据 / 假设数据必须区分</div></div>
 <span class="pill ${state.dataKind==='demo'?'warn':'good'}">${state.dataKind==='demo'?'演示数据（虚构）':state.dataKind==='real-historical'?'真实历史数据':'混合数据'}</span></div>
 <div class="quality-grid"><div class="quality-block"><strong>数据来源</strong><div>${esc(state.source)}</div></div>
 ${meta.snapshotDate?`<div class="quality-block"><strong>库存快照日</strong><div>${esc(meta.snapshotDate)}（历史业务日期，非实时）</div></div>`:''}
 ${meta.units?`<div class="quality-block"><strong>数据规模</strong><div>${num(meta.units)} unit · ${meta.riskSummary?`缺货风险 ${meta.riskSummary.critical} 项`:''}</div></div>`:''}
 ${meta.ordersSimulated?`<div class="quality-block warn"><strong>采购订单</strong><div>${num(state.orders.length)} 条【模拟】订单（原数据无采购数据）</div></div>`:
 `<div class="quality-block warn"><strong>采购订单</strong><div>0 条（原数据无采购数据，延期类测试标记“不适用”）</div></div>`}</div>
 ${flags.length?`<div class="quality-flags">${flags.map(f=>`<div class="flag-line">⚑ ${esc(f)}</div>`).join('')}</div>`:''}
 ${Object.keys(missing).length?`<div class="quality-flags"><div class="small-muted"><strong>缺失字段（已标注，不编造）：</strong>${Object.entries(missing).map(([k,v])=>`${esc(k)}：${esc(v)}`).join('；')}</div></div>`:''}
 </div></div>`;
}
function overview(){const risks=allRisks(),sel=risk(state.selectedSku);
 return header('供应链风险总览','把库存数据变成可解释的风险预警，并找到风险出现前的关键临界点。','RISK COMMAND CENTER',horizonHtml()+btn('⇧ 导入我的数据','to-data','btn-outline')+btn('开始自主压力测试','run-auto','btn-primary'))+
 metricHtml()+`<div class="columns"><div class="card"><div class="card-pad"><div class="card-header"><div><div class="card-headline">物料风险清单</div><div class="card-sub">根据未来 ${state.horizon} 天库存基准推演计算 · 点击物料查看解释</div></div><span class="pill">${risks.length} 项</span></div>${materialTable(risks,state.dataKind==='real-historical'?50:100)}</div></div>
 <div class="card"><div class="card-pad"><div class="card-header"><div class="card-headline">当前选中物料</div><span class="pill good">可追溯计算</span></div>${detail(sel)}</div></div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">库存趋势预测</div><div class="card-sub">${esc(sel.material.name)} · 先收货，再消耗；每天结束时记录剩余库存</div></div><span class="pill">基准情景</span></div>${chartSVG(sel.base,null,'',null,'',sel.material.safety)}<div class="tiny-footer">测算建立在录入的固定日均需求和预计到货基础上，不代表缺货必然发生；没有 BOM 与生产排程时，不将物料缺口直接认定为停产。</div></div></div>
 <div class="phase-strip"><div class="phase"><div class="phase-num">01</div><div><div class="phase-title">导入业务数据</div><div class="phase-desc">物料 / 在途采购 / A.zip</div></div></div><div class="phase"><div class="phase-num">02</div><div><div class="phase-title">查看当前风险</div><div class="phase-desc">覆盖天数 / 预计缺口</div></div></div><div class="phase"><div class="phase-num">03</div><div><div class="phase-title">自主多轮压力测试</div><div class="phase-desc">规则 / 千问 Agent</div></div></div><div class="phase"><div class="phase-num">04</div><div><div class="phase-title">审核并导出</div><div class="phase-desc">报告 / 千问办公</div></div></div></div>`;
}
function dataPage(){
 const msgs=state.importMessages;
 return header('数据工作台','上传业务数据进行真正的计算；支持 .xlsx 多工作表、CSV 文件与 A.zip 真实历史数据集。','DATA FOUNDATION',btn('↻ 恢复演示数据','sample','btn-outline')+btn('Excel 示例格式说明','open-template-note','btn-primary'))+
 `<div class="notice mb20">本工具在<strong>当前浏览器本地</strong>读取和分析上传文件；A.zip 数据集与千问相关功能经由本地后端（${esc(API_BASE)}）处理。如只上传物料表，将清空原采购订单，避免混用演示数据。</div>
 <div class="row-actions mb20">${btn('↓ 导出当前数据为 JSON','export-dataset','btn-outline')}${btn('⇧ 从 JSON 恢复数据','import-dataset','btn-outline')}${btn('⟳ 同步到后端（MCP 可用）','sync-backend','btn-primary')}<span class="filehint">导入数据后刷新页面会丢失，可先导出 JSON 备份；同步后 MCP 工具才能用这份数据</span></div>
 <div class="upload-grid"><div class="card"><div class="card-pad"><div class="card-header"><div class="card-headline">01 · 物料主数据</div><span class="pill">必需</span></div><div class="drop-area" data-action="open-upload"><div class="drop-symbol">⇧</div><div class="drop-title">选择 Excel 或 CSV 文件</div><div class="drop-sub">拖入含“物料”表的 .xlsx 文件，或导入 materials.csv<br>物料编码 · 名称 · 库存 · 日均用量 · 安全库存 · 交期 · 供应商</div></div><div class="mt16 flex-between"><span class="filehint">已加载 ${state.materials.length} 种物料</span>${btn('下载物料 CSV 模板','template-materials','btn-outline btn-sm')}</div></div></div>
 <div class="card"><div class="card-pad"><div class="card-header"><div class="card-headline">02 · 采购 / 在途订单</div><span class="pill">可选</span></div><div class="drop-area" data-action="open-upload"><div class="drop-symbol">⇧</div><div class="drop-title">选择 Excel 或 CSV 文件</div><div class="drop-sub">Excel 可同时含“物料”和“采购订单”两个工作表<br>采购单号 · 物料编码 · 数量 · 预计到货日期 · 供应商 · 状态</div></div><div class="mt16 flex-between"><span class="filehint">已加载 ${state.orders.length} 笔采购单</span>${btn('下载采购单 CSV 模板','template-orders','btn-outline btn-sm')}</div></div></div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">03 · A.zip 真实历史数据集（阿里云基础设施供应链数据）</div><div class="card-sub">632 unit（19 产品 × 81 地理）· 库存快照 2021-03-01 · 训练期至 2021-03-01 · 测试期 2021-03-02~06-07</div></div>
 ${state.dataKind==='real-historical'?'<span class="pill good">已载入</span>':'<span class="pill">未载入</span>'}</div>
 <div class="notice warn mb16">该数据为<strong>历史业务数据</strong>，快照日 2021-03-01 会原样保留，不会冒充实时库存；原始数据<strong>不含采购订单/供应商/到货日期</strong>，延期类测试将提示缺失，或使用明确标记为“模拟”的采购订单。</div>
 <div class="row-actions"><label class="checkbox-line"><input type="checkbox" id="simulate-pos" ${state.aZipInfo&&state.aZipInfo.simulatePos?'checked':''}/> 生成明确标记的“模拟”采购订单（仅用于演示延期测试）</label>
 ${btn('载入 A.zip 数据集','load-a-zip','btn-primary')}${state.dataKind==='real-historical'?btn('运行历史回测（use_test_A 无泄漏评估）','run-backtest','btn-outline'):''}</div>
 ${state.backtest?backtestCard():''}
 </div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">04 · 关系型数据集（inventory-supply-chain v1.0.0 · 合成教学数据）</div><div class="card-sub">2500 产品 × 12 仓 · 250 供应商（含交期）· 1.8 万采购单 · 快照 2025-12-15（数据集最后一笔下单日）· 后端确定性 join+聚合，不依赖大模型清洗</div></div>
 ${state.dataKind==='demo'&&state.relInfo?'<span class="pill good">已载入</span>':'<span class="pill">未载入</span>'}</div>
 <div class="notice mb16">该数据集为<strong>合成教学数据</strong>（README 声明虚构业务），按 demo 性质标注；物料按 product 跨仓聚合，库存=期初+截至快照日流水净变化，日均用量=快照日前 91 天销量均值，<strong>快照日之后的流水不参与计算（无未来泄漏）</strong>；真实采购单来自 open/partial 在途单（2025-12-16~12-31 到货）。</div>
 <div class="row-actions">${btn('载入关系型数据集','load-rel','btn-primary')}</div>
 </div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">05 · 截尾需求实证（FreshRetailNet-50K 公开数据集）</div><div class="card-sub">叮咚买菜生鲜零售基准（CC BY 4.0）· 证明“缺货日销量被库存截断”，并用确定性算法上修潜在需求</div></div>${btn('运行实证分析','censored-run','btn-primary btn-sm')}</div>${censoredCard()}</div></div>
 ${wizardHtml()}
 ${zipScanHtml()}
 ${dataQualityCard()}
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div class="card-headline">导入结果与校验</div><span class="pill">${esc(state.source)}</span></div>
 ${msgs.length?msgs.map(m=>`<div class="notice ${m.type==='error'?'negative':m.type==='warn'?'warn':'success'} mb16">${esc(m.message)}</div>`).join(''):`<div class="notice">当前使用内置演示数据。可以直接体验全流程，也可以导入企业自己的数据或 A.zip 数据集。</div>`}
 <div class="mt16 filehint">支持 UTF-8 CSV；数字应为非负数，采购数量须大于 0；到货日期建议用 YYYY-MM-DD；同一物料编码不能重复。缺少关键字段时拒绝该行并提示，不会凭空猜测。Excel 日期序列号可自动转换。</div></div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">当前数据预览</div><div class="card-sub">${state.materials.length} 种物料 · ${state.orders.length} 条采购记录</div></div>${btn('前往风险分析 →','to-overview','btn-primary btn-sm')}</div>
 <div class="table-wrap"><table class="data-table"><thead><tr><th>物料编码</th><th>物料</th><th>库存</th><th>每日需求</th><th>安全库存</th><th>交期 / 天</th><th>供应商</th></tr></thead><tbody>${state.materials.slice(0,60).map(m=>`<tr><td><strong>${esc(m.sku)}</strong></td><td>${esc(m.name)}</td><td>${num(m.stock)}</td><td>${num(m.usage)}</td><td>${num(m.safety)}</td><td>${num(m.leadTime)}</td><td>${esc(m.supplier||'未提供')}</td></tr>`).join('')}</tbody></table>
 ${state.materials.length>60?`<div class="small-muted mt12">仅显示前 60 项，共 ${state.materials.length} 项（A.zip 数据集全部加载，可用于引擎计算）。</div>`:''}</div>
 <div class="tiny-footer"><strong>业务边界：</strong>已下单与计划采购均按“预计到货日”作条件推演；计划采购尚未确认，不能当作已保证到货。逾期但未确认收到的订单不自动视为入库；历史用量视为未来固定日均需求，仅用于条件推演。A.zip 的交期/安全库存为明确标注的假设值。</div></div></div>`;
}
function censoredCard(){const c=state.censored;
 if(state.censoredLoading)return `<div class="notice mt16">正在分析 35 万行门店时序数据（确定性计算，约几秒）…</div>`;
 if(!c)return `<div class="notice mt16">评估集 eval.parquet 已随项目放入 data/retail/。点上方按钮得到：缺货天数占比、缺货日 vs 不缺货日销量对比、缺货日需求上修幅度分布与实例。该数据不需要也不支持上传导入。</div>`;
 if(!c.available)return `<div class="notice negative mt16">${esc(c.error||'数据不可用')}</div>`;
 const o=c.observedSales;
 return `<div class="chart-summary mt16">
  <span>时间窗 <strong>${esc(c.period[0])} ~ ${esc(c.period[1])}</strong></span>
  <span>缺货天数占比 <strong class="number-negative">${c.stockoutRatioPct}%</strong></span>
  <span>中位上修 <strong class="number-negative">+${c.upliftStats.medianUpliftPct}%</strong></span>
  <span>P90 上修 <strong class="number-negative">+${c.upliftStats.p90UpliftPct}%</strong></span>
  <span>样本 <strong>${c.rows} 行</strong></span></div>
 <div class="notice warn mt16">缺货日观测销量均值 ${o.stockoutDays.mean} ＜ 不缺货日 ${o.normalDays.mean}——库存截断了真实需求；直接用销量均值估计用量会系统性低估。${esc(c.method)}。</div>
 <div class="table-wrap mt16"><table class="data-table"><thead><tr><th>门店</th><th>商品</th><th>日期</th><th>观测销量</th><th>缺货小时</th><th>有货小时</th><th>估算需求</th><th>上修</th></tr></thead>
 <tbody>${c.examples.map(x=>`<tr><td>${x.store}</td><td>${x.product}</td><td>${esc(x.date)}</td><td>${x.observedSale}</td><td class="number-negative">${x.stockoutHours}</td><td>${x.inStockHours}</td><td>${x.imputedDemand}</td><td class="number-negative">+${x.upliftPct}%</td></tr>`).join('')}</tbody></table></div>
 <div class="tiny-footer mt12">${esc(c.note)}</div>`;
}
function backtestCard(){const bt=state.backtest;
 return `<div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">历史回测结果（真实消耗回放）</div><div class="card-sub">用 use_test_A 真实逐日消耗回放 2021-03-01 快照 · ${bt.startDate} ~ ${bt.endDate}（${bt.horizon} 天）· 只用于评估，不参与参数估计（无未来数据泄漏）</div></div><span class="pill ${bt.unitsWithShortage>0?'red':'good'}">${bt.unitsWithShortage}/${bt.unitsEvaluated} 个 unit 出现缺口</span></div>
 <div class="chart-summary"><span>评估 unit <strong>${num(bt.unitsEvaluated)}</strong></span><span>累计缺口 <strong class="number-negative">${num(bt.totalUnmetAll)} 件</strong></span><span>中位覆盖 <strong>约 ${num(state.aZipInfo?state.aZipInfo.medianCoverage:null)} 天</strong></span></div>
 <div class="table-wrap mt16"><table class="data-table"><thead><tr><th>unit</th><th>快照库存</th><th>估计日耗</th><th>实际日均</th><th>首次缺货</th><th>累计缺口</th><th>缺货天数</th></tr></thead>
 <tbody>${bt.results.slice(0,20).map(r=>`<tr><td class="sku-code">${esc(r.sku.slice(0,14))}…</td><td>${num(r.snapshotStock)}</td><td>${num(r.estDailyUsage)}</td><td>${num(r.actualMeanUsage)}</td><td>${esc(r.firstShortage||'未缺货')}</td><td class="${r.totalUnmet>0?'number-negative':''}">${num(r.totalUnmet)}</td><td>${r.shortageDays}</td></tr>`).join('')}</tbody></table></div>
 <div class="tiny-footer">结论：无采购订单数据支撑下，快照库存相对实际消耗极薄（多数 unit 数日内耗尽）。这不是模型预测，而是对真实历史数据的回放；在途库存缺失是数据边界，已在上方质量卡片中标注。</div></div></div>`;}
function fmtThreshold(t,unit){if(!t)return {v:'—',note:'未测试'};
 if(t.status==='found')return {v:`${num(t.threshold)}${unit}`,note:`搜索 ${t.rounds} 轮 · 首次缺货 ${dateShort(t.result.firstShortage)}`};
 if(t.status==='already')return {v:'基线已缺货',note:'已无需额外扰动才能触发缺口'};
 if(t.status==='not-found')return {v:'未触发',note:`测试上限内未在所选期间发现缺口 · ${t.rounds} 轮`};
 return {v:'不适用',note:unit==='天'?'缺少对应的到货或计划采购单':'缺少有效需求'};
}
function thresholdCards(entry){const l=[['deliveryDelay','采购到货可延期','天'],['purchaseDelay','下单可推迟','天'],['demandIncrease','需求可上涨','%']];
 return `<div class="threshold-row">${l.map(([k,name,unit])=>{const t=fmtThreshold(entry.thresholds[k],unit);return `<div class="threshold-card"><div class="threshold-label">${name} · 缺货临界点</div><div class="threshold-number">${esc(t.v)}</div><div class="threshold-context">${esc(t.note)}</div></div>`;}).join('')}</div>`;}
function impactMetrics(base,sim){if(!sim)return '';
 return `<div class="chart-summary"><span>基准累计缺口 <strong>${num(base.totalUnmet)} 件</strong></span><span>当前情景缺口 <strong class="${sim.totalUnmet>base.totalUnmet?'number-negative':''}">${num(sim.totalUnmet)} 件</strong></span><span>情景首次缺货 <strong>${sim.firstShortage||'未发生'}</strong></span></div>`;
}
function interventionHtml(m,scenario,baseKey){const options=E.mitigate(m,state.orders,scenario,{today:today(),horizon:state.horizon});
 if(!options.length)return '<div class="notice">缺少生成预案所需的数据。</div>';
 return `<div class="small-muted mb16">以下是按假设条件重新仿真的<strong>备选处置方案</strong>，未经供应商或客户确认，不能自动执行。审核勾选只用于报告记录。</div>
 ${options.map((o,i)=>{const key=baseKey+':'+o.id;const checked=state.reviewed.has(key);
 return `<div class="mitigation"><div class="mitigate-icon">${['↗','+','≋'][i%3]}</div><div class="mitigation-main"><div class="mitigation-name">${esc(o.title)}</div><div class="mitigation-desc">${esc(o.action)}。${esc(o.note)}</div><div class="mitigation-effect ${o.resolvesShortage?'':'weak'}">${o.dataRequired?'缺少备用供货数据，无法量化该预案':o.resolvesShortage?'模拟结果：当前测试窗口内消除缺口':'模拟结果：仍有缺口'}${o.dataRequired?'':' · 累计缺口改善 '+num(o.improvement)+' 件'}</div></div>
 <label class="review-control"><input type="checkbox" data-review="${esc(key)}" ${checked?'checked':''}/>已审核</label></div>`;}).join('')}`;
}
function traceLine(item,i){let text='';
 if(item.type==='select'||item.type==='decision')text=esc(item.message);
 else if(item.type==='deliveryDelay')text=`到货延迟 <strong>${item.value} 天</strong>`;
 else if(item.type==='purchaseDelay')text=`补货下单推迟 <strong>${item.value} 天</strong>`;
 else if(item.type==='demandIncrease')text=`需求上涨 <strong>${item.value}%</strong>`;
 if(typeof item.failed==='boolean')text+=` → <span class="${item.failed?'trace-bad':'trace-good'}">${item.failed?`触发缺货 ${item.firstShortage} / ${num(item.totalUnmet)}件`:'未缺货'}</span>`;
 return `<div class="trace-item"><span class="trace-index">${String(i+1).padStart(2,'0')}</span><span class="trace-code">${esc(item.sku)}</span><span>${text}</span></div>`;}
function autoPage(){const auto=state.auto;
 const heroButtons=horizonHtml()+btn(auto?'重新执行自主探索':'启动自主探索','run-auto','btn-primary')+btn('✺ 用千问 Agent 执行','to-agent','btn-outline');
 let body=`<div class="card"><div class="card-pad"><div class="card-header"><div><div class="card-headline">自主探索的四步闭环</div><div class="card-sub">不是由大模型随意猜数字；采用确定性库存模拟 + 试探/二分阈值搜索（规则控制器）</div></div><span class="pill good">可复核、可重跑</span></div>
 <div class="agent-flow"><div class="flow-step"><div class="n">STEP 01</div><strong>发现脆弱点</strong><span>基线缺口、安全库存与覆盖天数</span></div><div class="flow-step"><div class="n">STEP 02</div><strong>自主生成扰动</strong><span>到货延期、下单推迟、需求上涨</span></div><div class="flow-step"><div class="n">STEP 03</div><strong>多轮逼近临界点</strong><span>倍增试探 + 整数二分搜索</span></div><div class="flow-step"><div class="n">STEP 04</div><strong>验证干预预案</strong><span>复算首次缺货、缺口与改善</span></div></div>
 <div class="notice">本页为<strong>规则驱动自主探索器</strong>：自动决定先测哪种风险、根据上轮结果改变参数，并寻找阈值。接入千问大模型的版本请打开“千问 Agent”页面；两者共用同一个库存计算引擎。</div></div></div>`;
 if(!auto)body+=`<div class="card section-gap"><div class="card-pad"><div class="empty"><div class="big">◎</div>尚未开始自主探索<br>点击右上角“启动自主探索”，系统将自动运行多轮推演并保留完整测试轨迹。</div></div></div>`;
 else{
  const selected=auto.tested.find(t=>t.risk.material.sku===state.autoSku)||auto.tested[0];
  body+=`<div class="section-gap flex-between"><div><strong style="font-size:15px">压力测试执行摘要</strong><div class="small-muted mt12">${auto.tested.length} 个物料完成扫描 · ${auto.totalRounds} 次计算工具调用 · 期间 ${auto.horizon} 天</div></div><span class="pill good">已完成</span></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div class="card-headline">被自主检视的物料</div><span class="pill">选择物料查看阈值</span></div><div class="row-list">${auto.tested.map(t=>`<button class="chip ${selected.risk.material.sku===t.risk.material.sku?'active':''}" data-action="auto-select" data-sku="${esc(t.risk.material.sku)}">${esc(t.risk.material.name)} ${t.risk.level==='critical'?'· 基线缺货':''}</button>`).join('')}</div></div></div>
 <div class="columns section-gap"><div class="card"><div class="card-pad"><div class="card-header"><div><div class="card-headline">临界点探测结果</div><div class="card-sub">${esc(selected.risk.material.name)} · ${esc(selected.risk.material.sku)}</div></div>${getStatus(selected.risk.level)}</div>
 ${thresholdCards(selected)}<div class="section-gap focus-card"><div class="small-muted">自主生成的重点验证情景</div><div class="focus-metric">${scenarioLabels[selected.focusScenario.type]||'需求变化'} <small>${selected.focusScenario.value}${selected.focusScenario.type==='demandIncrease'?'%':' 天'}</small></div><div class="small-muted">与正常经营基准相比，重新测算首次缺货和累计缺口；结果并非事件发生概率。</div></div>
 <div class="section-gap"><div class="card-headline" style="font-size:13px;margin-bottom:10px">基线 vs. 重点情景</div>${impactMetrics(selected.risk.base,selected.impact)}${chartSVG(selected.risk.base,selected.impact,'自主扰动',null,'',selected.risk.material.safety)}</div></div></div>
 <div class="card"><div class="card-pad"><div class="card-header"><div class="card-headline">针对该风险的备选处置预案</div><span class="pill">人工审核</span></div>${interventionHtml(selected.risk.material,selected.focusScenario,'auto:'+selected.risk.material.sku+':'+selected.focusScenario.type+':'+selected.focusScenario.value)}</div></div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">临界点总览（可视化）</div><div class="card-sub">横向对比所有被检视物料的临界点</div></div></div>${thresholdBars(auto.tested)}</div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">Agent 多轮探索轨迹</div><div class="card-sub">真实记录每次测试的参数及结果，用来证明“上一轮影响下一轮”</div></div><span class="pill">${auto.log.length} 条日志</span></div><div class="trace">${auto.log.map(traceLine).join('')}</div><div class="tiny-footer">算法约束：整数天数与整数百分比；延期最多 30 天，需求上涨最多 200%，搜索窗口为 ${auto.horizon} 天；找到的是当前假设下的<strong>最小触发值</strong>。超过上限或没有对应订单时明确标记“未触发 / 不适用”。</div></div></div>`;
 }
 return header('自主压力测试 Agent','上传业务数据后，不用指定风险假设，由系统主动选目标、设计扰动、搜索临界点，并复算干预效果。','AUTONOMOUS RISK EXPLORATION',heroButtons)+body;
}
function manualPage(){const m=mat(state.manualSku),types=['deliveryDelay','purchaseDelay','demandIncrease','supplierOutage'];
 const isOutage=state.manualType==='supplierOutage';
 const r=state.manualResult&&state.manualResult.sku===m.sku?state.manualResult:null;
 const chosenSuppliers=[...new Set(state.orders.filter(o=>o.sku===m.sku).map(o=>o.supplier))];
 const supplier=state.manualSupplier||chosenSuppliers[0]||m.supplier;
 let scenarios=`<div class="card"><div class="card-pad"><div class="card-header"><div class="card-headline">设置情景参数</div><span class="pill">用户自定义</span></div>
 <div class="field"><label class="field-label">测试物料</label><select class="input" data-field="manualSku">${state.materials.slice(0,200).map(o=>`<option value="${esc(o.sku)}" ${o.sku===m.sku?'selected':''}>${esc(o.name)}（${esc(o.sku)}）</option>`).join('')}</select></div>
 <div class="field"><label class="field-label">压力测试类型</label><select class="input" data-field="manualType">${types.map(t=>`<option value="${t}" ${t===state.manualType?'selected':''}>${scenarioLabels[t]}</option>`).join('')}</select></div>
 ${isOutage?`<div class="field"><label class="field-label">模拟停止交付的供应商</label><select class="input" data-field="manualSupplier">${(chosenSuppliers.length?chosenSuppliers:[m.supplier]).map(s=>`<option ${s===supplier?'selected':''}>${esc(s)}</option>`).join('')}</select></div>`:
 `<div class="field"><label class="field-label">${state.manualType==='demandIncrease'?'需求上涨幅度 (%)':'推迟天数 (天)'}</label><input class="input" type="number" min="0" max="${state.manualType==='demandIncrease'?'500':'120'}" step="1" data-field="manualValue" value="${num(state.manualValue)}" /></div>`}
 <div class="scenario-box"><strong style="font-size:12px">测试说明</strong><div class="small-muted mt12">${{
 deliveryDelay:'使该物料最早一笔已下单采购单的预计到货日向后移动；其余订单不变。',
 purchaseDelay:'使最早一笔“计划”采购单的预计到货日向后移动；若没有计划采购，该情景不适用。',
 demandIncrease:'从测试首日起，整个测试窗口的日均消耗量按相同比例增长。',
 supplierOutage:'假设该供应商的关联在途订单在测试窗口内均无法到货。并不意味着现实中已经断供。'
 }[state.manualType]}</div></div>
 <div class="row-actions">${btn('▶ 运行本情景','run-manual','btn-primary')}${btn('切换自主模式','to-auto','btn-outline')}</div></div></div>`;
 let results=`<div class="card"><div class="card-pad"><div class="card-header"><div class="card-headline">情景计算结果</div><span class="pill">与基准对照</span></div>`;
 if(r){const original=r.baseline,sim=r.sim;
 results+=`<div class="focus-card"><div class="small-muted">${esc(r.description)}</div><div class="focus-metric" style="color:${sim.firstShortage?'#d85c65':'#1d9c90'}">${sim.firstShortage?num(sim.totalUnmet)+' 件缺口':'未出现缺货'}</div><div class="small-muted">${sim.firstShortage?'首次缺货：'+sim.firstShortage:`在未来 ${state.horizon} 天推演内未出现缺货`}</div></div>
 <div class="section-gap">${impactMetrics(original,sim)}${chartSVG(original,sim,'手动情景',null,'',m.safety)}</div>
 <div class="section-gap"><div class="card-headline">备选处置预案</div><div class="mt12">${interventionHtml(m,r.scenario,'manual:'+m.sku+':'+r.scenario.type+':'+(r.scenario.value||r.scenario.supplier))}</div></div>`;
 }else results+=`<div class="empty"><div class="big">⚑</div>参数已就绪<br>点击“运行本情景”，即可对比基准与假设变化后的库存轨迹。</div>`;
 results+='</div></div>';
 return header('手动情景模拟','你来提出风险假设，计算引擎会使用与自主模式完全相同的库存逻辑进行复算。','SCENARIO SANDBOX',horizonHtml())+
 `<div class="columns manual-columns">${scenarios}${results}</div>
 <div class="notice warn mt20">说明：手动情景是<strong>条件推演</strong>，不代表供应商必然延期或断供，也不构成采购指令。现实应急措施仍需确认采购价格、供货能力、实际交期及客户承诺。</div>`;
}
// ============================ 千问 Agent 决策面板 ============================
function agentStatusPill(){
 if(!state.qwenStatus)return '<span class="pill warn">后端未连接 · 状态未知</span>';
 return `<span class="pill ${state.qwenStatus.configured?'good':'warn'}">${esc(state.qwenStatus.modeLabel)}</span>`;
}
function agentRoundHtml(round){
 const calls=(round.toolCalls||[]).map(c=>{
  const argsTxt=esc(JSON.stringify(c.args||{}));
  let resTxt='';
  if(c.ok){
   const r=c.result||{};
   if(c.tool==='search_threshold'&&typeof r.failed==='boolean'){
    resTxt=`<span class="${r.failed?'trace-bad':'trace-good'}">${r.failed?('触发缺货 '+esc(r.firstShortage||'')+' / 累计 '+num(r.totalUnmet)+' 件'):'未缺货'}</span>`;
   }else if(c.tool==='search_threshold'&&r.status){
    resTxt=`<span class="${r.status==='found'?'trace-good':'trace-neutral'}">${r.status==='found'?('临界点 '+num(r.threshold)+'（'+r.rounds+' 轮）'):esc(r.status)}</span>`;
   }else if(c.tool==='get_risk_list'){
    resTxt=`<span class="trace-good">critical ${r.counts?r.counts.critical:'—'} / watch ${r.counts?r.counts.watch:'—'}</span>`;
   }else if(c.tool==='query_material'){
    resTxt=`<span class="trace-good">库存 ${num(r.stock)} · 覆盖 ${num(r.coverageDays)} 天 · ${esc(r.baselineLevel)}</span>`;
   }else if(c.tool==='run_simulation'){
    const rr=r.result||r;resTxt=`<span class="${rr.firstShortage?'trace-bad':'trace-good'}">首次缺货 ${esc(rr.firstShortage||'无')} · 缺口 ${num(rr.totalUnmet)}</span>`;
   }else if(c.tool==='compare_interventions'){
    resTxt=`<span class="trace-good">${r.interventions?r.interventions.length:0} 个预案</span>`;
   }else if(c.tool==='explain'){
    resTxt=`<span class="trace-good">${esc((r.explanation||'').slice(0,60))}…</span>`;
   }
  }else resTxt=`<span class="trace-bad">错误：${esc(c.error||'')}</span>`;
  return `<div class="agent-call"><div class="ac-head"><span class="ac-tool">🔧 ${esc(c.tool)}</span><code class="ac-args">${argsTxt}</code></div>
   <div class="ac-result">${resTxt}${c.ok&&c.result&&c.tool!=='search_threshold'?`<details class="ac-detail"><summary>查看完整结果</summary><pre>${esc(JSON.stringify(c.result,null,1).slice(0,1500))}</pre></details>`:''}</div></div>`;
 }).join('');
 return `<div class="agent-round"><div class="ar-head"><span class="ar-num">第 ${round.round} 轮</span><span class="pill sm">${esc(round.stage||'plan')}</span></div>
 <div class="ar-reasoning">${esc(round.reasoning||'（无文字说明，直接调用工具）')}</div>
 ${round.nextStep?`<div class="ar-next">⟳ 下一轮调整依据：${esc(round.nextStep)}</div>`:''}
 ${calls?`<div class="ar-calls">${calls}</div>`:''}</div>`;
}
function agentConclusionHtml(){
 const agent=state.agent;if(!agent)return '';
 const c=agent.conclusion||{};
 const frags=(c.mostFragile||[]).map(f=>`<div class="conc-frag"><strong>${esc(f.sku)}</strong><span class="status ${esc(f.level)}">${riskLabels[f.level]||f.level}</span><div class="small-muted">覆盖 ${num(f.coverageDays)} 天 · 首次缺货 ${esc(f.firstShortage||'—')} · 缺口 ${num(f.totalUnmet)} 件</div></div>`).join('')||'<div class="empty">无风险清单结果</div>';
 const thrs=(c.thresholdsFound||[]).map(t=>`<tr><td>${esc(t.sku)}</td><td>${esc(scenarioLabels[t.scenarioType]||t.scenarioType)}</td><td>${t.status==='found'?num(t.threshold)+(t.scenarioType==='demandIncrease'?'%':' 天'):esc(t.status)}</td><td>${t.rounds} 轮</td></tr>`).join('');
 const inters=Object.entries(c.interventionsTested||{}).map(([sku,list])=>list.map(o=>`<div class="mitigation"><div class="mitigate-icon">↗</div><div class="mitigation-main"><div class="mitigation-name">${esc(o.title)}</div><div class="mitigation-effect ${o.resolvesShortage?'':'weak'}">${esc(sku)} · 改善 ${num(o.improvementUnits)} 件 · ${o.resolvesShortage?'消除缺口':'仍有缺口'}</div></div><label class="review-control"><input type="checkbox" data-review="${esc('agent:'+sku+':'+o.id)}" ${state.reviewed.has('agent:'+sku+':'+o.id)?'checked':''}/>已审核</label></div>`).join('')).join('');
 return `<div class="conc-grid">
 <div class="card"><div class="card-pad"><div class="card-headline">最脆弱物料（来自 get_risk_list 工具）</div>${frags}</div></div>
 <div class="card"><div class="card-pad"><div class="card-headline">发现的临界点（来自 search_threshold 工具）</div>${thrs?`<div class="table-wrap mt12"><table class="data-table"><thead><tr><th>物料</th><th>扰动类型</th><th>临界点</th><th>轮数</th></tr></thead><tbody>${thrs}</tbody></table></div>`:'<div class="empty sm">未找到临界点</div>'}</div></div>
 <div class="card"><div class="card-pad"><div class="card-headline">干预方案（来自 compare_interventions 工具 · 待人工审核）</div>${inters||'<div class="empty sm">未比较干预方案</div>'}</div></div></div>`;
}
function agentPage(){
 const agent=state.agent;
 const statusLine=state.qwenStatus?`${state.qwenStatus.modeLabel} · 模型 ${state.qwenStatus.model||'—'} · Key ${state.qwenStatus.apiKeyHint}`:'后端未连接';
 return header('千问 Agent 决策面板','千问大模型负责“测什么、下一轮怎么调、何时停止、如何解释”，所有库存数字由确定性引擎工具计算。','QWEN AGENT ORCHESTRATION',
  agentStatusPill()+btn(agent?'重新运行':'启动千问 Agent','run-agent','btn-primary'))+
 `<div class="card"><div class="card-pad"><div class="card-header"><div><div class="card-headline">运行配置</div><div class="card-sub">${esc(statusLine)}</div></div><span class="pill sm">规划 → 调用工具 → 观察结果 → 调整计划</span></div>
 <div class="agent-form">
 <div class="field"><label class="field-label">任务指令（发送给千问）</label><textarea class="input" rows="3" id="agent-goal">${esc(state.agentGoal)}</textarea></div>
 <div class="agent-params"><div class="field"><label class="field-label">最大轮数</label><input class="input" type="number" min="1" max="20" id="p-maxRounds" value="${state.agentParams.maxRounds}"/></div>
 <div class="field"><label class="field-label">最大工具调用</label><input class="input" type="number" min="1" max="100" id="p-maxToolCalls" value="${state.agentParams.maxToolCalls}"/></div>
 <div class="field"><label class="field-label">总超时(秒)</label><input class="input" type="number" min="10" max="600" id="p-timeoutSec" value="${state.agentParams.timeoutSec}"/></div>
 <div class="field"><label class="field-label">Token 预算</label><input class="input" type="number" min="1000" max="200000" id="p-budgetTokens" value="${state.agentParams.budgetTokens}"/></div></div>
 <div class="notice warn">未配置千问 API Key 时，系统自动切换为<strong>明确标记的规则演示模式</strong>（内置规则控制器 + 同一引擎），页面会显著标明模式差异。API Key 只保存在后端环境变量，不会下发到浏览器。</div></div></div>`;
}
function agentResultHtml(){
 const agent=state.agent;if(!agent)return `<div class="card section-gap"><div class="card-pad"><div class="empty"><div class="big">✺</div>尚未运行<br>点击“启动千问 Agent”，观察规划、工具调用与结论的完整闭环。</div></div></div>`;
 const u=agent.usage||{};
 const sat=agent.constraintsSatisfied||{};
 return `<div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">执行摘要</div><div class="card-sub">${esc(agent.modeLabel)}</div></div>
 <span class="pill ${agent.error?'red':'good'}">${agent.error?'发生错误（已降级）':'已完成'}</span></div>
 <div class="chart-summary"><span>规划轮次 <strong>${num(u.roundsUsed)}/${num(u.maxRounds)}</strong></span><span>工具调用 <strong>${num(u.toolCalls)}/${num(u.maxToolCalls)}</strong></span><span>Token 估计 <strong>${num(u.tokensEstimate)}/${num(u.budgetTokens)}</strong></span><span>耗时 <strong>${num(u.durationMs)} ms</strong></span></div>
 <div class="constraint-row">${[['roundsOk','轮数未超限'],['toolCallsOk','调用未超限'],['budgetOk','预算未超限'],['timeoutOk','未超时']].map(([k,l])=>`<span class="constraint ${sat[k]?'pass':'fail'}">${sat[k]?'✓':'✗'} ${l}</span>`).join('')}</div>
 ${agent.error?`<div class="notice negative mt12">运行错误：${esc(agent.error)}。已自动降级为规则演示模式完成计算，结果仍然可信（确定性引擎）。</div>`:''}</div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">逐轮决策与工具调用轨迹</div><div class="card-sub">每轮展示：模型推理 → 调用哪些工具 → 返回什么结果 → 下一轮如何调整</div></div><span class="pill">${(agent.rounds||[]).length} 轮</span></div>
 <div class="agent-timeline">${(agent.rounds||[]).map(agentRoundHtml).join('')}</div>
 ${agent.finalText?`<div class="insight mt16"><strong>模型最终总结（文字由模型生成，数字以工具记录为准）</strong><div class="mt12">${esc(agent.finalText)}</div></div>`:''}</div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">结构化结论（只汇总工具结果，可审计）</div><div class="card-sub">${esc((agent.conclusion||{}).note||'')}</div></div><span class="pill good">数据有据</span></div>${agentConclusionHtml()}</div></div>`;
}
// ============================ 千问办公协作 ============================
function collabPage(){
 const sampleSku=state.materials[0]?.sku||'M-001';
 const MCP_TOOLS=[['get_inventory_risk_list','库存风险清单','{}'],['query_material','查询重点物料',JSON.stringify({sku:sampleSku})],
  ['run_autonomous_stress_test','自主压力测试',JSON.stringify({limit_skus:5,horizon:state.horizon})],
  ['query_risk_threshold','风险临界点',JSON.stringify({sku:sampleSku,scenario_type:'deliveryDelay'})],
  ['compare_interventions','比较干预方案',JSON.stringify({sku:sampleSku,scenario_type:'deliveryDelay',value:3})],
  ['get_agent_tool_logs','工具调用记录','{"limit":20}'],
  ['export_analysis_report','导出分析报告','{"format":"xlsx"}']];
 const testResults=state.collab.toolResults;
 const taskText='分析当前库存风险，找出最脆弱的物料，自动运行压力测试，推荐应对方案，并生成供应链风险分析报告。';
 const backendDataInfo=state.backend?`后端当前数据：${state.backend.dataset?.materials||'?'} 种物料 / ${state.backend.dataset?.orders||'?'} 条采购单（${esc(state.backend.dataset?.source||'未知')}）`:'后端未连接';
 return header('千问办公协作','千问办公 ≠ 千问大模型 API：本模块提供可运行的 ChainCoxswain MCP Server 与专属 Skill，让千问办公真正参与业务工作流并产出 Excel/Word/PPT。','QWENWORK COLLABORATION',
  btn('导出任务包','export-package','btn-outline')+btn('生成 Excel 报告','gen-report-xlsx','btn-primary'))+
 `<div class="collab-status-grid"><div class="card"><div class="card-pad"><div class="card-headline">ChainCoxswain MCP Server</div>
 <div class="collab-status"><span class="dot ok"></span>本地实现已交付（stdio + HTTP 桥接）</div>
 <div class="small-muted mt12">13 个工具：风险清单 / 物料查询 / 自主压力测试 / 临界点 / 干预对比 / 缺货概率 / 动态政策 / 反向干预 / 联合临界点 / 六维风险 / 分仓风险 / 工具日志 / 报告导出。数字全部来自确定性引擎或标注的统计推演。</div>
 <div class="small-muted mt12"><strong>${backendDataInfo}</strong></div>
 <div class="small-muted mt12">MCP 端点（千问办公配置此地址）：<code class="code-block">POST ${esc(API_BASE)}/api/mcp</code></div>
 <div class="small-muted mt12" style="color:#c0392b">⚠ 请勿使用 stdio 模式——stdio 创建独立服务实例，无法读取前端同步的数据集。HTTP 桥接与后端共享同一内存服务，同步后立即可用。</div></div></div>
 <div class="card"><div class="card-pad"><div class="card-headline">千问办公客户端联调</div>
 <div class="collab-status"><span class="dot warn"></span>真实客户端联调尚未完成（需千问办公账号授权）</div>
 <div class="small-muted mt12">本页以下能力均已在本机通过自动化测试（MCP JSON-RPC 全流程、13 个工具逐一调用、报告生成），但尚未在真实千问办公客户端中完成端到端联调，不谎称“已连接成功”。</div></div></div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">MCP 工具本地验证</div><div class="card-sub">基于<strong>后端已加载数据</strong>验证（前端示例物料 ${esc(sampleSku)}）· 每个按钮向 MCP 桥接端点发起真实调用并展示 JSON 结果</div></div>
 ${state.backend?'<span class="pill good">桥接在线</span>':'<span class="pill warn">后端未启动（需先运行后端）</span>'}</div>
 <div class="notice warn mb16"><strong>注意：</strong>MCP 工具在后端运行，使用的是后端加载的数据集（见上方"后端当前数据"），不是前端当前显示的数据。如需测试前端导入的数据，请先在后端载入对应数据集。</div>
 <div class="tool-test-grid">${MCP_TOOLS.map(([name,label,args])=>`<div class="tool-test"><div class="tt-head"><strong>${esc(label)}</strong><code>${esc(name)}</code></div>
 <div class="tt-actions">${btn('▶ 调用','test-tool','btn-outline btn-sm',`data-tool="${name}" data-args="${esc(args)}"`)}${testResults[name]?`<span class="pill sm ${testResults[name].ok?'good':'red'}">${testResults[name].ok?'成功':'失败'}</span>`:''}</div>
 ${testResults[name]?`<pre class="tt-result">${esc(testResults[name].text)}</pre>`:''}</div>`).join('')}</div></div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">千问办公任务指令</div><div class="card-sub">复制后粘贴到千问办公，配合已接入的 ChainCoxswain MCP 与 Skill 使用</div></div>${btn('复制指令','copy-task','btn-outline btn-sm')}</div>
 <textarea class="input task-box" rows="2" id="task-instruction">${esc(taskText)}</textarea>
 <div class="small-muted mt12">Skill 目录：<code class="code-block">qwen-office-skill/</code>（SKILL.md + SOP + 数据字典 + 报告模板 + 示例任务）</div></div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">业务成果导出（后端真实生成，可编辑）</div><div class="card-sub">Excel 风险表（4 个工作表）/ Word 分析报告 / PPT 汇报材料 / HTML</div></div></div>
 <div class="row-actions">${[['xlsx','Excel 风险表'],['docx','Word 报告'],['pptx','PPT 汇报'],['html','HTML 报告']].map(([f,l])=>btn(l,'gen-report-'+f,'btn-outline')).join('')}</div>
 <div class="tiny-footer mt12">所有业务动作（下单、通知供应商、调整库存）本版本均<strong>不自动执行</strong>，只生成供人工审核的分析与建议。</div></div></div>
 <div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">任务包说明</div><span class="pill sm">供千问办公离线导入</span></div></div>
 <div class="small-muted">“导出任务包”会下载 JSON 文件，包含：任务指令、数据来源与假设、工具 schema、授权边界（只读分析+导出，业务动作需人工确认）。可直接作为千问办公任务的上下文导入。</div></div></div>`;
}
// ============================ 对照实验 ============================
function comparePage(){
 const cmp=state.compare;
 const metricRow=(name,m)=>{const g=m[name];return g?`<td>${g.riskDetectionRate!==null&&g.riskDetectionRate!==undefined?(g.riskDetectionRate*100).toFixed(0)+'%':'—'}</td>
 <td>${g.thresholdErrorMean!==null&&g.thresholdErrorMean!==undefined?(g.thresholdErrorMean===0?'0（精确）':g.thresholdErrorMean):'不提供临界点'}</td>
 <td>${g.toolCalls}</td><td>${g.durationMs} ms</td>
 <td>${g.constraintSatisfaction===1?'100%':g.constraintSatisfaction}</td><td>${g.reproducibility===1?'可复现':g.reproducibility}</td>`:''};
 const groupCards=cmp?cmp.groups.map(g=>`<div class="card"><div class="card-pad"><div class="card-headline">${esc(g.name)}</div>
 <div class="chart-summary"><span>风险发现率 <strong>${cmp.metrics[g.name].riskDetectionRate!==null?((cmp.metrics[g.name].riskDetectionRate*100).toFixed(0)+'%'):'—'}</strong></span><span>工具调用 <strong>${g.toolCalls}</strong></span><span>临界点误差 <strong>${cmp.metrics[g.name].thresholdErrorMean!==null?cmp.metrics[g.name].thresholdErrorMean:'不提供'}</strong></span></div>
 ${g.mode?`<div class="small-muted mt12">模式：${esc(g.modeLabel||g.mode)}（${g.mode==='rule-demo'||g.mode==='rule-demo-fallback'?'规则演示，非真实千问':'真实千问 API'}）</div>`:''}
 <div class="small-muted mt12">${esc(g.note)}</div></div></div>`).join(''):'';
 return header('四组对照实验','同样的数据与计算预算下，比较传统静态预警、固定参数测试、规则自适应与千问 Agent 驱动的风险发现能力与临界点精度。','CONTROLLED COMPARISON',
  btn(cmp?'重新运行实验':'运行对照实验','run-compare','btn-primary'))+
 `<div class="card"><div class="card-pad"><div class="card-header"><div><div class="card-headline">实验设计</div><div class="card-sub">穷举搜索（延期 1~30 天、需求 1~200% 逐点）作为真实临界点基准；四组在相同数据上运行</div></div><span class="pill">公平对照</span></div>
 <div class="notice">G1 静态预警只做基线推演；G2 固定“延期 3 天”单次测试；G3 规则自适应（倍增+二分）；G4 千问 Agent（Function Calling 闭环）。G1/G2 不产出临界点，其“临界点误差”一栏为空；G4 在无 API Key 时为明确标记的规则演示模式。</div>
 ${cmp?`<div class="table-wrap mt16"><table class="data-table"><thead><tr><th>方法</th><th>风险发现率</th><th>临界点平均误差</th><th>工具调用次数</th><th>耗时</th><th>约束满足率</th><th>可复现性</th></tr></thead>
 <tbody>${cmp.groups.map(g=>`<tr><td><strong>${esc(g.name)}</strong>${g.mode&&g.mode.startsWith('rule')?`<div class="sku-code">${esc(g.modeLabel||g.mode)}</div>`:''}</td>${metricRow(g.name,cmp.metrics)}</tr>`).join('')}</tbody></table></div>
 <div class="section-gap"><div class="card-headline" style="font-size:13px;margin-bottom:10px">风险发现率对比</div>
 ${cmp.groups.map(g=>{const v=cmp.metrics[g.name].riskDetectionRate;return `<div class="cmp-bar"><div class="cmp-bar-label">${esc(g.name)}</div><div class="cmp-bar-track"><div class="cmp-bar-fill" style="width:${v!==null?v*100:0}%"></div></div><div class="cmp-bar-val">${v!==null?(v*100).toFixed(0)+'%':'—'}</div></div>`;}).join('')}</div>
 <div class="section-gap"><div class="card-headline" style="font-size:13px;margin-bottom:10px">穷举基准（真实临界点）</div>
 <div class="table-wrap mt12"><table class="data-table"><thead><tr><th>物料</th><th>到货延期(天)</th><th>下单推迟(天)</th><th>需求上涨(%)</th><th>是否脆弱</th></tr></thead><tbody>${Object.entries(cmp.groundTruth).map(([sku,gt])=>`<tr><td>${esc(sku)}</td><td>${gt.deliveryDelay!==null?gt.deliveryDelay:'—'}</td><td>${gt.purchaseDelay!==null?gt.purchaseDelay:'—'}</td><td>${gt.demandIncrease!==null?gt.demandIncrease:'—'}</td><td>${cmp.fragile[sku]?'是':'否'}</td></tr>`).join('')}</tbody></table></div></div>
 <div class="tiny-footer">${esc(cmp.note)}</div>`:`<div class="empty mt16"><div class="big">≣</div>点击“运行对照实验”，系统将在当前数据集上跑完全部四组并输出对比表。</div>`}
 </div></div>`;
}
// ============================ 报告 ============================
function reportPage(){const risks=allRisks();const shortage=risks.filter(r=>r.level==='critical');const selected=state.auto?.tested.find(t=>t.risk.material.sku===state.autoSku)||state.auto?.tested[0];
 const ranked=risks.slice(0,8);
 return header('风险评估报告','汇总当前库存风险、临界点试验、干预备选方案与用户审核记录，形成可导出的比赛展示材料。','RISK INTELLIGENCE REPORT',
  btn('导出风险 CSV','export-csv','btn-outline')+btn('Excel/Word/PPT（后端）','to-collab','btn-outline')+btn('↓ 下载 HTML 报告','export-html','btn-primary'))+
 `<div class="report-paper"><div class="flex-between"><div><div class="report-label">CHAINCOXSWAIN / RISK ASSESSMENT</div><div class="report-title">供应链库存风险与压力测试报告</div><div class="report-sub">生成日期：${today()} · 数据来源：${esc(state.source)} · 测试期间：${state.horizon} 天</div></div><span class="pill good">草稿 · 待业务复核</span></div>
 <div class="report-grid"><div class="report-kpi"><span>分析物料总量</span><strong>${risks.length}</strong></div><div class="report-kpi"><span>基线预计缺货</span><strong>${shortage.length}</strong></div><div class="report-kpi"><span>自主仿真轮次</span><strong>${state.auto?.totalRounds||0}</strong></div></div>
 <div class="card-headline">一、当前风险发现</div>${ranked.map(r=>`<div class="report-row"><div class="flex-between"><h4>${esc(r.material.sku)} · ${esc(r.material.name)}</h4>${getStatus(r.level)}</div><p>${esc(r.reason)}</p></div>`).join('')}
 <div class="section-gap"><div class="card-headline">二、自主压力测试结论</div>${!state.auto?`<div class="notice mt12">尚未运行自主探索。运行后将自动纳入本节和下载报告。</div>`:
 `<div class="small-muted mt12">本次共 ${state.auto.totalRounds} 轮试探，覆盖 ${state.auto.tested.length} 个物料。下表中的“临界点”表示首次在条件测试内触发缺货的最小整数扰动。</div><div class="table-wrap mt16"><table class="data-table"><thead><tr><th>物料</th><th>到货延期</th><th>下单推迟</th><th>需求上涨</th><th>基线风险</th></tr></thead><tbody>${state.auto.tested.map(t=>`<tr><td>${esc(t.risk.material.name)}</td><td>${esc(fmtThreshold(t.thresholds.deliveryDelay,'天').v)}</td><td>${esc(fmtThreshold(t.thresholds.purchaseDelay,'天').v)}</td><td>${esc(fmtThreshold(t.thresholds.demandIncrease,'%').v)}</td><td>${getStatus(t.risk.level)}</td></tr>`).join('')}</tbody></table></div>`}</div>
 ${selected?`<div class="section-gap"><div class="card-headline">三、重点情景 / 处置方案</div><div class="small-muted mt12">${esc(selected.risk.material.name)} · ${scenarioLabels[selected.focusScenario.type]||''} ${num(selected.focusScenario.value)}${selected.focusScenario.type==='demandIncrease'?'%':'天'} · 累计缺口 ${num(selected.impact.totalUnmet)} 件</div><div class="mt12">${interventionHtml(selected.risk.material,selected.focusScenario,'auto:'+selected.risk.material.sku+':'+selected.focusScenario.type+':'+selected.focusScenario.value)}</div></div>`:''}
 ${state.agent?`<div class="section-gap"><div class="card-headline">${selected?'四':'三'}、千问 Agent 结论</div><div class="small-muted mt12">模式：${esc(state.agent.modeLabel)} · ${num((state.agent.usage||{}).toolCalls)} 次工具调用 · ${num((state.agent.usage||{}).roundsUsed)} 轮</div><div class="mt12">${(state.agent.conclusion?.thresholdsFound||[]).slice(0,8).map(t=>`<div class="report-row"><p>${esc(t.sku)} · ${esc(scenarioLabels[t.scenarioType]||t.scenarioType)}：临界点 ${t.status==='found'?num(t.threshold)+(t.scenarioType==='demandIncrease'?'%':' 天'):esc(t.status)}（${t.rounds} 轮搜索）</p></div>`).join('')||'<div class="notice">Agent 未找到临界点或未运行。</div>'}</div></div>`:''}
 <div class="section-gap"><div class="card-headline">${state.agent&&selected?'五':(selected?'四':'三')}、模型说明与边界</div><div class="small-muted mt12">① 收货在每日消耗前发生，采购到货日期均按输入记录作为条件假设；② 日均消耗在窗口内恒定，需求激增情景通过固定倍率放大；③ 缺货数量由逐日仿真而非语言模型估算；④ 逾期订单不自动当作已收到；⑤ 未知概率、BOM、生产计划、供应商真实能力及跨仓调拨未纳入，不把模拟物料缺口等同于确定停产；⑥ 所有处置建议必须由业务人员审核后执行。</div></div>
 <div class="tiny-footer">本报告属于决策辅助产物。未覆盖生产排产、质量检验、运输签收及逆向物流等全流程。</div></div>`;
}
// ============================ render & events ============================
function render(){
 if(!state.materials.some(m=>m.sku===state.selectedSku))state.selectedSku=state.materials[0]?.sku;
 if(!state.materials.some(m=>m.sku===state.manualSku))state.manualSku=state.materials[0]?.sku;
 const screen={overview,data:dataPage,auto:autoPage,manual:manualPage,agent:agentPage,collab:collabPage,compare:comparePage,report:reportPage}[state.section];
 const agentBody=state.section==='agent'?agentPage()+agentResultHtml():'';
 document.getElementById('root').innerHTML=`<div class="layout">${navHtml()}<div class="workspace">${topHtml()}<div class="main">${screen?screen():overview()}${agentBody}<div class="footer-note">链舵 ChainCoxswain · 比赛产品原型 v2.0　|　本地引擎离线可用；千问与千问办公功能需后端　|　所有业务动作需人工审核</div></div></div></div><input type="file" id="file-upload" multiple accept=".csv,.xlsx" hidden><input type="file" id="wizard-upload" accept=".csv,.xlsx,.zip" hidden><input type="file" id="dataset-json-upload" accept=".json" hidden><div class="toast-area" id="toast-area" aria-live="polite"></div>`;
}
// ===== File readers (unchanged from v1) =====
function parseCSV(text){
 text=text.replace(/^\uFEFF/,'');
 let delimiter=',';const firstLine=text.split(/\r?\n/).find(s=>s.trim())||'';
 if((firstLine.match(/\t/g)||[]).length>(firstLine.match(/,/g)||[]).length)delimiter='\t';
 else if((firstLine.match(/;/g)||[]).length>(firstLine.match(/,/g)||[]).length)delimiter=';';
 const arr=[],row=[];let col='',quoted=false;
 for(let i=0;i<text.length;i++){
  const c=text[i];
  if(quoted){if(c==='"'&&text[i+1]==='"'){col+='"';i++;}else if(c==='"')quoted=false;else col+=c;}
  else if(c==='"')quoted=true;
  else if(c===delimiter){row.push(col);col='';}
  else if(c==='\r'||c==='\n'){if(c==='\r'&&text[i+1]==='\n')i++;row.push(col);if(row.some(x=>x.trim()))arr.push([...row]);row.length=0;col='';}
  else col+=c;
 }
 if(quoted)throw new Error('CSV 的引号没有闭合');
 row.push(col);if(row.some(x=>x.trim()))arr.push(row);
 if(!arr.length)return [];
 const headers=arr[0].map(x=>x.trim());
 return arr.slice(1).map((r,i)=>Object.fromEntries(headers.map((h,j)=>[h,(r[j]??'').trim()])));
}
function xmlDoc(text){const doc=new DOMParser().parseFromString(text,'text/xml');
 if(doc.querySelector('parsererror'))throw new Error('Excel 文件中的 XML 解析失败');return doc;}
function xmlNodes(parent,name){return Array.from(parent.getElementsByTagNameNS('*',name));}
function lettersToIndex(s){let n=0;for(const c of s)n=n*26+(c.charCodeAt(0)-64);return n-1;}
async function parseXLSX(buffer){
 if(!window.JSZip)throw new Error('Excel 解析组件未加载，请使用打包文件完整打开');
 const zip=await JSZip.loadAsync(buffer);
 const workbook=zip.file('xl/workbook.xml');
 if(!workbook)throw new Error('不是有效的 .xlsx 工作簿（缺少 workbook.xml）');
 const wd=xmlDoc(await workbook.async('string'));
 const sharedFile=zip.file('xl/sharedStrings.xml');
 const shared=sharedFile?xmlNodes(xmlDoc(await sharedFile.async('string')),'si').map(n=>n.textContent):[];
 const relFile=zip.file('xl/_rels/workbook.xml.rels');
 const relMap=new Map();
 if(relFile){for(const n of xmlNodes(xmlDoc(await relFile.async('string')),'Relationship')){
  let path=n.getAttribute('Target')||'';
  path=path.startsWith('/')?path.slice(1):'xl/'+path.replace(/^\.\//,'');
  relMap.set(n.getAttribute('Id'),path);
 }}
 const sheets=xmlNodes(wd,'sheet');const output=[];
 for(let si=0;si<sheets.length;si++){
  const s=sheets[si],rid=s.getAttribute('r:id')||s.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships','id');
  const path=relMap.get(rid)||`xl/worksheets/sheet${si+1}.xml`;
  const file=zip.file(path);
  if(!file)continue;
  const doc=xmlDoc(await file.async('string'));let rows=[];
  for(const rn of xmlNodes(doc,'row')){
   const row=[];
   for(const c of xmlNodes(rn,'c')){
    const addr=c.getAttribute('r')||'';
    const letter=addr.match(/^[A-Z]+/)?.[0];if(!letter)continue;
    const t=c.getAttribute('t')||'n',v=xmlNodes(c,'v')[0];
    let val=v?.textContent??'';
    if(t==='s')val=shared[Number(val)]??'';
    if(t==='inlineStr'){const is=xmlNodes(c,'is')[0];val=is?is.textContent:'';}
    if(t==='b')val=val==='1'?'TRUE':'FALSE';
    row[lettersToIndex(letter)]=String(val).trim();
   }
   if(row.some(x=>x!==undefined&&x!==''))rows.push(row);
  }
  if(!rows.length)continue;
  const header=rows[0].map(x=>String(x??'').trim());
  const records=rows.slice(1).filter(r=>r.some(x=>x!==undefined&&x!==''))
    .map(r=>Object.fromEntries(header.filter(Boolean).map((h,i)=>[h,String(r[i]??'').trim()])));
  output.push({name:s.getAttribute('name')||'Sheet '+(si+1),records});
 }
 return output;
}
const norm=s=>String(s||'').trim().toLowerCase().replace(/[\s_\-():：\/（）]/g,'');
const ALIASES={
 sku:['SKU','物料编码','物料编号','商品编码','料号','物料代码'],
 name:['name','物料名称','产品名称','品名'],
 stock:['stock','当前库存','现有库存','库存数量','库存'],
 usage:['usage','dailyusage','日均用量','每日消耗','日均消耗','每日需求','每日用量'],
 safety:['safety','safetystock','安全库存','安全库存数量'],
 leadTime:['leadtime','leadtimedays','采购交期天','交期天','采购交期','供应交期','交期'],
 supplier:['supplier','供应商','供应商名称'],
 unitCost:['unitcost','单价','单位成本','采购单价'],
 id:['id','orderid','采购单号','订单号','采购订单号'],
 quantity:['quantity','qty','采购数量','订单数量','数量'],
 arrival:['arrival','arrivaldate','eta','预计到货日期','预计到货','交货日期','到货日期'],
 status:['status','订单状态','状态']
};
function valueFrom(record,key){const normalized=new Map(Object.keys(record).map(k=>[norm(k),record[k]]));
 for(const alias of ALIASES[key])if(normalized.has(norm(alias)))return nonempty(normalized.get(norm(alias)));
 return '';
}
function roleOf(record,sheetName=''){if(!record)return '';
 if(valueFrom(record,'sku')&&valueFrom(record,'stock')!=='')return 'materials';
 if(valueFrom(record,'sku')&&valueFrom(record,'arrival'))return 'orders';
 const keys=new Set(Object.keys(record).map(norm));
 const has=k=>ALIASES[k].some(a=>keys.has(norm(a)));
 if(has('sku')&&has('stock'))return 'materials';
 if(has('sku')&&has('quantity')&&has('arrival'))return 'orders';
 if(/物料|库存|material/i.test(sheetName))return 'materials';
 if(/采购|订单|purchase/i.test(sheetName))return 'orders';
 return '';
}
function numeric(v,field,row,issues,allowZero=true){
 const str=String(v??'').replace(/,/g,'').trim();const n=Number(str);
 if(str===''||!Number.isFinite(n)||n<0||(!allowZero&&n===0)){issues.push(`第 ${row} 行“${field}”不是有效的${allowZero?'非负':'正'}数`);return null;}
 return n;
}
function excelDate(value){
 if(!value)return '';
 let v=String(value).trim();
 if(/^\d{4}[\/-]\d{1,2}[\/-]\d{1,2}/.test(v)){
  const hit=v.match(/^(\d{4})[\/-](\d{1,2})[\/-](\d{1,2})/);
  const candidate=hit[1]+'-'+hit[2].padStart(2,'0')+'-'+hit[3].padStart(2,'0');
  return Number.isFinite(E.dayNumber(candidate))?candidate:'';
 }
 if(/^\d+(\.\d+)?$/.test(v)&&Number(v)>20000&&Number(v)<100000){
  const utc=new Date(Date.UTC(1899,11,30)+Math.floor(Number(v))*86400000);
  return utc.toISOString().slice(0,10);
 }
 return '';
}
function normalizeMaterials(records,issues,opts={}){
 const loose=!!opts.loose;
 const items=[],seen=new Set();
 records.forEach((r,i)=>{
  const row=i+2,sku=valueFrom(r,'sku'),name=loose?(valueFrom(r,'name')||sku):valueFrom(r,'name'),supplier=loose?(valueFrom(r,'supplier')||'未提供'):valueFrom(r,'supplier');
  if(!sku&&!name)return;
  if(!sku||!name||!supplier){issues.push(`第 ${row} 行缺少物料编码、名称或供应商`);return;}
  if(seen.has(sku)){issues.push(`第 ${row} 行物料编码 ${sku} 重复，已忽略该行`);return;}seen.add(sku);
  const stock=numeric(valueFrom(r,'stock'),'当前库存',row,issues);
  let usage=numeric(valueFrom(r,'usage'),'日均用量',row,issues);
  let safety=numeric(valueFrom(r,'safety'),'安全库存',row,issues);
  let leadTime=numeric(valueFrom(r,'leadTime'),'交期天数',row,issues);
  if(loose){usage=usage??0;safety=safety??0;leadTime=leadTime??7;}
  const unitCostStr=valueFrom(r,'unitCost');const unitCost=unitCostStr?numeric(unitCostStr,'单价',row,issues):null;
  if([stock,usage,safety,leadTime].some(x=>x===null)||(unitCostStr&&unitCost===null))return;
  if(stock>1e9||usage>1e8||safety>1e9||leadTime>365){issues.push(`第 ${row} 行数值超出当前演示模型支持范围`);return;}
  items.push({sku,name,stock,usage,safety,leadTime,supplier,unitCost});
 });return items;
}
const STATUS_CANON=[[/已取消|取消|cancelled/i,'已取消'],[/部分收货|部分接收|partial/i,'部分收货'],
 [/已收货|已完成|received|closed|done/i,'已收货'],[/计划|planned|plan/i,'计划'],
 [/已下单|在途|已确认|open|ordered|in[-_]transit|运输中|confirmed/i,'已下单']];
function canonStatus(raw){const s=String(raw||'').trim();if(!s)return '已下单';
 for(const [re,v] of STATUS_CANON)if(re.test(s))return v;return '已下单';}
function normalizeOrders(records,issues){const items=[],seen=new Set();
 records.forEach((r,i)=>{
  const row=i+2,sku=valueFrom(r,'sku');if(!sku)return;
  let id=valueFrom(r,'id')||`PO-IMPORTED-${i+1}`;
  const lineKey=id+'|'+sku;   // 行级去重：同一采购单号允许多条物料明细
  if(seen.has(lineKey)){issues.push(`第 ${row} 行采购明细 ${id}/${sku} 重复，已忽略`);return;}seen.add(lineKey);
  const quantity=numeric(valueFrom(r,'quantity'),'采购数量',row,issues,false);
  const rawDate=valueFrom(r,'arrival'),arrival=excelDate(rawDate);
  if(!arrival){issues.push(`第 ${row} 行预计到货日期无效，建议 YYYY-MM-DD 格式`);return;}
  if(quantity===null||quantity>1e9)return;
  const status=canonStatus(valueFrom(r,'status'));
  const recvRaw=valueFrom(r,'receivedQty');
  const receivedQty=recvRaw?numeric(recvRaw,'已收货数量',row,issues,false):null;
  const supplier=valueFrom(r,'supplier');
  const o={id,sku,quantity,arrival,supplier,status};
  if(receivedQty!==null&&receivedQty!==undefined)o.receivedQty=Math.min(receivedQty,quantity);
  items.push(o);
 });return items;
}
// ===================================================== 通用映射导入向导（任意格式，无需改代码）
const FIELD_LABELS={sku:'物料编码',name:'物料名称',stock:'当前库存',usage:'日均用量',safety:'安全库存',leadTime:'交期(天)',supplier:'供应商',unitCost:'单位成本',id:'单号',quantity:'数量',arrival:'预计到货日期',status:'状态',date:'日期',type:'类型',receivedQty:'已收货数量'};
const FIELDS_CN={sku:'物料编码',name:'物料名称',stock:'当前库存',usage:'日均用量',safety:'安全库存',leadTime:'采购交期天',supplier:'供应商',unitCost:'单价',id:'采购单号',quantity:'采购数量',arrival:'预计到货日期',status:'订单状态',receivedQty:'已收货数量'};
const FIELD_DEFS=[
 ['sku','物料编码',/sku|code|料号|物料编码|编号|item|article|product_?id/i,true],
 ['supplier','供应商',/supplier|供应商|vendor/i,false],
 ['name','物料名称',/name|品名|物料名|描述|desc|title/i,false],
 ['stock','当前库存',/stock|库存|存量|结存|现有|在手|on_?hand|balance|inventory/i,false],
 ['usage','日均用量',/usage|日耗|日均|每日|需求|demand|销量|消耗|用量/i,false],
 ['safety','安全库存',/safety|安全库存|安全/i,false],
 ['leadTime','交期(天)',/lead|交期|提前期|^lt$/i,false],
 ['unitCost','单位成本',/cost|单价|价格|price/i,false],
 ['id','单号',/单号|order_?id|purchase_?order|^po$/i,false],
 ['quantity','数量',/qty|数量|quantity|units|件数|^qoh$/i,false],
 ['receivedQty','已收货数量',/已收|收货数量|received_?qty|qty_?received/i,false],
 ['arrival','预计到货日期',/arrival|eta|到货|交货|expected|due|预计/i,false],
 ['status','状态',/^status$|状态|订单状态/i,false],
 ['date','日期',/^date$|日期|^dt$|时间|^day$|period|^ts$/i,false],
 ['type','类型',/^type$|类型|类别|movement|业务/i,false],
];
function autoDetect(records){
 const cols=Object.keys(records[0]||{});if(!cols.length)return{mapping:{},mode:'materials',cols};
 const sample=records.slice(0,80);
 const isDate=v=>{const s=String(v??'').trim();return /^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}/.test(s)&&/^20\d{2}/.test(s);};
 const isNum=v=>{const s=String(v??'').replace(/,/g,'').trim();return s!==''&&Number.isFinite(Number(s));};
 const scoreOf=(field,col)=>{
  const n=norm(col);let s=0;
  const d=ALIASES[field];const exact=!!(d&&d.some(a=>norm(a)===n));
  if(exact)s+=100;
  const reMatch=FIELD_DEFS.find(x=>x[0]===field)[2].test(n);
  if(reMatch)s+=55;
  if(field==='arrival'&&!exact&&!reMatch)return 0;  // 预计到货列必须名字匹配，不能只凭“值像日期”
  const vals=sample.map(r=>r[col]).filter(v=>String(v??'').trim()!=='');
  if(!vals.length)return s;
  if(field==='date'||field==='arrival'){if(vals.filter(isDate).length/vals.length>0.7)s+=40;}
  else if(['stock','usage','safety','leadTime','unitCost','quantity'].includes(field)){if(vals.filter(isNum).length/vals.length>0.7)s+=25;}
  return s;
 };
 const mapping={};const claimed=new Set();const scores={};const sug={};
 for(const [f] of FIELD_DEFS){
  const cand=[];
  for(const c of cols){
   if(claimed.has(c))continue;            // 一列只认领一次，避免多字段抢同一列
   const sc=scoreOf(f,c);if(sc>0)cand.push([c,sc]);
  }
  cand.sort((a,b)=>b[1]-a[1]);
  scores[f]=cand.slice(0,4);
  sug[f]=cand.length?cand[0][0]:'';
  let best='',bs=0;
  for(const [c,sc] of cand){if(sc>bs){bs=sc;best=c;}}
  mapping[f]=bs>=40?best:'';
  if(mapping[f])claimed.add(mapping[f]);
 }
 const n=records.length;
 const uniq=new Set(records.map(r=>String(r[mapping.sku]??'').trim()).filter(Boolean)).size||1;
 let mode='materials';
 if(mapping.sku&&mapping.date&&mapping.quantity&&n>2&&n/uniq>2.5)mode='long';
 else if(mapping.id&&mapping.sku&&mapping.quantity&&mapping.arrival)mode='orders';
 return {mapping,mode,cols,scores,sug};
}
function mapRecords(records,mapping){
 return records.map(r=>{const o={};for(const f in mapping){const c=mapping[f];if(c)o[FIELDS_CN[f]]=String(r[c]??'').trim();}return o;});
}
function buildLong(records,mapping,issues){
 const skuC=mapping.sku,dateC=mapping.date,qtyC=mapping.quantity,stockC=mapping.stock,suppC=mapping.supplier,typeC=mapping.type;
 const bySku=new Map();let minD='',maxD='';
 records.forEach((r,i)=>{
  const sku=String(r[skuC]??'').trim();
  if(!sku){issues.push(`第 ${i+2} 行缺少物料编码，已跳过`);return;}
  const qs=String(r[qtyC]??'').replace(/,/g,'').trim();const q=Number(qs);
  if(qs===''||!Number.isFinite(q)){issues.push(`第 ${i+2} 行数量无效，已跳过`);return;}
  const d=String(r[dateC]??'').trim();
  const e=bySku.get(sku)||{net:0,out:0,lastStock:null,lastStockDate:'',supplier:'',rows:0};
  e.rows++;e.net+=q;
  const t=typeC?String(r[typeC]??''):'';
  const isOut=typeC?/出|售|销|消耗|issue|sale|out/i.test(t):q<0;
  if(isOut)e.out+=Math.abs(q);
  if(stockC){
   const ss=String(r[stockC]??'').replace(/,/g,'').trim();const sn=Number(ss);
   if(ss!==''&&Number.isFinite(sn)&&(!e.lastStockDate||d>=e.lastStockDate)){e.lastStock=sn;e.lastStockDate=d;}
  }
  if(suppC&&!e.supplier){const s=String(r[suppC]??'').trim();if(s)e.supplier=s;}
  if(d){if(!minD||d<minD)minD=d;if(!maxD||d>maxD)maxD=d;}
  bySku.set(sku,e);
 });
 let span=1;
 if(minD&&maxD&&Number.isFinite(E.dayNumber(minD))&&Number.isFinite(E.dayNumber(maxD)))span=Math.max(1,E.dayNumber(maxD)-E.dayNumber(minD)+1);
 const items=[];
 for(const [sku,e] of bySku){
  const usage=Math.round(e.out/span*10000)/10000;
  const stock=e.lastStock!=null?Math.max(0,e.lastStock):Math.max(0,e.net);
  items.push({sku,name:sku,stock,usage,safety:0,leadTime:7,supplier:e.supplier||'未提供',unitCost:null});
 }
 issues.push(`长表聚合：${bySku.size} 个物料，日期跨度 ${span} 天；日均用量=出库量/${span} 天；${stockC?'库存取“结存”列最后一天值':'无结存列，库存按数量净额估计（假设）'}；安全库存 0 / 交期 7 天为缺省假设`);
 return items;
}
function wizardHtml(){
 const w=state.wizard;
 if(!w||!w.records){
  return `<div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">06 · 通用数据映射导入（任意公司格式，无需改代码）</div><div class="card-sub">列名不认识也能导入：系统自动猜每列含义，你只需确认或微调；支持物料宽表 / 采购单表 / 明细长表（自动按物料聚合）</div></div></div>
  <div class="drop-area" data-action="open-wizard"><div class="drop-symbol">⇧</div><div class="drop-title">拖入任意 CSV / Excel / ZIP</div><div class="drop-sub">中文、英文、混合表头都行；拖 ZIP 会自动拆包并筛选数据文件（说明/字典类自动跳过）；不用改程序</div></div></div></div>`;
 }
 const {records,mapping,mode}=w;const cols=Object.keys(records[0]||{});
 const REQUIRED={materials:['sku','stock'],orders:['sku','quantity','arrival'],long:['sku','date','quantity']};
 const required=REQUIRED[mode]||['sku','stock'];
 const optional=['name','usage','safety','leadTime','supplier','unitCost','id','status','date','type'].filter(f=>!required.includes(f));
 const fieldRow=f=>{
  const scores=w.scores?.[f]||[];
  const orderCols=[...(mapping[f]?[mapping[f]]:scores.map(c=>c[0])),...cols].filter((c,i,a)=>a.indexOf(c)===i);
  const hint=mapping[f]
   ?`<span class="whint ok">✓ 自动 · 置信 ${scores.find(c=>c[0]===mapping[f])?.[1]??'—'}</span>`
   :(required.includes(f)
     ?`<span class="whint warn">需选择 · 建议：${esc(scores[0]?.[0]||'无匹配列')}</span>`
     :`<span class="whint">${scores[0]?('建议：'+esc(scores[0][0])):'可选'}</span>`);
  return `<div class="wrow"><span class="wlabel">${FIELD_LABELS[f]}${required.includes(f)?' *':''}</span>
  <select data-wfield="${f}"><option value="">（不映射）</option>${orderCols.map(c=>`<option value="${esc(c)}" ${mapping[f]===c?'selected':''}>${esc(c)}</option>`).join('')}</select>${hint}</div>`;
 };
 const reqOk=required.filter(f=>mapping[f]).length;
 const optOk=optional.filter(f=>mapping[f]).length;
 const preview=records.slice(0,3).map((r,i)=>`<tr><td>${i+1}</td>${cols.slice(0,5).map(c=>`<td>${esc(String(r[c]??'').slice(0,18))}</td>`).join('')}</tr>`).join('');
 return `<div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">06 · 通用数据映射导入 — 确认字段含义</div><div class="card-sub">${esc(w.fileName)} · ${records.length} 行 · 自动识别模式：${mode==='long'?'明细长表（按物料聚合）':mode==='orders'?'采购单表':'物料宽表'} · 必填 ${reqOk}/${required.length} 已识别 · 可选 ${optOk} 项已识别</div></div>${btn('重新选文件','open-wizard','btn-outline btn-sm')}</div>
 ${reqOk===required.length?`<div class="notice success mb16">必填字段已全部自动识别，直接点「应用导入」即可；不放心就扫一眼下方映射。</div>`:`<div class="notice warn mb16">${required.filter(f=>!mapping[f]).map(f=>FIELD_LABELS[f]).join('、')} 未识别，请从下拉中选（选项已按匹配度排序，第一项即最佳猜测），或点「按建议补全」。</div>`}
 <div class="wrow mb12"><span class="wlabel">导入方式</span><label class="checkbox-line"><input type="radio" name="wmode" value="materials" ${mode==='materials'?'checked':''}/> 物料宽表</label><label class="checkbox-line"><input type="radio" name="wmode" value="orders" ${mode==='orders'?'checked':''}/> 采购单表</label><label class="checkbox-line"><input type="radio" name="wmode" value="long" ${mode==='long'?'checked':''}/> 明细长表（按物料聚合）</label></div>
 <div class="wiz-grid"><div class="wiz-cols">
  ${required.map(fieldRow).join('')}
  <details class="wiz-opt"><summary>可选字段（${optOk} 项已自动识别，点击展开）</summary>${optional.map(fieldRow).join('')}</details>
 </div>
 <div class="wiz-preview"><div class="filehint">数据预览（前 3 行 × 前 5 列）</div><div class="table-wrap mt8"><table class="data-table"><thead><tr><th>#</th>${cols.slice(0,5).map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${preview}</tbody></table></div></div></div>
 <div class="row-actions mt16">${btn('按建议补全','wizard-autofill','btn-outline')}${btn('应用导入','apply-wizard','btn-primary')}${btn('清空重来','wizard-reset','btn-outline')}</div></div></div>`;
}
// ===================================================== ZIP 自动拆包筛选
async function scanZipFile(file){
 const zip=await JSZip.loadAsync(await file.arrayBuffer());
 const files=[];
 const entries=Object.values(zip.files).filter(f=>!f.dir&&!f.name.split('/').some(p=>p.startsWith('__MACOSX')||p.startsWith('.')));
 for(const entry of entries){
  const baseName=entry.name.split('/').pop();const lower=baseName.toLowerCase();
  try{
   let sheets=[];
   if(/readme|license|说明|字典|dict|manifest|example|模板/i.test(entry.name)){files.push({name:entry.name,role:'skip',rows:0,reason:'说明/字典/模板类，自动跳过'});continue;}
   if(lower.endsWith('.csv')){
    const content=await entry.async('string');
    const records=parseCSV(content);
    if(!records.length){files.push({name:entry.name,role:'skip',rows:0,reason:'无数据行'});continue;}
    sheets=[{name:baseName,records}];
   }else if(lower.endsWith('.xlsx')){
    const buf=await entry.async('arraybuffer');
    sheets=await parseXLSX(buf);
   }else{files.push({name:entry.name,role:'skip',rows:0,reason:'不支持的类型（仅 .csv/.xlsx）'});continue;}
   for(const sh of sheets){
    const label=entry.name+(sheets.length>1?'/'+sh.name:'');
    if(!sh.records||!sh.records.length){files.push({name:label,role:'skip',rows:0,reason:'无数据行'});continue;}
    if(/readme|license|说明|字典|dict|manifest|example|模板/i.test(label)){files.push({name:label,role:'skip',rows:sh.records.length,reason:'说明/字典/模板类，自动跳过'});continue;}
    const det=autoDetect(sh.records);
    files.push({name:label,rows:sh.records.length,role:det.mode,mapping:det.mapping,scores:det.scores,records:sh.records.slice(0,20000),reason:''});
   }
  }catch(e){files.push({name:entry.name,role:'skip',rows:0,reason:'解析失败：'+(e.message||String(e))});}
 }
 return files;
}
function detectRelationalZip(files){
 const dataFiles=files.filter(f=>['materials','orders','long'].includes(f.role));
 if(dataFiles.length<2)return null;
 const fkPattern=/_id$|_code$|_key$/i;
 const lookupPattern=/supplier|product|warehouse|store|vendor|item|sku_master/i;
 const fkCols=new Set();const lookupFiles=[];
 for(const f of dataFiles){
  const cols=Object.keys(f.records?.[0]||{});
  const fks=cols.filter(c=>fkPattern.test(c));
  fks.forEach(c=>fkCols.add(c));
  const baseName=f.name.split('/').pop().split('.').slice(0,-1).join('.');
  if(lookupPattern.test(baseName))lookupFiles.push(f.name);
 }
 if(fkCols.size>=2&&lookupFiles.length>=1){
  return {detected:true,reason:`检测到 ${fkCols.size} 个外键列（${[...fkCols].slice(0,4).join('、')}）和 ${lookupFiles.length} 个维表（${lookupFiles.slice(0,2).join('、')}），疑似关系型多表关联数据`,suggest:'建议走 04 预置适配器或手动关联后再导入'};
 }
 const matFile=dataFiles.find(f=>f.role==='materials');
 const ordFile=dataFiles.find(f=>f.role==='orders');
 if(matFile&&ordFile){
  const matSkus=new Set(matFile.records.map(r=>String(r[matFile.mapping.sku]??'').trim()).filter(Boolean));
  const ordSkus=new Set(ordFile.records.map(r=>String(r[ordFile.mapping.sku]??'').trim()).filter(Boolean));
  const overlap=[...ordSkus].filter(s=>matSkus.has(s)).length;
  if(overlap===0&&ordSkus.size>0&&matSkus.size>0){
   return {detected:true,reason:`物料表与采购单的物料编码无交集（物料表 ${matSkus.size} 个，采购单 ${ordSkus.size} 个，重叠 0 个），疑似两表来自不同业务维度`,suggest:'请确认是否选对文件，或走 04 预置适配器'};
  }
 }
 return null;
}
function autoChoose(files){
 const chosen=new Set();
 const pick=(role)=>files.filter(f=>f.role===role).sort((a,b)=>b.rows-a.rows)[0];
 const mat=pick('materials')||pick('long');
 if(mat)chosen.add(mat.name);
 const ord=pick('orders');
 if(ord)chosen.add(ord.name);
 return chosen;
}
function zipScanHtml(){
 const s=state.zipScan;
 if(!s)return '';
 if(s.loading)return `<div class="card section-gap"><div class="card-pad"><div class="notice">正在解包并扫描 ZIP 内的数据文件（本地浏览器完成，不上传）…</div></div></div>`;
 const rows=s.files.map(f=>{
  const isData=['materials','orders','long'].includes(f.role);
  const roleLabel=f.role==='materials'?'物料表':f.role==='orders'?'采购单表':f.role==='long'?'明细长表':'跳过';
  return `<tr><td>${esc(f.name)}</td><td>${isData?`<span class="pill ${f.role==='orders'?'warn':f.role==='long'?'sm':'good'}">${roleLabel}</span>`:`<span class="pill sm">跳过</span>`}</td><td>${f.rows}</td><td class="small-muted">${esc(f.reason||'')}</td><td>${isData?`<label class="checkbox-line"><input type="checkbox" data-action="zip-toggle" data-name="${esc(f.name)}" ${s.chosen.has(f.name)?'checked':''}/> 采用</label>`:'—'}</td></tr>`;
 }).join('');
 const dataCount=s.files.filter(f=>['materials','orders','long'].includes(f.role)).length;
 const relWarn=s.relHint?`<div class="notice warn mb16"><strong>⚠ 检测到关系型数据特征</strong><div class="small-muted mt8">${esc(s.relHint.reason)}。<br>${esc(s.relHint.suggest)}。</div></div>`:'';
 return `<div class="card section-gap"><div class="card-pad"><div class="card-header"><div><div class="card-headline">ZIP 扫描结果 — 系统已自动筛选</div><div class="card-sub">${esc(s.fileName)} · ${s.files.length} 个条目 · 识别出 ${dataCount} 个数据文件 · 说明/字典/不支持的文件自动跳过 · 可勾选调整后导入</div></div>${btn('清空重来','wizard-reset','btn-outline btn-sm')}</div>
 ${relWarn}
 <div class="table-wrap"><table class="data-table"><thead><tr><th>文件</th><th>识别为</th><th>行数</th><th>说明</th><th>采用</th></tr></thead><tbody>${rows}</tbody></table></div>
 <div class="row-actions mt16">${btn('按筛选结果导入','apply-zip','btn-primary')}</div></div></div>`;
}
function applyZipScan(){
 const scan=state.zipScan;if(!scan)return;
 const chosen=scan.files.filter(f=>scan.chosen.has(f.name));
 if(!chosen.length){toast('请至少勾选一个数据文件',true);return;}
 const issues=[];let materials=null,orders=null;
 for(const f of chosen){
  if(f.role==='orders'){const it=normalizeOrders(mapRecords(f.records,f.mapping),issues);if(it.length)orders=it;else issues.push(`${f.name}：没有可导入的有效采购记录`);}
  else if(f.role==='long'){const it=buildLong(f.records,f.mapping,issues);if(it.length)materials=it;else issues.push(`${f.name}：长表聚合失败`);}
  else{const it=normalizeMaterials(mapRecords(f.records,f.mapping),issues,{loose:true});if(it.length)materials=it;else issues.push(`${f.name}：没有可导入的有效物料记录`);}
 }
 applyStaging({materials,orders},issues,scan.fileName+'（ZIP 自动筛选导入）');
}
async function openWizardFiles(files){
 const file=files[0];if(!file)return;
 const lower=file.name.toLowerCase();
 if(lower.endsWith('.zip')){
  toast('正在解包 ZIP 并扫描数据文件…');
  state.zipScan={loading:true,fileName:file.name,files:[],chosen:new Set()};state.section='data';render();
  try{
   const scanned=await scanZipFile(file);
   const relHint=detectRelationalZip(scanned);
   state.zipScan={fileName:file.name,files:scanned,chosen:autoChoose(scanned),relHint};
   render();
   const dataCount=scanned.filter(f=>['materials','orders','long'].includes(f.role)).length;
   if(relHint){toast('⚠ '+relHint.reason,true);}
   else{toast('ZIP 扫描完成：识别出 '+dataCount+' 个数据文件，已自动筛选');}
  }catch(e){state.zipScan=null;toast('ZIP 解析失败：'+(e.message||String(e)),true);render();}
  return;
 }
 try{
  let sheets=[];
  if(lower.endsWith('.xlsx'))sheets=await parseXLSX(await file.arrayBuffer());
  else if(lower.endsWith('.csv')){
   const bytes=await file.arrayBuffer();let content=new TextDecoder('utf-8').decode(bytes);
   if(content.includes('\ufffd')){try{content=new TextDecoder('gb18030').decode(bytes);}catch(e){/* keep UTF-8 */}}
   sheets=[{name:file.name,records:parseCSV(content)}];
  }else{toast('仅支持 .csv / .xlsx',true);return;}
  const sheet=sheets.find(s=>s.records&&s.records.length)||sheets[0];
  if(!sheet||!sheet.records.length){toast('文件里没有可读的数据行',true);return;}
  const det=autoDetect(sheet.records);
  state.wizard={records:sheet.records.slice(0,20000),fileName:file.name,mapping:det.mapping,mode:det.mode,sourceSheet:sheet.name,scores:det.scores,sug:det.sug};
  state.section='data';render();toast('已识别：'+det.mapping.sku+' 为物料编码，模式='+(det.mode==='long'?'明细长表':det.mode==='orders'?'采购单':'物料表')+'，请确认映射');
 }catch(e){toast('解析失败：'+(e.message||String(e)),true);}
}
function applyMapped(){
 const w=state.wizard;if(!w||!w.records)return;
 const mapping={};
 document.querySelectorAll('[data-wfield]').forEach(s=>{if(s.value)mapping[s.dataset.wfield]=s.value;});
 if(!mapping.sku){toast('请至少映射“物料编码”列',true);return;}
 const modeEl=document.querySelector('[name="wmode"]:checked');
 let mode=modeEl?modeEl.value:w.mode;
 if(!['materials','orders','long'].includes(mode))mode='materials';
 const issues=[];let items=[],role='materials';
 if(mode==='long'){if(!mapping.date||!mapping.quantity){toast('明细长表需要映射“日期”和“数量”列',true);return;}items=buildLong(w.records,mapping,issues);}
 else if(mode==='orders'){items=normalizeOrders(mapRecords(w.records,mapping),issues);role='orders';}
 else{items=normalizeMaterials(mapRecords(w.records,mapping),issues,{loose:true});}
 applyStaging(role==='orders'?{materials:null,orders:items}:{materials:items,orders:null},issues,w.fileName+'（通用映射导入）');
}
function applyStaging(staging,issues,sourceLabel){
 if(!staging.materials&&!staging.orders){state.importMessages=issues.map(m=>({type:'error',message:m}));render();toast('导入失败：'+(issues[0]||'没有可导入的有效记录'),true);return;}
 if(staging.materials){state.materials=staging.materials;state.orders=staging.orders||[];}
 else if(staging.orders){state.orders=staging.orders;
  if(state.source==='演示数据')issues.push('仅导入采购订单，当前仍使用演示物料主数据；请补充真实物料表，不应将混合结果用于企业决策。');
 }
 const validSkus=new Set(state.materials.map(m=>m.sku));
 state.orders=state.orders.filter(o=>{
  if(!validSkus.has(o.sku)){issues.push(`采购单 ${o.id} 指向不存在的物料 ${o.sku}，已忽略`);return false;}
  if(!o.supplier){o.supplier=mat(o.sku).supplier;issues.push(`采购单 ${o.id} 未提供供应商，暂按物料主数据供应商 ${o.supplier} 假设`);}
  if(E.dayNumber(o.arrival)<E.dayNumber(today()))issues.push(`采购单 ${o.id} 的预计到货日已过且未确认收货，推演时不计作未来入库`);
  return true;
 });
 state.source=(!staging.materials&&state.source==='演示数据')?'混合数据（需复核）':'用户导入数据';
 state.dataKind='user';
 state.today=null;state.datasetMeta=null;state.backtest=null;state.aZipInfo=null;state.relInfo=null;
 state.importMessages=[{type:'success',message:`导入成功：${state.materials.length} 种物料、${state.orders.length} 条采购记录。来源：${sourceLabel}`},
  ...issues.slice(0,14).map(message=>({type:'warn',message}))];
 if(issues.length>14)state.importMessages.push({type:'warn',message:`另有 ${issues.length-14} 条导入提示未展开，请先核实数据质量`});
 state.selectedSku=state.materials[0].sku;state.manualSku=state.selectedSku;state.auto=null;state.manualResult=null;state.agent=null;state.reviewed.clear();resetUpgrade();
 state.section='data';render();toast(`导入成功：${state.materials.length} 项物料 / ${state.orders.length} 条采购单`);
 if(state.backend)setTimeout(()=>syncToBackend(),500);
}
async function handleFiles(files){
 const staging={materials:null,orders:null},issues=[];
 for(const file of files){
  try{
   const lower=file.name.toLowerCase();let sheets=[];
   if(lower.endsWith('.xlsx'))sheets=await parseXLSX(await file.arrayBuffer());
   else if(lower.endsWith('.csv')){
    const bytes=await file.arrayBuffer();let content=new TextDecoder('utf-8').decode(bytes);
    if(content.includes('\ufffd')){try{content=new TextDecoder('gb18030').decode(bytes);}catch(e){/* leave UTF-8 */}}
    sheets=[{name:file.name,records:parseCSV(content)}];
   }else{issues.push(`${file.name} 格式不支持，仅接收 .csv / .xlsx`);continue;}
   for(const sheet of sheets){
    const role=roleOf(sheet.records[0],sheet.name);
    if(!role){
     if(/说明|readme|instruction/i.test(sheet.name))continue;
     const hdrs=Object.keys(sheet.records[0]||{}).slice(0,8).join('、');
     let guide='需要物料表（物料编码/当前库存/日均用量/供应商…）或采购单表（采购单号/物料编码/采购数量/预计到货日期…）';
     if(/store_id|product_id|sale_amount|stock_hour/i.test(hdrs)&&/dt|date/i.test(hdrs))guide='检测到门店级时序数据（FreshRetailNet-50K 类型）——该形态不能导入为物料表，请用数据工作台“05 · 截尾需求实证”卡片查看分析';
     else if(/purchase_order|supplier_id|warehouse_id|opening_units|movement|balance/i.test(hdrs))guide='检测到关系型数据集表单（inventory-supply-chain 类型）——请用“04 · 关系型数据集”卡片整体载入，不要逐表上传';
     issues.push(`${file.name}：无法判断数据类型（检测到表头：${hdrs}）。${guide}`);
     continue;
    }
    const local=[];const items=role==='materials'?normalizeMaterials(sheet.records,local):normalizeOrders(sheet.records,local);
    issues.push(...local.slice(0,12).map(s=>`${file.name} / ${sheet.name}：${s}`));
    if(items.length){staging[role]=items;}
    else issues.push(`${file.name} / ${sheet.name}：没有可导入的有效记录`);
   }
  }catch(e){issues.push(`${file.name}：${e.message||String(e)}`);}
 }
 if(!staging.materials&&!staging.orders){
  const hint='；如果这份数据的列名与模板不同，请用下方“06 · 通用数据映射导入”向导手动映射，无需改代码';
  state.importMessages=issues.map(m=>({type:'error',message:m}));
  render();toast('导入失败：'+(issues[0]||'请检查文件表头与数据')+hint,true);return;
 }
 applyStaging(staging,issues,files.map(f=>f.name).join('、'));
}
function download(name,content,type='text/plain;charset=utf-8'){
 const blob=new Blob([content],{type});const url=URL.createObjectURL(blob);
 const a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),3000);
}
function csvCell(value){let s=String(value??'');if(/^[\s]*[=+@]/.test(s))s="'"+s;return /[",\r\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;}
function saveCSV(filename,head,rows){const content='\uFEFF'+[head,...rows].map(row=>row.map(csvCell).join(',')).join('\r\n');download(filename,content,'text/csv;charset=utf-8');}
function exportTemplate(type){if(type==='materials'){
 saveCSV('ChainCoxswain_物料模板.csv',['物料编码','物料名称','当前库存','日均用量','安全库存','采购交期天','供应商','单位成本'],
 [['M-001','精密电机轴承',80,10,15,5,'华东精工',48],['M-002','主控芯片',120,9,25,10,'科联半导体',126]]);
 }else{
 saveCSV('ChainCoxswain_采购订单模板.csv',['采购单号','物料编码','采购数量','预计到货日期','供应商','状态'],
 [['PO-1001','M-001',100,E.addDays(today(),6),'华东精工','已下单'],['PO-1002','M-002',90,E.addDays(today(),11),'科联半导体','已下单']]);
 }}
function exportCSV(){const risks=allRisks();
 saveCSV('ChainCoxswain_风险清单_'+today()+'.csv',
 ['物料编码','物料名称','供应商','当前库存','日均用量','安全库存','交期天数','库存覆盖天数','风险等级','首次缺货日期','首次低于安全库存日期','累计缺口','补货点估算','原因'],
 risks.map(r=>[r.material.sku,r.material.name,r.material.supplier,r.material.stock,r.material.usage,r.material.safety,r.material.leadTime,r.coverage??'',
 riskLabels[r.level],r.base.firstShortage??'',r.base.firstSafety??'',r.base.totalUnmet,r.replenishmentPoint,r.reason]));
 toast('风险清单 CSV 已导出');
}
function exportHTML(){const risks=allRisks();
 const table=risks.map(r=>`<tr><td>${esc(r.material.sku)}</td><td>${esc(r.material.name)}</td><td>${esc(r.material.supplier)}</td><td>${esc(riskLabels[r.level])}</td><td>${r.base.firstShortage||'—'}</td><td>${num(r.base.totalUnmet)}</td><td>${esc(r.reason)}</td></tr>`).join('');
 const threshold=state.auto?`<h2>二、自主阈值搜索结果</h2><p>共运行 ${state.auto.totalRounds} 次情景计算，测试 ${state.auto.tested.length} 项物料。</p><table><tr><th>物料</th><th>到货延期</th><th>下单推迟</th><th>需求上涨</th></tr>${state.auto.tested.map(t=>`<tr><td>${esc(t.risk.material.name)}</td><td>${esc(fmtThreshold(t.thresholds.deliveryDelay,'天').v)}</td><td>${esc(fmtThreshold(t.thresholds.purchaseDelay,'天').v)}</td><td>${esc(fmtThreshold(t.thresholds.demandIncrease,'%').v)}</td></tr>`).join('')}</table>`:'<h2>二、自主阈值搜索结果</h2><p>尚未运行自主扫描。</p>';
 const manual=state.manualResult?`<h2>三、手动情景</h2><p>${esc(state.manualResult.description)}；首次缺货：${state.manualResult.sim.firstShortage||'未发生'}；累计缺口：${num(state.manualResult.sim.totalUnmet)} 件。</p>`:'<h2>三、手动情景</h2><p>尚未进行手动测试。</p>';
 const reviews=Array.from(state.reviewed).map(k=>`<li>${esc(k)}</li>`).join('')||'<li>尚无人工勾选的已审核建议</li>';
 const html=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>链舵供应链风险评估报告</title><style>body{max-width:1100px;margin:45px auto;padding:0 22px;color:#22344a;font:14px/1.75 -apple-system,'Microsoft YaHei',sans-serif}h1{font-size:29px}h2{margin-top:38px;border-bottom:2px solid #1ab6a7;padding-bottom:9px;font-size:18px}table{border-collapse:collapse;width:100%;margin-top:20px}td,th{border:1px solid #dce7ee;padding:10px;text-align:left;font-size:12px;vertical-align:top}th{background:#f1f7f7}small,p{color:#647990}aside{background:#f3f8fa;padding:17px;border-radius:8px;margin-top:18px}footer{margin:35px 0 0;color:#94a5b4;border-top:1px solid #eee;padding-top:15px}@media print{body{margin:12mm auto}h2{break-after:avoid}}</style></head><body>
 <small>CHAINCOXSWAIN / SUPPLY CHAIN RISK REPORT</small><h1>供应链库存风险与压力测试报告</h1>
 <p>报告日期：${today()}　|　数据来源：${esc(state.source)}　|　推演天数：${state.horizon} 天</p>
 <aside><strong>重要提示</strong>：全部结果是数据输入与假设下的条件推演，未确认供应商交货能力、未来订单真实性或概率，且不代表物料缺口必然导致停产。所有行动仅提供给用户审核，系统没有下单权限。</aside>
 <h2>一、当前物料风险清单</h2><table><tr><th>物料编码</th><th>物料名称</th><th>供应商</th><th>风险等级</th><th>首次缺货</th><th>累计缺口</th><th>解释</th></tr>${table}</table>${threshold}${manual}
 <h2>四、人工审核记录</h2><p>以下是界面中被勾选“已审核”的建议标识，不代表采购单已发出。</p><ul>${reviews}</ul>
 <h2>五、假设与模型边界</h2><ol><li>收货按每日开始时入库，同一日消耗在入库后计算。</li><li>基础需求在推演期内保持不变，需求冲击假设从测试首日生效。</li><li>模拟窗口 ${state.horizon} 天；自动扰动上限：到货 / 下单推迟 30 天、需求增长 200%。</li><li>预测缺口只代表当前库存与已录入预计到货条件下的未满足需求量；实际采购、BOM、工序、产能及调拨能力未验证。</li><li>临时补货日期按照录入交期推算；供应商可能无法履约，需人工确认。</li><li>本报告不包含异常发生概率，不将缺失数据替换为隐含可信事实。</li></ol><footer>链舵 ChainCoxswain V2.0 · 可审计条件库存仿真报告</footer></body></html>`;
 download('ChainCoxswain_风险评估报告_'+today()+'.html',html,'text/html;charset=utf-8');toast('HTML 报告已生成，可用浏览器打开或打印为 PDF');
}
// ============================ new actions ============================
function selectAuto(sku){state.section='auto';
 if(!state.auto)state.auto=E.autonomousScan(state.materials,state.orders,{today:today(),horizon:state.horizon});
 state.autoSku=state.auto.tested.find(t=>t.risk.material.sku===sku)?.risk.material.sku||state.auto.tested[0]?.risk.material.sku;
 render();}
function runAuto(sku){state.auto=E.autonomousScan(state.materials,state.orders,{today:today(),horizon:state.horizon});
 const preferred=state.auto.tested.find(t=>t.risk.material.sku===sku) || state.auto.tested.find(t=>t.thresholds.deliveryDelay.status==='found') || state.auto.tested[0];
 state.autoSku=preferred?.risk.material.sku;state.section='auto';render();toast(`自主压力测试完成，共运行 ${state.auto.totalRounds} 轮工具计算`);
}
function runManual(){const m=mat(state.manualSku);
 const scenario={type:state.manualType,value:Number(state.manualValue)};
 if(state.manualType==='supplierOutage'){
  scenario.supplier=state.manualSupplier||state.orders.find(o=>o.sku===m.sku)?.supplier||m.supplier;
 }
 if(state.manualType==='purchaseDelay' && !state.orders.some(o=>o.sku===m.sku&&o.status==='计划'&&E.dayNumber(o.arrival)>=E.dayNumber(today()))){
  toast('该物料没有测试窗口内的未来“计划”采购单，请选择其他物料或压力类型',true);return;
 }
 if(state.manualType==='deliveryDelay'&&!state.orders.some(o=>o.sku===m.sku&&o.status!=='计划'&&E.dayNumber(o.arrival)>=E.dayNumber(today()))){
  toast('该物料没有未来已下单采购单，无法模拟单笔到货延期',true);return;
 }
 if(scenario.value<0||scenario.value>(scenario.type==='demandIncrease'?500:120)||!Number.isInteger(scenario.value)){
  toast('参数须为合理范围内的非负整数',true);return;
 }
 const baseline=E.simulate(m,state.orders,{today:today(),horizon:state.horizon});
 const sim=E.simulate(m,state.orders,{today:today(),horizon:state.horizon,scenario});
 const description=scenario.type==='supplierOutage'?`假设供应商 ${scenario.supplier} 相关采购订单无法到货`:
 `${scenarioLabels[scenario.type]} ${scenario.value}${scenario.type==='demandIncrease'?'%':'天'}`;
 state.manualResult={sku:m.sku,scenario,sim,baseline,description};state.section='manual';render();toast('情景测算完成，已更新对照结果');
}
function restoreDemo(){const d=E.demoData();state.materials=d.materials;state.orders=d.orders;
 state.source='演示数据';state.dataKind='demo';state.today=null;state.datasetMeta=null;state.backtest=null;state.aZipInfo=null;
 state.selectedSku='M-004';state.manualSku='M-001';state.manualValue=3;state.manualType='deliveryDelay';
 state.auto=null;state.manualResult=null;state.agent=null;state.reviewed.clear();state.importMessages=[];
 state.section='overview';render();toast('已恢复完整示例数据');
}
async function loadAZip(){
 const simulate=document.getElementById('simulate-pos')?.checked||false;
 toast('正在后端解析 A.zip（约 1000 天 × 632 unit 历史数据）…');
 const r=await api('/api/data-a/load',{method:'POST',body:{simulatePos:simulate},timeout:120000});
 if(!r.ok){toast('载入失败：'+(r.data.error||'后端不可用'),true);render();return;}
 const d=await api('/api/data-a/dataset',{timeout:120000});
 if(!d.ok||!Array.isArray(d.data?.materials)||!d.data.materials.length){toast('获取数据集失败：'+(d.data?.error||'响应格式异常'),true);render();return;}
 state.materials=d.data.materials;state.orders=d.data.orders;
 state.source='A.zip 历史数据（快照 '+d.data.today+'）';state.dataKind='real-historical';
 state.today=d.data.today;state.datasetMeta=r.data.meta.datasetMeta;
 state.aZipInfo={simulatePos:simulate};
 state.selectedSku=d.data.materials[0].sku;state.manualSku=d.data.materials[0].sku;resetUpgrade();
 state.auto=null;state.manualResult=null;state.agent=null;state.backtest=null;state.reviewed.clear();
 state.importMessages=[{type:'success',message:`已载入 A.zip：${d.data.materials.length} 个 unit、${d.data.orders.length} 条${simulate?'【模拟】':''}采购记录；快照日 ${d.data.today}（历史业务日期，原样保留）`}];
 state.section='data';render();toast('A.zip 数据集载入完成');
}
async function loadRel(){
 const r=await api('/api/data-rel/load',{method:'POST',body:{},timeout:180000});
 if(!r.ok){toast('载入失败：'+(r.data.error||'后端不可用'),true);render();return;}
 const d=await api('/api/data-rel/dataset',{timeout:180000});
 if(!d.ok||!Array.isArray(d.data?.materials)||!d.data.materials.length){toast('获取数据集失败：'+(d.data?.error||'响应格式异常'),true);render();return;}
 state.materials=d.data.materials;state.orders=d.data.orders;
 state.source='关系型数据集 inventory-supply-chain v1.0.0（合成教学数据 · 快照 '+d.data.today+'）';state.dataKind='demo';
 state.today=d.data.today;state.datasetMeta=r.data.meta.datasetMeta;
 state.relInfo={snapshot:d.data.today,poStats:r.data.meta.datasetMeta.poStats};
 state.selectedSku=d.data.materials[0].sku;state.manualSku=d.data.materials[0].sku;resetUpgrade();
 state.auto=null;state.manualResult=null;state.agent=null;state.backtest=null;state.aZipInfo=null;state.reviewed.clear();
 state.importMessages=[{type:'success',message:`已载入关系型数据集：${d.data.materials.length} 个产品（跨 12 仓聚合）、${d.data.orders.length} 条在途采购记录；快照日 ${d.data.today}；数据为合成教学数据（demo 性质）`},
  ...(state.datasetMeta?.flags||[]).slice(0,4).map(f=>({type:'warn',message:f}))];
 state.section='data';render();toast('关系型数据集载入完成：'+d.data.materials.length+' 个产品');
}
async function loadCensored(){
 state.censoredLoading=true;state.section='data';render();
 const r=await api('/api/censored/analysis',{timeout:120000});
 state.censoredLoading=false;
 if(!r.ok){toast('分析失败：'+(r.data.error||'后端不可用'),true);render();return;}
 state.censored=r.data.analysis;render();
 toast('截尾需求实证完成：缺货天数占比 '+state.censored.stockoutRatioPct+'%，中位上修 +'+state.censored.upliftStats.medianUpliftPct+'%');
}
async function runBacktest(){
 state.backtestLoading=true;render();
 const r=await api('/api/data-a/backtest?limit=60',{timeout:180000});
 state.backtestLoading=false;
 if(!r.ok){toast('回测失败：'+(r.data.error||'后端不可用'),true);render();return;}
 state.backtest=r.data.backtest;state.section='data';render();toast('历史回测完成（无泄漏：参数只用训练期，测试期只用于回放）');
}
async function runAgent(){
 const goal=document.getElementById('agent-goal')?.value||state.agentGoal;
 state.agentGoal=goal;
 for(const k of ['maxRounds','maxToolCalls','timeoutSec','budgetTokens']){
  const v=document.getElementById('p-'+k)?.value;
  if(v!==undefined&&v!=='')state.agentParams[k]=Number(v);
 }
 state.agentRunning=true;state.agent=null;state.section='agent';render();
 toast('千问 Agent 运行中（规划→工具→观察→调整，最多 '+state.agentParams.maxRounds+' 轮）…');
 const r=await api('/api/qwen/agent/run',{method:'POST',body:{goal,params:state.agentParams},timeout:(state.agentParams.timeoutSec+30)*1000});
 state.agentRunning=false;
 if(!r.ok){toast('运行失败：'+(r.data.error||'后端不可用'),true);render();return;}
 state.agent=r.data.agent;state.section='agent';render();
 toast(`${state.agent.modeLabel} · ${state.agent.usage.toolCalls} 次工具调用 / ${state.agent.usage.roundsUsed} 轮`);
}
async function testMCPTool(el){
 const name=el.dataset.tool;let args={};
 try{args=JSON.parse(el.dataset.args||'{}');}catch(e){args={};}
 toast('调用 MCP 工具：'+name);
 const r=await api('/api/mcp/tool/'+name,{method:'POST',body:args,timeout:120000});
 let text='';
 if(r.ok)text='OK: '+JSON.stringify(r.data.result).slice(0,600);
 else text='失败：'+(r.data.error||r.data.result?.error||'后端不可用');
 state.collab.toolResults[name]={ok:r.ok,text};
 state.section='collab';render();
}
function copyTask(){
 const ta=document.getElementById('task-instruction');
 if(ta&&navigator.clipboard){navigator.clipboard.writeText(ta.value).then(()=>toast('任务指令已复制到剪贴板'));}
 else toast('已选中文本框内容，请手动复制');
}
async function exportPackage(){
 const r=await api('/api/task/package');
 if(!r.ok){toast('后端不可用，无法导出任务包',true);return;}
 download('ChainCoxswain_千问办公任务包.json',JSON.stringify(r.data,null,2),'application/json;charset=utf-8');
 toast('任务包 JSON 已导出');
}
async function genReport(fmt){
 toast('正在生成 '+(fmt==='xlsx'?'Excel':fmt==='docx'?'Word':fmt==='pptx'?'PPT':'HTML')+' 报告…');
 const r=await api('/api/report/generate',{method:'POST',body:{format:fmt},timeout:180000});
 if(!r.ok){toast('生成失败：'+(r.data.error||'后端不可用，请先启动后端'),true);return;}
 const url=API_BASE+r.data.downloadUrl;
 const a=document.createElement('a');a.href=url;a.download=r.data.report.filename;document.body.appendChild(a);a.click();a.remove();
 toast('已生成 '+r.data.report.filename+'（'+Math.round(r.data.report.sizeBytes/1024)+' KB）');
}
async function runCompare(){
 state.compareRunning=true;state.compare=null;state.section='compare';render();
 toast('四组对照实验运行中（含穷举基准搜索）…');
 const r=await api('/api/compare',{method:'POST',body:{topN:6,horizon:14},timeout:300000});
 state.compareRunning=false;
 if(!r.ok){toast('实验失败：'+(r.data.error||'后端不可用'),true);render();return;}
 state.compare=r.data.comparison;state.section='compare';render();toast('对照实验完成');
}
function exportDataset(){
 const dataset={
  version:'3.2',
  exportedAt:new Date().toISOString(),
  source:state.source,
  dataKind:state.dataKind,
  today:state.today,
  materials:state.materials,
  orders:state.orders,
  horizon:state.horizon
 };
 const json=JSON.stringify(dataset,null,2);
 download('ChainCoxswain_数据备份_'+today()+'.json',json,'application/json;charset=utf-8');
 toast('当前数据已导出为 JSON（含 '+state.materials.length+' 种物料、'+state.orders.length+' 条采购单）');
}
async function syncToBackend(){
 if(!state.backend){toast('后端未启动，无法同步',true);return;}
 toast('正在同步数据到后端…');
 const r=await api('/api/dataset/sync',{method:'POST',body:{
  materials:state.materials,
  orders:state.orders,
  source:state.source,
  dataKind:state.dataKind,
  today:state.today
 },timeout:30000});
 if(!r.ok){toast('同步失败：'+(r.data.error||'后端不可用'),true);return;}
 await checkBackend();
 toast('已同步 '+state.materials.length+' 种物料、'+state.orders.length+' 条采购单到后端（MCP 工具现在可用这份数据）');
}
function importDatasetFromJSON(text){
 try{
  const dataset=JSON.parse(text);
  if(!dataset.materials||!Array.isArray(dataset.materials)||!dataset.materials.length){
   toast('JSON 格式无效：缺少 materials 数组',true);return;
  }
  if(!dataset.orders)dataset.orders=[];
  state.materials=dataset.materials;
  state.orders=dataset.orders;
  state.source=dataset.source||'从 JSON 恢复';
  state.dataKind=dataset.dataKind||'user';
  state.today=dataset.today||null;
  state.horizon=dataset.horizon||14;
  state.selectedSku=state.materials[0]?.sku||'M-001';resetUpgrade();
  state.manualSku=state.selectedSku;
  state.auto=null;state.manualResult=null;state.agent=null;state.reviewed.clear();
  state.importMessages=[{type:'success',message:`从 JSON 恢复成功：${state.materials.length} 种物料、${state.orders.length} 条采购记录。来源：${state.source}`}];
  state.datasetMeta=null;state.backtest=null;state.aZipInfo=null;state.relInfo=null;
  state.section='data';render();
  toast('数据已从 JSON 恢复：'+state.materials.length+' 种物料 / '+state.orders.length+' 条采购单');
 }catch(e){
  toast('JSON 解析失败：'+(e.message||String(e)),true);
 }
}
function onAction(el){const action=el.dataset.action;
 if(action==='nav'){state.section=el.dataset.page;render();return;}
 if(action==='horizon'){state.horizon=Number(el.dataset.value);state.auto=null;state.manualResult=null;state.agent=null;state.reviewed.clear();render();return;}
 if(action==='sample'){restoreDemo();return;}
 if(action==='to-data'){state.section='data';render();return;}
 if(action==='to-overview'){state.section='overview';render();return;}
 if(action==='to-auto'){state.section='auto';render();return;}
 if(action==='to-agent'){state.section='agent';render();return;}
 if(action==='to-collab'){state.section='collab';render();return;}
 if(action==='select-sku'){state.selectedSku=el.dataset.sku;render();return;}
 if(action==='fetch-upgrade'){fetchUpgrade(state.selectedSku);return;}
 if(action==='fetch-recovery'){fetchRecovery(state.selectedSku);return;}
 if(action==='analyze-selected'){runAuto(state.selectedSku);return;}
 if(action==='manual-selected'){state.manualSku=state.selectedSku;state.section='manual';state.manualResult=null;render();return;}
 if(action==='agent-selected'){state.section='agent';render();return;}
 if(action==='auto-select'){state.autoSku=el.dataset.sku;render();return;}
 if(action==='run-auto'){runAuto();return;}
 if(action==='run-manual'){runManual();return;}
 if(action==='run-agent'){runAgent();return;}
 if(action==='run-compare'){runCompare();return;}
 if(action==='load-a-zip'){loadAZip();return;}
 if(action==='load-rel'){loadRel();return;}
 if(action==='censored-run'){loadCensored();return;}
 if(action==='run-backtest'){runBacktest();return;}
 if(action==='test-tool'){testMCPTool(el);return;}
 if(action==='copy-task'){copyTask();return;}
 if(action==='export-package'){exportPackage();return;}
 if(action.startsWith('gen-report-')){genReport(action.slice('gen-report-'.length));return;}
 if(action==='open-upload'){document.getElementById('file-upload').click();return;}
 if(action==='open-wizard'){document.getElementById('wizard-upload').click();return;}
 if(action==='export-dataset'){exportDataset();return;}
 if(action==='import-dataset'){document.getElementById('dataset-json-upload').click();return;}
 if(action==='sync-backend'){syncToBackend();return;}
 if(action==='wizard-autofill'){
  const w=state.wizard;
  if(!w)return;
  document.querySelectorAll('[data-wfield]').forEach(s=>{
   if(!s.value){const top=(w.scores?.[s.dataset.wfield]||[])[0];if(top)s.value=top[0];}
  });
  const mapping={};
  document.querySelectorAll('[data-wfield]').forEach(s=>{if(s.value)mapping[s.dataset.wfield]=s.value;});
  state.wizard.mapping=mapping;render();toast('已按建议补全未映射字段');return;
 }
 if(action==='zip-toggle'){const nm=el.dataset.name;if(el.checked)state.zipScan.chosen.add(nm);else state.zipScan.chosen.delete(nm);return;}
 if(action==='apply-zip'){applyZipScan();return;}
 if(action==='apply-wizard'){applyMapped();return;}
 if(action==='wizard-reset'){state.wizard=null;state.zipScan=null;render();return;}
 if(action==='template-materials'){exportTemplate('materials');return;}
 if(action==='template-orders'){exportTemplate('orders');return;}
 if(action==='open-template-note'){toast('提供物料 CSV 和采购单 CSV 模板；完整 Excel 示例也已包含在产品文件夹的 demo_data.xlsx 中');return;}
 if(action==='export-csv'){exportCSV();return;}
 if(action==='export-html'){exportHTML();return;}
}
document.addEventListener('click',e=>{const target=e.target.closest('[data-action]');if(target){try{onAction(target);}catch(err){toast(err.message||String(err),true);console.error(err);}}});
document.addEventListener('change',async e=>{
 const target=e.target;
 if(target.id==='file-upload'){
  if(target.files?.length)await handleFiles(Array.from(target.files));
  return;
 }
 if(target.id==='wizard-upload'){
  if(target.files?.length)await openWizardFiles(Array.from(target.files));
  target.value='';return;
 }
 if(target.id==='dataset-json-upload'){
  if(target.files?.length){
   const file=target.files[0];
   const reader=new FileReader();
   reader.onload=()=>importDatasetFromJSON(reader.result);
   reader.readAsText(file);
  }
  target.value='';return;
 }
 if(target.dataset.review){if(target.checked)state.reviewed.add(target.dataset.review);else state.reviewed.delete(target.dataset.review);return;}
 if(target.dataset.field){
  const k=target.dataset.field;
  if(k==='manualSku'){state.manualSku=target.value;state.manualSupplier='';state.manualResult=null;render();}
  else if(k==='manualType'){state.manualType=target.value;state.manualValue=target.value==='demandIncrease'?20:3;state.manualResult=null;render();}
  else if(k==='manualValue')state.manualValue=Number(target.value);
  else if(k==='manualSupplier')state.manualSupplier=target.value;
 }
});
document.addEventListener('dragover',e=>{const el=e.target.closest('.drop-area');if(el){e.preventDefault();el.classList.add('dragover');}});
document.addEventListener('dragleave',e=>{const el=e.target.closest('.drop-area');if(el)el.classList.remove('dragover');});
document.addEventListener('drop',e=>{const el=e.target.closest('.drop-area');if(el){e.preventDefault();el.classList.remove('dragover');if(e.dataTransfer?.files?.length){if(el.dataset.action==='open-wizard')openWizardFiles(Array.from(e.dataTransfer.files));else handleFiles(Array.from(e.dataTransfer.files));}}});
render();
checkBackend();
})();
