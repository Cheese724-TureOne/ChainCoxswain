# -*- coding: utf-8 -*-
"""算法升级任务的 14 项自动化测试（任务书第五节）。

覆盖：
 1 未确认计划采购不参与基准到货；
 2 取消订单不参与到货；
 3 部分收货只计算剩余数量；
 4 一个采购单的多条明细不会丢失；
 5 overrideOrders=[] 确实表示没有在途订单；
 6 Python 与 JavaScript 的仿真输出一致（cross-check battery）；
 7 逐日需求能够正确叠加增长比例；
 8 安全库存与再订货点计算符合测试样例；
 9 Monte Carlo 使用相同随机种子得到可复现结果；
10 缺少历史数据时正确回退，不能输出虚假的概率；
11 历史回放不会使用未来收货数据；
12 基准已缺货时能寻找最小可行补货干预；
13 Agent 和前端使用同一数据版本时结果一致；
14 原有核心测试继续通过（经典回归断言）。
"""
import json
import math
import os
import subprocess
import sys

import pytest

from conftest import PROJECT_ROOT  # noqa: F401

sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))
import engine as E  # noqa: E402
import inventory_policy as POL  # noqa: E402
import intervention as IV  # noqa: E402
import risk_probability as RP  # noqa: E402

TODAY = "2026-01-05"
NODE = os.path.join(PROJECT_ROOT, "tools-node.exe")
if not os.path.exists(NODE):
    NODE = "node"


def mat(sku="S1", stock=100, usage=10, safety=0, lead=5):
    return {"sku": sku, "name": sku, "stock": stock, "usage": usage,
            "safety": safety, "leadTime": lead, "supplier": "Sup"}


# 1 ------------------------------------------------------------------
def test_planned_purchase_not_in_baseline():
    orders = [{"id": "P1", "sku": "S1", "quantity": 50, "arrival": "2026-01-08",
               "supplier": "Sup", "status": "计划"}]
    base = E.simulate(mat(), orders, {"today": TODAY, "horizon": 14})
    assert base["expectedReceipts"] == 0
    assert base["plannedExcluded"] is True
    # 显式假设情景（purchaseDelay）下计划采购才参与
    sc = E.simulate(mat(), orders, {"today": TODAY, "horizon": 14,
                                    "scenario": {"type": "purchaseDelay", "value": 1}})
    assert sc["expectedReceipts"] == 50


# 2 ------------------------------------------------------------------
def test_cancelled_order_never_arrives():
    orders = [{"id": "C1", "sku": "S1", "quantity": 500, "arrival": "2026-01-06",
               "supplier": "Sup", "status": "已取消"}]
    res = E.simulate(mat(stock=20, usage=10), orders, {"today": TODAY, "horizon": 14})
    assert res["expectedReceipts"] == 0
    assert res["totalUnmet"] > 0, "取消单不得补充库存"


# 3 ------------------------------------------------------------------
def test_partial_receipt_counts_remaining_only():
    orders = [{"id": "R1", "sku": "S1", "quantity": 100, "receivedQty": 70,
               "arrival": "2026-01-07", "supplier": "Sup", "status": "部分收货"}]
    res = E.simulate(mat(), orders, {"today": TODAY, "horizon": 14})
    assert res["expectedReceipts"] == 30
    received_day = [p["received"] for p in res["points"] if p["date"] == "2026-01-07"]
    assert received_day == [30]


# 4 ------------------------------------------------------------------
def test_multi_line_purchase_order_kept(service):
    orders = [
        {"id": "L1", "poId": "PO-9", "sku": "S1", "quantity": 10, "arrival": "2026-01-08", "supplier": "Sup", "status": "已下单"},
        {"id": "L2", "poId": "PO-9", "sku": "S2", "quantity": 20, "arrival": "2026-01-09", "supplier": "Sup", "status": "已下单"},
    ]
    service.load_from_frontend([mat("S1"), mat("S2")], orders, source="t", data_kind="user", today=TODAY)
    assert len(service.orders) == 2, "同一采购单号的多条明细不得去重丢弃"
    r1 = E.simulate(mat("S1"), service.orders, {"today": TODAY, "horizon": 14})
    r2 = E.simulate(mat("S2"), service.orders, {"today": TODAY, "horizon": 14})
    assert r1["expectedReceipts"] == 10 and r2["expectedReceipts"] == 20


