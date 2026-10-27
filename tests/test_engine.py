# -*- coding: utf-8 -*-
"""确定性库存仿真引擎测试：经典样例 + 临界点 + 数据不可变 + 边界。

经典样例（比赛保留样例）：
  物料 M-001：库存 80，日均消耗 10，一笔 100 件采购单在第 7 天开始前到货。
  - 到货延期 2 天：不缺货
  - 到货延期 3 天：第 9 天首次缺货 10 件
  - 缺货临界点 = 3 天
"""
from conftest import START  # noqa: F401  (re-export for tests that import it)


def test_classic_baseline_no_shortage(engine, demo):
    materials, orders = demo
    m = next(x for x in materials if x["sku"] == "M-001")
    base = engine.simulate(m, orders, {"today": START, "horizon": 14})
    assert base["firstShortage"] is None
    assert base["totalUnmet"] == 0
    # 第 1 天：无到货，先收货再消耗 -> 日末库存 80 - 10 = 70
    assert base["points"][0]["stock"] == 70


def test_classic_delay_2_days_ok(engine, demo):
    materials, orders = demo
    m = next(x for x in materials if x["sku"] == "M-001")
    sim = engine.simulate(m, orders, {"today": START, "horizon": 14,
                                      "scenario": {"type": "deliveryDelay", "value": 2}})
    assert sim["firstShortage"] is None
    assert sim["totalUnmet"] == 0


def test_classic_delay_3_days_shortage_day9(engine, demo):
    materials, orders = demo
    m = next(x for x in materials if x["sku"] == "M-001")
    sim = engine.simulate(m, orders, {"today": START, "horizon": 14,
                                      "scenario": {"type": "deliveryDelay", "value": 3}})
    assert sim["firstShortage"] == "2026-11-03"
    assert sim["totalUnmet"] == 10
    # 库存永不为负（缺口被计为未满足需求，不在库存上扣成负数）
    assert all(p["stock"] >= 0 for p in sim["points"])


def test_classic_threshold_is_3(engine, demo):
    materials, orders = demo
    m = next(x for x in materials if x["sku"] == "M-001")
    th = engine.find_threshold(m, orders, {"today": START, "horizon": 14,
                                           "type": "deliveryDelay", "maxValue": 30})
    assert th["status"] == "found"
    assert th["threshold"] == 3
    assert th["rounds"] >= 3  # 倍增试探 + 二分，至少多轮


def test_purchase_order_original_arrival(engine, demo):
    """采购单预计到货日应为基准日 + 6 天（第 7 天开始前）。"""
    _, orders = demo
    po = next(o for o in orders if o["id"] == "PO-1001")
    assert po["arrival"] == "2026-11-01"


def test_data_immutability(engine, demo):
    """所有仿真/阈值/自主扫描之后，原始业务数据必须保持不变。"""
    materials, orders = demo
    snapshot_mat = {m["sku"]: dict(m) for m in materials}
    snapshot_ord = {o["id"]: dict(o) for o in orders}
    m = next(x for x in materials if x["sku"] == "M-001")
    for delay in range(0, 6):
        engine.simulate(m, orders, {"today": START, "horizon": 14,
                                    "scenario": {"type": "deliveryDelay", "value": delay}})
    engine.find_threshold(m, orders, {"today": START, "horizon": 14,
                                      "type": "deliveryDelay", "maxValue": 30})
    engine.autonomous_scan(materials, orders, {"today": START, "horizon": 14})
    engine.mitigate(m, orders, {"type": "deliveryDelay", "value": 3},
                    {"today": START, "horizon": 14})
    for m2 in materials:
        assert m2 == snapshot_mat[m2["sku"]], "物料数据被仿真修改"
    for o in orders:
        assert o == snapshot_ord[o["id"]], "采购单数据被仿真修改"


def test_overdue_order_not_counted(engine):
    m = {"sku": "X", "stock": 0, "usage": 1, "safety": 0, "leadTime": 3, "supplier": "S"}
    orders = [{"id": "PO", "sku": "X", "arrival": "2026-10-24", "quantity": 99,
               "supplier": "S", "status": "已下单"}]
    sim = engine.simulate(m, orders, {"today": START, "horizon": 2})
    assert sim["totalUnmet"] == 2  # 逾期且未确认入库的订单不计入未来到货
    assert all(p["stock"] >= 0 for p in sim["points"])


def test_demand_increase_threshold_m002(engine, demo):
    """M-002：库存 120、日均 9、覆盖 13.33 天；需求上涨临界点应为 22%。"""
    materials, orders = demo
    m = next(x for x in materials if x["sku"] == "M-002")
    th = engine.find_threshold(m, orders, {"today": START, "horizon": 14,
                                           "type": "demandIncrease", "maxValue": 200})
    assert th["status"] == "found"
    assert th["threshold"] == 22


def test_baseline_risk_levels(engine, demo):
    materials, orders = demo
    crit = next(m for m in materials if m["sku"] == "M-004")
    r = engine.baseline_risk(crit, orders, 14, START)
    assert r["level"] == "critical"
    assert r["base"]["firstShortage"] == "2026-10-31"


def test_supplier_outage_scenario(engine, demo):
    materials, orders = demo
    m = next(x for x in materials if x["sku"] == "M-002")
    sim = engine.simulate(m, orders, {"today": START, "horizon": 14,
                                      "scenario": {"type": "supplierOutage", "supplier": "科联半导体"}})
    assert sim["totalUnmet"] > 0


def test_mitigate_outage_needs_data(engine, demo):
    """断供且无备用供应商数据 -> 明确 dataRequired，不编造效果。"""
    materials, orders = demo
    m = next(x for x in materials if x["sku"] == "M-002")
    opts = engine.mitigate(m, orders, {"type": "supplierOutage", "supplier": "科联半导体"},
                           {"today": START, "horizon": 14})
    fallback = next((x for x in opts if x["id"] == "alternative"), None)
    assert fallback is not None
    assert fallback["dataRequired"] is True
    assert fallback["improvement"] == 0


def test_date_boundary(engine):
    assert engine.add_days("2026-12-31", 1) == "2027-01-01"
    assert engine.add_days("2026-10-26", 6) == "2026-11-01"


def test_threshold_invalid_scenario_returns_not_found_or_error(engine, demo):
    materials, orders = demo
    m = next(x for x in materials if x["sku"] == "M-003")
    # M-003 无未来已下单采购单 -> 到货延期阈值应标记为不适用/未找到，而不是报缺货
    th = engine.find_threshold(m, orders, {"today": START, "horizon": 14,
                                           "type": "deliveryDelay", "maxValue": 30})
    assert th["status"] in ("not-applicable", "not-found", "already")
