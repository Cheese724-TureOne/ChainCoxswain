# -*- coding: utf-8 -*-
"""ChainCoxswain Qwen agent: planning -> tool calling -> observation -> re-planning.

Architecture (single agent + tools, no multi-agent framework):
  * The Qwen model (阿里云百炼, OpenAI-compatible endpoint) decides WHAT to test
    and HOW to adjust the next round. It never computes inventory numbers.
  * Every numeric result comes from backend/engine.py via ChainCoxswainService tools.
  * Each round is recorded (reasoning from the model, tool arguments, tool results)
    so judges can verify that round N+1 depends on the result of round N.

Safety / cost bounds:
  * maxRounds, maxToolCalls, per-call timeout, total timeout, token budget.
  * Tool errors are fed back to the model so it can correct its arguments.
  * Without DASHSCOPE_API_KEY (or when the API fails) the loop falls back to a
    clearly-labelled rule-driven demo mode (deterministic, same engine).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_MODEL = "qwen-plus"

SYSTEM_PROMPT = """你是 ChainCoxswain 的供应链风险分析 Agent。你的职责是规划并执行库存压力测试，而不是代替计算引擎报数字。

工作 SOP：
1. 先调用 get_risk_list 获取风险清单，了解整体情况；
2. 用 query_material 查看重点物料的库存、用量、覆盖天数、下一笔到货；
3. 用 run_simulation 建立基准；再针对物料特性选择压力类型：
   - 有已下单采购单 → deliveryDelay（到货延期）；
   - 有“计划”采购单 → purchaseDelay（补货下单推迟）；
   - 任何有需求的物料 → demandIncrease（需求上涨）；
4. 用 search_threshold 搜索首次缺货临界点（倍增试探 + 二分，由引擎完成）；
5. 对最关键的情景用 compare_interventions 比较催货/临时补货/需求平滑等预案；
6. 需要回顾证据时用 get_tool_history 或 explain。