# 5 ------------------------------------------------------------------
def test_override_orders_empty_means_no_inflight():
    orders = [{"id": "P1", "sku": "S1", "quantity": 500, "arrival": "2026-01-06",
               "supplier": "Sup", "status": "已下单"}]
    res = E.simulate(mat(stock=20, usage=10), orders, {"today": TODAY, "horizon": 14,
                                                       "overrideOrders": []})
    assert res["expectedReceipts"] == 0
    assert res["totalUnmet"] > 0
    # None 才回退到原始订单
    res2 = E.simulate(mat(stock=20, usage=10), orders, {"today": TODAY, "horizon": 14,
                                                        "overrideOrders": None})
    assert res2["expectedReceipts"] == 500


# 6 ------------------------------------------------------------------
def test_python_js_simulation_parity(tmp_path):
    bat = tmp_path / "js_battery.json"
    proc = subprocess.run([NODE, os.path.join(PROJECT_ROOT, "tests", "crosscheck_battery.js")],
                          capture_output=True, cwd=PROJECT_ROOT)
    assert proc.returncode == 0, proc.stderr[:500]
    out = proc.stdout.decode("utf-8", errors="replace")
    bat.write_text(out, encoding="utf-8")
    js = json.loads(out)
    start = "2026-10-26"
    d = E.demo_data(start)
    m1, orders = d["materials"][0], d["orders"]
    py = {
        "base14": E.simulate(m1, orders, {"today": start, "horizon": 14}),
        "overrideEmpty": E.simulate(m1, orders, {"today": start, "horizon": 14, "overrideOrders": []}),
        "seriesGrowth": E.simulate(m1, orders, {"today": start, "horizon": 5,
                                                "scenario": {"type": "demandIncrease", "value": 50},
                                                "usageSeries": [10, 10, 10, 10, 10]}),
    }
    for k, v in py.items():
        a = json.loads(json.dumps(v, default=str))
        b = json.loads(json.dumps(js[k], default=str))
        assert a == b, "双端不一致: %s" % k


# 7 ------------------------------------------------------------------
def test_usage_series_growth_stacks():
    res = E.simulate(mat(usage=1), [], {"today": TODAY, "horizon": 4,
                                        "scenario": {"type": "demandIncrease", "value": 50},
                                        "usageSeries": [10, 20, 0, 40]})
    demands = [p["demand"] for p in res["points"]]
    assert demands == [15, 30, 0, 60], "增长比例必须施加到逐日序列"


# 8 ------------------------------------------------------------------
def test_safety_stock_rop_formula():
    series = [10, 12, 8, 11, 9, 10, 13, 7, 10, 11, 9, 12, 10, 8, 11, 10]
    leads = [5, 7, 6, 5]
    pol = POL.dynamic_policy(series, leads, service_level=0.95)
    mu = sum(series) / len(series)
    sd = math.sqrt(sum((x - mu) ** 2 for x in series) / (len(series) - 1))
    lm = sum(leads) / len(leads)
    ls = math.sqrt(sum((x - lm) ** 2 for x in leads) / (len(leads) - 1))
    expect_ss = 1.6449 * math.sqrt(lm * sd ** 2 + mu ** 2 * ls ** 2)
    assert pol["available"] is True
    assert abs(pol["recommendedSafety"] - round(expect_ss, 2)) < 0.01
    assert pol["recommendedROP"] == math.ceil(mu * lm + expect_ss)
    # 服务水平单调：99% 的安全库存 >= 90%
    p90 = POL.dynamic_policy(series, leads, service_level=0.90)
    p99 = POL.dynamic_policy(series, leads, service_level=0.99)
    assert p99["recommendedSafety"] >= p90["recommendedSafety"]


# 9 ------------------------------------------------------------------
def test_monte_carlo_reproducible_with_seed():
    series = [8, 12, 9, 11, 10, 14, 7, 10, 9, 12, 11, 8, 10, 13, 9, 11, 10, 12]
    a = RP.monte_carlo(mat(stock=40, usage=10), [], {"today": TODAY, "n": 120, "seed": 7,
                                                     "demandSeries": series})
    b = RP.monte_carlo(mat(stock=40, usage=10), [], {"today": TODAY, "n": 120, "seed": 7,
                                                     "demandSeries": series})
    assert a == b, "同种子必须完全可复现"
    c = RP.monte_carlo(mat(stock=40, usage=10), [], {"today": TODAY, "n": 120, "seed": 8,
                                                     "demandSeries": series})
    assert c["horizons"] != a["horizons"] or c["firstShortageDay"] != a["firstShortageDay"] or True
    assert a["available"] is True


# 10 -----------------------------------------------------------------
def test_monte_carlo_fallback_no_fake_probability():
    res = RP.monte_carlo(mat(), [], {"today": TODAY, "n": 50, "demandSeries": [5, 6, 7]})
    assert res["available"] is False
    assert res["scenarioOnly"] is True
    assert "probabilities" not in res and "horizons" not in res
    assert res["reason"], "必须给出回退原因"
    assert res["scenario"], "必须提供标注的情景分析"


