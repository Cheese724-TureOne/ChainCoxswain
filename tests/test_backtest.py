# -*- coding: utf-8 -*-
"""A.zip 真实历史数据适配与无泄漏回测测试。

数据边界（必须保留）：
  - 库存快照日 = 2021-03-01（历史业务日期，不得冒充实时）
  - 用量估计只用 train 期内数据（≤ 2021-03-01）
  - 回测重放只用 test 期数据（2021-03-02 起），不参与参数估计
  - 原始数据不含采购订单/供应商/到货日期 -> 缺失标注或明确标记的模拟订单
"""
import pandas as pd

from conftest import PROJECT_ROOT  # noqa: F401
from data_a import SNAPSHOT_DATE, DataAAdapter  # noqa: F401


def test_zip_present(a_zip_path):
    assert a_zip_path.endswith("A.zip")


def test_load_basic_fields(a_zip_path):
    adapter = DataAAdapter(a_zip_path)
    ds = adapter.load()
    assert ds["snapshotDate"] == "2021-03-01"
    assert ds["units"] == 632
    assert len(ds["materials"]) == 632
    assert ds["orders"] == [] or all(o.get("simulated") for o in ds["orders"])
    # 缺失字段必须显式标注
    assert "采购" in "；".join(ds["flags"] + [ds["missingFields"].get("采购订单", "")]) or \
           ds["missingFields"]
    assert ds["ordersSimulated"] is False


def test_load_with_simulated_orders_marked(a_zip_path):
    adapter = DataAAdapter(a_zip_path)
    ds = adapter.load(simulate_pos=True)
    assert ds["ordersSimulated"] is True
    assert len(ds["orders"]) > 0
    for o in ds["orders"]:
        assert o.get("simulated") is True, "模拟采购订单必须带明确标记"
        assert str(o["id"]).startswith("SIM-PO-A"), "模拟单号必须可辨识"


def test_materials_have_no_nan(a_zip_path):
    """浏览器 strict JSON.parse 不能接受 NaN（历史教训）。"""
    import math
    adapter = DataAAdapter(a_zip_path)
    ds = adapter.load()
    for m in ds["materials"]:
        for k, v in m.items():
            assert not (isinstance(v, float) and (math.isnan(v) or math.isinf(v))), \
                "字段 %s 含 NaN/Inf: %s" % (k, v)


def test_train_test_split_no_leakage(a_zip_path):
    """用量估计只用 train 期；测试期数据绝不参与参数估计。"""
    import zipfile
    from data_a import _read_csv_from_zip
    with zipfile.ZipFile(a_zip_path) as z:
        train = _read_csv_from_zip(z, "use_train_A.csv")
        test = _read_csv_from_zip(z, "use_test_A.csv")
    assert pd.to_datetime(train["ts"]).max() <= pd.Timestamp(SNAPSHOT_DATE)
    assert pd.to_datetime(test["ts"]).min() > pd.Timestamp(SNAPSHOT_DATE)
    ds = DataAAdapter(a_zip_path).load()
    train_used_max = pd.Timestamp(ds["trainRange"][1])
    assert train_used_max <= pd.Timestamp(SNAPSHOT_DATE)
    assert pd.Timestamp(ds["testRange"][0]) > pd.Timestamp(SNAPSHOT_DATE)


def test_backtest_replays_test_period_only(a_zip_path):
    adapter = DataAAdapter(a_zip_path)
    bt = adapter.backtest(unit_limit=30)
    assert bt["startDate"] > SNAPSHOT_DATE  # 从 2021-03-02 开始
    assert bt["unitsEvaluated"] == 30
    assert bt["unitsWithShortage"] <= 30
    # 诚实结果：无采购数据时，多数 unit 的薄库存会在数日内耗尽
    assert bt["totalUnmetAll"] > 0
    for r in bt["results"]:
        assert r["firstShortage"] is None or r["firstShortage"] >= bt["startDate"]


def test_risk_summary_honest_without_pos(a_zip_path):
    adapter = DataAAdapter(a_zip_path)
    ds = adapter.load()
    rs = ds["riskSummary"]
    # 无采购订单的真实数据下，多数 unit 覆盖极低 -> critical 占比高，这是数据的真实状态
    assert rs["critical"] > 0
    assert rs["critical"] + rs["watch"] + rs["excess"] + rs["normal"] == ds["units"]
    # 中位覆盖天数（对材料重新计算）
    import statistics
    cov = [m["stock"] / m["usage"] for m in ds["materials"] if m["usage"] > 0]
    assert statistics.median(cov) < 1
