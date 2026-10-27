# -*- coding: utf-8 -*-
"""千问 Agent 闭环测试（MockLLM 注入，不依赖真实 API Key）。

覆盖：
  1. plan -> call -> observe -> re-plan 循环与结论组装（数字只来自工具结果）；
  2. 自适应 Mock：下一轮参数解析自上一轮工具返回（证明反馈依赖）；
  3. 未知工具 / 错误参数 / 非法 JSON 参数 -> 报错不崩溃，循环继续；
  4. 无采购数据 -> 延期类临界点 not-applicable（演示 M-003）或 already=0（A.zip 真实薄库存），不编造；
  5. 无 Key -> 明确标记的规则演示模式；强制 qwen 模式无 Key 时报错；
  6. 轮数/工具调用预算硬约束；
  7. LLM 抛异常 -> 自动降级 rule-demo-fallback 且带错误标记。
"""
import json
import os

import pytest

import qwen_agent
from service import ChainCoxswainService  # noqa: F401  (backend 已在 sys.path)

GOAL = "对当前数据集执行库存压力测试：先看风险清单，再搜索临界点并比较干预方案。"


# ------------------------------------------------------------------ mocks
def _resp(content="", tool_calls=None, usage=None):
    msg = {"content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    out = {"choices": [{"message": msg}]}
    if usage:
        out["usage"] = usage
    return out


def _call(name, args, cid):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


class ScriptedMockLLM:
    """按脚本逐步返回 OpenAI 风格响应；最后一步重复返回（content-only 会结束循环）。"""

    model = "mock-qwen"  # run_agent 组装 modeLabel 时读取 client.model

    def __init__(self, steps):
        self.steps = list(steps)
        self.messages_seen = []

    def chat(self, messages, tools=None):
        self.messages_seen.append(list(messages))
        return self.steps[min(len(self.messages_seen) - 1, len(self.steps) - 1)]


class AdaptiveMockLLM:
    """第二轮参数 = 解析上一轮工具结果，证明“观察 -> 调整 -> 再调用”闭环。"""

    model = "mock-qwen"

    def __init__(self):
        self.chat_calls = 0
        self.observed_threshold = None

    def chat(self, messages, tools=None):
        self.chat_calls += 1
        if self.chat_calls == 1:
            return _resp("先对 M-002 搜索需求上涨临界点",
                         [_call("search_threshold",
                                {"sku": "M-002", "scenario_type": "demandIncrease"}, "c1")])
        if self.chat_calls == 2:
            tool_msgs = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
            threshold = tool_msgs[-1]["threshold"]  # 来自上一轮引擎结果
            self.observed_threshold = threshold
            return _resp("上一轮显示临界点为 %s，据此比较干预方案" % threshold,
                         [_call("compare_interventions",
                                {"sku": "M-002", "scenario_type": "demandIncrease",
                                 "value": threshold}, "c2")])
        return _resp("完成。")


class BoomLLM:
    model = "mock-qwen"

    def chat(self, messages, tools=None):
        raise RuntimeError("模拟网络失败：连接超时")


# ------------------------------------------------------------------ tests
def test_mock_loop_records_trace_and_conclusion(service):
    steps = [
        _resp("先获取风险清单", [_call("get_risk_list", {"limit": 5}, "c1")]),
        _resp("风险清单已获得，M-002 为重点，搜索需求上涨临界点",
              [_call("search_threshold",
                     {"sku": "M-002", "scenario_type": "demandIncrease"}, "c2")]),
        _resp("临界点已找到，比较干预方案",
              [_call("compare_interventions",
                     {"sku": "M-002", "scenario_type": "demandIncrease", "value": 22}, "c3")]),
        _resp("总结：M-002 在需求上涨 22%% 时首次缺货，建议临时补货预案供人工审核。"),
    ]
    rec = qwen_agent.run_agent(service, GOAL, {"maxRounds": 8, "maxToolCalls": 30},
                               llm=ScriptedMockLLM(steps))
    assert rec["mode"] == "qwen"
    assert [t["tool"] for t in rec["trace"]] == ["get_risk_list", "search_threshold", "compare_interventions"]
    assert all(t["ok"] for t in rec["trace"])
    thr = rec["conclusion"]["thresholdsFound"]
    assert any(x["sku"] == "M-002" and x["scenarioType"] == "demandIncrease"
               and x["threshold"] == 22 and x["status"] == "found" for x in thr)
    assert "M-002" in rec["conclusion"]["interventionsTested"]
    assert rec["conclusion"]["mostFragile"], "风险清单应给出脆弱物料"
    assert rec["finalText"].startswith("总结")
    assert all(rec["constraintsSatisfied"].values())
    assert "工具" in rec["conclusion"]["note"]  # 结论数字以工具记录为准


def test_adaptive_mock_next_round_depends_on_previous_result(service):
    """下一轮参数必须来自上一轮工具结果（feedback dependency）。"""
    mock = AdaptiveMockLLM()
    rec = qwen_agent.run_agent(service, GOAL, {"maxRounds": 8, "maxToolCalls": 30}, llm=mock)
    first_result = rec["trace"][0]["result"]
    assert first_result["status"] == "found"
    assert mock.observed_threshold == first_result["threshold"] == 22
    assert rec["trace"][1]["args"]["value"] == 22  # 第二轮的 value 直接引用第一轮阈值
    assert rec["trace"][1]["ok"] is True


def test_unknown_tool_returns_error_and_loop_continues(service):
    steps = [
        _resp("尝试一个不存在的工具", [_call("no_such_tool", {}, "c1")]),
        _resp("该工具不存在，改用风险清单", [_call("get_risk_list", {"limit": 3}, "c2")]),
        _resp("完成。"),
    ]
    rec = qwen_agent.run_agent(service, GOAL, {}, llm=ScriptedMockLLM(steps))
    assert rec["trace"][0]["ok"] is False
    assert "未知工具" in rec["trace"][0]["error"]
    assert rec["trace"][1]["ok"] is True  # 错误被反馈后循环继续
    assert rec["mode"] == "qwen"


def test_bad_arguments_return_error_not_crash(service):
    steps = [
        _resp("查询物料但漏了 sku 参数", [_call("query_material", {}, "c1")]),
        _resp("参数错误，改查风险清单", [_call("get_risk_list", {"limit": 3}, "c2")]),
        _resp("完成。"),
    ]
    rec = qwen_agent.run_agent(service, GOAL, {}, llm=ScriptedMockLLM(steps))
    assert rec["trace"][0]["ok"] is False
    assert "工具执行失败" in rec["trace"][0]["error"]


def test_malformed_json_arguments_safe(service):
    bad = {"id": "c1", "type": "function",
           "function": {"name": "query_material", "arguments": "这不是JSON"}}
    rec = qwen_agent.run_agent(service, GOAL, {},
                               llm=ScriptedMockLLM([_resp("", [bad]), _resp("完成。")]))
    assert rec["trace"][0]["ok"] is False


def test_no_po_material_threshold_not_applicable(service):
    """演示数据 M-003 没有任何采购订单：延期类临界点严格 not-applicable，不得编造。"""
    for stype in ("deliveryDelay", "purchaseDelay"):
        r = service.tool_search_threshold("M-003", stype, horizon=14)
        assert r["status"] == "not-applicable", "%s 无采购数据必须不适用" % stype
        assert r["threshold"] is None
    q = service.tool_query_material("M-003")
    assert q["nextExpectedArrival"] == "无采购记录"


def test_no_purchase_data_azip_honest_status(a_zip_path, tmp_path):
    """A.zip 无采购订单且库存极薄：延期类临界点只能是 already（阈值 0）或 not-applicable。

    实测属性：632 个 unit 中位覆盖 < 1 天，14 天窗口内全部基线缺货，
    因此几乎都会得到 already=0 —— 引擎如实报告，而不是编造一个“临界点”。
    """
    s = ChainCoxswainService(data_dir=os.path.dirname(a_zip_path),
                             log_path=str(tmp_path / "tool_logs.json"))
    meta = s.load_a_zip()
    assert meta["dataKind"] == "real-historical"
    assert s.orders == []
    sku = s.materials[0]["sku"]
    for stype in ("deliveryDelay", "purchaseDelay"):
        r = s.tool_search_threshold(sku, stype, horizon=14)
        assert r["status"] in ("already", "not-applicable")
        if r["status"] == "already":
            assert r["threshold"] == 0
        else:
            assert r["threshold"] is None
    q = s.tool_query_material(sku)
    assert q["nextExpectedArrival"] == "无采购记录"
    ex = s.tool_explain(sku)
    assert "无采购数据" in ex["explanation"]


def test_no_key_falls_back_to_rule_demo(service, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    rec = qwen_agent.run_agent(service, "压力测试", {"limitSkus": 3, "horizon": 14,
                                                     "maxRounds": 8, "maxToolCalls": 30})
    assert rec["mode"] == "rule-demo"
    assert "规则演示" in rec["modeLabel"]
    assert rec["finalText"].startswith("（规则演示模式")
    assert rec["rounds"], "规则控制器也要有可见的轮次记录"
    assert all(rec["constraintsSatisfied"].values())


def test_force_rule_mode_ignores_mock_llm(service):
    rec = qwen_agent.run_agent(service, GOAL, {"mode": "rule", "limitSkus": 2, "maxToolCalls": 20},
                               llm=ScriptedMockLLM([_resp("不应被调用")]))
    assert rec["mode"] == "rule-demo"


def test_force_qwen_without_key_raises(service, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        qwen_agent.run_agent(service, GOAL, {"mode": "qwen"})


def test_tight_budget_respected(service, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    rec = qwen_agent.run_agent(service, "压力测试", {"limitSkus": 3, "horizon": 14,
                                                     "maxRounds": 3, "maxToolCalls": 10})
    u = rec["usage"]
    assert u["toolCalls"] <= 10, "工具调用数不能超过预算"
    assert u["roundsUsed"] <= 3, "轮数不能超过预算"
    assert rec["constraintsSatisfied"]["roundsOk"] is True
    assert rec["constraintsSatisfied"]["toolCallsOk"] is True


def test_llm_exception_falls_back_with_label(service, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    rec = qwen_agent.run_agent(service, GOAL, {"limitSkus": 2}, llm=BoomLLM())
    assert rec["mode"] == "rule-demo-fallback"
    assert "模拟网络失败" in rec["agentError"]
    assert "降级" in rec["modeLabel"]
    assert rec["conclusion"], "降级后仍要交付可审计结论"
    assert all(rec["constraintsSatisfied"].values())
