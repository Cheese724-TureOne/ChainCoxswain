# -*- coding: utf-8 -*-
"""ChainCoxswain MCP Server（Model Context Protocol, JSON-RPC 2.0）。

Two transports, one handler:
  * stdio  — standard MCP transport for real clients (千问办公 / Claude / IDE):
             python backend/mcp_server.py
  * HTTP bridge — POST JSON-RPC to /api/mcp on the Flask server, used for local
             automated tests and for clients that cannot spawn stdio processes.

Exposed tools (per competition spec):
  1. get_inventory_risk_list   获取库存风险清单
  2. query_material            查询重点物料
  3. run_autonomous_stress_test 执行自主压力测试
  4. query_risk_threshold      查询风险临界点
  5. compare_interventions     比较干预方案
  6. get_agent_tool_logs       获取 Agent 工具调用记录
  7. export_analysis_report    导出分析报告（Excel/Word/PPT）

Business actions (下单/通知供应商/调库存) are intentionally NOT exposed.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

try:
    from .service import ChainCoxswainService
except ImportError:  # pragma: no cover
    from service import ChainCoxswainService  # type: ignore

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "chaincoxswain-mcp"
SERVER_VERSION = "1.0.0"


def _json_safe(obj: Any) -> Any:
    """NaN/Infinity are not valid JSON tokens for strict clients."""
    import math
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def build_service(data_dir: str, service: Optional[ChainCoxswainService] = None) -> ChainCoxswainService:
    """默认新建服务并加载演示数据；stdio 模式下可用 CHAINCOXSWAIN_DATASET=demo|azip|relational
    指定启动时加载的数据集（与前端/API 无共享内存时使用）。"""
    if service is not None:
        return service
    s = ChainCoxswainService(data_dir=data_dir, log_path=os.path.join(data_dir, "..", "logs", "tool_logs.json"))
    kind = os.environ.get("CHAINCOXSWAIN_DATASET", "demo")
    if kind == "azip":
        s.load_a_zip()
    elif kind == "relational":
        s.load_rel()
    return s


class McpServer:
    def __init__(self, data_dir: str, service: Optional[ChainCoxswainService] = None):
        # 传入 service 时与 REST API 共享同一内存服务：页面加载的数据集，MCP 工具立即可见
        self.service = service or build_service(data_dir)
        self.tools = [
            {
                "name": "get_inventory_risk_list",
                "description": "获取当前数据集的库存风险清单：按缺货风险/低于安全库存/覆盖偏高排序，返回总体统计与 top N 项（含首次缺货日期、累计缺口与原因）。数字由确定性库存仿真引擎计算。",
                "inputSchema": {"type": "object", "properties": {
                    "limit": {"type": "integer", "description": "返回条数（1-200），默认 20", "default": 20}},
                    "required": []},
            },
            {
                "name": "query_material",
                "description": "查询单个物料的库存、日均用量、覆盖天数、安全库存、交期假设、供应商（如缺失会明确标注）与下一笔预计到货。",
                "inputSchema": {"type": "object", "properties": {
                    "sku": {"type": "string", "description": "物料编码"}},
                    "required": ["sku"]},
            },
            {
                "name": "run_autonomous_stress_test",
                "description": "执行自主压力测试：自动筛选脆弱物料、按业务证据决定测试顺序、逐轮搜索到货延期/下单推迟/需求上涨的首次缺货临界点，并返回完整逐轮日志。",
                "inputSchema": {"type": "object", "properties": {
                    "limit_skus": {"type": "integer", "description": "最多测试的物料数（1-15），默认 5", "default": 5},
                    "horizon": {"type": "integer", "description": "测试窗口天数（1-365），默认 14", "default": 14}},
                    "required": []},
            },
            {
                "name": "query_risk_threshold",
                "description": "查询指定物料在指定扰动类型下的缺货临界点（最小触发整数）。引擎采用倍增试探+整数二分，返回逐轮测试日志。",
                "inputSchema": {"type": "object", "properties": {
                    "sku": {"type": "string", "description": "物料编码"},
                    "scenario_type": {"type": "string", "enum": ["deliveryDelay", "purchaseDelay", "demandIncrease"],
                                      "description": "deliveryDelay=到货延期(天)；purchaseDelay=补货下单推迟(天)；demandIncrease=需求上涨(%)"},
                    "max_value": {"type": "integer", "description": "搜索上限（1-500），默认 30（天）或 200（%）"}},
                    "required": ["sku", "scenario_type"]},
            },
            {
                "name": "compare_interventions",
                "description": "比较给定情景下的干预预案（催货/临时补货/需求平滑等）的仿真效果：改善缺口量、是否消除缺货、数据是否充分。所有方案需人工审核后执行。",
                "inputSchema": {"type": "object", "properties": {
                    "sku": {"type": "string", "description": "物料编码"},
                    "scenario_type": {"type": "string", "enum": ["deliveryDelay", "purchaseDelay", "demandIncrease", "supplierOutage"]},
                    "value": {"type": "integer", "description": "延期天数或上涨百分比"},
                    "horizon": {"type": "integer", "description": "窗口天数，默认 14"}},
                    "required": ["sku", "scenario_type", "value"]},
            },
            {
                "name": "query_stockout_probability",
                "description": "Monte Carlo 缺货概率仿真：未来 7/14/30 天缺货概率、预计未满足量、P95 缺口、首次缺货日分布、跌破安全库存概率。基于历史需求波动与到货偏移抽样；历史不足时返回 available=false 与标注的情景分析，不输出虚假概率。固定种子可复现。",
                "inputSchema": {"type": "object", "properties": {
                    "sku": {"type": "string", "description": "物料编码"},
                    "n": {"type": "integer", "description": "仿真次数，默认 1000", "default": 1000},
                    "seed": {"type": "integer", "description": "随机种子，默认 42", "default": 42}},
                    "required": ["sku"]},
            },
            {
                "name": "query_material_policy",
                "description": "动态安全库存与再订货点：SS=z*sqrt(L̄σD²+μD²σL²)、ROP=μD*L̄+SS，含库存位置、是否需补货、低于 ROP 预警与服务水平（0.90/0.95/0.99）。历史不足时回退并标注原因。",
                "inputSchema": {"type": "object", "properties": {
                    "sku": {"type": "string", "description": "物料编码"},
                    "service_level": {"type": "number", "description": "目标服务水平，默认 0.95", "default": 0.95}},
                    "required": ["sku"]},
            },
            {
                "name": "query_recovery_plan",
                "description": "反向干预优化：基准/情景已缺货时，计算解除缺货的最小补货量、最晚可行到货日、最小需求削减比例、加急天数与组合方案；约束补货上限/供应商交期/加急能力，不可行方案不会被推荐。",
                "inputSchema": {"type": "object", "properties": {
                    "sku": {"type": "string", "description": "物料编码"},
                    "scenario_type": {"type": "string", "enum": ["baseline", "deliveryDelay", "purchaseDelay", "demandIncrease"]},
                    "value": {"type": "integer", "description": "情景参数，默认 3"},
                    "horizon": {"type": "integer", "description": "窗口天数，默认 14"}},
                    "required": ["sku"]},
            },
            {
                "name": "query_joint_threshold",
                "description": "联合压力临界点：固定需求增长比例下，引发缺货的最小供应延期天数（线性扫描，不假设单调性，返回实测单调性标记）。",
                "inputSchema": {"type": "object", "properties": {
                    "sku": {"type": "string", "description": "物料编码"},
                    "demand_pct": {"type": "number", "description": "需求增长百分比，默认 20", "default": 20},
                    "max_delay": {"type": "integer", "description": "延期扫描上限天数，默认 10", "default": 10},
                    "horizon": {"type": "integer", "description": "窗口天数，默认 14"}},
                    "required": ["sku"]},
            },
            {
                "name": "get_risk_dimensions",
                "description": "多维度内部风险：库存短缺/需求波动/采购履约/库存积压/仓库库存/关键物料六类指标 + 规则分层优先级（P0-P3），缺失数据显式标注，不伪造综合分数。",
                "inputSchema": {"type": "object", "properties": {
                    "sku": {"type": "string", "description": "物料编码"}},
                    "required": ["sku"]},
            },
            {
                "name": "get_warehouse_risk",
                "description": "分仓风险识别：以 SKU+warehouse 为单元给出各仓库存/用量/在途/缺货风险与覆盖离散度；仅在提供调拨交期与成本约束时才把他仓库存计为可用。",
                "inputSchema": {"type": "object", "properties": {
                    "sku": {"type": "string", "description": "物料编码（可选，缺省为全部分仓单元）"},
                    "transship": {"type": "object", "description": "调拨约束 {enabled, leadDays, costPerUnit}，可选"}},
                    "required": []},
            },
            {
                "name": "get_agent_tool_logs",
                "description": "获取 Agent 历史工具调用记录（含调用方、参数、结果摘要、耗时与错误），用于审计与追溯。",
                "inputSchema": {"type": "object", "properties": {
                    "limit": {"type": "integer", "description": "返回条数（1-500），默认 50", "default": 50}},
                    "required": []},
            },
            {
                "name": "export_analysis_report",
                "description": "导出供应链风险分析报告。format: xlsx（可编辑 Excel 风险表）/ docx（Word 分析报告）/ pptx（PPT 汇报材料）/ html。返回生成文件的路径与内容摘要。",
                "inputSchema": {"type": "object", "properties": {
                    "format": {"type": "string", "enum": ["xlsx", "docx", "pptx", "html"],
                               "description": "报告格式，默认 xlsx", "default": "xlsx"}},
                    "required": []},
            },
        ]

    # ------------------------------------------------------------- JSON-RPC 2.0
    def handle(self, request: Dict) -> Optional[Dict]:
        """Return a JSON-RPC response dict, or None when nothing may be sent back.

        Per JSON-RPC 2.0 a notification (a message without an "id" member) MUST NOT
        be answered. MCP clients validate incoming messages with a strict schema and
        reject a reply whose "id" is null, so replying to notifications breaks the
        handshake instead of being harmless.
        """
        method = request.get("method")
        req_id = request.get("id")
        params = request.get("params") or {}
        if "id" not in request:
            return None  # notification: acknowledge silently
        if request.get("jsonrpc") != "2.0":
            return self._err(req_id, -32600, "仅支持 JSON-RPC 2.0")
        try:
            if method == "initialize":
                return {"jsonrpc": "2.0", "id": req_id, "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}
            if method == "notifications/initialized":
                return {"jsonrpc": "2.0", "id": req_id, "result": {}}
            if method == "ping":
                return {"jsonrpc": "2.0", "id": req_id, "result": {}}
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self.tools}}
            if method == "tools/call":
                name = params.get("name")
                args = params.get("arguments") or {}
                result = self._call_tool(name, args)
                # A JSON-RPC response carries either "result" or "error", never both:
                # MCP clients parse the two shapes as a union and reject "result" +
                # "error": null. Tool-level failures stay inside result.isError.
                return {"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text", "text": json.dumps(_json_safe(result), ensure_ascii=False, default=str)}],
                    "isError": result.get("error") is not None}}
            return self._err(req_id, -32601, "未知方法: %s" % method)
        except Exception as e:  # noqa: BLE001
            return self._err(req_id, -32603, "内部错误: %s" % e)

    @staticmethod
    def _err(req_id: Any, code: int, message: str) -> Dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def _call_tool(self, name: str, args: Dict) -> Dict:
        s = self.service
        try:
            if name == "get_inventory_risk_list":
                return s.tool_risk_list(limit=args.get("limit", 20), caller="mcp")
            if name == "query_material":
                return s.tool_query_material(sku=str(args["sku"]), caller="mcp")
            if name == "run_autonomous_stress_test":
                return s.tool_autonomous_stress(limit_skus=int(args.get("limit_skus", 5)),
                                                horizon=int(args.get("horizon", 14)), caller="mcp")
            if name == "query_risk_threshold":
                return s.tool_search_threshold(sku=str(args["sku"]), scenario_type=str(args["scenario_type"]),
                                               max_value=args.get("max_value"),
                                               horizon=int(args.get("horizon", 14)), caller="mcp")
            if name == "compare_interventions":
                return s.tool_compare_interventions(sku=str(args["sku"]), scenario_type=str(args["scenario_type"]),
                                                    value=int(args["value"]), horizon=int(args.get("horizon", 14)),
                                                    caller="mcp")
            if name == "query_stockout_probability":
                return s.tool_stockout_probability(sku=str(args["sku"]), n=int(args.get("n", 1000)),
                                                   seed=int(args.get("seed", 42)), caller="mcp")
            if name == "query_material_policy":
                return s.tool_material_policy(sku=str(args["sku"]),
                                              service_level=float(args.get("service_level", 0.95)),
                                              caller="mcp")
            if name == "query_recovery_plan":
                return s.tool_recovery_plan(sku=str(args["sku"]),
                                            scenario_type=str(args.get("scenario_type", "baseline")),
                                            value=int(args.get("value", 3)),
                                            horizon=int(args.get("horizon", 14)), caller="mcp")
            if name == "query_joint_threshold":
                return s.tool_joint_threshold(sku=str(args["sku"]),
                                              demand_pct=float(args.get("demand_pct", 20)),
                                              max_delay=int(args.get("max_delay", 10)),
                                              horizon=int(args.get("horizon", 14)), caller="mcp")
            if name == "get_risk_dimensions":
                return s.tool_risk_dimensions(sku=str(args["sku"]), caller="mcp")
            if name == "get_warehouse_risk":
                return s.tool_warehouse_risk(sku=args.get("sku") or None,
                                             transship=args.get("transship"), caller="mcp")
            if name == "get_agent_tool_logs":
                return s.tool_history(limit=int(args.get("limit", 50)), caller="mcp")
            if name == "export_analysis_report":
                return s.export_report(fmt=str(args.get("format", "xlsx")))
            return {"error": "未知工具: %s" % name, "available": [t["name"] for t in self.tools]}
        except Exception as e:  # noqa: BLE001
            return {"error": "工具调用失败: %s" % e}


# ------------------------------------------------------------------ stdio loop
def _pin_stdio_utf8():
    """Force UTF-8 on the JSON-RPC pipes.

    The MCP stdio transport is UTF-8 by spec, but on Chinese Windows a redirected
    pipe defaults to the OEM code page (cp936/GBK). Without this, every Chinese tool
    description, material name and report field leaves the process as GBK bytes and
    the client decodes them as replacement characters.
    """
    for stream, errors in ((sys.stdout, "strict"), (sys.stderr, "replace"), (sys.stdin, "replace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors=errors)
            except (ValueError, OSError):  # pragma: no cover - already detached
                pass


def run_stdio(data_dir: str):
    """Read JSON-RPC requests line by line on stdin, respond on stdout.
    Standard MCP stdio transport."""
    _pin_stdio_utf8()
    server = McpServer(data_dir)
    sys.stderr.write("[chaincoxswain-mcp] stdio server ready, %d tools\n" % len(server.tools))
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = server.handle(request)
        if response is None:
            continue  # notification -> no reply on the wire
        sys.stdout.write(json.dumps(_json_safe(response), ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    data_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data")
    run_stdio(data_dir)
