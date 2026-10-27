# -*- coding: utf-8 -*-
"""ChainCoxswain backend server (Flask, single process).

Serves the static frontend and exposes:
  /api/health, /api/qwen/status, /api/qwen/agent/run, /api/data-a/*,
  /api/compare, /api/report/generate, /api/export/<file>, /api/task/package,
  /api/mcp (HTTP JSON-RPC bridge for the MCP server) and direct tool endpoints.

The API key lives ONLY in the environment variable DASHSCOPE_API_KEY and is
never forwarded to the browser. The frontend keeps working offline (file://);
the backend is optional for pure demo-data usage.
"""
from __future__ import annotations

import json
import os
import sys

from flask import Flask, jsonify, request, send_from_directory, send_file

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from service import ChainCoxswainService  # noqa: E402
from mcp_server import McpServer  # noqa: E402
from qwen_agent import status as qwen_status  # noqa: E402
from compare import run_comparison  # noqa: E402

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
SERVICE = ChainCoxswainService(data_dir=os.path.join(BASE_DIR, "data"),
                            log_path=os.path.join(BASE_DIR, "logs", "tool_logs.json"))
# MCP 与 REST 共享同一内存服务：前端加载 A.zip/关系型数据集后，MCP 工具读到同一份数据
MCP = McpServer(os.path.join(BASE_DIR, "data"), service=SERVICE)
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

AGENT_GOAL_DEFAULT = ("分析当前库存风险，找出最脆弱的物料，自动运行压力测试，"
                      "推荐应对方案。先用工具获取风险清单，再对重点物料搜索临界点并比较干预方案；"
                      "所有数字必须来自工具结果。")


def _sanitize(obj):
    """NaN/Infinity are invalid JSON tokens; convert them to None so browsers
    can always parse responses (resp.json() uses strict JSON.parse)."""
    import math
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def _json(data, code=200):
    resp = jsonify(_sanitize(data))
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp, code


@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/health")
def health():
    return _json({"ok": True, "service": "ChainCoxswain", "version": "2.0.0",
                  "qwen": qwen_status(), "dataset": SERVICE.meta(),
                  "mcpTools": [t["name"] for t in MCP.tools]})


@app.route("/api/qwen/status")
def qwen_status_api():
    return _json(qwen_status())


@app.route("/api/qwen/agent/run", methods=["POST"])
def agent_run():
    body = request.get_json(silent=True) or {}
    goal = body.get("goal") or AGENT_GOAL_DEFAULT
    params = body.get("params") or {}
    for k in ("maxRounds", "maxToolCalls", "budgetTokens"):
        if k in params:
            params[k] = int(params[k])
    if "timeoutSec" in params:
        params["timeoutSec"] = float(params["timeoutSec"])
    try:
        result = SERVICE.run_agent(goal, params)
        return _json({"ok": True, "agent": result})
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": str(e)}, 400)


@app.route("/api/data-a/info")
def data_a_info():
    import data_a as da_mod
    info = {"available": os.path.exists(os.path.join(BASE_DIR, "data", "A.zip")),
            "snapshotDate": da_mod.SNAPSHOT_DATE,
            "description": "A.zip 真实历史数据：632 unit（19 产品 × 81 地理），含库存快照、历史消耗与留出测试数据；不含采购订单/供应商/到货日期"}
    if SERVICE.dataset_meta and SERVICE.data_kind == "real-historical":
        info["loaded"] = True
        info["meta"] = SERVICE.meta()
    return _json(info)


@app.route("/api/data-a/load", methods=["POST"])
def data_a_load():
    body = request.get_json(silent=True) or {}
    try:
        meta = SERVICE.load_a_zip(simulate_pos=bool(body.get("simulatePos")))
        return _json({"ok": True, "meta": meta})
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": str(e)}, 400)