硬性约束：
- 禁止自行编造缺货数量、缺货日期、库存余额；所有数字必须来自工具返回结果；
- 每轮只调整一个变量，根据上一轮结果决定下一轮参数（上移/下移/停止）；
- 最多测试 3 个物料、每种扰动最多搜索 1 次阈值，找到临界点或触及边界即停止；
- 工具返回 status=not-applicable 时（例如无采购数据）不得强行测试该类型；
- 最后用一段话总结：哪些物料最脆弱、临界条件是什么、推荐哪些预案供人工审核；
- 总结中引用的每个数字都要能在工具结果中找到对应出处。"""

TOOLS: List[Dict] = [
    {"type": "function", "function": {
        "name": "get_risk_list",
        "description": "获取当前数据的库存风险清单（按缺货风险、低于安全库存、覆盖偏高排序），返回 top N 项及总体统计",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "返回条数，1-50，默认 10"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "query_material",
        "description": "查询单个物料的库存、日均用量、覆盖天数、安全库存、下一笔预计到货与基准风险等级",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string", "description": "物料编码"}},
            "required": ["sku"]}}},
    {"type": "function", "function": {
        "name": "run_simulation",
        "description": "运行确定性库存仿真。scenario_type 为 baseline/deliveryDelay/purchaseDelay/demandIncrease；value 为延期天数或需求上涨百分比",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"},
            "scenario_type": {"type": "string", "enum": ["baseline", "deliveryDelay", "purchaseDelay", "demandIncrease"]},
            "value": {"type": "integer", "minimum": 0, "maximum": 500},
            "horizon": {"type": "integer", "minimum": 1, "maximum": 365}},
            "required": ["sku", "scenario_type"]}}},
    {"type": "function", "function": {
        "name": "search_threshold",
        "description": "搜索首次触发缺货的最小整数扰动（临界点）。引擎用倍增试探+整数二分，返回逐轮日志",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"},
            "scenario_type": {"type": "string", "enum": ["deliveryDelay", "purchaseDelay", "demandIncrease"]},
            "max_value": {"type": "integer", "minimum": 1, "maximum": 500},
            "horizon": {"type": "integer", "minimum": 1, "maximum": 365}},
            "required": ["sku", "scenario_type"]}}},
    {"type": "function", "function": {
        "name": "compare_interventions",
        "description": "对给定情景比较催货、临时补货、需求平滑等干预预案的仿真效果",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"},
            "scenario_type": {"type": "string", "enum": ["deliveryDelay", "purchaseDelay", "demandIncrease", "supplierOutage"]},
            "value": {"type": "integer", "minimum": 0, "maximum": 500},
            "horizon": {"type": "integer", "minimum": 1, "maximum": 365}},
            "required": ["sku", "scenario_type"]}}},
    {"type": "function", "function": {
        "name": "query_stockout_probability",
        "description": "Monte Carlo 缺货概率：未来 7/14/30 天缺货概率、预计未满足量、P95 缺口、首次缺货日分布。历史不足时返回 available=false，不得把情景分析当概率引用",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"},
            "n": {"type": "integer", "minimum": 100, "maximum": 5000},
            "seed": {"type": "integer"}},
            "required": ["sku"]}}},
    {"type": "function", "function": {
        "name": "query_material_policy",
        "description": "动态安全库存/再订货点（SS=z*sqrt(L̄σD²+μD²σL²)、ROP=μD*L̄+SS）、库存位置与补货预警；历史不足时回退并标注原因",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"},
            "service_level": {"type": "number", "enum": [0.90, 0.95, 0.99]}},
            "required": ["sku"]}}},
    {"type": "function", "function": {
        "name": "query_recovery_plan",
        "description": "反向干预优化：解除缺货的最小补货量、最晚可行到货日、最小需求削减、加急天数与组合方案；只推荐约束内可行方案",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"},
            "scenario_type": {"type": "string", "enum": ["baseline", "deliveryDelay", "purchaseDelay", "demandIncrease"]},
            "value": {"type": "integer", "minimum": 0, "maximum": 500},
            "horizon": {"type": "integer", "minimum": 1, "maximum": 365}},
            "required": ["sku"]}}},
    {"type": "function", "function": {
        "name": "query_joint_threshold",
        "description": "联合压力临界点：固定需求增长下引发缺货的最小供应延期天数（线性扫描并返回单调性实测标记）",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"},
            "demand_pct": {"type": "number", "minimum": 0, "maximum": 500},
            "max_delay": {"type": "integer", "minimum": 1, "maximum": 60},
            "horizon": {"type": "integer", "minimum": 1, "maximum": 365}},
            "required": ["sku"]}}},
    {"type": "function", "function": {
        "name": "get_risk_dimensions",
        "description": "六维内部风险（短缺/波动/履约/积压/仓库/关键性）与规则分层优先级 P0-P3；缺失数据显式标注",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"}},
            "required": ["sku"]}}},
    {"type": "function", "function": {
        "name": "get_warehouse_risk",
        "description": "分仓风险：SKU+warehouse 单元的库存/用量/在途/缺货与覆盖离散度；无调拨约束时不把他仓库存计为可用",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"},
            "transship": {"type": "object"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "get_tool_history",
        "description": "读取本次会话已执行过的工具调用记录（含参数与结果摘要），用于回顾证据",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "explain",
        "description": "生成指定物料的数据依据解释（由确定性引擎输出，不含模型编造数字）",
        "parameters": {"type": "object", "properties": {
            "sku": {"type": "string"}},
            "required": ["sku"]}}},
]


class LLMClient:
    """OpenAI-compatible chat client (阿里云百炼 DashScope compatible-mode)."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None,
                 base_url: str = DASHSCOPE_URL, timeout: float = 60.0):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.model = model or os.environ.get("QWEN_MODEL", DEFAULT_MODEL)
        self.base_url = base_url
        self.timeout = timeout

    def chat(self, messages: List[Dict], tools: Optional[List[Dict]] = None) -> Dict:
        if not self.api_key:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY")
        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Authorization": "Bearer %s" % self.api_key, "Content-Type": "application/json"}
        last_err: Optional[Exception] = None
        for attempt in (0, 1):
            try:
                resp = httpx.post(self.base_url, json=payload, headers=headers, timeout=self.timeout)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.5)
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise RuntimeError("千问 API 调用失败: %s" % last_err)


