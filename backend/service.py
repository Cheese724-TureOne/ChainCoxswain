# -*- coding: utf-8 -*-
"""ChainCoxswainService: shared business service behind the Qwen agent, the MCP
server and the REST API. Every numeric result flows through backend/engine.py
(the deterministic simulator); the LLM never computes inventory numbers.

All tool invocations are recorded in `tool_logs` for auditability, and every
record carries a source tag: demo / real / real-historical / simulated.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    from . import engine as E
    from . import inventory_policy as POL
    from . import demand_forecast as FC
    from . import risk_probability as RP
    from . import risk_dimensions as RD
    from . import intervention as IV
    from .data_a import DataAAdapter, SNAPSHOT_DATE
except ImportError:  # pragma: no cover
    import engine as E  # type: ignore
    import inventory_policy as POL  # type: ignore
    import demand_forecast as FC  # type: ignore
    import risk_probability as RP  # type: ignore
    import risk_dimensions as RD  # type: ignore
    import intervention as IV  # type: ignore
    from data_a import DataAAdapter, SNAPSHOT_DATE  # type: ignore

SCENARIO_LABELS = {
    "deliveryDelay": "采购到货延迟",
    "purchaseDelay": "补货下单推迟",
    "demandIncrease": "需求突然上涨",
    "supplierOutage": "供应商断供",
}


class ChainCoxswainService:
    """In-memory service state. Thread-safe for the single-process Flask/MCP use."""

    def __init__(self, data_dir: str, log_path: Optional[str] = None):
        self.data_dir = data_dir
        self.log_path = log_path
        self.lock = threading.RLock()
        self.materials: List[Dict] = []
        self.orders: List[Dict] = []
        self.source = "演示数据"
        self.data_kind = "demo"
        self.today: Optional[str] = None  # None -> engine default (real today)
        self.dataset_meta: Dict[str, Any] = {}
        self.tool_logs: List[Dict] = []
        self.agent_runs: List[Dict] = []
        # 升级算法所需的历史证据（仅当数据集提供时填充；缺失时算法显式回退）
        self.demand_history: Dict[str, Dict[str, float]] = {}   # sku -> {date: qty}
        self.lead_samples: Dict[str, List[float]] = {}          # supplier -> 实际交期天数
        self.supplier_history: List[Dict] = []                  # 已完成采购行（履约统计）
        self.warehouse_units: List[Dict] = []                   # SKU+warehouse 视图
        self.data_version = 0                                   # 每次载入递增，保证全端同版本
        self._prob_cache: Dict[str, Dict] = {}
        self._load_persisted_logs()
        self.load_demo()

    # ------------------------------------------------------------------ state
    def _load_persisted_logs(self):
        if self.log_path and os.path.exists(self.log_path):
            try:
                with open(self.log_path, encoding="utf-8") as f:
                    data = json.load(f)
                self.tool_logs = data.get("tool_logs", [])
                self.agent_runs = data.get("agent_runs", [])
            except Exception:
                pass

    def _persist(self):
        if not self.log_path:
            return
        try:
            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump({"tool_logs": self.tool_logs[-2000:], "agent_runs": self.agent_runs[-200:]},
                          f, ensure_ascii=False, default=str)
        except Exception:
            pass

    def _log(self, tool: str, args: Dict, result: Any, ok: bool = True, error: str = "",
             caller: str = "unknown", t0: Optional[float] = None) -> Dict:
        rec = {
            "id": uuid.uuid4().hex[:10],
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tool": tool,
            "caller": caller,          # agent / mcp / api / rule-engine
            "args": args,
            "ok": ok,
            "error": error,
            "durationMs": int((time.time() - t0) * 1000) if t0 else None,
            "summary": _summarize(tool, result) if ok else error,
        }
        with self.lock:
            self.tool_logs.append(rec)
            self._persist()
        return rec

    # ------------------------------------------------------------------ data
    def _reset_history(self):
        self.demand_history = {}
        self.lead_samples = {}
        self.supplier_history = []
        self.warehouse_units = []
        self._prob_cache = {}
        self.data_version += 1

    def load_demo(self):
        with self.lock:
            d = E.demo_data()
            self.materials, self.orders = d["materials"], d["orders"]
            self.source, self.data_kind = "演示数据（虚构物料）", "demo"
            self.today = None
            self.dataset_meta = {"flags": ["演示数据为虚构物料与订单，仅用于功能演示"],
                                 "missingFields": {}}
            self._reset_history()
        return self.meta()

    def load_from_frontend(self, materials: List[Dict], orders: List[Dict],
                           source: str = "前端同步数据", data_kind: str = "user",
                           today: Optional[str] = None) -> Dict:
        """Accept data pushed from the frontend so MCP tools use the same dataset."""
        with self.lock:
            self.materials = [dict(m) for m in materials]
            self.orders = [dict(o) for o in orders]
            self.source = source
            self.data_kind = data_kind
            self.today = today
            self.dataset_meta = {
                "flags": ["数据由前端同步，与浏览器当前导入一致"],
                "missingFields": {},
                "syncedFromFrontend": True,
                "materialCount": len(self.materials),
                "orderCount": len(self.orders)
            }
            self._reset_history()
        return self.meta()

    def load_a_zip(self, simulate_pos: bool = False) -> Dict:
        zip_path = os.path.join(self.data_dir, "A.zip")
        if not os.path.exists(zip_path):
            raise FileNotFoundError("服务器 data/ 目录缺少 A.zip，请将比赛数据包放入后重试")
        adapter = DataAAdapter(zip_path)
        ds = adapter.load(simulate_pos=simulate_pos)
        with self.lock:
            self.materials = ds["materials"]
            self.orders = ds["orders"]
            self.source = "A.zip 历史数据（快照 %s）" % SNAPSHOT_DATE
            self.data_kind = "real-historical"
            self.today = SNAPSHOT_DATE
            self.dataset_meta = {
                "flags": ds["flags"],
                "missingFields": ds["missingFields"],
                "snapshotDate": ds["snapshotDate"],
                "trainRange": ds["trainRange"],
                "testRange": ds["testRange"],
                "units": ds["units"],
                "riskSummary": ds["riskSummary"],
                "ordersSimulated": ds["ordersSimulated"],
            }
            self._reset_history()
        return self.meta()

    def load_rel(self, path: Optional[str] = None) -> Dict:
        """关系型数据集（inventory-supply-chain v1.0.0，合成教学数据）确定性适配加载。"""
        from data_rel import TODAY, RelationalAdapter
        adapter = RelationalAdapter(path)
        ds = adapter.load()
        with self.lock:
            self.materials = ds["materials"]
            self.orders = ds["orders"]
            self.source = "关系型数据集 inventory-supply-chain v1.0.0（合成教学数据 · 快照 %s）" % TODAY
            self.data_kind = "demo"  # synthetic 教学数据，绝不标注为真实历史数据
            self.today = TODAY
            self.dataset_meta = {
                "flags": ds["flags"],
                "missingFields": ds["missingFields"],
                "snapshotDate": TODAY,
                "units": ds["units"],
                "usageWindow": ds["usageWindow"],
                "poStats": ds["poStats"],
                "ordersSimulated": False,
            }
            self._reset_history()
            self.demand_history = ds.get("demandHistory", {}) or {}
            self.lead_samples = ds.get("leadTimeSamples", {}) or {}
            self.supplier_history = ds.get("supplierHistory", []) or []
            self.warehouse_units = ds.get("warehouseUnits", []) or []
        return self.meta()

    def meta(self) -> Dict:
        with self.lock:
            return {
                "source": self.source,
                "dataKind": self.data_kind,
                "materials": len(self.materials),
                "orders": len(self.orders),
                "today": self.today or E.to_day_string(),
                "datasetMeta": self.dataset_meta,
                "dataVersion": self.data_version,
                "toolLogCount": len(self.tool_logs),
                "agentRunCount": len(self.agent_runs),
            }

    def _today(self) -> str:
        return self.today or E.to_day_string()

    def _mat(self, sku: str) -> Dict:
        for m in self.materials:
            if m["sku"] == sku:
                return m
        raise ValueError("找不到物料编码 %s" % sku)

    # ------------------------------------------------------------------ tools
    def tool_query_material(self, sku: str, caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            m = self._mat(sku)
            risk = E.baseline_risk(m, self.orders, 14, self._today())
            out = {
                "sku": m["sku"], "name": m.get("name"), "stock": m.get("stock"),
                "dailyUsage": m.get("usage"), "safety": m.get("safety"),
                "leadTimeDays": m.get("leadTime"), "supplier": m.get("supplier") or "未提供",
                "coverageDays": risk["coverage"],
                "baselineLevel": risk["level"],
                "baselineReason": risk["reason"],
                "nextExpectedArrival": (risk["firstPO"] or {}).get("arrival") if risk["firstPO"] else "无采购记录",
                "replenishmentPoint": risk["replenishmentPoint"],
                "policy": POL.policy_for_material(m, self.orders, self._today(),
                                                  demand_series=self._series_for(sku),
                                                  lead_times=self._leads_for(m)),
                "flags": risk.get("flags", []),
                "dataVersion": self.data_version,
                "dataKind": self.data_kind,
                "sourceNote": "库存与用量来自 %s；数量均由确定性引擎计算" % self.source,
            }
            self._log("query_material", {"sku": sku}, out, t0=t0, caller=caller)
            return out
        except Exception as e:
            self._log("query_material", {"sku": sku}, None, ok=False, error=str(e), t0=t0, caller=caller)
            raise

    # ------------------------------------------------- 升级算法（P1/P2）工具
    def _series_for(self, sku: str) -> List[float]:
        """按日期升序返回该 SKU 的历史逐日需求；无历史时返回空列表（算法自行回退）。"""
        hist = self.demand_history.get(sku) or {}
        if not hist:
            return []
        return [hist[k] for k in sorted(hist)]

    def _leads_for(self, m: Dict) -> List[float]:
        sup = m.get("supplier")
        return list(self.lead_samples.get(sup) or []) if sup else []

    def _delay_samples(self) -> List[float]:
        """历史到货偏移（实际收货日 - 计划到货日），来自快照日前已完成的采购。"""
        out = []
        for h in self.supplier_history:
            if h.get("received_at") and h.get("expected_at"):
                out.append(float(E.day_number(h["received_at"]) - E.day_number(h["expected_at"])))
        return out

    def tool_material_policy(self, sku: str, service_level: float = 0.95, caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            m = self._mat(sku)
            out = POL.policy_for_material(m, self.orders, self._today(),
                                           service_level=service_level,
                                           demand_series=self._series_for(sku),
                                           lead_times=self._leads_for(m))
            out["sku"] = sku
            out["dataVersion"] = self.data_version
            out["historyDays"] = len(self._series_for(sku))
            self._log("material_policy", {"sku": sku, "serviceLevel": service_level}, out, t0=t0, caller=caller)
            return out
        except Exception as e:
            self._log("material_policy", {"sku": sku}, None, ok=False, error=str(e), t0=t0, caller=caller)
            raise

    def tool_stockout_probability(self, sku: str, n: int = 1000, seed: int = 42,
                                  horizons=(7, 14, 30), caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            m = self._mat(sku)
            key = (sku, int(n), int(seed), tuple(horizons), self.data_version)
            with self.lock:
                cached = self._prob_cache.get(key)
            if cached is None:
                cached = RP.monte_carlo(m, self.orders, {
                    "today": self._today(), "n": int(n), "seed": int(seed),
                    "horizons": list(horizons),
                    "demandSeries": self._series_for(sku),
                    "delaySamples": self._delay_samples(),
                })
                cached["dataVersion"] = self.data_version
                with self.lock:
                    self._prob_cache[key] = cached
            self._log("stockout_probability", {"sku": sku, "n": n, "seed": seed},
                      {k: cached[k] for k in ("probabilities", "fallback") if k in cached}, t0=t0, caller=caller)
            return cached
        except Exception as e:
            self._log("stockout_probability", {"sku": sku}, None, ok=False, error=str(e), t0=t0, caller=caller)
            raise

    def tool_recovery_plan(self, sku: str, scenario_type: str = "deliveryDelay", value: int = 3,
                           horizon: int = 14, caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            m = self._mat(sku)
            out = IV.min_recovery_plan(m, self.orders, {
                "today": self._today(), "horizon": horizon,
                "scenario": {"type": scenario_type, "value": value}})
            out["sku"] = sku
            out["dataVersion"] = self.data_version
            self._log("recovery_plan", {"sku": sku, "scenario": scenario_type, "value": value},
                      out, t0=t0, caller=caller)
            return out
        except Exception as e:
            self._log("recovery_plan", {"sku": sku}, None, ok=False, error=str(e), t0=t0, caller=caller)
            raise

    def tool_joint_threshold(self, sku: str, demand_pct: float = 20.0, max_delay: int = 10,
                             horizon: int = 14, caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            m = self._mat(sku)
            out = E.find_joint_threshold(m, self.orders, {
                "today": self._today(), "horizon": horizon,
                "demandPct": demand_pct, "maxDelay": max_delay})
            out["sku"] = sku
            out["dataVersion"] = self.data_version
            self._log("joint_threshold", {"sku": sku, "demandPct": demand_pct, "maxDelay": max_delay},
                      out, t0=t0, caller=caller)
            return out
        except Exception as e:
            self._log("joint_threshold", {"sku": sku}, None, ok=False, error=str(e), t0=t0, caller=caller)
            raise

    def tool_risk_dimensions(self, sku: str, caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            m = self._mat(sku)
            risk = E.baseline_risk(m, self.orders, 14, self._today())
            series = self._series_for(sku)
            policy = POL.policy_for_material(m, self.orders, self._today(),
                                             demand_series=series, lead_times=self._leads_for(m))
            fc = FC.forecast(series, 14, fallback_usage=m.get("usage"))
            mc = self.tool_stockout_probability(sku, n=300, caller="internal")
            wh_disp = RD.coverage_dispersion([u for u in self.warehouse_units if u["sku"] == sku])
            dims = RD.dimension_scores(m, risk, policy, fc, mc if mc.get("available") else None,
                                       RD.supplier_fulfillment(self.supplier_history),
                                       wh_disp.get(sku))
            out = {"sku": sku, "dimensions": dims, "priority": RD.priority_tier(dims),
                   "dataVersion": self.data_version}
            self._log("risk_dimensions", {"sku": sku}, out, t0=t0, caller=caller)
            return out
        except Exception as e:
            self._log("risk_dimensions", {"sku": sku}, None, ok=False, error=str(e), t0=t0, caller=caller)
            raise

    def tool_warehouse_risk(self, sku: Optional[str] = None, transship: Optional[Dict] = None,
                            caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            units = [u for u in self.warehouse_units if (sku is None or u["sku"] == sku)]
            rows = RD.warehouse_risk(units, self.orders, self._today(), transship=transship)
            out = {"sku": sku, "units": rows,
                   "dispersion": RD.coverage_dispersion(units) if sku else {},
                   "note": "分仓视图仅在数据集提供 warehouse 明细时可用；无调拨约束时不把他仓库存计为可用",
                   "dataVersion": self.data_version}
            self._log("warehouse_risk", {"sku": sku}, {"rows": len(rows)}, t0=t0, caller=caller)
            return out
        except Exception as e:
            self._log("warehouse_risk", {"sku": sku}, None, ok=False, error=str(e), t0=t0, caller=caller)
            raise

    def tool_risk_list(self, limit: int = 20, caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            limit = max(1, min(int(limit), 200))
            risks = E.rank_risks(self.materials, self.orders, 14, self._today())
            out = {
                "asOf": self._today(),
                "horizonDays": 14,
                "total": len(risks),
                "counts": {"critical": sum(1 for r in risks if r["level"] == "critical"),
                           "watch": sum(1 for r in risks if r["level"] == "watch"),
                           "excess": sum(1 for r in risks if r["level"] == "excess"),
                           "normal": sum(1 for r in risks if r["level"] == "normal")},
                "top": [{"sku": r["material"]["sku"], "name": r["material"].get("name"),
                         "level": r["level"], "stock": r["material"]["stock"],
                         "dailyUsage": r["material"]["usage"], "coverageDays": r["coverage"],
                         "firstShortage": r["base"]["firstShortage"],
                         "totalUnmet": r["base"]["totalUnmet"], "reason": r["reason"]}
                        for r in risks[:limit]],
            }
            self._log("get_risk_list", {"limit": limit}, out, t0=t0, caller=caller)
            return out
        except Exception as e:
            self._log("get_risk_list", {"limit": limit}, None, ok=False, error=str(e), t0=t0, caller=caller)
            raise

    def tool_run_simulation(self, sku: str, scenario_type: str = "baseline", value: int = 0,
                            horizon: int = 14, caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            m = self._mat(sku)
            scenario = {"type": scenario_type, "value": int(value)}
            base = E.simulate(m, self.orders, {"today": self._today(), "horizon": int(horizon)})
            sim = E.simulate(m, self.orders, {"today": self._today(), "horizon": int(horizon), "scenario": scenario}) \
                if scenario_type != "baseline" else base
            out = {
                "sku": sku, "today": self._today(), "horizonDays": int(horizon),
                "scenario": scenario,
                "baseline": {"firstShortage": base["firstShortage"], "totalUnmet": base["totalUnmet"],
                             "endStock": base["endStock"]},
                "result": {"firstShortage": sim["firstShortage"], "totalUnmet": sim["totalUnmet"],
                           "endStock": sim["endStock"], "expectedReceipts": sim["expectedReceipts"],
                           "points": sim["points"]},
            }
            self._log("run_simulation", {"sku": sku, "scenario_type": scenario_type, "value": value,
                                         "horizon": horizon}, out, t0=t0, caller=caller)
            return out
        except Exception as e:
            self._log("run_simulation", {"sku": sku, "scenario_type": scenario_type, "value": value},
                      None, ok=False, error=str(e), t0=t0, caller=caller)
            raise

    def tool_search_threshold(self, sku: str, scenario_type: str, max_value: Optional[int] = None,
                              horizon: int = 14, caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            m = self._mat(sku)
            if scenario_type not in ("deliveryDelay", "purchaseDelay", "demandIncrease"):
                raise ValueError("临界点搜索仅支持 deliveryDelay / purchaseDelay / demandIncrease")
            max_value = int(max_value) if max_value is not None else (200 if scenario_type == "demandIncrease" else 30)
            max_value = max(1, min(max_value, 500))
            round_log: List[Dict] = []
            res = E.find_threshold(m, self.orders, {
                "today": self._today(), "horizon": int(horizon), "type": scenario_type,
                "maxValue": max_value,
                "initial": 10 if scenario_type == "demandIncrease" else 1,
                "log": lambda l: round_log.append(l)})
            out = {
                "sku": sku, "scenarioType": scenario_type, "unit": "%" if scenario_type == "demandIncrease" else "天",
                "status": res["status"], "threshold": res["threshold"], "rounds": res["rounds"],
                "roundLog": round_log,
                "firstShortageAtThreshold": (res.get("result") or {}).get("firstShortage"),
                "totalUnmetAtThreshold": (res.get("result") or {}).get("totalUnmet"),
                "note": "阈值由倍增试探 + 整数二分确定；每轮都调用了确定性库存仿真",
            }
            self._log("search_threshold", {"sku": sku, "scenario_type": scenario_type,
                                           "max_value": max_value, "horizon": horizon}, out, t0=t0, caller=caller)
            return out
        except Exception as e:
            self._log("search_threshold", {"sku": sku, "scenario_type": scenario_type},
                      None, ok=False, error=str(e), t0=t0, caller=caller)
            raise

    def tool_compare_interventions(self, sku: str, scenario_type: str = "deliveryDelay", value: int = 3,
                                   horizon: int = 14, caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            m = self._mat(sku)
            scenario = {"type": scenario_type, "value": int(value)}
            options = E.mitigate(m, self.orders, scenario, {"today": self._today(), "horizon": int(horizon)})
            out = {
                "sku": sku, "scenario": scenario,
                "interventions": [{"id": o["id"], "title": o["title"], "action": o["action"], "note": o["note"],
                                   "improvementUnits": o["improvement"],
                                   "resolvesShortage": o["resolvesShortage"],
                                   "dataRequired": o.get("dataRequired", False),
                                   "simulatedTotalUnmet": o["simulation"]["totalUnmet"],
                                   "simulatedFirstShortage": o["simulation"]["firstShortage"]}
                                  for o in options],
                "note": "所有方案均为条件仿真；执行前必须人工确认供应商与客户可行性",
            }
            self._log("compare_interventions", {"sku": sku, "scenario_type": scenario_type, "value": value},
                      out, t0=t0, caller=caller)
            return out
        except Exception as e:
            self._log("compare_interventions", {"sku": sku, "scenario_type": scenario_type},
                      None, ok=False, error=str(e), t0=t0, caller=caller)
            raise

    def tool_autonomous_stress(self, limit_skus: int = 6, horizon: int = 14, caller: str = "api") -> Dict:
        t0 = time.time()
        try:
            scan = E.autonomous_scan(self.materials, self.orders,
                                     {"today": self._today(), "horizon": int(horizon), "maxSkus": int(limit_skus)})
            out = {
                "today": scan["today"], "horizon": scan["horizon"], "totalRounds": scan["totalRounds"],
                "log": scan["log"],
                "tested": [{
                    "sku": t["risk"]["material"]["sku"], "name": t["risk"]["material"].get("name"),
                    "level": t["risk"]["level"], "focusScenario": t["focusScenario"],
                    "thresholds": {k: {"status": v["status"], "threshold": v["threshold"], "rounds": v["rounds"]}
                                   for k, v in t["thresholds"].items()},
                    "impact": {"firstShortage": t["impact"]["firstShortage"],
                               "totalUnmet": t["impact"]["totalUnmet"]},
                } for t in scan["tested"]],
            }
            self._log("autonomous_stress_test", {"limit_skus": limit_skus, "horizon": horizon},
                      out, t0=t0, caller=caller)
            return out
        except Exception as e:
            self._log("autonomous_stress_test", {"limit_skus": limit_skus}, None, ok=False,
                      error=str(e), t0=t0, caller=caller)
            raise

    def tool_history(self, limit: int = 50, caller: str = "api") -> Dict:
        with self.lock:
            logs = self.tool_logs[-max(1, min(int(limit), 500)):]
        out = {"total": len(self.tool_logs), "records": logs}
        self._log("get_tool_history", {"limit": limit}, {"returned": len(logs)}, t0=None, caller=caller)
        return out

    def tool_explain(self, sku: str, caller: str = "api") -> Dict:
        """Deterministic, data-grounded explanation (no LLM numbers)."""
        m = self._mat(sku)
        risk = E.baseline_risk(m, self.orders, 14, self._today())
        parts = [
            "物料 %s（%s）当前快照库存 %s 件，按最近 %s 的日均用量 %s 件估算，静态覆盖约 %s 天。" % (
                m.get("name"), sku, m["stock"], "历史窗口" if self.data_kind != "demo" else "录入值",
                m["usage"], risk["coverage"] if risk["coverage"] is not None else "—"),
            "14 天基准推演：%s" % risk["reason"],
        ]
        if risk["firstPO"]:
            parts.append("下一笔预计到货：%s 到 %s 件（状态：%s）。" % (
                risk["firstPO"]["arrival"], risk["firstPO"]["quantity"], risk["firstPO"].get("status")))
        else:
            parts.append("该物料没有任何测试窗口内的采购到货记录；延期类压力测试不适用（无采购数据）。")
        out = {"sku": sku, "explanation": " ".join(parts),
               "citations": ["engine.baseline_risk(%s, today=%s, horizon=14)" % (sku, self._today())]}
        self._log("explain", {"sku": sku}, out, caller=caller)
        return out

    # ------------------------------------------------------------------ agent
    def run_agent(self, goal: str, params: Optional[Dict] = None) -> Dict:
        try:
            from . import qwen_agent  # deferred import to avoid cycles
        except ImportError:  # pragma: no cover
            import qwen_agent  # type: ignore
        return qwen_agent.run_agent(self, goal, params or {})

    def agent_status(self) -> Dict:
        try:
            from . import qwen_agent
        except ImportError:  # pragma: no cover
            import qwen_agent  # type: ignore
        return qwen_agent.status()

    # ------------------------------------------------------------------ reports
    def export_report(self, fmt: str = "xlsx") -> Dict:
        try:
            from . import report_gen
        except ImportError:  # pragma: no cover
            import report_gen  # type: ignore
        return report_gen.generate(self, fmt)

    def task_package(self) -> Dict:
        """Structured task package for 千问办公: dataset snapshot + assumptions + tool schemas."""
        return {
            "task": "分析当前库存风险，找出最脆弱的物料，自动运行压力测试，推荐应对方案，并生成供应链风险分析报告。",
            "authorization": "只读分析 + 导出报告；任何下单/通知供应商/调库存动作必须人工确认",
            "dataset": self.meta(),
            "materialsSample": self.materials[:50],
            "ordersSample": self.orders[:50],
            "assumptions": [
                "到货日按日初收货、日末核算",
                "日均用量在测试窗口内恒定（需求上涨情景按倍率放大）",
                "逾期且未确认收货的采购单不计入未来到货",
                "缺货数量由确定性引擎逐日计算，不由大模型生成",
                "缺少 BOM/生产计划时不把物料缺口直接认定为停产",
            ],
        }


def _summarize(tool: str, result: Any) -> str:
    try:
        if tool == "get_risk_list":
            return "风险清单：critical=%s watch=%s" % (result["counts"]["critical"], result["counts"]["watch"])
        if tool == "query_material":
            return "%s 库存 %s，覆盖 %s 天，等级 %s" % (result["sku"], result["stock"],
                                                        result["coverageDays"], result["baselineLevel"])
        if tool == "run_simulation":
            r = result["result"]
            return "情景 %s：首次缺货 %s，累计缺口 %s" % (result["scenario"]["type"],
                                                          r["firstShortage"] or "无", r["totalUnmet"])
        if tool == "search_threshold":
            return "临界点搜索 %s：status=%s threshold=%s rounds=%s" % (
                result["scenarioType"], result["status"], result["threshold"], result["rounds"])
        if tool == "compare_interventions":
            return "干预方案 %d 个" % len(result["interventions"])
        if tool == "autonomous_stress_test":
            return "自主压力测试：%s 个物料，%s 轮" % (len(result["tested"]), result["totalRounds"])
        if tool == "explain":
            return "解释已生成：%s" % result["explanation"][:60]
        return "OK"
    except Exception:
        return "OK"
