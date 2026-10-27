// Cross-check battery: emits JSON of engine.js results for comparison with engine.py
'use strict';
const E = require('../engine.js');
const start = '2026-10-26';
const { materials, orders } = E.demoData(start);
const out = {};
const m1 = materials[0];
out.base14 = E.simulate(m1, orders, { today: start, horizon: 14 });
out.delay2 = E.simulate(m1, orders, { today: start, horizon: 14, scenario: { type: 'deliveryDelay', value: 2 } });
out.delay3 = E.simulate(m1, orders, { today: start, horizon: 14, scenario: { type: 'deliveryDelay', value: 3 } });
out.demand30 = E.simulate(m1, orders, { today: start, horizon: 14, scenario: { type: 'demandIncrease', value: 30 } });
out.thrDelay = E.findThreshold(m1, orders, { today: start, horizon: 14, type: 'deliveryDelay', maxValue: 30 });
out.thrDemand = E.findThreshold(m1, orders, { today: start, horizon: 14, type: 'demandIncrease', maxValue: 200 });
out.thrPurch = E.findThreshold(m1, orders, { today: start, horizon: 14, type: 'purchaseDelay', maxValue: 30 });
out.risks = E.rankRisks(materials, orders, 14, start).map(r => ({ sku: r.material.sku, level: r.level, coverage: r.coverage, firstShortage: r.base.firstShortage, totalUnmet: r.base.totalUnmet, replenishmentPoint: r.replenishmentPoint, reason: r.reason, firstPO: r.firstPO, daysUntilPO: r.daysUntilPO }));
const scan = E.autonomousScan(materials, orders, { today: start, horizon: 14 });
out.scanTrimmed = {
  totalRounds: scan.totalRounds,
  tested: scan.tested.map(t => ({ sku: t.risk.material.sku, rounds: t.rounds, focus: t.focusScenario, thresholds: Object.fromEntries(Object.entries(t.thresholds).map(([k, v]) => [k, { status: v.status, threshold: v.threshold, rounds: v.rounds }])) })),
  log: scan.log
};
out.mitigateDelay3 = E.mitigate(m1, orders, { type: 'deliveryDelay', value: 3 }, { today: start, horizon: 14 }).map(r => ({ id: r.id, improvement: r.improvement, resolves: r.resolvesShortage, totalUnmet: r.simulation.totalUnmet, firstShortage: r.simulation.firstShortage, dataRequired: r.dataRequired }));
out.mitigateOutage = E.mitigate(materials[1], orders, { type: 'supplierOutage', supplier: '科联半导体' }, { today: start, horizon: 14 }).map(r => ({ id: r.id, improvement: r.improvement, resolves: r.resolvesShortage, dataRequired: r.dataRequired }));
out.overdue = E.simulate({ sku: 'X', stock: 0, usage: 1, safety: 0, leadTime: 3, supplier: 'S' }, [{ id: 'PO', sku: 'X', arrival: '2026-10-24', quantity: 99, supplier: 'S', status: '已下单' }], { today: start, horizon: 2 });
out.backtest = E.backtestRun(m1, orders, [9, 11, 10, 8, 12, 10, 10, 9, 11, 10, 10, 10, 9, 10], { today: start });
const mixOrders = [
  { id: 'A', sku: 'M-001', quantity: 50, arrival: '2026-10-29', supplier: 's', status: '已取消' },
  { id: 'B', sku: 'M-001', quantity: 50, arrival: '2026-10-30', supplier: 's', status: '已收货' },
  { id: 'C', sku: 'M-001', quantity: 50, receivedQty: 20, arrival: '2026-10-31', supplier: 's', status: '部分收货' },
  { id: 'D', sku: 'M-001', quantity: 50, arrival: '2026-11-01', supplier: 's', status: '计划' }];
out.overrideEmpty = E.simulate(m1, orders, { today: start, horizon: 14, overrideOrders: [] });
out.statusMix = E.simulate(m1, mixOrders, { today: start, horizon: 14 });
out.statusMixPlanned = E.simulate(m1, mixOrders, { today: start, horizon: 14, scenario: { type: 'purchaseDelay', value: 1 } });
out.seriesGrowth = E.simulate(m1, orders, { today: start, horizon: 5, scenario: { type: 'demandIncrease', value: 50 }, usageSeries: [10, 10, 10, 10, 10] });
out.joint = E.simulate(m1, orders, { today: start, horizon: 14, scenario: { type: 'joint', demandPct: 20, delayDays: 2 } });
out.thrJoint = E.findJointThreshold(m1, orders, { today: start, horizon: 14, demandPct: 20, maxDelay: 10 });
out.dates = { add1: E.addDays('2026-12-31', 1), addN1: E.addDays('2026-01-01', -1), dayNum: E.dayNumber('2026-10-26') };
out.ordersAfter = orders; // prove immutability of originals
console.log(JSON.stringify(out));