def status() -> Dict:
    key = os.environ.get("DASHSCOPE_API_KEY", "")
    model = os.environ.get("QWEN_MODEL", DEFAULT_MODEL)
    has_key = bool(key.strip())
    return {
        "configured": has_key,
        "mode": "qwen" if has_key else "rule-demo",
        "modeLabel": "真实千问 API（百炼）" if has_key else "规则演示模式（未配置 API Key）",
        "model": model if has_key else None,
        "endpoint": DASHSCOPE_URL,
        "apiKeyHint": ("已配置（%s...%s）" % (key[:4], key[-4:])) if len(key) > 8 else ("已配置（过短，请检查）" if has_key else "未配置"),
        "note": "API Key 仅保存在服务器环境变量 DASHSCOPE_API_KEY 中，不会下发到浏览器",
    }


def _token_estimate(text: str) -> int:
    return max(1, len(text) // 2)


def _tool_result_content(tool_name: str, result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def run_agent(service, goal: str, params: Optional[Dict] = None,
              llm: Optional[LLMClient] = None) -> Dict:
    """Execute the plan->call->observe->re-plan loop. `llm` injectable for tests."""
    params = params or {}
    max_rounds = int(params.get("maxRounds", 10))
    max_tool_calls = int(params.get("maxToolCalls", 40))
    total_timeout = float(params.get("timeoutSec", 240))
    budget_tokens = int(params.get("budgetTokens", 60000))
    force_mode = params.get("mode")  # None | "qwen" | "rule"
    st = status()
    t_start = time.time()
    used_tokens = 0

    use_qwen = (force_mode != "rule") and (st["configured"] or llm is not None)
    if force_mode == "qwen" and not st["configured"] and llm is None:
        raise RuntimeError("请求了真实千问模式，但服务器未配置 DASHSCOPE_API_KEY")

    tool_map = {
        "get_risk_list": lambda a: service.tool_risk_list(limit=a.get("limit") or 10, caller="agent"),
        "query_material": lambda a: service.tool_query_material(sku=a["sku"], caller="agent"),
        "run_simulation": lambda a: service.tool_run_simulation(
            sku=a["sku"], scenario_type=a.get("scenario_type") or "baseline",
            value=a.get("value") or 0, horizon=a.get("horizon") or 14, caller="agent"),
        "search_threshold": lambda a: service.tool_search_threshold(
            sku=a["sku"], scenario_type=a["scenario_type"],
            max_value=a.get("max_value"), horizon=a.get("horizon") or 14, caller="agent"),
        "compare_interventions": lambda a: service.tool_compare_interventions(
            sku=a["sku"], scenario_type=a.get("scenario_type") or "deliveryDelay",
            value=a.get("value") or 0, horizon=a.get("horizon") or 14, caller="agent"),
        "query_stockout_probability": lambda a: service.tool_stockout_probability(
            sku=a["sku"], n=a.get("n") or 500, seed=a.get("seed") or 42, caller="agent"),
        "query_material_policy": lambda a: service.tool_material_policy(
            sku=a["sku"], service_level=a.get("service_level") or 0.95, caller="agent"),
        "query_recovery_plan": lambda a: service.tool_recovery_plan(
            sku=a["sku"], scenario_type=a.get("scenario_type") or "baseline",
            value=a.get("value") or 0, horizon=a.get("horizon") or 14, caller="agent"),
        "query_joint_threshold": lambda a: service.tool_joint_threshold(
            sku=a["sku"], demand_pct=a.get("demand_pct") or 20,
            max_delay=a.get("max_delay") or 10, horizon=a.get("horizon") or 14, caller="agent"),
        "get_risk_dimensions": lambda a: service.tool_risk_dimensions(sku=a["sku"], caller="agent"),
        "get_warehouse_risk": lambda a: service.tool_warehouse_risk(
            sku=a.get("sku") or None, transship=a.get("transship"), caller="agent"),
        "get_tool_history": lambda a: service.tool_history(limit=a.get("limit") or 30, caller="agent"),
        "explain": lambda a: service.tool_explain(sku=a["sku"], caller="agent"),
    }

    def guard_timeout():
        if time.time() - t_start > total_timeout:
            raise RuntimeError("Agent 总运行时间超过 %.0f 秒限制" % total_timeout)

    if not use_qwen:
        return _rule_demo_run(service, goal, params, reason=st["modeLabel"])

    client = llm or LLMClient()
    messages: List[Dict] = [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": goal}]
    rounds: List[Dict] = []
    trace: List[Dict] = []
    tool_calls_total = 0
    final_text = ""
    error_final = None
    try:
        for r in range(1, max_rounds + 1):
            guard_timeout()
            if tool_calls_total >= max_tool_calls:
                final_text = "（达到工具调用上限 %d，停止探索）" % max_tool_calls
                break
            resp = client.chat(messages, tools=TOOLS)
            if "usage" in resp and resp["usage"].get("total_tokens"):
                used_tokens += int(resp["usage"]["total_tokens"])
            if used_tokens > budget_tokens:
                raise RuntimeError("超过 token 费用预算（%d），停止运行" % budget_tokens)
            msg = resp["choices"][0]["message"]
            text = msg.get("content") or ""
            calls = msg.get("tool_calls") or []
            round_entry = {"round": r, "stage": "plan", "reasoning": text, "toolCalls": []}
            if not calls:
                final_text = text
                rounds.append(round_entry)
                break
            for call in calls:
                guard_timeout()
                fn = call["function"]
                name = fn["name"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result, err = _execute_tool(service, tool_map, name, args)
                tool_calls_total += 1
                entry = {"tool": name, "args": args, "ok": err is None,
                         "error": err, "result": result}
                round_entry["toolCalls"].append(entry)
                trace.append({"round": r, **entry})
                messages.append({"role": "assistant", "content": text,
                                 "tool_calls": [{"id": call.get("id") or ("call_%d" % tool_calls_total),
                                                 "type": "function",
                                                 "function": {"name": name, "arguments": fn.get("arguments") or "{}"}}]})
                content = {"ok": err is None, "error": err} if err else result
                messages.append({"role": "tool", "tool_call_id": call.get("id") or ("call_%d" % tool_calls_total),
                                 "content": json.dumps(content, ensure_ascii=False, default=str)})
            rounds.append(round_entry)
        if not final_text and not rounds:
            final_text = "（模型未返回任何内容）"
    except Exception as e:  # noqa: BLE001
        error_final = str(e)
        # Graceful degradation: fall back to the deterministic explorer so the
        # product still delivers a complete, labelled result.
        fallback = _rule_demo_run(service, goal, params,
                                  reason="千问调用失败：%s" % e)
        fallback["agentError"] = error_final
        fallback["mode"] = "rule-demo-fallback"
        fallback["modeLabel"] = "规则演示模式（千问调用失败自动降级）"
        return fallback

    conclusion = _build_conclusion(trace, rounds, final_text)
    record = {
        "mode": "qwen",
        "modeLabel": "真实千问 API（百炼 %s）" % client.model,
        "goal": goal,
        "rounds": rounds,
        "trace": trace,
        "finalText": final_text,
        "conclusion": conclusion,
        "usage": {"roundsUsed": len(rounds), "toolCalls": tool_calls_total,
                  "tokensEstimate": used_tokens, "durationMs": int((time.time() - t_start) * 1000),
                  "maxRounds": max_rounds, "maxToolCalls": max_tool_calls,
                  "budgetTokens": budget_tokens, "totalTimeoutSec": total_timeout},
        "constraintsSatisfied": {
            "roundsOk": len(rounds) <= max_rounds,
            "toolCallsOk": tool_calls_total <= max_tool_calls,
            "budgetOk": used_tokens <= budget_tokens,
            "timeoutOk": (time.time() - t_start) <= total_timeout,
        },
        "error": error_final,
    }
    service.agent_runs.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": record["mode"],
                               "goal": goal[:80], "toolCalls": tool_calls_total,
                               "rounds": len(rounds)})
    service._persist()
    return record


def _execute_tool(service, tool_map, name: str, args: Dict) -> Tuple[Any, Optional[str]]:
    if name not in tool_map:
        return None, "未知工具 %s；可用工具：%s" % (name, ", ".join(sorted(tool_map)))
    try:
        # argument validation happens inside the service tools (they raise ValueError)
        return tool_map[name](args), None
    except Exception as e:  # noqa: BLE001
        return None, "工具执行失败：%s" % e


def _build_conclusion(trace: List[Dict], rounds: List[Dict], final_text: str) -> Dict:
    """Assemble a structured conclusion ONLY from tool results (auditable)."""
    thresholds: Dict[str, Dict] = {}
    sims: Dict[str, Dict] = {}
    interventions: Dict[str, List] = {}
    risk_lists: List[Dict] = []
    for t in trace:
        if not t["ok"]:
            continue
        r = t["result"] or {}
        if t["tool"] == "get_risk_list":
            risk_lists.append(r)
        if t["tool"] == "search_threshold":
            sku = r.get("sku") or t["args"].get("sku")
            stype = r.get("scenarioType") or t["args"].get("scenario_type")
            if sku and stype:
                thresholds["%s|%s" % (sku, stype)] = r
        if t["tool"] == "run_simulation":
            sims.setdefault(t["args"].get("sku", "?"), {})[t["args"].get("scenario_type", "baseline")] = r.get("result", r)
        if t["tool"] == "compare_interventions":
            sku = r.get("sku") or t["args"].get("sku")
            if sku:
                interventions.setdefault(sku, r.get("interventions", []))
    top_risks = risk_lists[-1]["top"] if risk_lists else []
    found_thresholds = [{"sku": k.split("|")[0], "scenarioType": k.split("|")[1],
                         "threshold": v["threshold"], "status": v["status"], "rounds": v["rounds"]}
                        for k, v in thresholds.items()]
    return {
        "mostFragile": [{"sku": x["sku"], "level": x["level"], "coverageDays": x["coverageDays"],
                         "firstShortage": x["firstShortage"], "totalUnmet": x["totalUnmet"]}
                        for x in top_risks[:3]],
        "thresholdsFound": found_thresholds,
        "interventionsTested": {k: [{"id": x["id"], "title": x["title"],
                                     "improvementUnits": x["improvementUnits"],
                                     "resolvesShortage": x["resolvesShortage"]} for x in v]
                                for k, v in interventions.items()},
        "modelSummary": final_text[:1200] if final_text else "",
        "note": "结论数字全部来自工具返回结果；模型总结文字仅供阅读，以工具记录为准",
    }


# ------------------------------------------------------------------ rule demo
def _rule_demo_run(service, goal: str, params: Dict, reason: str) -> Dict:
    """Deterministic rule-driven explorer, shaped like the agent loop, so the
    frontend can show plan -> tool call -> observe -> re-plan with real feedback.
    Every reasoning string is generated by fixed rules, clearly not an LLM.
    Budget-aware: stops adding materials before exceeding maxToolCalls/maxRounds."""
    t_start = time.time()
    limit_skus = int(params.get("limitSkus", 5))
    horizon = int(params.get("horizon", 14))
    max_rounds = int(params.get("maxRounds", 10))
    max_calls = int(params.get("maxToolCalls", 40))
    scan = service.tool_autonomous_stress(limit_skus=limit_skus, horizon=horizon, caller="rule-engine")
    rounds: List[Dict] = []
    trace: List[Dict] = []
    tool_calls = 0
    trimmed = []
    # Round 1: risk list
    rl = service.tool_risk_list(limit=limit_skus, caller="rule-engine")
    tool_calls += 1
    rounds.append({"round": 1, "stage": "plan", "reasoning": "规则控制器：先获取风险清单，按缺口与覆盖天数筛选最脆弱物料",
                   "toolCalls": [{"tool": "get_risk_list", "args": {"limit": limit_skus}, "ok": True, "result": rl}]})
    trace.append({"round": 1, "tool": "get_risk_list", "args": {"limit": limit_skus}, "ok": True, "result": rl})
    # Group per-material probes from scan.log
    by_sku: Dict[str, List[Dict]] = {}
    for l in scan["log"]:
        by_sku.setdefault(l["sku"], []).append(l)
    r_idx = 2
    for t in scan["tested"]:
        sku = t["sku"]
        sel = by_sku.get(sku, [])
        probe_entries = [{"tool": "search_threshold", "args": {"sku": sku, "scenario_type": l["type"], "value": l["value"]},
                          "ok": True, "result": {"failed": l["failed"], "firstShortage": l.get("firstShortage"),
                                                 "totalUnmet": l.get("totalUnmet")}}
                         for l in sel if isinstance(l.get("failed"), bool)]
        # per-material final summaries (3) + interventions (1)
        additional = len(probe_entries) + 4
        if tool_calls + additional > max_calls or r_idx + 2 > max_rounds:
            trimmed.append(sku)
            continue
        sel_msgs = [l["message"] for l in sel if l.get("type") == "select"]
        reasoning = "规则控制器：%s（上一轮风险清单显示其覆盖与缺口最脆弱）；" % sku + \
            ("；".join(sel_msgs[:2]) if sel_msgs else "")
        for e in probe_entries:
            trace.append({"round": r_idx, **e})
        tool_calls += len(probe_entries)
        rounds.append({"round": r_idx, "stage": "execute+observe", "reasoning": reasoning,
                       "nextStep": "根据失败/未失败结果进行倍增或二分，逼近最小触发整数",
                       "toolCalls": probe_entries})
        r_idx += 1
        # Final threshold summaries + interventions, batched in one observe round
        final_calls = []
        for stype, th in t["thresholds"].items():
            summary = {"sku": sku, "scenarioType": stype, "status": th["status"],
                       "threshold": th["threshold"], "rounds": th["rounds"],
                       "firstShortageAtThreshold": None, "totalUnmetAtThreshold": None}
            e = {"tool": "search_threshold", "args": {"sku": sku, "scenario_type": stype},
                 "ok": True, "result": summary}
            final_calls.append(e)
            trace.append({"round": r_idx, **e})
        try:
            inter = service.tool_compare_interventions(sku, t["focusScenario"]["type"],
                                                       t["focusScenario"]["value"], horizon=horizon,
                                                       caller="rule-engine")
        except Exception:
            inter = None
        if inter:
            e2 = {"tool": "compare_interventions",
                  "args": {"sku": sku, "scenario_type": t["focusScenario"]["type"],
                           "value": t["focusScenario"]["value"]},
                  "ok": True, "result": inter}
            final_calls.append(e2)
            trace.append({"round": r_idx, **e2})
        tool_calls += len(final_calls)
        reason_txt = "；".join("%s：%s（阈值 %s，%s 轮）" % (stype, th["status"], th["threshold"], th["rounds"])
                               for stype, th in t["thresholds"].items())
        rounds.append({"round": r_idx, "stage": "observe",
                       "reasoning": "规则控制器：%s 临界点汇总 %s；并针对重点情景 %s 比较处置预案" % (
                           sku, reason_txt, json.dumps(t["focusScenario"], ensure_ascii=False)),
                       "nextStep": "预算内继续下一脆弱物料，或结束探索并汇总结论",
                       "toolCalls": final_calls})
        r_idx += 1
    if trimmed:
        rounds.append({"round": r_idx, "stage": "stop",
                       "reasoning": "规则控制器：剩余物料 %s 因工具调用/轮数预算不足而未做临界点搜索，仅保留基线风险清单结论" % ("、".join(trimmed)),
                       "toolCalls": []})
    conclusion = _build_conclusion(trace, rounds, "")
    rounds_ok = len(rounds) <= max_rounds
    calls_ok = tool_calls <= max_calls
    record = {
        "mode": "rule-demo",
        "modeLabel": reason,
        "goal": goal,
        "rounds": rounds,
        "trace": trace,
        "finalText": "（规则演示模式：以上推理文字由内置规则控制器生成，未经过大模型）",
        "conclusion": conclusion,
        "usage": {"roundsUsed": len(rounds), "toolCalls": tool_calls,
                  "tokensEstimate": 0, "durationMs": int((time.time() - t_start) * 1000),
                  "maxRounds": max_rounds,
                  "maxToolCalls": max_calls,
                  "budgetTokens": int(params.get("budgetTokens", 60000)),
                  "totalTimeoutSec": float(params.get("timeoutSec", 240))},
        "constraintsSatisfied": {"roundsOk": rounds_ok, "toolCallsOk": calls_ok,
                                 "budgetOk": True, "timeoutOk": True},
        "error": None,
    }
    service.agent_runs.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": record["mode"],
                               "goal": goal[:80], "toolCalls": tool_calls, "rounds": len(rounds)})
    service._persist()
    return record
