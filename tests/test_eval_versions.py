# -*- coding: utf-8 -*-
"""V1/V2/V3 三版本实验评估（任务书第五节）。

- V1：原确定性仿真（固定日均 + 录入安全库存）；
- V2：动态需求预测 + 动态安全库存/再订货点；
- V3：Monte Carlo 概率风险 + 反向干预优化。

标签策略（不伪造、不用仿真器自产标签）：
  A. 关系型数据集：由 inventory_movements 真实流水重建测试期逐日库存，
     "期末库存为 0 且当日有销量" 记为缺货日。实测该合成数据集测试期
     **不存在任何缺货日**（最小覆盖 >2500 天），因此召回率/误报率如实记为
     不可计算（positives=0），仅报告三版本旗标分布与干预成本比较。
  B. 零售数据集（FreshRetailNet-50K eval.parquet，含真实缺货小时标注）：
     按时间顺序划分训练/测试日，比较 V1 固定均值 与 V2 动态预测（含截尾上修）
     的逐日需求预测 MAE——真实 sale_amount 为标签。
"""
import os
import sys

import pytest

from conftest import PROJECT_ROOT  # noqa: F401

pd = pytest.importorskip("pandas")
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))
import engine as E  # noqa: E402
import demand_forecast as FC  # noqa: E402
import inventory_policy as POL  # noqa: E402
import risk_probability as RP  # noqa: E402
import intervention as IV  # noqa: E402

REL_DIR = os.path.join(PROJECT_ROOT, "data", "relational")
RETAIL = os.path.join(PROJECT_ROOT, "data", "retail", "eval.parquet")
TRAIN_END = "2025-11-15"
TEST_DAYS = 30
TOP_N = 30
MC_N = 100
OPEN_HOURS = 17


