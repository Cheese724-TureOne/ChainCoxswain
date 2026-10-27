# -*- coding: utf-8 -*-
"""ChainCoxswain deterministic planning and stress-testing engine (Python port).

This module is a function-by-function port of ../engine.js so that backend
services (Qwen agent tool execution, MCP server, A.zip backtests, report
generation) share exactly the same deterministic calculations as the browser.

All inventory quantities are calculated here. The LLM never computes numbers.
Convention: receipts arrive at the start of a day, before that day's consumption.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import math
import re
from typing import Any, Callable, Dict, List, Optional

DEFAULT_HORIZON = 14


def _pad(n: int) -> str:
    return str(n).rjust(2, "0")


def to_day_string(date: Optional[Any] = None) -> str:
    if isinstance(date, str):
        return date[:10]
    if date is None:
        date = _dt.date.today()
    if isinstance(date, _dt.datetime):
        date = date.date()
    return "%04d-%02d-%02d" % (date.year, date.month, date.day)


def day_number(date: Any) -> float:
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", str(date or "").strip())
    if not m:
        return float("nan")
    y, mm, dd = (int(g) for g in m.groups())
    try:
        d = _dt.date(y, mm, dd)
    except ValueError:
        return float("nan")
    epoch = _dt.date(1970, 1, 1)
    return float((d - epoch).days)


def add_days(date: str, days: Any) -> str:
    t = day_number(date)
    if not _finite(t):
        raise ValueError("无效日期 " + str(date))
    d = _dt.date(1970, 1, 1) + _dt.timedelta(days=int(t + int(days)))
    return "%04d-%02d-%02d" % (d.year, d.month, d.day)


def _finite(n: Any) -> bool:
    if n is None:
        return False
    try:
        return n == n and n not in (float("inf"), float("-inf"))
    except Exception:
        return False


def _nice(n: Any):
    """Mirror JS: Math.round(n*100)/100 (half toward +inf). Integral results become ints."""
    if not _finite(n):
        return None
    v = math.floor(n * 100 + 0.5) / 100
    return int(v) if v == int(v) else v


# ------------------------------------------------------------------ order semantics
# Canonical status vocabulary. Raw import values are normalised here so both the
# Python and JavaScript engines interpret purchase orders identically.
STATUS_CONFIRMED = "confirmed"    # 已下单/在途/已确认：有效在途，未收部分计入未来到货
STATUS_PLANNED = "planned"        # 计划采购：基准情景不参与，仅在显式假设情景参与
STATUS_CANCELLED = "cancelled"    # 已取消：任何情景都不参与到货
STATUS_RECEIVED = "received"      # 已收货：数量已在库存快照中，不得重复补充
STATUS_PARTIAL = "partial"        # 部分收货：仅剩余未收数量计入未来到货

_STATUS_ALIASES = {
    "confirmed": STATUS_CONFIRMED, "已下单": STATUS_CONFIRMED, "在途": STATUS_CONFIRMED,
    "已确认": STATUS_CONFIRMED, "open": STATUS_CONFIRMED, "ordered": STATUS_CONFIRMED,
    "in-transit": STATUS_CONFIRMED, "in_transit": STATUS_CONFIRMED, "运输中": STATUS_CONFIRMED,
    "planned": STATUS_PLANNED, "计划": STATUS_PLANNED, "plan": STATUS_PLANNED, "计划采购": STATUS_PLANNED,
    "cancelled": STATUS_CANCELLED, "已取消": STATUS_CANCELLED, "取消": STATUS_CANCELLED,
    "received": STATUS_RECEIVED, "已收货": STATUS_RECEIVED, "已完成": STATUS_RECEIVED,
    "closed": STATUS_RECEIVED, "done": STATUS_RECEIVED,
    "partial": STATUS_PARTIAL, "部分收货": STATUS_PARTIAL, "部分接收": STATUS_PARTIAL,
}


def order_status(order: Dict) -> str:
    """Normalise any raw status string to the canonical vocabulary (unknown -> confirmed)."""
    raw = str(order.get("status") or "").strip()
    if not raw:
        return STATUS_CONFIRMED
    return _STATUS_ALIASES.get(raw, _STATUS_ALIASES.get(raw.lower(), STATUS_CONFIRMED))


def order_remaining(order: Dict) -> float:
    """Quantity still expected to arrive. Received orders contribute nothing: their
    quantity is already inside the stock snapshot, so counting it again would
    understate shortage risk."""
    status = order_status(order)
    if status in (STATUS_CANCELLED, STATUS_RECEIVED):
        return 0.0
    qty = float(order.get("quantity") or 0)
    received = float(order.get("receivedQty") or order.get("received_qty") or 0)
    return max(0.0, qty - received)


def prepare_orders(material: Dict, all_orders: List[Dict], opts: Dict) -> Dict:
    """Split orders of one SKU into semantic pools.

    Returns dict with keys:
      receipts  : confirmed/partial orders with remaining>0 and a valid arrival date
      overdue   : subset of receipts whose arrival is before the simulation start
                  (not evidence of received stock; excluded from future receipts)
      planned   : planned purchases (baseline excludes them)
      active    : receipts that participate in the current run (overdue excluded;
                  planned included only when the scenario/opt explicitly assumes so)
    """
    start = opts.get("_start")
    sku = material["sku"]
    source = opts.get("overrideOrders")
    if source is None:                      # explicit [] means "no orders in transit"
        source = all_orders
    scenario = opts.get("scenario") or {"type": "baseline", "value": 0}
    include_planned = bool(opts.get("includePlanned")) or scenario.get("type") == "purchaseDelay"
    receipts, planned = [], []
    for o in source:
        if o.get("sku") != sku:
            continue
        o = dict(o)
        o["_status"] = order_status(o)
        o["_remaining"] = order_remaining(o)
        if o["_status"] == STATUS_PLANNED:
            if _finite(day_number(o.get("arrival"))):
                planned.append(o)
            continue
        if o["_status"] in (STATUS_CANCELLED, STATUS_RECEIVED) or o["_remaining"] <= 0:
            continue
        if not _finite(day_number(o.get("arrival"))):
            continue
        receipts.append(o)
    start_num = day_number(start)
    overdue = [o for o in receipts if day_number(o.get("arrival")) < start_num]
    on_time = [o for o in receipts if day_number(o.get("arrival")) >= start_num]
    active = list(on_time) + (list(planned) if include_planned else [])
    return {"receipts": receipts, "overdue": overdue, "planned": planned,
            "active": active, "includePlanned": include_planned}


def simulate(material: Dict, all_orders: List[Dict], opts: Optional[Dict] = None) -> Dict:
    """Deterministic day-by-day inventory simulation. Mirrors engine.js simulate().

    Order semantics (see prepare_orders): cancelled/received orders never add stock,
    partial orders add only the unreceived remainder, planned purchases stay out of
    the baseline, overdue receipts are flagged instead of assumed to arrive.
    """
    opts = opts or {}
    start = opts.get("today") or to_day_string()
    horizon = opts.get("horizon") or DEFAULT_HORIZON
    scenario = opts.get("scenario") or {"type": "baseline", "value": 0}
    if horizon < 1 or horizon > 365:
        raise ValueError("仿真天数超出范围")
    pools = prepare_orders(material, all_orders, {**opts, "_start": start})
    orders = pools["active"]
    usage = float(material.get("usage") or 0)
    value = float(scenario.get("value") or 0)
    multiplier = 1.0
    if scenario.get("type") == "demandIncrease":
        multiplier = 1 + value / 100.0
    if scenario.get("type") == "demandReduction":
        multiplier = max(0.0, 1 - value / 100.0)
    if scenario.get("type") == "joint":
        multiplier = 1 + float(scenario.get("demandPct") or 0) / 100.0
        joint_delay = int(scenario.get("delayDays") or 0)
        for o in orders:
            if o["_status"] != STATUS_PLANNED:
                o["arrival"] = add_days(o["arrival"], joint_delay)
    scope_ids = None
    scope_supplier = None
    scope = opts.get("orderScope") or {}
    if isinstance(scope, dict):
        scope_ids = set(scope["orderIds"]) if scope.get("orderIds") else None
        scope_supplier = scope.get("supplier")

    def _in_scope(o: Dict) -> bool:
        if scope_ids is not None:
            return o.get("id") in scope_ids or o.get("poId") in scope_ids
        if scope_supplier is not None:
            return o.get("supplier") == scope_supplier
        return True

    if scenario.get("type") in ("deliveryDelay", "purchaseDelay", "expedite"):
        if scenario.get("type") == "purchaseDelay":
            pool = [o for o in orders if o["_status"] == STATUS_PLANNED]
        else:
            pool = [o for o in orders if o["_status"] != STATUS_PLANNED]
        future = [o for o in pool if _in_scope(o) and day_number(o.get("arrival")) >= day_number(start)]
        future.sort(key=lambda o: day_number(o.get("arrival")))
        targets = future if (scope_ids is not None or scope_supplier) else future[:1]
        for t in targets:
            t["arrival"] = add_days(t["arrival"], (-1 if scenario.get("type") == "expedite" else 1) * int(value))
    if scenario.get("type") == "supplierOutage":
        orders = [o for o in orders if o.get("supplier") != scenario.get("supplier")]
    if isinstance(opts.get("extraOrders"), list):
        for o in opts["extraOrders"]:
            eo = dict(o)
            eo.setdefault("_status", order_status(eo))
            eo.setdefault("_remaining", order_remaining(eo))
            orders.append(eo)
    usage_series = [float(v) for v in opts["usageSeries"]] if isinstance(opts.get("usageSeries"), list) else None
    stock = float(material.get("stock") or 0)
    total_unmet = 0.0
    first_shortage = None
    first_safety = None
    points: List[Dict] = []
    for i in range(horizon):
        date = add_days(start, i)
        received = sum(o["_remaining"] for o in orders if o.get("arrival") == date)
        base_usage = usage_series[i] if (usage_series is not None and i < len(usage_series)
                                         and _finite(usage_series[i])) else usage
        day_usage = max(0.0, base_usage * multiplier)
        available = stock + received
        unmet = max(0.0, day_usage - available)
        stock = max(0.0, available - day_usage)
        total_unmet += unmet
        if unmet > 1e-8 and first_shortage is None:
            first_shortage = date
        if stock < float(material.get("safety") or 0) and first_safety is None:
            first_safety = date
        points.append({"date": date, "day": i + 1, "received": _nice(received), "demand": _nice(day_usage),
                       "stock": _nice(stock), "unmet": _nice(unmet)})
    expected_receipts = sum(o["_remaining"] for o in orders
                            if day_number(o.get("arrival")) >= day_number(start)
                            and day_number(o.get("arrival")) < day_number(add_days(start, horizon)))
    return {"sku": material["sku"], "horizon": horizon, "today": start, "scenario": scenario,
            "points": points, "firstShortage": first_shortage, "firstSafety": first_safety,
            "totalUnmet": _nice(total_unmet), "endStock": _nice(stock),
            "expectedReceipts": _nice(expected_receipts), "actualDailyDemand": _nice(usage * multiplier),
            "overdueOrders": [{"id": o.get("id"), "poId": o.get("poId"), "arrival": o.get("arrival"),
                               "quantity": _nice(o["_remaining"]), "supplier": o.get("supplier")}
                              for o in pools["overdue"]],
            "plannedExcluded": not pools["includePlanned"] and len(pools["planned"]) > 0,
            "plannedCount": len(pools["planned"])}


def baseline_risk(material: Dict, orders: List[Dict], horizon: int = DEFAULT_HORIZON, today: Optional[str] = None) -> Dict:
    today = today or to_day_string()
    base = simulate(material, orders, {"horizon": horizon, "today": today})
    usage = float(material.get("usage") or 0)
    coverage = float(material.get("stock") or 0) / usage if usage > 0 else None
    safety = float(material.get("safety") or 0)
    policy = material.get("policy") or {}
    effective_safety = safety if safety > 0 else (policy.get("recommendedSafety") or 0)
    replenishment_point = math.ceil(usage * float(material.get("leadTime") or 0) + effective_safety)
    pools = prepare_orders(material, orders, {"_start": today})
    own_orders = sorted([o for o in pools["receipts"] if day_number(o.get("arrival")) >= day_number(today)],
                        key=lambda o: day_number(o.get("arrival")))
    first_po = own_orders[0] if own_orders else None
    level, reason = "normal", "在所选推演期内未发现缺货或安全库存预警"
    if base["firstShortage"]:
        level = "critical"
        reason = "基准推演预计 %s 首次出现无法满足的需求，累计缺口约 %s 件" % (base["firstShortage"], _nice(base["totalUnmet"]))
    elif base["firstSafety"]:
        level = "watch"
        reason = "预计 %s 低于安全库存 %s 件，应检查补货安排" % (base["firstSafety"], _nice(effective_safety))
    elif coverage is not None and coverage > 60:
        level = "excess"
        reason = "按当前日均用量，库存覆盖约 %d 天（超过展示阈值 60 天）；需结合保质期与业务计划确认是否积压" % int(coverage)
    flags = []
    if base["overdueOrders"]:
        flags.append("有 %d 笔逾期未到货采购单（%s），未计入未来到货，需单独跟进" % (
            len(base["overdueOrders"]), "、".join(str(o["id"]) for o in base["overdueOrders"][:3])))
    if base["plannedExcluded"]:
        flags.append("有 %d 笔计划采购未计入基准情景（计划采购仅在显式假设情景参与）" % base["plannedCount"])
    if policy:
        if policy.get("belowROP"):
            flags.append("库存位置 %s 低于推荐再订货点 %s（服务水平 %s）" % (
                _nice(policy.get("inventoryPosition")), _nice(policy.get("recommendedROP")),
                policy.get("serviceLevel")))
        if policy.get("fallbackReason"):
            flags.append("动态安全库存回退：%s" % policy["fallbackReason"])
    days_until_po = int(day_number(first_po["arrival"]) - day_number(today)) if first_po else None
    return {"material": material, "base": base, "coverage": _nice(coverage), "replenishmentPoint": replenishment_point,
            "firstPO": first_po, "daysUntilPO": days_until_po, "level": level, "reason": reason,
            "flags": flags, "overdueOrders": base["overdueOrders"], "policy": policy or None,
            "effectiveSafety": _nice(effective_safety)}


def shortage_queue(risks: List[Dict]) -> List[Dict]:
    """Independent shortage ranking: more likely / larger impact first.
    Key: level (critical before watch), then larger unmet first, then shorter coverage."""
    level_rank = {"critical": 0, "watch": 1}
    return sorted([r for r in risks if r["level"] in level_rank],
                  key=lambda a: (level_rank[a["level"]],
                                 -(float(a["base"]["totalUnmet"] or 0)),
                                 a["coverage"] if a["coverage"] is not None else float("inf"),
                                 str(a["material"].get("sku"))))


def excess_queue(risks: List[Dict]) -> List[Dict]:
    """Independent overstock ranking: longest coverage (most capital tied up) first."""
    return sorted([r for r in risks if r["level"] == "excess"],
                  key=lambda a: (-(a["coverage"] if a["coverage"] is not None else 0),
                                 str(a["material"].get("sku"))))


def stress_candidates(risks: List[Dict]) -> List[Dict]:
    """SKUs the shortage stress test should examine: shortage queue first; when no
    shortage signal exists, the shortest-coverage consuming SKUs. Overstocked SKUs
    are never stress-tested for shortage."""
    queue = shortage_queue(risks)
    if queue:
        return queue
    normals = [r for r in risks if r["level"] == "normal" and float(r["material"].get("usage") or 0) > 0]
    return sorted(normals, key=lambda a: (a["coverage"] if a["coverage"] is not None else float("inf"),
                                          str(a["material"].get("sku"))))


def rank_risks(materials: List[Dict], orders: List[Dict], horizon: int = DEFAULT_HORIZON, today: Optional[str] = None) -> List[Dict]:
    """Flat, backwards-compatible ordering: shortage queue, then overstock queue,
    then normals. Use shortage_queue()/excess_queue() for the independent views."""
    today = today or to_day_string()
    risks = [baseline_risk(m, orders, horizon, today) for m in materials]
    normals = sorted([r for r in risks if r["level"] == "normal"],
                     key=lambda a: (a["coverage"] if a["coverage"] is not None else float("inf"),
                                    str(a["material"].get("sku"))))
    return shortage_queue(risks) + excess_queue(risks) + normals


def find_threshold(material: Dict, orders: List[Dict], opts: Dict) -> Dict:
    """Doubling probe + integer bisection for the minimal integer perturbation that
    first triggers a shortage. Mirrors engine.js findThreshold().

    opts additions:
      orderScope : {"orderIds":[...]} / {"supplier":name} to target specific orders;
      seekRecovery : when the baseline already shortages, also compute the minimal
                     intervention that resolves it (see intervention.py).
    """
    today = opts.get("today") or to_day_string()
    horizon = opts.get("horizon") or DEFAULT_HORIZON
    type_ = opts["type"]
    max_value = opts.get("maxValue")
    log = opts.get("log")
    initial = opts.get("initial", 1)
    base = simulate(material, orders, {"today": today, "horizon": horizon})
    if base["firstShortage"]:
        out = {"status": "already", "threshold": 0, "result": base, "rounds": 0}
        if opts.get("seekRecovery"):
            try:
                from . import intervention as IV
            except ImportError:  # pragma: no cover
                import intervention as IV  # type: ignore
            out["recovery"] = IV.min_recovery_plan(material, orders, {"today": today, "horizon": horizon})
        return out
    pools = prepare_orders(material, orders, {"_start": today})
    scope = opts.get("orderScope") or {}
    scope_ids = set(scope["orderIds"]) if scope.get("orderIds") else None
    scope_supplier = scope.get("supplier")

    def _in_scope(o: Dict) -> bool:
        if scope_ids is not None:
            return o.get("id") in scope_ids or o.get("poId") in scope_ids
        if scope_supplier is not None:
            return o.get("supplier") == scope_supplier
        return True

    if type_ == "demandIncrease":
        applicable = float(material.get("usage") or 0) > 0 or any(
            _finite(v) and float(v) > 0 for v in (opts.get("usageSeries") or []))
    elif type_ == "purchaseDelay":
        applicable = any(_in_scope(o) for o in pools["planned"])
    else:
        applicable = any(_in_scope(o) for o in pools["receipts"]
                         if day_number(o.get("arrival")) >= day_number(today))
    if not applicable:
        return {"status": "not-applicable", "threshold": None, "rounds": 0}
    rounds = 0
    lo, hi, hit = 0, int(initial), None

    def test(n: int) -> Dict:
        nonlocal rounds, hit
        res = simulate(material, orders, {"today": today, "horizon": horizon,
                                          "orderScope": opts.get("orderScope"),
                                          "scenario": {"type": type_, "value": n}})
        rounds += 1
        if callable(log):
            log({"sku": material["sku"], "type": type_, "round": rounds, "value": n,
                 "failed": bool(res["firstShortage"]), "firstShortage": res["firstShortage"],
                 "totalUnmet": res["totalUnmet"]})
        return res

    while hi <= max_value:
        res = test(hi)
        if res["firstShortage"]:
            hit = res
            break
        lo = hi
        if hi == max_value:
            break
        hi = min(max_value, hi * 2)
    if hit is None:
        return {"status": "not-found", "threshold": None, "rounds": rounds, "maxTested": hi}
    while hi - lo > 1:
        mid = (hi + lo) // 2
        res = test(mid)
        if res["firstShortage"]:
            hi, hit = mid, res
        else:
            lo = mid
    if hi != int(hit["scenario"]["value"]):
        hit = test(hi)
    return {"status": "found", "threshold": hi, "rounds": rounds, "result": hit}


def find_joint_threshold(material: Dict, orders: List[Dict], opts: Dict) -> Dict:
    """Combined demand-growth + delivery-delay stress.

    The joint response surface is not guaranteed monotone (delaying one order can
    push a receipt out of the window entirely), so bisection is NOT used: a linear
    scan over delay days is performed and monotonicity is verified empirically.
    opts: demandPct (fixed demand growth %), maxDelay (days), horizon, today.
    """
    today = opts.get("today") or to_day_string()
    horizon = opts.get("horizon") or DEFAULT_HORIZON
    demand_pct = float(opts.get("demandPct") or 0)
    max_delay = int(opts.get("maxDelay") or 30)
    log = opts.get("log")
    rounds = 0
    first_hit = None
    monotone = True
    prev_failed = False
    for d in range(0, max_delay + 1):
        res = simulate(material, orders, {"today": today, "horizon": horizon,
                                          "scenario": {"type": "joint", "demandPct": demand_pct, "delayDays": d}})
        rounds += 1
        failed = bool(res["firstShortage"])
        if failed and not prev_failed and first_hit is not None:
            monotone = False
        if failed:
            if first_hit is None:
                first_hit = {"delay": d, "result": res}
            prev_failed = True
        elif first_hit is not None:
            monotone = False
            prev_failed = False
        if callable(log):
            log({"sku": material["sku"], "type": "joint", "round": rounds, "demandPct": demand_pct,
                 "delayDays": d, "failed": failed, "firstShortage": res["firstShortage"],
                 "totalUnmet": res["totalUnmet"]})
    base = simulate(material, orders, {"today": today, "horizon": horizon})
    if base["firstShortage"]:
        return {"status": "already", "threshold": 0, "demandPct": demand_pct, "rounds": rounds,
                "monotone": None, "result": base}
    if first_hit is None:
        return {"status": "not-found", "threshold": None, "demandPct": demand_pct,
                "rounds": rounds, "maxTested": max_delay, "monotone": monotone}
    return {"status": "found", "threshold": first_hit["delay"],
            "demandPct": demand_pct, "rounds": rounds, "monotone": monotone, "result": first_hit["result"],
            "note": "联合情景采用线性扫描而非二分（单调性未假设）；monotone=%s 为实测结果"
                    % ("true" if monotone else "false")}


def autonomous_scan(materials: List[Dict], orders: List[Dict], opts: Optional[Dict] = None) -> Dict:
    """Rule-driven adaptive explorer: select fragile SKUs, decide test order from
    business evidence, search thresholds per perturbation, then build a focus scenario."""
    opts = opts or {}
    today = opts.get("today") or to_day_string()
    horizon = opts.get("horizon") or DEFAULT_HORIZON
    max_skus = opts.get("maxSkus", 15)
    log: List[Dict] = []
    baseline = rank_risks(materials, orders, horizon, today)
    candidates = stress_candidates(baseline)
    if len(candidates) < max_skus:
        seen = {r["material"]["sku"] for r in candidates}
        extras = [r for r in baseline if r["material"]["sku"] not in seen
                  and r["level"] == "normal" and float(r["material"].get("usage") or 0) > 0]
        extras.sort(key=lambda a: (a["coverage"] if a["coverage"] is not None else float("inf"),
                                   str(a["material"].get("sku"))))
        candidates = candidates + extras[:max_skus - len(candidates)]
    if candidates and candidates[0]["level"] not in ("critical", "watch"):
        log.append({"sku": "*", "type": "select",
                    "message": "当前数据集无缺货/安全库存预警物料；压力测试改选覆盖天数最短的消耗型物料，积压物料不参与缺货测试"})
    tested = []
    for risk in candidates[:max_skus]:
        m = risk["material"]
        log.append({"sku": m["sku"], "type": "select",
                    "message": "按缺货可能性与预计缺口筛选 %s；先建立基线" % m.get("name")})
        thresholds: Dict[str, Dict] = {}
        nearest = risk.get("firstPO")
        buffer_days = risk["coverage"] if risk["coverage"] is not None else float("inf")
        pools = prepare_orders(m, orders, {"_start": today})
        relevant = [o for o in pools["receipts"] if day_number(o.get("arrival")) >= day_number(today)]
        has_plan = len(pools["planned"]) > 0
        has_placed = len(relevant) > 0
        priorities: List[str] = []
        if has_placed and nearest and buffer_days <= (risk["daysUntilPO"] or float("inf")) + 7:
            priorities.append("deliveryDelay")
        if has_plan:
            priorities.append("purchaseDelay")
        if float(m.get("usage") or 0) > 0:
            priorities.append("demandIncrease")
        if has_placed and "deliveryDelay" not in priorities:
            priorities.append("deliveryDelay")
        for t in ("deliveryDelay", "purchaseDelay", "demandIncrease"):
            if t not in priorities:
                priorities.append(t)
        log.append({"sku": m["sku"], "type": "select",
                    "message": "根据当前库存覆盖、预计到货与计划采购决定测试顺序：%s" % " → ".join(priorities)})
        for type_ in priorities:
            thresholds[type_] = find_threshold(m, orders, {
                "today": today, "horizon": horizon, "type": type_,
                "maxValue": 200 if type_ == "demandIncrease" else 30,
                "initial": 10 if type_ == "demandIncrease" else 1,
                "log": lambda l: log.append(l)})
        first_found = next((t for t in priorities if thresholds[t]["status"] == "found"), None)
        focus_type = first_found or "demandIncrease"
        focus_value = thresholds[first_found]["threshold"] if first_found else 20
        if risk["base"]["firstShortage"]:
            po = next((o for o in relevant if o["_status"] != STATUS_PLANNED), None)
            focus_type = "deliveryDelay" if po else "demandIncrease"
            focus_value = 2 if po else 20
        focus_scenario = {"type": focus_type, "value": focus_value}
        impact = simulate(m, orders, {"today": today, "horizon": horizon, "scenario": focus_scenario})
        log.append({"sku": m["sku"], "type": "decision",
                    "message": ("基线已有缺口，执行额外扰动以比较风险变化" if risk["base"]["firstShortage"]
                                else "已完成%d轮参数试验；生成重点情景" % sum(t["rounds"] for t in thresholds.values()))})
        tested.append({"risk": risk, "thresholds": thresholds, "focusScenario": focus_scenario, "impact": impact,
                       "rounds": sum(t["rounds"] for t in thresholds.values())})
    return {"today": today, "horizon": horizon, "baseline": baseline, "tested": tested, "log": log,
            "totalRounds": sum(1 for l in log if isinstance(l.get("failed"), bool))}


def mitigate(material: Dict, orders: List[Dict], scenario: Dict, opts: Optional[Dict] = None) -> List[Dict]:
    opts = opts or {}
    today = opts.get("today") or to_day_string()
    horizon = opts.get("horizon") or DEFAULT_HORIZON
    incident = simulate(material, orders, {"today": today, "horizon": horizon, "scenario": scenario})
    results: List[Dict] = []
    pools = prepare_orders(material, orders, {**opts, "_start": today, "scenario": scenario})
    own = sorted([o for o in pools["receipts"] if day_number(o.get("arrival")) >= day_number(today)],
                 key=lambda o: day_number(o.get("arrival")))
    next_confirmed = own[0] if own else None
    if next_confirmed and scenario.get("type") != "supplierOutage":
        copied = copy.deepcopy(orders)
        order = next(o for o in copied if o.get("id") == next_confirmed["id"])
        original = order["arrival"]
        advance = min(4, max(0, int(day_number(order["arrival"]) - day_number(today))))
        if scenario.get("type") == "deliveryDelay":
            shortened = max(0, int(scenario.get("value") or 0) - max(1, advance))
            change = simulate(material, orders, {"today": today, "horizon": horizon,
                                                 "scenario": {**scenario, "value": shortened}})
            results.append({"id": "expedite", "title": "与供应商协商缩短延期",
                            "action": "将假设延迟从 %s 天缩短至 %s 天" % (scenario.get("value"), shortened),
                            "note": "需供应商确认加急可行性与额外费用；不代表已落实", "simulation": change})
        elif advance > 0:
            order["arrival"] = add_days(original, -advance)
            change = simulate(material, orders, {"today": today, "horizon": horizon, "scenario": scenario,
                                                 "overrideOrders": copied})
            results.append({"id": "expedite", "title": "争取现有采购单提前到货",
                            "action": "尝试将 %s 提前 %s 天，至 %s" % (next_confirmed["id"], advance, order["arrival"]),
                            "note": "假设供应商同意提前交付，未验证供应能力", "simulation": change})
    lead = max(1, int(round(float(material.get("leadTime") or 1))))
    max_qty = opts.get("maxReplenQty")
    qty = math.ceil(max(incident["totalUnmet"] + float(material.get("safety") or 0),
                        float(material.get("usage") or 0) * 3, 1))
    if max_qty is not None and qty > max_qty:
        qty = int(max_qty)
    if scenario.get("type") == "supplierOutage":
        results.append({"id": "alternative", "title": "核实备用供应来源",
                        "action": "收集替代供应商、可用数量和承诺交期后再开展补货仿真",
                        "note": "由于尚无可信的备用供应商供货数据，本预案不量化改善、不假设可用库存",
                        "simulation": incident, "dataRequired": True})
    else:
        # 假设情景下的建议补货单：标记为 confirmed 才会参与仿真到货（计划采购不进基准）
        extra = {"id": "PROPOSAL", "sku": material["sku"], "quantity": qty, "arrival": add_days(today, lead),
                 "supplier": material.get("supplier"), "status": STATUS_CONFIRMED}
        emergency = simulate(material, orders, {"today": today, "horizon": horizon, "scenario": scenario,
                                                "extraOrders": [extra]})
        results.append({"id": "emergency", "title": "提出临时补货计划",
                        "action": "建议核实 %s 件是否可在 %s 天后（%s）到货" % (qty, lead, extra["arrival"]),
                        "note": "到货日期仅按录入的供应商交期假设，未经确认；即使补货，也可能无法阻止更早的缺口",
                        "simulation": emergency})
    if float(material.get("usage") or 0) > 0:
        effective_usage = (float(material["usage"]) * (1 + float(scenario.get("value") or 0) / 100.0)
                           if scenario.get("type") == "demandIncrease" else float(material["usage"])) * 0.9
        substitute = {**material, "usage": effective_usage}
        safe_scenario = {"type": "baseline"} if scenario.get("type") == "demandIncrease" else scenario
        reduction = simulate(substitute, orders, {"today": today, "horizon": horizon, "scenario": safe_scenario})
        results.append({"id": "coordinate", "title": "协调分批交付 / 需求平滑",
                        "action": "假设在测试窗口内将每日需求减少 10%",
                        "note": "需销售与客户确认；是需求协商情景，不代表原订单可直接减少",
                        "simulation": reduction})
    return [{**r, "improvement": _nice(incident["totalUnmet"] - r["simulation"]["totalUnmet"]),
             "resolvesShortage": not r["simulation"]["firstShortage"]} for r in results]


def backtest_run(material: Dict, orders: List[Dict], actual_series: List[float], opts: Optional[Dict] = None) -> Dict:
    """Replay actual historical consumption against the recorded snapshot (evaluation only)."""
    opts = opts or {}
    horizon = opts.get("horizon") or len(actual_series)
    res = simulate(material, orders, {"today": opts.get("today"), "horizon": horizon,
                                      "scenario": {"type": "baseline"}, "usageSeries": actual_series})
    mean = sum(float(v or 0) for v in actual_series) / max(1, len(actual_series))
    return {**res, "actualDailyDemand": _nice(mean), "method": "backtest",
            "actualSeries": [_nice(v) for v in actual_series]}


def demo_data(today: Optional[str] = None) -> Dict:
    today = today or to_day_string()
    materials = [
        {"sku": "M-001", "name": "精密电机轴承", "stock": 80, "usage": 10, "safety": 15, "leadTime": 5, "supplier": "华东精工", "unitCost": 48},
        {"sku": "M-002", "name": "主控芯片", "stock": 120, "usage": 9, "safety": 25, "leadTime": 10, "supplier": "科联半导体", "unitCost": 126},
        {"sku": "M-003", "name": "防潮包装膜", "stock": 600, "usage": 6, "safety": 40, "leadTime": 7, "supplier": "华东精工", "unitCost": 3.8},
        {"sku": "M-004", "name": "功率转换模块", "stock": 45, "usage": 8, "safety": 20, "leadTime": 8, "supplier": "锐控电子", "unitCost": 85},
        {"sku": "M-005", "name": "铝制散热片", "stock": 190, "usage": 7, "safety": 30, "leadTime": 5, "supplier": "南方铝材", "unitCost": 19},
        {"sku": "M-006", "name": "专用连接器", "stock": 65, "usage": 6, "safety": 12, "leadTime": 6, "supplier": "锐控电子", "unitCost": 22},
    ]
    orders = [
        {"id": "PO-1001", "sku": "M-001", "quantity": 100, "arrival": add_days(today, 6), "supplier": "华东精工", "status": "已下单"},
        {"id": "PO-1002", "sku": "M-002", "quantity": 90, "arrival": add_days(today, 11), "supplier": "科联半导体", "status": "已下单"},
        {"id": "PO-1003", "sku": "M-004", "quantity": 60, "arrival": add_days(today, 8), "supplier": "锐控电子", "status": "已下单"},
        {"id": "PO-1004", "sku": "M-005", "quantity": 80, "arrival": add_days(today, 9), "supplier": "南方铝材", "status": "已下单"},
        {"id": "PO-1005", "sku": "M-006", "quantity": 40, "arrival": add_days(today, 7), "supplier": "锐控电子", "status": "计划"},
    ]
    return {"materials": materials, "orders": orders}


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
