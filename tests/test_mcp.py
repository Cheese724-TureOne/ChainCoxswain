# -*- coding: utf-8 -*-
"""ChainCoxswain MCP Server 测试：JSON-RPC 2.0 handler + stdio 子进程往返。

真实实现验证（评审可复现）：
  - initialize 握手 / tools/list 恰好 13 个工具（7 经典 + 6 升级算法） / tools/call 返回真实引擎结果；
  - 未知工具与缺失参数返回 isError，业务动作工具（下单等）不存在；
  - stdio 传输层：真实子进程逐行 JSON-RPC 往返。
"""
import json
import os
import subprocess
import sys
import time

import pytest

from conftest import PROJECT_ROOT  # noqa: F401
from mcp_server import PROTOCOL_VERSION, SERVER_NAME, McpServer

EXPECTED_TOOLS = [
    "get_inventory_risk_list",
    "query_material",
    "run_autonomous_stress_test",
    "query_risk_threshold",
    "compare_interventions",
    "query_stockout_probability",
    "query_material_policy",
    "query_recovery_plan",
    "query_joint_threshold",
    "get_risk_dimensions",
    "get_warehouse_risk",
    "get_agent_tool_logs",
    "export_analysis_report",
]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    s = McpServer(PROJECT_ROOT)
    # 测试日志写临时目录，不污染产品 logs/
    s.service.log_path = str(tmp_path_factory.mktemp("mcp_logs") / "tool_logs.json")
    return s


def _call(server, req_id, name, arguments):
    return server.handle({"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}})


# ------------------------------------------------------------------ handler
def test_initialize_handshake(server):
    r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r["result"]["protocolVersion"] == PROTOCOL_VERSION == "2024-11-05"
    assert r["result"]["serverInfo"]["name"] == SERVER_NAME == "chaincoxswain-mcp"
    assert "tools" in r["result"]["capabilities"]


def test_tools_list_exposes_thirteen_tools(server):
    r = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = [t["name"] for t in r["result"]["tools"]]
    assert names == EXPECTED_TOOLS, "必须恰好暴露 13 个分析类工具（7 经典 + 6 升级算法）"
    assert all(t.get("inputSchema") for t in r["result"]["tools"])


def test_call_risk_list_real_computation(server):
    r = _call(server, 3, "get_inventory_risk_list", {"limit": 5})
    assert r["result"]["isError"] is False
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["counts"]["critical"] >= 1  # 演示数据 M-004 基线缺货
    assert len(payload["top"]) <= 5
    assert payload["top"][0]["sku"]


def test_call_threshold_classic_sample(server):
    """经典样例：M-001 到货延期 3 天 -> 临界点 3。"""
    r = _call(server, 4, "query_risk_threshold",
              {"sku": "M-001", "scenario_type": "deliveryDelay", "max_value": 30})
    assert r["result"]["isError"] is False
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["status"] == "found"
    assert payload["threshold"] == 3
    assert payload["rounds"] >= 3  # 倍增 + 二分逐轮日志


def test_call_autonomous_stress_test(server):
    r = _call(server, 5, "run_autonomous_stress_test", {"limit_skus": 3, "horizon": 14})
    assert r["result"]["isError"] is False
    payload = json.loads(r["result"]["content"][0]["text"])
    assert 1 <= len(payload["tested"]) <= 3
    assert payload["totalRounds"] >= len(payload["tested"])
    for t in payload["tested"]:
        assert {"deliveryDelay", "purchaseDelay", "demandIncrease"} <= set(t["thresholds"])


def test_call_unknown_tool_is_error(server):
    r = _call(server, 6, "place_order", {})
    assert r["result"]["isError"] is True
    payload = json.loads(r["result"]["content"][0]["text"])
    assert "未知工具" in payload.get("error", "")
    # 业务动作（下单/通知供应商/调库存）首版不暴露
    exposed = [t["name"] for t in server.tools]
    assert not any(n in exposed for n in ("place_order", "notify_supplier", "adjust_stock"))


def test_call_missing_required_arg_is_error(server):
    r = _call(server, 7, "query_material", {})
    assert r["result"]["isError"] is True
    payload = json.loads(r["result"]["content"][0]["text"])
    assert "工具调用失败" in payload.get("error", "")


def test_agent_tool_logs_recorded(server):
    _call(server, 8, "get_inventory_risk_list", {"limit": 3})
    r = _call(server, 9, "get_agent_tool_logs", {"limit": 10})
    assert r["result"]["isError"] is False
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["total"] >= 1
    # 服务内部记录的工具名为 get_risk_list（MCP 暴露名为 get_inventory_risk_list）
    assert any(rec["caller"] == "mcp" and rec["tool"] == "get_risk_list"
               for rec in payload["records"]), "工具调用必须留痕可审计"


def test_invalid_jsonrpc_version(server):
    r = server.handle({"jsonrpc": "1.0", "id": 10, "method": "ping", "params": {}})
    assert r["error"]["code"] == -32600


def test_unknown_method(server):
    r = server.handle({"jsonrpc": "2.0", "id": 11, "method": "no/such", "params": {}})
    assert r["error"]["code"] == -32601


# ------------------------------------------------------------------ stdio
def test_stdio_subprocess_roundtrip(tmp_path):
    """真实 stdio 传输：子进程逐行 JSON-RPC 往返（千问办公接入形态）。"""
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, os.path.join(PROJECT_ROOT, "backend", "mcp_server.py"), data_dir],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=os.path.join(PROJECT_ROOT, "backend"), encoding="utf-8")
    try:
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "get_inventory_risk_list", "arguments": {"limit": 3}}},
        ]
        for rq in reqs:
            proc.stdin.write(json.dumps(rq) + "\n")
        proc.stdin.flush()
        lines, deadline = [], time.time() + 60
        while len(lines) < len(reqs) and time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(json.loads(line))
        assert len(lines) == 3, "stdio 应逐行返回 3 条 JSON-RPC 响应"
        assert lines[0]["result"]["serverInfo"]["name"] == "chaincoxswain-mcp"
        assert lines[0]["result"]["protocolVersion"] == "2024-11-05"
        assert len(lines[1]["result"]["tools"]) == 13
        payload = json.loads(lines[2]["result"]["content"][0]["text"])
        assert "counts" in payload and payload["total"] >= 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