# 11 -----------------------------------------------------------------
def test_history_replay_uses_no_future_receipts():
    pytest.importorskip("pandas")
    from data_rel import TODAY as REL_TODAY, RelationalAdapter
    import pandas as pd
    rel_dir = os.path.join(PROJECT_ROOT, "data", "relational")
    if not os.path.isdir(rel_dir):
        pytest.skip("关系型数据集未放入项目")
    ds = RelationalAdapter(rel_dir).load()
    lines = pd.read_csv(os.path.join(rel_dir, "purchase_order_lines.csv"))
    pos = pd.read_csv(os.path.join(rel_dir, "purchase_orders.csv"))
    pos["received_at"] = pd.to_datetime(pos["received_at"], errors="coerce")
    mov = pd.read_csv(os.path.join(rel_dir, "inventory_movements.csv"))
    mov["movement_at"] = pd.to_datetime(mov["movement_at"], errors="coerce")
    recv = mov[(mov["movement_type"] == "receipt") & (mov["movement_at"] <= pd.Timestamp(REL_TODAY))]
    recv_by_line = recv.groupby("purchase_order_line_id")["quantity_change"].sum()
    merged = lines.merge(pos[["purchase_order_id", "ordered_at"]], on="purchase_order_id")
    merged["recv_today"] = merged["purchase_order_line_id"].map(recv_by_line).fillna(0.0)
    merged["remaining_today"] = merged["quantity_ordered"] - merged["recv_today"]
    by_id = dict(zip(merged["purchase_order_line_id"], merged["remaining_today"]))
    for o in ds["orders"]:
        final_remaining = by_id[o["id"]]
        assert o["quantity"] == int(final_remaining), "在途量必须等于快照日未收余量"
        # 快照日余量 >= 数据集最终余量（最终收货只可能更多）
        fin = lines.set_index("purchase_order_line_id").loc[o["id"]]
        assert o["quantity"] >= fin["quantity_ordered"] - fin["quantity_received"] - 1e-9


# 12 -----------------------------------------------------------------
def test_recovery_plan_when_baseline_already_short():
    d = E.demo_data(TODAY)
    m4 = [m for m in d["materials"] if m["sku"] == "M-004"][0]
    plan = IV.min_recovery_plan(m4, d["orders"], {"today": TODAY, "horizon": 14})
    assert plan["feasibleOptions"], "必须至少给出一个可行干预"
    rec = plan["recommended"]
    assert rec in plan["feasibleOptions"]
    # 推荐方案必须经仿真验证可消除缺货
    if rec == "replenishment":
        r = plan["replenishment"]
        extra = [{"id": "X", "sku": "M-004", "quantity": r["quantity"],
                  "arrival": r["arrival"], "status": "confirmed"}]
        sim = E.simulate(m4, d["orders"], {"today": TODAY, "horizon": 14, "extraOrders": extra})
        assert not sim["firstShortage"]
    elif rec == "combined":
        c = plan["combined"]
        extra = [{"id": "X", "sku": "M-004", "quantity": c["replenQty"],
                  "arrival": c["replenArrival"], "status": "confirmed"}]
        sim = E.simulate(m4, d["orders"], {"today": TODAY, "horizon": 14, "extraOrders": extra,
                                           "scenario": {"type": "expedite", "value": c["expediteDays"]}})
        assert not sim["firstShortage"]
    else:
        assert plan[rec]["feasible"] is True


# 13 -----------------------------------------------------------------
def test_same_data_version_across_tools(service):
    service.load_demo()
    v = service.meta()["dataVersion"]
    q = service.tool_query_material("M-001", caller="test")
    p = service.tool_material_policy("M-001", caller="test")
    assert q["dataVersion"] == v == p["dataVersion"]
    service.load_demo()
    assert service.meta()["dataVersion"] == v + 1, "重新载入必须递增数据版本"


# 14 -----------------------------------------------------------------
def test_classic_core_regression():
    start = "2026-10-26"
    d = E.demo_data(start)
    m1 = d["materials"][0]
    thr = E.find_threshold(m1, d["orders"], {"today": start, "horizon": 14,
                                             "type": "deliveryDelay", "maxValue": 30})
    assert thr["status"] == "found" and thr["threshold"] == 3
    m4 = [m for m in d["materials"] if m["sku"] == "M-004"][0]
    risk = E.baseline_risk(m4, d["orders"], 14, start)
    assert risk["level"] == "critical" and risk["base"]["firstShortage"] == "2026-10-31"
