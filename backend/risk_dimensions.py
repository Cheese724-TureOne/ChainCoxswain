# -*- coding: utf-8 -*-
"""多维度内部风险模块（P2-10）与分仓风险识别（P2-11）。

六类风险各自独立计算指标，缺失数据显式标注（不补假数）：
  1 shortage      库存短缺：基准推演缺口 + Monte Carlo 缺货概率
  2 volatility    需求波动：CV、ADI 与分类
  3 fulfillment   采购履约：供应商历史准时率、平均延期、满足率
  4 excess        库存积压：覆盖天数与占用资金（有 unitCost 时）
  5 warehouse     仓库库存：分仓覆盖天数离散度（有分仓数据时）
  6 criticality   关键物料：年消耗金额（usage×unitCost）代理 + 类别标注

综合决策优先级采用规则分层（P0/P1/P2/P3），不在缺少可靠概率与损失数据时
伪造精确综合分数；每层规则与命中原因随结果返回，可解释、可复核。

分仓（P2-11）：以 SKU+warehouse_id 为分析单元；他仓库存仅在调用方显式提供
调拨能力（leadDays 与 costPerUnit）时才计入可用库存，否则单独列出并标注。
"""
from __future__ import annotations

from typing import Dict, List, Optional

try:
    from . import engine as E
except ImportError:  # pragma: no cover
    import engine as E  # type: ignore


def supplier_fulfillment(history: List[Dict]) -> Dict[str, Dict]:
    """Per-supplier fulfillment stats from completed purchase history.
    history rows: {supplier, expected_at, received_at, quantity_ordered, quantity_received}"""
    out: Dict[str, Dict] = {}
    for h in history:
        sup = h.get("supplier") or "未提供"
        rec = out.setdefault(sup, {"completed": 0, "onTime": 0, "delaySum": 0,
                                   "ordered": 0.0, "received": 0.0})
        if not h.get("received_at") or not h.get("expected_at"):
            continue
        rec["completed"] += 1
        delay = int(E.day_number(h["received_at"]) - E.day_number(h["expected_at"]))
        if delay <= 0:
            rec["onTime"] += 1
        rec["delaySum"] += max(0, delay)
        rec["ordered"] += float(h.get("quantity_ordered") or 0)
        rec["received"] += float(h.get("quantity_received") or 0)
    for sup, r in out.items():
        n = r["completed"]
        r["onTimeRate"] = round(r["onTime"] / n, 4) if n else None
        r["avgDelayDays"] = round(r["delaySum"] / n, 2) if n else None
        r["fillRate"] = round(r["received"] / r["ordered"], 4) if r["ordered"] > 0 else None
        r["sampleSize"] = n
    return out


def dimension_scores(material: Dict, risk: Dict, policy: Optional[Dict],
                     forecast: Optional[Dict], mc: Optional[Dict],
                     fulfillment: Optional[Dict], warehouse: Optional[Dict]) -> Dict:
    m = material
    sup = m.get("supplier") or "未提供"
    ful = (fulfillment or {}).get(sup) or {}
    dims: Dict[str, Dict] = {}
    dims["shortage"] = {
        "level": risk["level"] if risk["level"] in ("critical", "watch") else "none",
        "totalUnmet": risk["base"]["totalUnmet"],
        "firstShortage": risk["base"]["firstShortage"],
        "stockoutProb14d": (mc or {}).get("horizons", {}).get("14", {}).get("stockoutProbability"),
        "missing": [] if (risk["base"]["totalUnmet"] is not None) else ["基准推演缺失"],
    }
    if dims["shortage"]["stockoutProb14d"] is None:
        dims["shortage"]["missing"].append("缺货概率（历史波动不足）")
    cls = (forecast or {}).get("classification") or {}
    dims["volatility"] = {"cv": (forecast or {}).get("cv"), "adi": cls.get("adi"),
                          "category": cls.get("category"),
                          "missing": [] if (forecast or {}).get("cv") is not None else ["需求波动历史不足"]}
    dims["fulfillment"] = {"onTimeRate": ful.get("onTimeRate"), "avgDelayDays": ful.get("avgDelayDays"),
                           "fillRate": ful.get("fillRate"), "sampleSize": ful.get("sampleSize"),
                           "missing": [] if ful.get("onTimeRate") is not None else ["该供应商无已完成采购历史"]}
    coverage = risk["coverage"]
    dims["excess"] = {"coverageDays": coverage,
                      "tiedUpValue": round(float(m.get("stock") or 0) * float(m["unitCost"]), 2)
                      if m.get("unitCost") is not None and coverage is not None else None,
                      "missing": [] if m.get("unitCost") is not None else ["缺单位成本，不估算占用资金"]}
    dims["warehouse"] = warehouse or {"missing": ["无分仓数据（当前为跨仓聚合视图）"]}
    spend = float(m.get("usage") or 0) * float(m["unitCost"]) * 365 if m.get("unitCost") is not None else None
    dims["criticality"] = {"annualSpend": round(spend, 2) if spend is not None else None,
                           "category": m.get("category"),
                           "missing": [] if spend is not None else ["缺单位成本，关键性仅按类别参考"]}
    return dims