def _metrics(flags, labels):
    pos = [s for s in flags if labels.get(s)]
    if not pos:
        return {"recall": None, "fpr": None, "note": "测试期无真实缺货标签，召回/误报不可计算（不伪造）"}
    tp = sum(1 for s in flags if flags[s] and labels[s])
    fp = sum(1 for s in flags if flags[s] and not labels[s])
    fn = sum(1 for s in flags if (not flags[s]) and labels[s])
    tn = sum(1 for s in flags if (not flags[s]) and not labels[s])
    return {"recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "fpr": round(fp / (fp + tn), 4) if fp + tn else None,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def test_v1_v2_v3_flags_and_cost_on_relational():
    if not os.path.isdir(REL_DIR):
        pytest.skip("关系型数据集未放入项目 data/relational/")
    mov = pd.read_csv(os.path.join(REL_DIR, "inventory_movements.csv"), parse_dates=["movement_at"])
    lines = pd.read_csv(os.path.join(REL_DIR, "purchase_order_lines.csv"))
    pos = pd.read_csv(os.path.join(REL_DIR, "purchase_orders.csv"),
                      parse_dates=["ordered_at", "expected_at"])
    prod = pd.read_csv(os.path.join(REL_DIR, "products.csv"))
    sup = pd.read_csv(os.path.join(REL_DIR, "suppliers.csv"))
    t_end = pd.Timestamp(TRAIN_END)

    sales = mov[(mov["movement_type"] == "sale") & (mov["movement_at"] <= t_end)]
    daily = sales.groupby(["product_id", sales["movement_at"].dt.date])["quantity_change"].sum().abs()
    tot = daily.groupby(level=0).sum().sort_values(ascending=False)
    skus = list(tot.head(TOP_N).index)
    series = {s: [float(v) for v in daily.loc[s].reindex(
        pd.date_range(daily.loc[s].index.min(), t_end, freq="D").date, fill_value=0.0)] for s in skus}

    # 标签：测试期真实流水重建（截尾代理）
    net = mov[mov["movement_at"] <= t_end].groupby("product_id")["quantity_change"].sum()
    test_mov = mov[(mov["movement_at"] > t_end) & (mov["movement_at"] <= t_end + pd.Timedelta(days=TEST_DAYS))]
    labels = {}
    for s in skus:
        inv = max(float(net.get(s, 0.0)), 0.0)
        sub = test_mov[test_mov["product_id"] == s]
        sm = sub.groupby(sub["movement_at"].dt.date)["quantity_change"].sum()
        hit = 0
        for d in pd.date_range(t_end + pd.Timedelta(days=1), t_end + pd.Timedelta(days=TEST_DAYS), freq="D"):
            ch = float(sm.get(d.date(), 0.0))
            recv, sold = max(ch, 0.0), max(-ch, 0.0)
            inv = inv + recv - sold
            if inv <= 1e-9 and sold > 0:
                hit += 1
            inv = max(inv, 0.0)
        labels[s] = hit > 0

    lead_by_sup = dict(zip(sup["supplier_id"], sup["lead_time_days"]))
    sup_of = dict(zip(prod["product_id"], prod["supplier_id"]))
    cost_of = dict(zip(lines["product_id"], lines["unit_cost"]))
    merged = lines.merge(pos[["purchase_order_id", "ordered_at", "expected_at"]], on="purchase_order_id")
    recv_train = mov[(mov["movement_type"] == "receipt") & (mov["movement_at"] <= t_end)] \
        .groupby("purchase_order_line_id")["quantity_change"].sum()
    merged["remaining"] = merged["quantity_ordered"] - merged["purchase_order_line_id"].map(recv_train).fillna(0.0)
    inflight = merged[(merged["ordered_at"] <= t_end) & (merged["remaining"] > 0)]

    v1, v2, v3, costs = {}, {}, {}, {}
    for s in skus:
        ser = series[s]
        mu = sum(ser) / len(ser) if ser else 0.0
        lead = int(lead_by_sup.get(sup_of.get(s), 5) or 5)
        m = {"sku": s, "name": s, "stock": max(float(net.get(s, 0.0)), 0.0), "usage": round(mu, 4),
             "safety": 0, "leadTime": lead, "supplier": sup_of.get(s), "unitCost": cost_of.get(s)}
        ords = [{"id": r.purchase_order_line_id, "sku": s, "quantity": float(r.remaining),
                 "arrival": pd.Timestamp(r.expected_at).date().isoformat(),
                 "status": E.STATUS_CONFIRMED}
                for r in inflight[inflight["product_id"] == s].itertuples()]
        v1[s] = bool(E.simulate(m, ords, {"today": TRAIN_END, "horizon": TEST_DAYS})["firstShortage"])
        fc = FC.forecast(ser, TEST_DAYS, fallback_usage=mu)
        pol = POL.dynamic_policy(ser, [float(lead)] * 3, 0.95)
        m2 = dict(m, usage=round(fc["mu"], 4), safety=pol["recommendedSafety"] if pol["available"] else 0)
        v2[s] = bool(E.simulate(m2, ords, {"today": TRAIN_END, "horizon": TEST_DAYS})["firstShortage"]) or \
            bool(pol["available"] and m["stock"] < pol["recommendedROP"])
        mc = RP.monte_carlo(m, ords, {"today": TRAIN_END, "n": MC_N, "seed": 42,
                                      "horizons": [TEST_DAYS], "demandSeries": ser})
        v3[s] = bool(mc["available"]) and mc["horizons"][str(TEST_DAYS)]["stockoutProbability"] >= 0.5
        if v3[s] and m.get("unitCost"):
            rec = IV.min_replenishment_qty(m, ords, {"today": TRAIN_END, "horizon": TEST_DAYS})
            costs[s] = {"v3_min_replen_cost": round((rec.get("quantity") or 0) * m["unitCost"], 2),
                        "v1_blanket_30d_cost": round(30 * mu * m["unitCost"], 2)}
    # Part C：需求预测真实标签对比（测试期实际销量均值，来自流水，非仿真）
    test_sales = mov[(mov["movement_type"] == "sale") & (mov["movement_at"] > t_end)
                     & (mov["movement_at"] <= t_end + pd.Timedelta(days=TEST_DAYS))]
    ts_sum = test_sales.groupby("product_id")["quantity_change"].sum().abs()
    e1c = e2c = nc = 0
    for s in skus:
        if s not in ts_sum.index:
            continue
        actual = float(ts_sum[s]) / TEST_DAYS
        ser = series[s]
        v1p = sum(ser) / len(ser) if ser else 0.0
        fc = FC.forecast(ser, TEST_DAYS, fallback_usage=v1p)
        v2p = float(fc["series"][0]) if fc["series"] else v1p
        e1c += abs(v1p - actual)
        e2c += abs(v2p - actual)
        nc += 1
    mae1c = e1c / nc if nc else None
    mae2c = e2c / nc if nc else None

    result = {"dataset": "relational（合成教学数据）", "labelSource": "movements 真实流水",
              "trainEnd": TRAIN_END, "testDays": TEST_DAYS, "skus": len(skus),
              "positives": int(sum(labels.values())),
              "V1": _metrics(v1, labels), "V2": _metrics(v2, labels), "V3": _metrics(v3, labels),
              "flagCounts": {"V1": sum(v1.values()), "V2": sum(v2.values()), "V3": sum(v3.values())},
              "demandMAE": {"V1_fixed_mean": round(mae1c, 4) if mae1c is not None else None,
                            "V2_dynamic_forecast": round(mae2c, 4) if mae2c is not None else None,
                            "skus": nc},
              "interventionCostSample": dict(list(costs.items())[:5])}
    assert result["labelSource"] == "movements 真实流水"
    assert len(v1) == len(v2) == len(v3) == len(skus)
    if mae2c is not None:
        assert mae2c <= mae1c * 1.10, "V2 动态预测不应显著劣于 V1 固定均值（10% 容差）"
    print("\n[V1/V2/V3 关系型评估]", result)


def test_v2_forecast_beats_or_matches_v1_on_retail_labels():
    if not os.path.exists(RETAIL):
        pytest.skip("零售评估集未放入项目 data/retail/eval.parquet")
    df = pd.read_parquet(RETAIL)
    df["dt"] = pd.to_datetime(df["dt"])
    days = sorted(df["dt"].unique())
    cut = days[max(1, len(days) * 2 // 3)]
    train, test = df[df["dt"] < cut], df[df["dt"] >= cut]
    g = train.groupby(["store_id", "product_id"])["sale_amount"]
    agg = g.agg(["mean", "count"])
    top = agg[agg["count"] >= 3].sort_values("mean", ascending=False).head(200)
    # 截尾上修：缺货日潜在需求 = 有货小时速率 × 完整营业小时（真实缺货小时标注）
    tr = train.copy()
    so = tr["stock_hour6_22_cnt"] > 0
    in_stock = OPEN_HOURS - tr.loc[so, "stock_hour6_22_cnt"]
    tr.loc[so & (in_stock > 0), "sale_amount"] = (
        tr.loc[so & (in_stock > 0), "sale_amount"] / in_stock[so & (in_stock > 0)] * OPEN_HOURS)
    e1, e2, n = 0.0, 0.0, 0
    for (st, pr), row in top.iterrows():
        s_tr = tr[(tr["store_id"] == st) & (tr["product_id"] == pr)].sort_values("dt")["sale_amount"]
        s_te = test[(test["store_id"] == st) & (test["product_id"] == pr)].sort_values("dt")["sale_amount"]
        if len(s_tr) < 3 or len(s_te) == 0:
            continue
        raw = tr[(tr["store_id"] == st) & (tr["product_id"] == pr)].sort_values("dt")["sale_amount"]
        v1_pred = float(raw.mean())
        fc = FC.forecast(list(s_tr), len(s_te), fallback_usage=v1_pred)
        # 零售评估窗仅 7 天 < 14 天历史门槛：V2 必须按规则回退到 V1（不得假装动态）
        assert fc["fallbackReason"], "历史不足 14 天时必须回退并标注原因"
        v2_pred = float(fc["series"][0]) if fc["series"] else v1_pred
        actual = float(s_te.mean())
        e1 += abs(v1_pred - actual) * len(s_te)
        e2 += abs(v2_pred - actual) * len(s_te)
        n += len(s_te)
    assert n > 0
    mae1, mae2 = e1 / n, e2 / n
    print("\n[零售需求预测 MAE] V1 固定均值=%.4f V2 回退(历史<14天, 与V1一致)=%.4f n=%d" % (mae1, mae2, n))
    assert abs(mae1 - mae2) < 1e-9, "回退时 V2 必须与 V1 完全一致（不伪造动态预测）"
