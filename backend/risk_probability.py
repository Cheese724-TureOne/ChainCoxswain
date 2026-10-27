# -*- coding: utf-8 -*-
"""Monte Carlo 缺货概率仿真（P1-7）。

复用确定性引擎 engine.simulate：每次抽样生成一条“未来需求逐日序列 + 在途单到货偏移”
的情景，跑一次完整仿真，聚合统计：
  * 未来 7/14/30 天缺货概率
  * 预计未满足需求量（均值）与第 95 百分位缺口
  * 首次缺货日期分布（p10/p50/p90）
  * 跌破安全库存的概率

不确定性来源（必须有历史依据，否则拒绝输出“概率”）：
  * 需求波动：历史逐日需求序列的残差 bootstrap；序列不足 14 天 -> available=False；
  * 到货偏移：历史实际交期与计划交期之差的 empirical 分布；无样本时偏移恒 0 并标注。

固定随机种子可复现；仿真次数与窗口可配置。缺少真实波动历史时只返回明确标注的
情景分析（scenarioOnly=True），绝不把人为假设宣称为真实缺货概率。
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional

try:
    from . import engine as E
except ImportError:  # pragma: no cover
    import engine as E  # type: ignore

DEFAULT_N = 1000
HORIZONS = (7, 14, 30)


def _percentile(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    frac = k - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def monte_carlo(material: Dict, orders: List[Dict], opts: Optional[Dict] = None) -> Dict:
    opts = opts or {}
    today = opts.get("today") or E.to_day_string()
    n = int(opts.get("n") or DEFAULT_N)
    seed = opts.get("seed")
    seed = int(seed) if seed is not None else 42
    horizons = list(opts.get("horizons") or HORIZONS)
    max_h = max(horizons)
    demand_series = opts.get("demandSeries")          # 历史逐日需求（训练窗口）
    delay_samples = opts.get("delaySamples")          # 历史到货偏移（天，实际-计划）
    safety = float(material.get("safety") or 0)
    policy_safety = (material.get("policy") or {}).get("recommendedSafety")
    eff_safety = safety if safety > 0 else (policy_safety or 0)

    obs = [float(v) for v in (demand_series or []) if v is not None]
    if len(obs) < 14:
        return {"available": False, "sku": material["sku"], "scenarioOnly": True,
                "reason": "日需求历史不足 14 天（实际 %d 天），无法估计需求波动分布；"
                          "仅可提供标注的情景分析，不输出缺货概率" % len(obs),
                "scenario": _scenario_only(material, orders, opts)}
    mu = sum(obs) / len(obs)
    residuals = [v - mu for v in obs]
    delays = [int(v) for v in (delay_samples or []) if v is not None]
    rng = random.Random(seed)

    pools = E.prepare_orders(material, orders, {"_start": today})
    base_orders = [{**o, "quantity": o["_remaining"], "receivedQty": 0, "status": E.STATUS_CONFIRMED}
                   for o in pools["receipts"]]

    shortage_by_h = {h: 0 for h in horizons}
    unmet_by_h = {h: [] for h in horizons}
    below_safety = 0
    first_dates: List[float] = []
    for _ in range(n):
        series = []
        for i in range(max_h):
            d = mu + residuals[rng.randrange(len(residuals))]
            series.append(max(0.0, d))
        sim_orders = []
        for o in base_orders:
            oo = dict(o)
            if delays:
                oo["arrival"] = E.add_days(oo["arrival"], delays[rng.randrange(len(delays))])
            sim_orders.append(oo)
        res = E.simulate(material, sim_orders, {"today": today, "horizon": max_h,
                                                "overrideOrders": sim_orders,
                                                "usageSeries": series})
        first_num = E.day_number(res["firstShortage"]) if res["firstShortage"] else None
        if first_num is not None:
            first_dates.append(first_num - E.day_number(today))
        for h in horizons:
            unmet_h = sum(p["unmet"] or 0 for p in res["points"][:h])
            unmet_by_h[h].append(unmet_h)
            if any((p["unmet"] or 0) > 1e-9 for p in res["points"][:h]):
                shortage_by_h[h] += 1
        if eff_safety > 0 and any((p["stock"] or 0) < eff_safety for p in res["points"]):
            below_safety += 1
    start_num = E.day_number(today)
    return {
        "available": True, "sku": material["sku"], "n": n, "seed": seed,
        "method": "Monte Carlo（需求残差 bootstrap + 到货偏移 empirical 分布）",
        "delayModel": "empirical(%d 样本)" % len(delays) if delays else "无历史偏移样本，偏移恒 0（已标注）",
        "horizons": {
            str(h): {
                "stockoutProbability": round(shortage_by_h[h] / n, 4),
                "expectedUnmet": round(sum(unmet_by_h[h]) / n, 2),
                "p95Unmet": round(_percentile(unmet_by_h[h], 0.95) or 0.0, 2),
            } for h in horizons},
        "firstShortageDay": {
            "p10": round(_percentile(first_dates, 0.10), 1) if first_dates else None,
            "p50": round(_percentile(first_dates, 0.50), 1) if first_dates else None,
            "p90": round(_percentile(first_dates, 0.90), 1) if first_dates else None,
            "probability": round(len(first_dates) / n, 4)},
        "belowSafetyProbability": round(below_safety / n, 4) if eff_safety > 0 else None,
        "safetyUsed": eff_safety,
        "note": "概率基于历史需求波动与到货偏移的抽样推演，为条件概率而非确定性预测；"
                "种子固定可复现",
    }


def _scenario_only(material: Dict, orders: List[Dict], opts: Dict) -> Dict:
    """Labelled deterministic what-if used when probability cannot be estimated."""
    today = opts.get("today") or E.to_day_string()
    horizon = int(opts.get("horizon") or 14)
    out = {}
    for pct in (0, 20, 50):
        res = E.simulate(material, orders, {"today": today, "horizon": horizon,
                                            "scenario": {"type": "demandIncrease", "value": pct}})
        out["demand+%d%%" % pct] = {"firstShortage": res["firstShortage"],
                                    "totalUnmet": res["totalUnmet"]}
    return {"label": "情景分析（非概率）：需求上涨 0/20/50% 的确定性推演结果", "scenarios": out}
