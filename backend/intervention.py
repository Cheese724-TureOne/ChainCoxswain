# -*- coding: utf-8 -*-
"""反向干预优化：从“发现风险”升级为“寻找可执行的最小干预”。

所有结论均由确定性库存仿真（engine.simulate）逐次验证，不做解析近似：
  * min_replenishment_qty   避免缺货所需的最小补货量（二分，补货量单调）
  * latest_feasible_arrival 给定补货量下最晚可行到货日（线性扫描，避免非单调假设）
  * min_demand_reduction    消除缺货所需的最小需求削减比例（二分，单调）
  * min_expedite_days       消除缺货所需的最小提前天数（作用于最早在途单，线性扫描）
  * min_recovery_plan       基准已缺货时的组合恢复方案

约束（opts）：maxReplenQty 补货上限（仓容/供应商能力）、minLeadDays 最小采购提前期、
maxExpediteDays 加急上限、unitCost/expediteCostPerUnitPct 成本（缺失时不输出成本）。
无法在约束内消除缺货的方案返回 feasible=False，不会被推荐为可行方案。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

try:
    from . import engine as E
except ImportError:  # pragma: no cover
    import engine as E  # type: ignore


def _sim_unmet(material: Dict, orders: List[Dict], opts: Dict) -> Dict:
    return E.simulate(material, orders, opts)


def min_replenishment_qty(material: Dict, orders: List[Dict], opts: Optional[Dict] = None) -> Dict:
    """Smallest replenishment quantity (arriving today+leadDays) that removes the
    shortage under opts.scenario (default: current scenario/baseline)."""
    opts = opts or {}
    today = opts.get("today") or E.to_day_string()
    horizon = opts.get("horizon") or E.DEFAULT_HORIZON
    scenario = opts.get("scenario") or {"type": "baseline", "value": 0}
    lead = int(opts.get("leadDays") if opts.get("leadDays") is not None
               else max(1, round(float(material.get("leadTime") or 1))))
    max_qty = int(opts.get("maxReplenQty") or 100000)
    base = _sim_unmet(material, orders, {"today": today, "horizon": horizon, "scenario": scenario})
    if not base["firstShortage"]:
        return {"feasible": True, "needed": False, "quantity": 0, "arrival": E.add_days(today, lead),
                "leadDays": lead, "baselineUnmet": base["totalUnmet"],
                "note": "基准/情景下未缺货，无需补货"}

    def unmet_with(qty: int) -> Dict:
        extra = {"id": "REPLEN-SEARCH", "sku": material["sku"], "quantity": qty,
                 "arrival": E.add_days(today, lead), "supplier": material.get("supplier"),
                 "status": E.STATUS_CONFIRMED}
        return _sim_unmet(material, orders, {"today": today, "horizon": horizon, "scenario": scenario,
                                             "extraOrders": [extra]})

    if unmet_with(max_qty)["firstShortage"]:
        return {"feasible": False, "needed": True, "quantity": None, "maxTested": max_qty,
                "leadDays": lead, "arrival": E.add_days(today, lead),
                "reason": "在补货上限 %s 件内仍无法消除缺货（受仓容/供应能力约束）" % max_qty}
    lo, hi = 0, max_qty
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if unmet_with(mid)["firstShortage"]:
            lo = mid
        else:
            hi = mid
    res = unmet_with(hi)
    out = {"feasible": True, "needed": True, "quantity": hi, "leadDays": lead,
           "arrival": E.add_days(today, lead), "baselineUnmet": base["totalUnmet"],
           "residualUnmet": res["totalUnmet"], "resolvesShortage": not res["firstShortage"],
           "note": "最小补货量由二分搜索 + 确定性仿真验证；到货日按最小采购提前期 %s 天假设" % lead}
    out.update(_cost(material, hi, opts))
    return out


def latest_feasible_arrival(material: Dict, orders: List[Dict], opts: Optional[Dict] = None) -> Dict:
    """Latest arrival date for a given replenishment quantity that still avoids the
    shortage. Linear scan over delay days (response is not assumed monotone)."""
    opts = opts or {}
    today = opts.get("today") or E.to_day_string()
    horizon = opts.get("horizon") or E.DEFAULT_HORIZON
    scenario = opts.get("scenario") or {"type": "baseline", "value": 0}
    qty = int(opts.get("quantity") or math.ceil(float(material.get("usage") or 0) * horizon +
                                                float(material.get("safety") or 0)))
    latest = None
    for d in range(0, horizon + 1):
        extra = {"id": "REPLEN-LATE", "sku": material["sku"], "quantity": qty,
                 "arrival": E.add_days(today, d), "supplier": material.get("supplier"),
                 "status": E.STATUS_CONFIRMED}
        res = _sim_unmet(material, orders, {"today": today, "horizon": horizon, "scenario": scenario,
                                            "extraOrders": [extra]})
        if not res["firstShortage"]:
            latest = d
    if latest is None:
        return {"feasible": False, "quantity": qty,
                "reason": "在 %s 天窗口内任何到货日都无法以 %s 件消除缺货" % (horizon, qty)}
    return {"feasible": True, "quantity": qty, "latestDelayDays": latest,
            "latestArrival": E.add_days(today, latest),
            "note": "最晚可行到货日由逐日线性扫描验证（不假设单调性）"}


def min_demand_reduction(material: Dict, orders: List[Dict], opts: Optional[Dict] = None) -> Dict:
    """Smallest demand-reduction percentage that removes the shortage (monotone)."""
    opts = opts or {}
    today = opts.get("today") or E.to_day_string()
    horizon = opts.get("horizon") or E.DEFAULT_HORIZON
    scenario = opts.get("scenario") or {"type": "baseline", "value": 0}
    base = _sim_unmet(material, orders, {"today": today, "horizon": horizon, "scenario": scenario})
    if not base["firstShortage"]:
        return {"feasible": True, "needed": False, "percent": 0}
    if float(material.get("usage") or 0) <= 0 and not isinstance(opts.get("usageSeries"), list):
        return {"feasible": False, "reason": "零需求物料不存在需求削减空间"}

    def sim_with(pct: int) -> Dict:
        factor = 1 - pct / 100.0
        m2 = dict(material)
        m2["usage"] = float(material.get("usage") or 0) * factor
        sim_opts: Dict[str, Any] = {"today": today, "horizon": horizon, "scenario": scenario}
        if isinstance(opts.get("usageSeries"), list):
            sim_opts["usageSeries"] = [float(v) * factor for v in opts["usageSeries"]]
        return _sim_unmet(m2, orders, sim_opts)

    if sim_with(100)["firstShortage"]:
        return {"feasible": False, "reason": "即使削减 100% 需求仍缺货（存在非需求类缺口）"}
    lo, hi = 0, 100
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if sim_with(mid)["firstShortage"]:
            lo = mid
        else:
            hi = mid
    return {"feasible": True, "needed": True, "percent": hi,
            "note": "最小需求削减比例由二分搜索 + 确定性仿真验证；需销售/客户确认后方可执行"}


def min_expedite_days(material: Dict, orders: List[Dict], opts: Optional[Dict] = None) -> Dict:
    """Smallest expedite (advance) days on the earliest confirmed receipt that removes
    the shortage. Linear scan up to maxExpediteDays (default 4, documented cap)."""
    opts = opts or {}
    today = opts.get("today") or E.to_day_string()
    horizon = opts.get("horizon") or E.DEFAULT_HORIZON
    scenario = opts.get("scenario") or {"type": "baseline", "value": 0}
    max_adv = int(opts.get("maxExpediteDays") or 4)
    base = _sim_unmet(material, orders, {"today": today, "horizon": horizon, "scenario": scenario})
    if not base["firstShortage"]:
        return {"feasible": True, "needed": False, "days": 0}
    for d in range(1, max_adv + 1):
        res = _sim_unmet(material, orders, {"today": today, "horizon": horizon,
                                            "scenario": {**scenario, "type": "expedite", "value": d}})
        if not res["firstShortage"]:
            return {"feasible": True, "needed": True, "days": d,
                    "note": "加急 %s 天可消除缺货；需供应商确认加急能力与费用" % d}
    return {"feasible": False, "maxTested": max_adv,
            "reason": "加急上限 %s 天内无法消除缺货" % max_adv}


def combined_recovery(material: Dict, orders: List[Dict], opts: Optional[Dict] = None) -> Dict:
    """Expedite + replenishment combination for shortages a single lever cannot fix
    (e.g. an early gap plus an end-of-horizon gap). Searches expedite days (desc) and
    replenishment arrival day (asc, bounded by the supplier lead time), quantity by
    bisection. Infeasible combinations are reported, never recommended."""
    opts = opts or {}
    today = opts.get("today") or E.to_day_string()
    horizon = opts.get("horizon") or E.DEFAULT_HORIZON
    scenario = opts.get("scenario") or {"type": "baseline", "value": 0}
    lead = int(opts.get("leadDays") if opts.get("leadDays") is not None
               else max(1, round(float(material.get("leadTime") or 1))))
    max_adv = int(opts.get("maxExpediteDays") or 4)
    max_qty = int(opts.get("maxReplenQty") or 100000)
    base = E.simulate(material, orders, {"today": today, "horizon": horizon, "scenario": scenario})
    if not base["firstShortage"]:
        return {"feasible": True, "needed": False}
    fs_idx = int(E.day_number(base["firstShortage"]) - E.day_number(today))

    def unmet_with(exp: int, arr_day: int, qty: int) -> Dict:
        extra = {"id": "REPLEN-COMB", "sku": material["sku"], "quantity": qty,
                 "arrival": E.add_days(today, arr_day), "supplier": material.get("supplier"),
                 "status": E.STATUS_CONFIRMED}
        sim_opts = {"today": today, "horizon": horizon, "extraOrders": [extra]}
        if exp > 0:
            sim_opts["scenario"] = {**scenario, "type": "expedite", "value": exp}
        else:
            sim_opts["scenario"] = scenario
        return E.simulate(material, orders, sim_opts)

    for exp in range(max_adv, -1, -1):
        for arr in range(min(fs_idx, lead), lead + 1):
            if unmet_with(exp, arr, max_qty)["firstShortage"]:
                continue
            lo, hi = 0, max_qty
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if unmet_with(exp, arr, mid)["firstShortage"]:
                    lo = mid
                else:
                    hi = mid
            res = unmet_with(exp, arr, hi)
            if not res["firstShortage"]:
                return {"feasible": True, "needed": True, "expediteDays": exp,
                        "replenQty": hi, "replenArrival": E.add_days(today, arr),
                        "residualUnmet": res["totalUnmet"],
                        "note": "组合方案（加急 %s 天 + 补货 %s 件于 %s 到货）经确定性仿真验证可消除缺货；"
                                "两项均需供应商确认后执行" % (exp, hi, E.add_days(today, arr))}
    return {"feasible": False,
            "reason": "在加急上限 %s 天、补货不晚于交期 %s 天的约束内，无组合方案可消除缺货" % (max_adv, lead)}


def min_recovery_plan(material: Dict, orders: List[Dict], opts: Optional[Dict] = None) -> Dict:
    """Combined answer for 'baseline already shortages': which minimal intervention
    resolves it, ranked by feasibility. Never recommends infeasible options."""
    opts = opts or {}
    plan = {
        "replenishment": min_replenishment_qty(material, orders, opts),
        "expedite": min_expedite_days(material, orders, opts),
        "demandReduction": min_demand_reduction(material, orders, opts),
        "combined": combined_recovery(material, orders, opts),
    }
    feasible = [k for k, v in plan.items() if v.get("feasible") and v.get("needed") is not False]
    plan["feasibleOptions"] = feasible
    plan["recommended"] = feasible[0] if feasible else None
    plan["note"] = ("基准已缺货：以下为解除缺货的最小干预条件，均经确定性仿真验证；"
                    "执行前必须人工确认供应与客户可行性")
    return plan


def _cost(material: Dict, qty: int, opts: Dict) -> Dict:
    unit = material.get("unitCost")
    if unit is None:
        return {"cost": None, "costNote": "缺少单位成本数据，不估算方案成本"}
    pct = float(opts.get("expediteCostPerUnitPct") or 0)
    return {"cost": round(qty * float(unit) * (1 + pct / 100.0), 2),
            "costNote": "成本 = 补货量 × 单位成本%s；仅当成本数据可靠时用于方案比较" % (
                "（含加急溢价 %s%%）" % pct if pct else "")}
