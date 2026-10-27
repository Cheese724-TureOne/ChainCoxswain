# -*- coding: utf-8 -*-
"""关系型数据集适配器测试（inventory-supply-chain v1.0.0，合成教学数据）。

边界断言：
  - 2500 产品、快照日 2025-12-15 原样保留；
  - 库存只用 <= 快照日的流水（无未来泄漏）；用量只用快照日前 91 天窗口；
  - 在途采购单按快照日重构：未逾期单 expected_at > 快照日，逾期单标记 overdue 且不计入未来到货；
  - 无 NaN、无负库存、供应商/交期来自真实 join；
  - dataKind 为 demo（合成数据，不得冒充真实历史数据）。
"""
import math
import os

import pytest

from conftest import PROJECT_ROOT  # noqa: F401
from data_rel import TODAY, USAGE_WINDOW_DAYS, RelationalAdapter

REL_DIR = os.path.join(PROJECT_ROOT, "data", "relational")

pytestmark = pytest.mark.skipif(not os.path.isdir(REL_DIR),
                                reason="data/relational 目录不存在（关系型数据集未放入项目）")


@pytest.fixture(scope="module")
def ds():
    return RelationalAdapter(REL_DIR).load()


def test_materials_count_and_snapshot(ds):
    assert ds["today"] == TODAY == "2025-12-15"
    assert ds["units"] == 2500
    assert len(ds["materials"]) == 2500
    skus = [m["sku"] for m in ds["materials"]]
    assert len(set(skus)) == 2500, "SKU 必须唯一"


def test_no_nan_and_non_negative_stock(ds):
    for m in ds["materials"]:
        for k, v in m.items():
            if isinstance(v, float):
                assert not (math.isnan(v) or math.isinf(v)), "字段 %s 含 NaN/Inf: %s" % (k, m["sku"])
        assert m["stock"] >= 0
        assert m["usage"] >= 0
        assert m["leadTime"] >= 0


def test_usage_window_and_no_leakage(ds):
    w = ds["usageWindow"]
    assert w["end"] == TODAY and w["days"] == USAGE_WINDOW_DAYS == 91
    assert w["start"] == "2025-09-15"  # 2025-12-15 - 91 天
    assert ds["flags"], "数据边界必须显式标注"


def test_orders_are_future_arrivals(ds):
    """在途单 = 快照日未收完的订单行；逾期单单独标记 overdue（引擎不计入未来到货）。"""
    assert ds["ordersCount"] > 0, "必须有在途采购单（2025-12-16~12-31）"
    skus = {m["sku"] for m in ds["materials"]}
    for o in ds["orders"]:
        if o.get("overdue"):
            assert o["arrival"] <= TODAY, "标记逾期的单到货日必须 <= 快照日"
        else:
            assert o["arrival"] > TODAY, "未标记逾期的在途单到货日必须在快照日之后（无未来泄漏）"
        assert o["quantity"] > 0
        assert o["sku"] in skus, "采购单必须指向已存在物料"
        assert o["supplier"], "采购单供应商来自 join，不得为空"
    po = ds["poStats"]
    future_qty = sum(o["quantity"] for o in ds["orders"] if not o.get("overdue"))
    overdue_qty = sum(o["quantity"] for o in ds["orders"] if o.get("overdue"))
    assert po["futureQuantity"] == future_qty
    assert po["overdueQuantity"] == overdue_qty
    assert po["arrivalRange"][0] >= "2025-12-16" and po["arrivalRange"][1] <= "2025-12-31"


def test_supplier_join_real_fields(ds):
    """交期/供应商/单价来自真实表 join，而不是编造。"""
    lead_times = [m["leadTime"] for m in ds["materials"]]
    assert 5 <= min(lead_times) and max(lead_times) <= 60
    assert sum(1 for m in ds["materials"] if m["supplier"] == "未提供") == 0
    assert sum(1 for m in ds["materials"] if m["unitCost"] is not None) == 2500


def test_service_integration_risk_list_works(tmp_path):
    from service import ChainCoxswainService
    s = ChainCoxswainService(data_dir=PROJECT_ROOT, log_path=str(tmp_path / "tool_logs.json"))
    meta = s.load_rel()
    assert meta["dataKind"] == "demo", "合成教学数据不得标注为真实历史数据"
    assert "合成教学数据" in meta["source"]
    assert meta["materials"] == 2500
    assert meta["today"] == TODAY
    rl = s.tool_risk_list(limit=5)
    assert rl["asOf"] == TODAY
    assert sum(rl["counts"].values()) == 2500
    q = s.tool_query_material(s.materials[0]["sku"])
    assert q["supplier"] != "未提供"
    assert q["nextExpectedArrival"] in ("无采购记录",) or q["nextExpectedArrival"] >= TODAY