@app.route("/api/data-a/backtest")
def data_a_backtest():
    try:
        import data_a as da_mod
        adapter = da_mod.DataAAdapter(os.path.join(BASE_DIR, "data", "A.zip"))
        limit = min(int(request.args.get("limit", 50)), 632)
        return _json({"ok": True, "backtest": adapter.backtest(unit_limit=limit)})
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": str(e)}, 400)


@app.route("/api/data-a/dataset")
def data_a_dataset():
    """Full A.zip-derived dataset for the browser engine (632 units)."""
    try:
        meta = SERVICE.load_a_zip()
        return _json({"ok": True, "meta": meta, "materials": SERVICE.materials,
                      "orders": SERVICE.orders, "today": SERVICE._today()})
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": str(e)}, 400)


# ------------------------------------------------ relational synthetic dataset
@app.route("/api/data-rel/info")
def data_rel_info():
    from data_rel import REL_DEFAULT_DIR, TODAY, USAGE_WINDOW_DAYS
    import os as _os
    return _json({"available": _os.path.isdir(REL_DEFAULT_DIR),
                  "snapshotDate": TODAY,
                  "usageWindowDays": USAGE_WINDOW_DAYS,
                  "description": "inventory-supply-chain v1.0.0：合成教学数据集（2500 产品 / 250 供应商 / 12 仓 / 1.8 万采购单 / 13.8 万流水），8 张规范化表经确定性 join+聚合适配为 2500 物料 + 在途采购单"})


@app.route("/api/data-rel/load", methods=["POST"])
def data_rel_load():
    body = request.get_json(silent=True) or {}
    try:
        meta = SERVICE.load_rel(path=body.get("path"))
        return _json({"ok": True, "meta": meta})
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": str(e)}, 400)


@app.route("/api/data-rel/dataset")
def data_rel_dataset():
    try:
        meta = SERVICE.load_rel()
        return _json({"ok": True, "meta": meta, "materials": SERVICE.materials,
                      "orders": SERVICE.orders, "today": SERVICE._today()})
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": str(e)}, 400)


# ------------------------------------------------------ censored demand (retail)
@app.route("/api/censored/analysis")
def censored_analysis():
    try:
        import censored
        return _json({"ok": True, "analysis": censored.analyze()})
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": str(e)}, 500)


@app.route("/api/dataset/demo", methods=["POST"])
def dataset_demo():
    return _json({"ok": True, "meta": SERVICE.load_demo()})


@app.route("/api/dataset/sync", methods=["POST"])
def dataset_sync():
    """Receive data from frontend so MCP tools use the same dataset."""
    body = request.get_json(silent=True) or {}
    materials = body.get("materials", [])
    orders = body.get("orders", [])
    if not isinstance(materials, list) or not isinstance(orders, list):
        return _json({"ok": False, "error": "materials 和 orders 必须是数组"}, 400)
    if not materials:
        return _json({"ok": False, "error": "materials 不能为空"}, 400)
    try:
        meta = SERVICE.load_from_frontend(
            materials=materials,
            orders=orders,
            source=body.get("source", "前端同步数据"),
            data_kind=body.get("dataKind", "user"),
            today=body.get("today")
        )
        return _json({"ok": True, "meta": meta, "message": f"已同步 {len(materials)} 种物料、{len(orders)} 条采购单到后端"})
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": str(e)}, 500)


@app.route("/api/compare", methods=["POST"])
def compare():
    body = request.get_json(silent=True) or {}
    try:
        result = run_comparison(SERVICE, top_n=int(body.get("topN", 6)),
                                horizon=int(body.get("horizon", 14)),
                                agent_params=body.get("agentParams"))
        return _json({"ok": True, "comparison": result})
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": str(e)}, 400)


