# -*- coding: utf-8 -*-
"""动态安全库存与再订货点（P1-5）。

公式（需求与交期近似独立、提前期需求近似正态时的经典模型）：
    SS  = z * sqrt(L̄ * σD² + μD² * σL²)
    ROP = μD * L̄ + SS
其中 μD/σD 为日需求均值/标准差，L̄/σL 为采购交期均值/标准差，z 为目标服务水平系数。

适用条件与限制（结果中显式标注）：
  * 日需求序列不足 14 天 -> 回退到录入安全库存/0，并给出 fallbackReason；
  * 交期样本不足 2 个 -> σL 取 0（仅考虑需求波动），标注 leadTimeSigmaAssumed；
  * 需求高度间歇（ADI>=1.32）时正态近似偏弱，标注 normalApproxWeak；
  * 库存位置 IP = 在手库存 + 有效在途未收量（confirmed/partial 剩余）- 预留/欠交；
    逾期在途单不计入 IP（无依据假设其到货），并单独标注 overdueExcluded。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

try:
    from . import engine as E
except ImportError:  # pragma: no cover
    import engine as E  # type: ignore

SERVICE_LEVELS = {0.90: 1.2816, 0.95: 1.6449, 0.99: 2.3263}


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


def z_for(service_level: float) -> float:
    key = min(SERVICE_LEVELS.keys(), key=lambda k: abs(k - service_level))
    return SERVICE_LEVELS[key]


def safety_stock(mu_d: float, sigma_d: float, lead_mean: float, lead_sigma: float,
                 service_level: float = 0.95) -> float:
    z = z_for(service_level)
    return z * math.sqrt(max(0.0, lead_mean * sigma_d ** 2 + (mu_d ** 2) * (lead_sigma ** 2)))


def dynamic_policy(demand_series: Optional[List[float]], lead_times: Optional[List[float]],
                   service_level: float = 0.95, fallback_safety: float = 0.0,
                   lead_time_mean: Optional[float] = None) -> Dict:
    """Compute recommended SS/ROP from history. Returns explicit fallback when the
    data cannot support the formula (never fabricates a 'precise' number)."""
    series = [float(v) for v in (demand_series or []) if v is not None]
    notes: List[str] = []
    if len(series) < 14:
        return {"available": False, "recommendedSafety": fallback_safety,
                "recommendedROP": None, "serviceLevel": service_level,
                "fallbackReason": "日需求历史不足 14 天（实际 %d 天），回退录入安全库存" % len(series)}
    mu_d = _mean(series)
    sigma_d = _stdev(series)
    leads = [float(v) for v in (lead_times or []) if v is not None and v >= 0]
    if len(leads) >= 2:
        lead_mean = _mean(leads)
        lead_sigma = _stdev(leads)
    else:
        lead_mean = float(lead_time_mean or 0.0)
        lead_sigma = 0.0
        notes.append("交期样本不足 2 个，σL 取 0（仅考虑需求波动）")
    if lead_mean <= 0:
        return {"available": False, "recommendedSafety": fallback_safety, "recommendedROP": None,
                "serviceLevel": service_level,
                "fallbackReason": "缺少采购交期均值，无法计算再订货点"}
    ss = safety_stock(mu_d, sigma_d, lead_mean, lead_sigma, service_level)
    rop = mu_d * lead_mean + ss
    adi = _average_demand_interval(series)
    if adi >= 1.32:
        notes.append("需求间歇性较高（ADI=%.2f），正态近似偏弱，建议结合 Croston/SBA 预测复核" % adi)
    return {"available": True, "muD": round(mu_d, 4), "sigmaD": round(sigma_d, 4),
            "leadMean": round(lead_mean, 2), "leadSigma": round(lead_sigma, 2),
            "serviceLevel": service_level, "z": z_for(service_level),
            "recommendedSafety": round(ss, 2), "recommendedROP": math.ceil(rop),
            "adi": round(adi, 2), "notes": notes}


def _average_demand_interval(series: List[float]) -> float:
    nonzero = [i for i, v in enumerate(series) if v > 0]
    if len(nonzero) < 2:
        return float(len(series))
    gaps = [nonzero[i + 1] - nonzero[i] for i in range(len(nonzero) - 1)]
    return _mean(gaps)


def inventory_position(material: Dict, orders: List[Dict], today: str) -> Dict:
    """IP = on-hand + confirmed/partial unreceived (future arrivals) - reserved.
    Overdue receipts are excluded and reported (no basis to assume they arrive)."""
    pools = E.prepare_orders(material, orders, {"_start": today})
    on_hand = float(material.get("stock") or 0)
    inbound = sum(o["_remaining"] for o in pools["receipts"]
                  if E.day_number(o.get("arrival")) >= E.day_number(today))
    overdue = sum(o["_remaining"] for o in pools["overdue"])
    reserved = float(material.get("reserved") or 0)
    return {"onHand": on_hand, "inboundConfirmed": inbound, "reserved": reserved,
            "inventoryPosition": on_hand + inbound - reserved,
            "overdueExcluded": overdue,
            "overdueOrders": [o.get("id") for o in pools["overdue"]]}


def policy_for_material(material: Dict, orders: List[Dict], today: str,
                        service_level: float = 0.95,
                        demand_series: Optional[List[float]] = None,
                        lead_times: Optional[List[float]] = None) -> Dict:
    """Full policy record attached to a material: recommendation + position + alerts."""
    fallback = float(material.get("safety") or 0)
    pol = dynamic_policy(demand_series, lead_times, service_level, fallback,
                         lead_time_mean=material.get("leadTime"))
    pos = inventory_position(material, orders, today)
    out = dict(pol)
    out.update({"inventoryPosition": pos["inventoryPosition"],
                "onHand": pos["onHand"], "inboundConfirmed": pos["inboundConfirmed"],
                "overdueExcluded": pos["overdueExcluded"],
                "overdueOrders": pos["overdueOrders"]})
    if pol["available"]:
        out["needsReorder"] = pos["inventoryPosition"] < pol["recommendedROP"]
        out["belowROP"] = out["needsReorder"]
    else:
        out["needsReorder"] = None
        out["belowROP"] = None
    return out
