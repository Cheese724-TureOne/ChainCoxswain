# -*- coding: utf-8 -*-
"""截尾需求（censored demand）实证与确定性上修演示。

数据来源：FreshRetailNet-50K（Dingdong-Inc，CC BY 4.0 公开基准，data/retail/eval.parquet）。
问题：缺货日的观测销量被库存上限截断（卖光即停），直接用销量均值估计日均需求会系统性低估。
本模块做两件事（全部确定性计算，不依赖 LLM）：
  1. 实证统计：缺货天数占比、缺货日 vs 不缺货日的观测销量差异；
  2. 上修演示：对缺货日按"营业时段内销售速率 × 完整营业小时"估算潜在需求，给出上修幅度分布与实例。

边界：sale_amount 为全局归一化后的数值（原数据集声明），上修幅度只作方法论演示，
不构成对本产品性能或预测能力的承诺；无缺货标注的数据集（如 A.zip）不套用本修正。
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENSORED_DIR = os.path.join(BASE_DIR, "data", "retail")
OPEN_HOURS = 17  # 营业时段 6:00-22:00 共 17 小时（数据集字段定义）


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or math.isinf(x)) else x


def analyze() -> Dict:
    path = os.path.join(CENSORED_DIR, "eval.parquet")
    if not os.path.exists(path):
        return {"available": False,
                "error": "data/retail/eval.parquet 不存在（FreshRetailNet-50K 评估集未放入项目）"}
    df = pd.read_parquet(path)
    total = int(len(df))
    stockout = df["stock_hour6_22_cnt"] > 0
    so_days = int(stockout.sum())

    g = df.groupby(stockout)["sale_amount"].agg(["count", "mean", "max"])
    normal = g.loc[False] if False in g.index else pd.Series({"count": 0, "mean": 0, "max": 0})
    so = g.loc[True] if True in g.index else pd.Series({"count": 0, "mean": 0, "max": 0})

    # ---- 确定性上修：缺货日潜在需求 = 有货小时销售速率 × 完整营业小时 ----
    sub = df[stockout & (df["stock_hour6_22_cnt"] < OPEN_HOURS)].copy()
    sub["in_stock_hours"] = OPEN_HOURS - sub["stock_hour6_22_cnt"]
    sub["rate"] = sub["sale_amount"] / sub["in_stock_hours"]
    sub["imputed"] = sub["rate"] * OPEN_HOURS
    sub["uplift_pct"] = (sub["imputed"] - sub["sale_amount"]) / sub["sale_amount"].replace(0, 1) * 100
    sub = sub[sub["sale_amount"] > 0]
    uplift = sub["uplift_pct"]
    examples = sub.nlargest(8, "uplift_pct")[
        ["store_id", "product_id", "dt", "sale_amount", "stock_hour6_22_cnt",
         "in_stock_hours", "imputed", "uplift_pct"]]

    return {
        "available": True,
        "source": "FreshRetailNet-50K（Dingdong-Inc，CC BY 4.0）· eval 集",
        "period": [str(df["dt"].min()), str(df["dt"].max())],
        "rows": total,
        "stores": int(df["store_id"].nunique()),
        "cities": int(df["city_id"].nunique()),
        "products": int(df["product_id"].nunique()),
        "stockoutDays": so_days,
        "stockoutRatioPct": round(100.0 * so_days / total, 2),
        "observedSales": {
            "normalDays": {"count": int(normal["count"]), "mean": round(float(normal["mean"]), 3),
                           "max": round(float(normal["max"]), 3)},
            "stockoutDays": {"count": int(so["count"]), "mean": round(float(so["mean"]), 3),
                             "max": round(float(so["max"]), 3)},
        },
        "upliftStats": {
            "estimableDays": int(len(sub)),
            "medianUpliftPct": round(float(uplift.median()), 1) if len(uplift) else None,
            "meanUpliftPct": round(float(uplift.mean()), 1) if len(uplift) else None,
            "p90UpliftPct": round(float(uplift.quantile(0.9)), 1) if len(uplift) else None,
        },
        "examples": [{"store": int(r["store_id"]), "product": int(r["product_id"]), "date": str(r["dt"]),
                      "observedSale": round(float(r["sale_amount"]), 3),
                      "stockoutHours": int(r["stock_hour6_22_cnt"]),
                      "inStockHours": int(r["in_stock_hours"]),
                      "imputedDemand": round(float(r["imputed"]), 3),
                      "upliftPct": round(float(r["uplift_pct"]), 1)}
                     for _, r in examples.iterrows()],
        "method": "缺货日潜在需求 =（有货小时销售速率）× %d 营业小时；确定性算法，非模型猜测" % OPEN_HOURS,
        "note": "sale_amount 为数据集全局归一化数值，仅看相对关系；该统计为方法论实证，不构成本产品预测性能承诺。"
                "A.zip 等无缺货标注的数据集不套用本修正（标注局限）。",
    }
