# -*- coding: utf-8 -*-
"""截尾需求实证模块测试（FreshRetailNet-50K 评估集）。"""
import math
import os

import pytest

from censored import CENSORED_DIR, OPEN_HOURS, analyze
from conftest import PROJECT_ROOT  # noqa: F401

_PARQUET = os.path.join(PROJECT_ROOT, "data", "retail", "eval.parquet")

pytestmark = pytest.mark.skipif(not os.path.exists(_PARQUET),
                                reason="data/retail/eval.parquet 不存在")


@pytest.fixture(scope="module")
def result():
    return analyze()


def test_available_and_basic_stats(result):
    assert result["available"] is True
    assert result["rows"] == 350000
    assert result["stores"] == 898 and result["cities"] == 18 and result["products"] == 865
    assert result["period"] == ["2024-06-26", "2024-07-02"]
    assert result["source"].startswith("FreshRetailNet-50K")


def test_stockout_evidence_consistent(result):
    """缺货日观测销量均值 < 不缺货日（截断证据），占比约 41%。"""
    assert 35 <= result["stockoutRatioPct"] <= 48
    o = result["observedSales"]
    assert o["stockoutDays"]["mean"] < o["normalDays"]["mean"]
    assert o["stockoutDays"]["count"] + o["normalDays"]["count"] == result["rows"]


def test_uplift_deterministic_and_bounded(result):
    """上修算法：估算需求 = 有货小时速率 × 17 小时；缺货越深上修越大。"""
    u = result["upliftStats"]
    assert u["estimableDays"] > 100000
    assert u["medianUpliftPct"] > 0 and u["meanUpliftPct"] > u["medianUpliftPct"]
    assert OPEN_HOURS == 17
    for x in result["examples"]:
        assert x["inStockHours"] + x["stockoutHours"] == OPEN_HOURS
        assert x["imputedDemand"] >= x["observedSale"]
        assert x["upliftPct"] > 0
        assert not any(isinstance(v, float) and math.isnan(v) for v in x.values())


def test_honesty_notes_present(result):
    """必须声明：归一化数值、方法论实证而非性能承诺、无标注数据集不套用。"""
    assert "归一化" in result["note"]
    assert "性能承诺" in result["note"] or "不构成" in result["note"]
    assert "确定性算法" in result["method"]


def test_missing_parquet_returns_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("censored.CENSORED_DIR", str(tmp_path))
    r = analyze()
    assert r["available"] is False and "eval.parquet" in r["error"]
