# -*- coding: utf-8 -*-
"""需求预测与波动建模（P1-6）。

按 Syntetos-Boylan-Croston 分类选择方法：
  * smooth (ADI<1.32, CV²<0.49)        -> 移动平均 / 指数平滑（SES）
  * erratic (ADI<1.32, CV²>=0.49)      -> 指数平滑（SES，波动大时标注）
  * intermittent / lumpy (ADI>=1.32)   -> Croston / SBA（默认 SBA，偏差更小）
  * 显著线性趋势                          -> Holt 线性趋势（叠加在 SES 水平上）

输出逐日预测序列，可直接作为 engine.simulate 的 usageSeries（需求增长等压力参数
由引擎按倍率施加到逐日序列上）。数据不足（<14 天）回退固定日均并标注原因。
缺失需求不等于零需求：序列缺口由调用方以 None 标注，本模块按“观测天数”计算均值。
截尾（缺货日销量被库存截断）由 backend/censored.py 上修后再进入本模块。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

ADI_CUT = 1.32
CV2_CUT = 0.49


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = _mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


def classify(series: List[float]) -> Dict:
    """Syntetos-Boylan-Croston classification on a daily series."""
    nonzero = [v for v in series if v > 0]
    n = len(series)
    if n == 0 or not nonzero:
        return {"category": "no-data", "adi": None, "cv2": None}
    adi = n / len(nonzero)
    cv2 = (_stdev(nonzero) / _mean(nonzero)) ** 2 if _mean(nonzero) > 0 else 0.0
    if adi < ADI_CUT and cv2 < CV2_CUT:
        cat = "smooth"
    elif adi < ADI_CUT:
        cat = "erratic"
    elif cv2 < CV2_CUT:
        cat = "intermittent"
    else:
        cat = "lumpy"
    return {"category": cat, "adi": round(adi, 3), "cv2": round(cv2, 3),
            "obsDays": n, "nonZeroDays": len(nonzero)}


def _has_trend(series: List[float]) -> Tuple[bool, float]:
    """Simple, explainable trend test: second-half mean vs first-half mean."""
    if len(series) < 8:
        return False, 0.0
    half = len(series) // 2
    a, b = _mean(series[:half]), _mean(series[half:])
    base = max(abs(a), 1e-9)
    change = (b - a) / base
    return abs(change) > 0.15, change


def _ses(series: List[float], alpha: float = 0.3) -> float:
    level = series[0]
    for v in series[1:]:
        level = alpha * v + (1 - alpha) * level
    return level


def _moving_average(series: List[float], window: int = 7) -> float:
    return _mean(series[-window:])


def _croston_sba(series: List[float], alpha: float = 0.1) -> float:
    """Croston with Syntetos-Boylan bias correction; returns per-period demand rate."""
    intervals: List[float] = []
    sizes: List[float] = []
    count = 0
    last_z = None
    for v in series:
        count += 1
        if v > 0:
            intervals.append(count)
            sizes.append(v)
            count = 0
            last_z = v
    if not intervals:
        return 0.0
    p = intervals[0]
    z = sizes[0]
    for i in range(1, len(intervals)):
        p = alpha * intervals[i] + (1 - alpha) * p
        z = alpha * sizes[i] + (1 - alpha) * z
    if p <= 0:
        return 0.0
    return (z / p) * (1 - alpha / 2)


def _tsb(series: List[float], alpha: float = 0.1, beta: float = 0.1) -> float:
    """Teunter-Syntetos-Babai: updates demand probability and size each period."""
    p = sum(1 for v in series if v > 0) / max(1, len(series))
    z = _mean([v for v in series if v > 0]) if any(v > 0 for v in series) else 0.0
    for v in series:
        if v > 0:
            p = beta * 1 + (1 - beta) * p
            z = alpha * v + (1 - alpha) * z
        else:
            p = beta * 0 + (1 - beta) * p
    return p * z


def forecast(series: Optional[List[float]], horizon: int = 14,
             fallback_usage: Optional[float] = None,
             method: Optional[str] = None) -> Dict:
    """Per-day forecast series + statistics. Falls back with an explicit reason."""
    obs = [float(v) for v in (series or []) if v is not None]
    if len(obs) < 14:
        usage = fallback_usage if fallback_usage is not None else (_mean(obs) if obs else 0.0)
        return {"method": "constant", "series": [usage] * horizon, "mu": usage,
                "sigma": _stdev(obs) if len(obs) >= 2 else 0.0,
                "cv": None, "classification": classify(obs),
                "fallbackReason": "日需求历史不足 14 天（实际 %d 天），回退固定日均，不输出概率性预测" % len(obs)}
    cls = classify(obs)
    mu, sigma = _mean(obs), _stdev(obs)
    trend, trend_change = _has_trend(obs)
    if method is None:
        if cls["category"] in ("intermittent", "lumpy"):
            method = "sba"
        elif trend:
            method = "holt"
        elif cls["category"] == "smooth":
            method = "ma7" if sigma / mu < 0.35 else "ses"
        else:
            method = "ses"
    if method == "sba":
        level = _croston_sba(obs)
    elif method == "tsb":
        level = _tsb(obs)
    elif method == "ses":
        level = _ses(obs)
    elif method == "ma7":
        level = _moving_average(obs)
    elif method == "holt":
        level = _ses(obs)
    else:
        level = mu
    out_series = [level] * horizon
    slope = 0.0
    if method == "holt":
        n = len(obs)
        xs = list(range(n))
        xm, ym = _mean(xs), _mean(obs)
        denom = sum((x - xm) ** 2 for x in xs) or 1.0
        slope = sum((x - xm) * (y - ym) for x, y in zip(xs, obs)) / denom
        out_series = [max(0.0, level + slope * (i + 1)) for i in range(horizon)]
    return {"method": method, "series": [max(0.0, v) for v in out_series],
            "mu": round(mu, 4), "sigma": round(sigma, 4),
            "cv": round(sigma / mu, 4) if mu > 0 else None,
            "trend": trend, "trendChange": round(trend_change, 4), "slope": round(slope, 4),
            "classification": cls, "fallbackReason": None,
            "note": "预测值仅用于条件推演；方法=%s，分类=%s" % (method, cls["category"])}


def apply_growth(fc_series: List[float], pct: float) -> List[float]:
    """Pressure parameter applied to the per-day forecast series (elementwise)."""
    factor = 1 + pct / 100.0
    return [max(0.0, v * factor) for v in fc_series]