@app.route("/api/report/generate", methods=["POST"])
def report_generate():
    body = request.get_json(silent=True) or {}
    fmt = body.get("format") or "xlsx"
    if fmt not in ("xlsx", "docx", "pptx", "html"):
        return _json({"ok": False, "error": "不支持的格式: %s" % fmt}, 400)
    try:
        info = SERVICE.export_report(fmt=fmt)
        return _json({"ok": True, "report": info,
                      "downloadUrl": "/api/export/%s" % info["filename"]})
    except Exception as e:  # noqa: BLE001
        return _json({"ok": False, "error": str(e)}, 500)


@app.route("/api/export/<path:filename>")
def export_download(filename):
    from report_gen import OUT_DIR
    safe = os.path.basename(filename)
    return send_file(os.path.join(OUT_DIR, safe), as_attachment=True)


@app.route("/api/tools/logs")
def tools_logs():
    return _json(SERVICE.tool_history(limit=int(request.args.get("limit", 50))))


@app.route("/api/task/package")
def task_package():
    return _json(SERVICE.task_package())


# ------------------------------------------------- 升级算法端点（P1/P2）
@app.route("/api/policy", methods=["POST"])
def api_policy():
    b = request.get_json(silent=True) or {}
    return _json(SERVICE.tool_material_policy(str(b.get("sku", "")),
                                               service_level=float(b.get("serviceLevel", 0.95)),
                                               caller="api"))


@app.route("/api/probability", methods=["POST"])
def api_probability():
    b = request.get_json(silent=True) or {}
    return _json(SERVICE.tool_stockout_probability(str(b.get("sku", "")),
                                                   n=int(b.get("n", 1000)),
                                                   seed=int(b.get("seed", 42)),
                                                   horizons=b.get("horizons") or (7, 14, 30),
                                                   caller="api"))


@app.route("/api/recovery", methods=["POST"])
def api_recovery():
    b = request.get_json(silent=True) or {}
    return _json(SERVICE.tool_recovery_plan(str(b.get("sku", "")),
                                            scenario_type=str(b.get("scenarioType", "deliveryDelay")),
                                            value=int(b.get("value", 3)),
                                            horizon=int(b.get("horizon", 14)), caller="api"))


@app.route("/api/joint-threshold", methods=["POST"])
def api_joint_threshold():
    b = request.get_json(silent=True) or {}
    return _json(SERVICE.tool_joint_threshold(str(b.get("sku", "")),
                                              demand_pct=float(b.get("demandPct", 20)),
                                              max_delay=int(b.get("maxDelay", 10)),
                                              horizon=int(b.get("horizon", 14)), caller="api"))


@app.route("/api/dimensions", methods=["POST"])
def api_dimensions():
    b = request.get_json(silent=True) or {}
    return _json(SERVICE.tool_risk_dimensions(str(b.get("sku", "")), caller="api"))


@app.route("/api/warehouse-risk", methods=["POST"])
def api_warehouse_risk():
    b = request.get_json(silent=True) or {}
    return _json(SERVICE.tool_warehouse_risk(b.get("sku") or None,
                                             transship=b.get("transship"), caller="api"))


# ------------------------------------------------------------ MCP HTTP bridge
@app.route("/api/mcp", methods=["POST", "OPTIONS"])
def mcp_bridge():
    if request.method == "OPTIONS":
        return _json({})
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json({"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32700, "message": "需要 JSON-RPC 请求体"}}, 400)
    response = MCP.handle(body)
    if response is None:
        # JSON-RPC notification: nothing may be answered, only acknowledge the POST.
        return ("", 204)
    return _json(response)


@app.route("/api/mcp/tool/<name>", methods=["POST"])
def mcp_tool_direct(name):
    args = request.get_json(silent=True) or {}
    if name not in [t["name"] for t in MCP.tools]:
        return _json({"ok": False, "error": "未知工具"}, 404)
    result = MCP._call_tool(name, args)
    return _json({"ok": result.get("error") is None, "result": result})


if __name__ == "__main__":
    port = int(os.environ.get("CHAINCOXSWAIN_PORT", "8765"))
    print("ChainCoxswain backend on http://127.0.0.1:%d  (Ctrl+C 停止)" % port)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
