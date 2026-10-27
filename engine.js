/* ChainCoxswain deterministic planning and stress-testing engine.
   No network, no model-generated quantities. All quantities are calculated here.
   Convention: receipts arrive at the start of a day, before that day's consumption;
   all calculations are conditional scenarios rather than predictions. */
(function(root, factory){
  const engine=factory();
  if (typeof module !== 'undefined' && module.exports) module.exports=engine;
  root.ChainEngine=engine;
})(typeof window !== 'undefined' ? window : globalThis, function(){
  'use strict';
  const DEFAULT_HORIZON=14;
  const clone=x=>JSON.parse(JSON.stringify(x));
  const pad=n=>String(n).padStart(2,'0');
  function toDayString(date=new Date()){
    if(typeof date==='string') return date.slice(0,10);
    return date.getFullYear()+'-'+pad(date.getMonth()+1)+'-'+pad(date.getDate());
  }
  function dayNumber(date){
    const m=/^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(String(date||'').trim());
    if(!m) return NaN;
    const [_,y,mm,dd]=m.map(Number);
    const t=Date.UTC(y,mm-1,dd);
    const a=new Date(t);
    if(a.getUTCFullYear()!==y || a.getUTCMonth()!==mm-1 || a.getUTCDate()!==dd) return NaN;
    return t/86400000;
  }
  function addDays(date, days){
    let t=dayNumber(date);
    if(!Number.isFinite(t)) throw new Error('无效日期 '+date);
    const d=new Date((t+Number(days))*86400000);
    return d.getUTCFullYear()+'-'+pad(d.getUTCMonth()+1)+'-'+pad(d.getUTCDate());
  }
  const nice=(n)=>Number.isFinite(n)?Math.round(n*100)/100:null;
  // ---- order semantics (mirror of backend/engine.py) ----
  const STATUS={CONFIRMED:'confirmed',PLANNED:'planned',CANCELLED:'cancelled',RECEIVED:'received',PARTIAL:'partial'};
  const STATUS_ALIASES={'confirmed':'confirmed','已下单':'confirmed','在途':'confirmed','已确认':'confirmed',
    'open':'confirmed','ordered':'confirmed','in-transit':'confirmed','in_transit':'confirmed','运输中':'confirmed',
    'planned':'planned','计划':'planned','plan':'planned','计划采购':'planned',
    'cancelled':'cancelled','已取消':'cancelled','取消':'cancelled',
    'received':'received','已收货':'received','已完成':'received','closed':'received','done':'received',
    'partial':'partial','部分收货':'partial','部分接收':'partial'};
  function orderStatus(o){const raw=String(o.status||'').trim();if(!raw)return STATUS.CONFIRMED;
    return STATUS_ALIASES[raw]||STATUS_ALIASES[raw.toLowerCase()]||STATUS.CONFIRMED;}
  function orderRemaining(o){const st=orderStatus(o);if(st===STATUS.CANCELLED||st===STATUS.RECEIVED)return 0;
    const qty=Number(o.quantity)||0;const rec=Number(o.receivedQty??o.received_qty??0)||0;return Math.max(0,qty-rec);}
  function prepareOrders(material,allOrders,opts){
    const start=opts._start;const source=(opts.overrideOrders===undefined||opts.overrideOrders===null)?allOrders:opts.overrideOrders;
    const scenario=opts.scenario||{type:'baseline',value:0};
    const includePlanned=!!opts.includePlanned||scenario.type==='purchaseDelay';
    const receipts=[],planned=[];
    for(const raw of source){
      if(raw.sku!==material.sku)continue;
      const o={...raw};o._status=orderStatus(o);o._remaining=orderRemaining(o);
      if(o._status===STATUS.PLANNED){if(Number.isFinite(dayNumber(o.arrival)))planned.push(o);continue;}
      if(o._status===STATUS.CANCELLED||o._status===STATUS.RECEIVED||o._remaining<=0)continue;
      if(!Number.isFinite(dayNumber(o.arrival)))continue;
      receipts.push(o);
    }
    const s=dayNumber(start);
    const overdue=receipts.filter(o=>dayNumber(o.arrival)<s);
    const onTime=receipts.filter(o=>dayNumber(o.arrival)>=s);
    const active=onTime.concat(includePlanned?planned:[]);
    return {receipts,overdue,planned,active,includePlanned};
  }
  function simulate(material, allOrders, opts={}){
    const start=opts.today||toDayString();
    const horizon=opts.horizon||DEFAULT_HORIZON;
    const scenario=opts.scenario||{type:'baseline',value:0};
    if(horizon<1 || horizon>365) throw new Error('仿真天数超出范围');
    const pools=prepareOrders(material,allOrders,Object.assign({},opts,{_start:start}));
    let orders=pools.active;
    const usage=Number(material.usage)||0;
    const value=Number(scenario.value||0);
    let multiplier=1;
    if(scenario.type==='demandIncrease') multiplier=1+value/100;
    if(scenario.type==='demandReduction') multiplier=Math.max(0,1-value/100);
    if(scenario.type==='joint'){
      multiplier=1+Number(scenario.demandPct||0)/100;
      const jd=Number(scenario.delayDays||0)|0;
      for(const o of orders) if(o._status!==STATUS.PLANNED) o.arrival=addDays(o.arrival,jd);
    }
    const scope=opts.orderScope||{};
    const scopeIds=scope.orderIds?new Set(scope.orderIds):null;
    const scopeSupplier=scope.supplier||null;
    const inScope=(o)=>{if(scopeIds)return scopeIds.has(o.id)||scopeIds.has(o.poId);
      if(scopeSupplier)return o.supplier===scopeSupplier;return true;};
    if(['deliveryDelay','purchaseDelay','expedite'].includes(scenario.type)){
      const pool=scenario.type==='purchaseDelay'?orders.filter(o=>o._status===STATUS.PLANNED)
                                                 :orders.filter(o=>o._status!==STATUS.PLANNED);
      const future=pool.filter(o=>inScope(o)&&dayNumber(o.arrival)>=dayNumber(start))
                       .sort((a,b)=>dayNumber(a.arrival)-dayNumber(b.arrival));
      const targets=(scopeIds||scopeSupplier)?future:future.slice(0,1);
      for(const t of targets) t.arrival=addDays(t.arrival,(scenario.type==='expedite'?-1:1)*Number(value|0));
    }
    if(scenario.type==='supplierOutage') orders=orders.filter(o=>o.supplier!==scenario.supplier);
    if(Array.isArray(opts.extraOrders)) for(const raw of opts.extraOrders){
      const eo={...raw};if(eo._status===undefined)eo._status=orderStatus(eo);
      if(eo._remaining===undefined)eo._remaining=orderRemaining(eo);orders.push(eo);}
    const usageSeries=Array.isArray(opts.usageSeries)?opts.usageSeries.map(Number):null;
    let stock=Number(material.stock)||0, totalUnmet=0, firstShortage=null, firstSafety=null;
    const points=[];
    for(let i=0;i<horizon;i++){
      const date=addDays(start,i);
      const received=orders.filter(o=>o.arrival===date).reduce((acc,o)=>acc+o._remaining,0);
      const baseUsage=(usageSeries&&Number.isFinite(usageSeries[i]))?Math.max(0,usageSeries[i]):usage;
      const dayUsage=Math.max(0,baseUsage*multiplier);
      const available=stock+received;
      const unmet=Math.max(0,dayUsage-available);
      stock=Math.max(0,available-dayUsage);
      totalUnmet+=unmet;
      if(unmet>1e-8 && !firstShortage) firstShortage=date;
      if(stock < Number(material.safety) && !firstSafety) firstSafety=date;
      points.push({date,day:i+1,received:nice(received),demand:nice(dayUsage),stock:nice(stock),unmet:nice(unmet)});
    }
    const expectedReceipts=orders.filter(o=>dayNumber(o.arrival)>=dayNumber(start) && dayNumber(o.arrival)<dayNumber(addDays(start,horizon)))
      .reduce((acc,o)=>acc+o._remaining,0);
    return {sku:material.sku,horizon,today:start,scenario,points,firstShortage,firstSafety,
      totalUnmet:nice(totalUnmet),endStock:nice(stock),expectedReceipts:nice(expectedReceipts),
      actualDailyDemand:nice(usage*multiplier),
      overdueOrders:pools.overdue.map(o=>({id:o.id,poId:o.poId,arrival:o.arrival,quantity:nice(o._remaining),supplier:o.supplier})),
      plannedExcluded:!pools.includePlanned&&pools.planned.length>0,plannedCount:pools.planned.length};
  }
  function baselineRisk(material, orders, horizon=DEFAULT_HORIZON, today=toDayString()){
    const base=simulate(material,orders,{horizon,today});
    const coverage=material.usage>0?material.stock/material.usage:null;
    const safety=Number(material.safety)||0;
    const policy=material.policy||null;
    const effSafety=safety>0?safety:((policy&&policy.recommendedSafety)||0);
    const replenishmentPoint=Math.ceil(material.usage*material.leadTime+effSafety);
    const pools=prepareOrders(material,orders,{_start:today});
    const ownOrders=pools.receipts.filter(o=>dayNumber(o.arrival)>=dayNumber(today)).sort((a,b)=>dayNumber(a.arrival)-dayNumber(b.arrival));
    const firstPO=ownOrders[0];
    let level='normal',reason='在所选推演期内未发现缺货或安全库存预警';
    if(base.firstShortage){level='critical';reason=`基准推演预计 ${base.firstShortage} 首次出现无法满足的需求，累计缺口约 ${nice(base.totalUnmet)} 件`;}
    else if(base.firstSafety){level='watch';reason=`预计 ${base.firstSafety} 低于安全库存 ${nice(effSafety)} 件，应检查补货安排`;}
    else if(coverage!==null && coverage>60){level='excess';reason=`按当前日均用量，库存覆盖约 ${Math.floor(coverage)} 天（超过展示阈值 60 天）；需结合保质期与业务计划确认是否积压`;}
    const flags=[];
    if(base.overdueOrders.length)flags.push(`有 ${base.overdueOrders.length} 笔逾期未到货采购单（${base.overdueOrders.slice(0,3).map(o=>o.id).join('、')}），未计入未来到货，需单独跟进`);
    if(base.plannedExcluded)flags.push(`有 ${base.plannedCount} 笔计划采购未计入基准情景（计划采购仅在显式假设情景参与）`);
    if(policy&&policy.belowROP)flags.push(`库存位置 ${nice(policy.inventoryPosition)} 低于推荐再订货点 ${nice(policy.recommendedROP)}（服务水平 ${policy.serviceLevel}）`);
    if(policy&&policy.fallbackReason)flags.push(`动态安全库存回退：${policy.fallbackReason}`);
    const daysUntilPO=firstPO?dayNumber(firstPO.arrival)-dayNumber(today):null;
    return {material,base,coverage:nice(coverage),replenishmentPoint,firstPO,daysUntilPO,level,reason,
      flags,overdueOrders:base.overdueOrders,policy:policy||null,effectiveSafety:nice(effSafety)};
  }
  function shortageQueue(risks){
    const rank={critical:0,watch:1};
    return risks.filter(r=>rank[r.level]!==undefined).sort((a,b)=>
      rank[a.level]-rank[b.level] || -(Number(a.base.totalUnmet)||0)-( -(Number(b.base.totalUnmet)||0)) ||
      (a.coverage??Infinity)-(b.coverage??Infinity) || String(a.material.sku).localeCompare(String(b.material.sku)));
  }
  function excessQueue(risks){
    return risks.filter(r=>r.level==='excess').sort((a,b)=>
      -(a.coverage??0)+(b.coverage??0) || String(a.material.sku).localeCompare(String(b.material.sku)));
  }
  function stressCandidates(risks){
    const q=shortageQueue(risks);
    if(q.length)return q;
    return risks.filter(r=>r.level==='normal'&&Number(r.material.usage)>0)
      .sort((a,b)=>(a.coverage??Infinity)-(b.coverage??Infinity)||String(a.material.sku).localeCompare(String(b.material.sku)));
  }
  function rankRisks(materials,orders,horizon,today){
    const risks=materials.map(m=>baselineRisk(m,orders,horizon,today));
    const normals=risks.filter(r=>r.level==='normal').sort((a,b)=>
      (a.coverage??Infinity)-(b.coverage??Infinity)||String(a.material.sku).localeCompare(String(b.material.sku)));
    return shortageQueue(risks).concat(excessQueue(risks),normals);
  }
  function findThreshold(material,orders,opts){
    const {today,horizon,type,maxValue,log,initial=1} = opts;
    const base=simulate(material,orders,{today,horizon});
    if(base.firstShortage) return {status:'already',threshold:0,result:base,rounds:0};
    const pools=prepareOrders(material,orders,{_start:today});
    const scope=opts.orderScope||{};
    const scopeIds=scope.orderIds?new Set(scope.orderIds):null;
    const scopeSupplier=scope.supplier||null;
    const inScope=(o)=>{if(scopeIds)return scopeIds.has(o.id)||scopeIds.has(o.poId);
      if(scopeSupplier)return o.supplier===scopeSupplier;return true;};
    let applicable;
    if(type==='demandIncrease') applicable=(Number(material.usage)||0)>0;
    else if(type==='purchaseDelay') applicable=pools.planned.some(inScope);
    else applicable=pools.receipts.some(o=>inScope(o)&&dayNumber(o.arrival)>=dayNumber(today));
    if(!applicable) return {status:'not-applicable',threshold:null,rounds:0};
    let rounds=0, lo=0, hi=initial, hit=null;
    const test=(n)=>{
      const res=simulate(material,orders,{today,horizon,orderScope:opts.orderScope,scenario:{type,value:n}});
      rounds++;
      if(typeof log==='function') log({sku:material.sku,type,round:rounds,value:n,failed:!!res.firstShortage,firstShortage:res.firstShortage,totalUnmet:res.totalUnmet});
      return res;
    };
    while(hi<=maxValue){
      const res=test(hi);
      if(res.firstShortage){hit=res;break;}
      lo=hi;
      if(hi===maxValue) break;
      hi=Math.min(maxValue,hi*2);
    }
    if(!hit) return {status:'not-found',threshold:null,rounds,maxTested:hi};
    while(hi-lo>1){
      const mid=Math.floor((hi+lo)/2);
      const res=test(mid);
      if(res.firstShortage){hi=mid;hit=res;}else{lo=mid;}
    }
    if(hi!==Number(hit.scenario.value))hit=test(hi);
    return {status:'found',threshold:hi,rounds,result:hit};
  }
  function findJointThreshold(material,orders,opts){
    const today=opts.today||toDayString(),horizon=opts.horizon||DEFAULT_HORIZON;
    const demandPct=Number(opts.demandPct||0),maxDelay=Number(opts.maxDelay||30)|0;
    const log=opts.log;let rounds=0,firstHit=null,monotone=true,prevFailed=false;
    const base=simulate(material,orders,{today,horizon});
    for(let d=0;d<=maxDelay;d++){
      const res=simulate(material,orders,{today,horizon,scenario:{type:'joint',demandPct,delayDays:d}});
      rounds++;const failed=!!res.firstShortage;
      if(failed&&!prevFailed&&firstHit!==null)monotone=false;
      if(failed){if(firstHit===null)firstHit={delay:d,result:res};prevFailed=true;}
      else if(firstHit!==null){monotone=false;prevFailed=false;}
      if(typeof log==='function')log({sku:material.sku,type:'joint',round:rounds,demandPct,delayDays:d,failed,firstShortage:res.firstShortage,totalUnmet:res.totalUnmet});
    }
    if(base.firstShortage)return {status:'already',threshold:0,demandPct,rounds,monotone:null,result:base};
    if(!firstHit)return {status:'not-found',threshold:null,demandPct,rounds,maxTested:maxDelay,monotone};
    return {status:'found',threshold:firstHit.delay,demandPct,rounds,monotone,result:firstHit.result,
      note:`联合情景采用线性扫描而非二分（单调性未假设）；monotone=${monotone} 为实测结果`};
  }
  function autonomousScan(materials,orders,opts={}){
    const today=opts.today||toDayString(),horizon=opts.horizon||DEFAULT_HORIZON;
    const maxSkus=opts.maxSkus||15;
    const log=[];
    const baseline=rankRisks(materials,orders,horizon,today);
    let candidates=stressCandidates(baseline);
    if(candidates.length<maxSkus){
      const seen=new Set(candidates.map(r=>r.material.sku));
      const extras=baseline.filter(r=>!seen.has(r.material.sku)&&r.level==='normal'&&Number(r.material.usage)>0)
        .sort((a,b)=>(a.coverage??Infinity)-(b.coverage??Infinity)||String(a.material.sku).localeCompare(String(b.material.sku)));
      candidates=candidates.concat(extras.slice(0,maxSkus-candidates.length));
    }
    if(candidates.length&&candidates[0].level!=='critical'&&candidates[0].level!=='watch')
      log.push({sku:'*',type:'select',message:'当前数据集无缺货/安全库存预警物料；压力测试改选覆盖天数最短的消耗型物料，积压物料不参与缺货测试'});
    const tested=candidates.slice(0,maxSkus).map(risk=>{
      const m=risk.material;
      log.push({sku:m.sku,type:'select',message:`按缺货可能性与预计缺口筛选 ${m.name}；先建立基线`});
      const thresholds={};
      const nearest=risk.firstPO, bufferDays=risk.coverage??Infinity;
      const pools=prepareOrders(m,orders,{_start:today});
      const relevant=pools.receipts.filter(o=>dayNumber(o.arrival)>=dayNumber(today));
      const hasPlan=pools.planned.length>0;
      const hasPlaced=relevant.length>0;
      let priorities=[];
      if(hasPlaced && nearest && bufferDays<=(risk.daysUntilPO??Infinity)+7)priorities.push('deliveryDelay');
      if(hasPlan)priorities.push('purchaseDelay');
      if(m.usage>0)priorities.push('demandIncrease');
      if(hasPlaced&&!priorities.includes('deliveryDelay'))priorities.push('deliveryDelay');
      for(const type of ['deliveryDelay','purchaseDelay','demandIncrease'])if(!priorities.includes(type))priorities.push(type);
      log.push({sku:m.sku,type:'select',message:`根据当前库存覆盖、预计到货与计划采购决定测试顺序：${priorities.join(' → ')}`});
      for(const type of priorities){
        thresholds[type]=findThreshold(m,orders,{today,horizon,type,maxValue:type==='demandIncrease'?200:30,
          initial:type==='demandIncrease'?10:1,log:(l)=>log.push(l)});
      }
      const firstFound=priorities.find(type=>thresholds[type].status==='found');
      let focusType=firstFound||'demandIncrease',focusValue=firstFound?thresholds[firstFound].threshold:20;
      if(risk.base.firstShortage) {
        const po=relevant.find(o=>o._status!==STATUS.PLANNED);
        focusType=po?'deliveryDelay':'demandIncrease';focusValue=po?2:20;
      }
      const focusScenario={type:focusType,value:focusValue};
      const impact=simulate(m,orders,{today,horizon,scenario:focusScenario});
      log.push({sku:m.sku,type:'decision',message:risk.base.firstShortage?
        `基线已有缺口，执行额外扰动以比较风险变化`:
        `已完成${Object.values(thresholds).reduce((a,t)=>a+t.rounds,0)}轮参数试验；生成重点情景`});
      return {risk,thresholds,focusScenario,impact,rounds:Object.values(thresholds).reduce((a,t)=>a+t.rounds,0)};
    });
    return {today,horizon,baseline,tested,log,totalRounds:log.filter(l=>typeof l.failed==='boolean').length};
  }
  function mitigate(material,orders,scenario,opts={}){
    const today=opts.today||toDayString(),horizon=opts.horizon||DEFAULT_HORIZON;
    const incident=simulate(material,orders,{today,horizon,scenario});
    const results=[];
    const pools=prepareOrders(material,orders,Object.assign({},opts,{_start:today,scenario}));
    const own=pools.receipts.filter(o=>dayNumber(o.arrival)>=dayNumber(today)).sort((a,b)=>dayNumber(a.arrival)-dayNumber(b.arrival));
    const nextConfirmed=own[0];
    if(nextConfirmed && scenario.type!=='supplierOutage'){
      const copied=clone(orders);
      const order=copied.find(o=>o.id===nextConfirmed.id);
      const original=order.arrival;
      const advance=Math.min(4,Math.max(0,dayNumber(order.arrival)-dayNumber(today)));
      if(scenario.type==='deliveryDelay'){
        const shortened=Math.max(0,Number(scenario.value||0)-Math.max(1,advance));
        const change=simulate(material,orders,{today,horizon,scenario:{...scenario,value:shortened}});
        results.push({id:'expedite',title:'与供应商协商缩短延期',action:`将假设延迟从 ${scenario.value} 天缩短至 ${shortened} 天`,
          note:'需供应商确认加急可行性与额外费用；不代表已落实',simulation:change});
      }else if(advance>0){
        order.arrival=addDays(original,-advance);
        const change=simulate(material,orders,{today,horizon,scenario,overrideOrders:copied});
        results.push({id:'expedite',title:'争取现有采购单提前到货',action:`尝试将 ${nextConfirmed.id} 提前 ${advance} 天，至 ${order.arrival}`,
          note:'假设供应商同意提前交付，未验证供应能力',simulation:change});
      }
    }
    const lead=Math.max(1,Math.ceil(Number(material.leadTime)||1));
    let qty=Math.ceil(Math.max(incident.totalUnmet+Number(material.safety||0),material.usage*3,1));
    if(opts.maxReplenQty!==undefined&&qty>opts.maxReplenQty)qty=Number(opts.maxReplenQty)|0;
    if(scenario.type==='supplierOutage'){
      results.push({id:'alternative',title:'核实备用供应来源',action:'收集替代供应商、可用数量和承诺交期后再开展补货仿真',
        note:'由于尚无可信的备用供应商供货数据，本预案不量化改善、不假设可用库存',simulation:incident,dataRequired:true});
    }else{
      const extra={id:'PROPOSAL',sku:material.sku,quantity:qty,arrival:addDays(today,lead),supplier:material.supplier,status:'confirmed'};
      const emergency=simulate(material,orders,{today,horizon,scenario,extraOrders:[extra]});
      results.push({id:'emergency',title:'提出临时补货计划',action:`建议核实 ${qty} 件是否可在 ${lead} 天后（${extra.arrival}）到货`,
        note:'到货日期仅按录入的供应商交期假设，未经确认；即使补货，也可能无法阻止更早的缺口',simulation:emergency});
    }
    if(material.usage>0){
      const effectiveUsage=(scenario.type==='demandIncrease'?material.usage*(1+scenario.value/100):material.usage)*0.9;
      const substitute={...material,usage:effectiveUsage};
      const safeScenario=scenario.type==='demandIncrease'?{type:'baseline'}:scenario;
      const reduction=simulate(substitute,orders,{today,horizon,scenario:safeScenario});
      results.push({id:'coordinate',title:'协调分批交付 / 需求平滑',action:'假设在测试窗口内将每日需求减少 10%',
        note:'需销售与客户确认；是需求协商情景，不代表原订单可直接减少',simulation:reduction});
    }
    return results.map(r=>({...r,improvement:nice(incident.totalUnmet-r.simulation.totalUnmet),
      resolvesShortage:!r.simulation.firstShortage}));
  }
  function backtestRun(material,orders,actualSeries,opts={}){
    // Replay actual historical consumption against the recorded stock snapshot.
    // actualSeries: per-day consumption aligned with opts.today (index 0 = today).
    // This is evaluation-only: parameters (usage estimate etc.) must come from an
    // earlier period, otherwise the result leaks the future.
    const res=simulate(material,orders,{today:opts.today,horizon:opts.horizon||actualSeries.length,
      scenario:{type:'baseline'},usageSeries:actualSeries});
    const observed=actualSeries.map((v,i)=>nice(v));
    return {...res,actualDailyDemand:nice(actualSeries.reduce((a,b)=>a+Number(b||0),0)/Math.max(1,actualSeries.length)),
      method:'backtest',actualSeries:observed};
  }
  function demoData(today=toDayString()){
    const materials=[
      {sku:'M-001',name:'精密电机轴承',stock:80,usage:10,safety:15,leadTime:5,supplier:'华东精工',unitCost:48},
      {sku:'M-002',name:'主控芯片',stock:120,usage:9,safety:25,leadTime:10,supplier:'科联半导体',unitCost:126},
      {sku:'M-003',name:'防潮包装膜',stock:600,usage:6,safety:40,leadTime:7,supplier:'华东精工',unitCost:3.8},
      {sku:'M-004',name:'功率转换模块',stock:45,usage:8,safety:20,leadTime:8,supplier:'锐控电子',unitCost:85},
      {sku:'M-005',name:'铝制散热片',stock:190,usage:7,safety:30,leadTime:5,supplier:'南方铝材',unitCost:19},
      {sku:'M-006',name:'专用连接器',stock:65,usage:6,safety:12,leadTime:6,supplier:'锐控电子',unitCost:22}
    ];
    const orders=[
      {id:'PO-1001',sku:'M-001',quantity:100,arrival:addDays(today,6),supplier:'华东精工',status:'已下单'},
      {id:'PO-1002',sku:'M-002',quantity:90,arrival:addDays(today,11),supplier:'科联半导体',status:'已下单'},
      {id:'PO-1003',sku:'M-004',quantity:60,arrival:addDays(today,8),supplier:'锐控电子',status:'已下单'},
      {id:'PO-1004',sku:'M-005',quantity:80,arrival:addDays(today,9),supplier:'南方铝材',status:'已下单'},
      {id:'PO-1005',sku:'M-006',quantity:40,arrival:addDays(today,7),supplier:'锐控电子',status:'计划'}
    ];
    return {materials,orders};
  }
  return {toDayString,dayNumber,addDays,simulate,backtestRun,baselineRisk,rankRisks,shortageQueue,excessQueue,
    stressCandidates,findThreshold,findJointThreshold,autonomousScan,mitigate,demoData,orderStatus,orderRemaining,
    prepareOrders,STATUS,DEFAULT_HORIZON};
});
