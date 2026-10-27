# -*- coding: utf-8 -*-
"""四组对照实验测试（模块 E）：

  G1 传统静态风险预警 / G2 固定参数单次压力测试 /
  G3 规则驱动自适应压力测试 / G4 千问 Agent 驱动多轮测试。

断言要点：四组结构完整、G3/G4 对脆弱物料的检测率 100% 且临界点误差为 0、
G1/G2 无临界点输出、G4 预算与 G3 实测调用数挂钩、结果完全可复现。
G4 在无 API Key 时走明确标记的规则演示模式（测试环境强制无 Key，不依赖外部 API）。
"""
from compare import run_comparison
from conftest import PROJECT_ROOT  # noqa: F401

G3_NAME = "G3 规则驱动自适应压力测试"
G4_NAME = "G4 千问 Agent 驱动多轮测试"
G1_NAME = "G1 传统静态风险预警"
G2_NAME = "G2 固定参数单次压力测试"


def test_four_groups_structure(service, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    res = run_comparison(service, top_n=6, horizon=14)
    assert len(res["groups"]) == 4
    names = [g["name"] for g in res["groups"]]
    assert [n[:2] for n in names] == ["G1", "G2", "G3", "G4"]
    assert len(res["groundTruth"]) == 6, "穷举基准必须覆盖全部被测物料"
    assert any(res["fragile"].values())
    assert set(res["metrics"]) == set(names), "每个组都要有指标"


def test_g3_g4_full_detection_and_zero_threshold_error(service, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    res = run_comparison(service, top_n=6, horizon=14)
    m = res["metrics"]
    g3 = m[G3_NAME]
    assert g3["riskDetectionRate"] == 1.0
    assert g3["thresholdErrorMean"] == 0.0, "倍增+二分必须精确命中穷举基准"
    g4 = m[G4_NAME]
    assert g4["riskDetectionRate"] == 1.0
    assert g4["thresholdErrorMean"] == 0.0
    assert g4["constraintSatisfaction"] == 1.0
    assert g3["reproducibility"] == 1.0 and g4["reproducibility"] == 1.0


def test_g1_g2_have_no_thresholds(service, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    res = run_comparison(service, top_n=4)
    by_name = {g["name"]: g for g in res["groups"]}
    assert "thresholds" not in by_name[G1_NAME], "静态预警不产生临界点"
    assert "thresholds" not in by_name[G2_NAME], "固定参数测试不搜索临界点"
    assert res["metrics"][G1_NAME]["thresholdErrorMean"] is None
    assert res["metrics"][G2_NAME]["thresholdErrorMean"] is None


def test_g4_budget_tracks_g3_calls(service, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    res = run_comparison(service, top_n=6)
    by_name = {g["name"]: g for g in res["groups"]}
    g3_calls = by_name[G3_NAME]["toolCalls"]
    g4 = by_name[G4_NAME]
    budget = max(40, g3_calls + 4 * 6 + 10)  # 公平预算公式
    assert g4["toolCalls"] <= budget
    assert g4["rounds"] <= 2 * 6 + 3
    assert g4["mode"] in ("qwen", "rule-demo")
    assert g4["modeLabel"], "模式必须明确标记"


def test_reproducibility_deterministic(service, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    r1 = run_comparison(service, top_n=5)
    r2 = run_comparison(service, top_n=5)
    m1, m2 = r1["metrics"], r2["metrics"]
    assert r1["groundTruth"] == r2["groundTruth"], "穷举基准可复现"
    for k in m1:
        assert m1[k]["riskDetectionRate"] == m2[k]["riskDetectionRate"]
        assert m1[k]["toolCalls"] == m2[k]["toolCalls"]
        assert m1[k]["reproducibility"] == 1.0
        assert m2[k]["reproducibility"] == 1.0