def priority_tier(dims: Dict) -> Dict:
    """Rule-based tiering. Rules are returned so the decision is auditable."""
    hits: List[str] = []
    sh = dims["shortage"]
    ful = dims["fulfillment"]
    if sh["level"] == "critical":
        tier = "P0"
        hits.append("基准推演已存在缺货缺口")
    elif (sh.get("stockoutProb14d") or 0) >= 0.5:
        tier = "P0"
        hits.append("14 天缺货概率 ≥ 50%")
    elif sh["level"] == "watch" or (sh.get("stockoutProb14d") or 0) >= 0.2:
        tier = "P1"
        hits.append("安全库存预警或 14 天缺货概率 ≥ 20%")
    elif ful.get("onTimeRate") is not None and ful["onTimeRate"] < 0.7 and ful.get("sampleSize", 0) >= 5:
        tier = "P1"
        hits.append("供应商准时率 < 70%（样本 ≥5）")
    elif dims["excess"]["coverageDays"] is not None and dims["excess"]["coverageDays"] > 60:
        tier = "P2"
        hits.append("库存覆盖 > 60 天，积压风险")
    else:
        tier = "P3"
        hits.append("无显著短缺/履约/积压信号")
    return {"tier": tier, "rules": hits,
            "note": "分层规则固定可复核；未使用加权综合分，避免在缺概率/损失数据时伪造精度"}


def warehouse_risk(wh_units: List[Dict], orders: List[Dict], today: str,
                   horizon: int = 14, transship: Optional[Dict] = None) -> List[Dict]:
    """Per (sku, warehouse) risk. Other warehouses' stock counts as available ONLY
    when transship={enabled:True, leadDays:int, costPerUnit:float} is provided."""
    transship = transship or {}
    usable_elsewhere = bool(transship.get("enabled")) and transship.get("leadDays") is not None
    by_sku: Dict[str, List[Dict]] = {}
    for u in wh_units:
        by_sku.setdefault(u["sku"], []).append(u)
    out = []
    for u in wh_units:
        mat = {"sku": u["sku"], "name": "%s @ %s" % (u["sku"], u["warehouseId"]),
               "stock": u["stock"], "usage": u["usage"], "safety": u.get("safety") or 0,
               "leadTime": u.get("leadTime") or 0}
        own = [o for o in orders if o.get("sku") == u["sku"] and o.get("warehouseId") == u["warehouseId"]]
        risk = E.baseline_risk(mat, own, horizon, today)
        siblings = [w for w in by_sku[u["sku"]] if w["warehouseId"] != u["warehouseId"]]
        elsewhere = sum(w["stock"] for w in siblings)
        rec = {"sku": u["sku"], "warehouseId": u["warehouseId"], "level": risk["level"],
               "stock": u["stock"], "usage": u["usage"], "coverageDays": risk["coverage"],
               "firstShortage": risk["base"]["firstShortage"], "totalUnmet": risk["base"]["totalUnmet"],
               "otherWarehouseStock": elsewhere,
               "otherStockUsable": usable_elsewhere,
               "transship": transship if usable_elsewhere else None}
        if not usable_elsewhere and elsewhere > 0 and risk["level"] == "critical":
            rec["note"] = ("其他仓共有 %s 件库存，但未提供有效调拨交期/成本约束，不计入可用库存；"
                           "如确认可调拨，请传入 transship 参数重算" % elsewhere)
        out.append(rec)
    return out


def coverage_dispersion(wh_units: List[Dict]) -> Dict[str, Dict]:
    """Imbalance indicator per SKU: spread of coverage days across warehouses."""
    by_sku: Dict[str, List[Dict]] = {}
    for u in wh_units:
        cov = (float(u["stock"]) / u["usage"]) if u.get("usage") else None
        if cov is not None:
            by_sku.setdefault(u["sku"], []).append(cov)
    out = {}
    for sku, covs in by_sku.items():
        if len(covs) < 2:
            continue
        out[sku] = {"warehouses": len(covs), "minCoverage": round(min(covs), 1),
                    "maxCoverage": round(max(covs), 1),
                    "imbalanceRatio": round(max(covs) / max(min(covs), 1e-9), 1)}
    return out
