# -*- coding: utf-8 -*-
"""Cross-check engine.py against engine.js outputs (produced by node).
Usage: node tests/crosscheck_battery.js > /tmp/js_battery.json, then run this file."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import engine as E  # noqa: E402

BATTERY = os.environ.get("JS_BATTERY", "/tmp/js_battery.json")


def _norm(v):
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, float):
        return round(v, 6)
    return v


def main():
    with open(BATTERY, encoding="utf-8") as f:
        js = json.load(f)
    start = "2026-10-26"
    materials, orders = E.demo_data(start)["materials"], E.demo_data(start)["orders"]
    m1 = materials[0]
    py = {
        "base14": E.simulate(m1, orders, {"today": start, "horizon": 14}),
        "delay2": E.simulate(m1, orders, {"today": start, "horizon": 14, "scenario": {"type": "deliveryDelay", "value": 2}}),
        "delay3": E.simulate(m1, orders, {"today": start, "horizon": 14, "scenario": {"type": "deliveryDelay", "value": 3}}),
        "demand30": E.simulate(m1, orders, {"today": start, "horizon": 14, "scenario": {"type": "demandIncrease", "value": 30}}),
        "thrDelay": E.find_threshold(m1, orders, {"today": start, "horizon": 14, "type": "deliveryDelay", "maxValue": 30}),
        "thrDemand": E.find_threshold(m1, orders, {"today": start, "horizon": 14, "type": "demandIncrease", "maxValue": 200}),
        "thrPurch": E.find_threshold(m1, orders, {"today": start, "horizon": 14, "type": "purchaseDelay", "maxValue": 30}),
        "risks": [{"sku": r["material"]["sku"], "level": r["level"], "coverage": r["coverage"],
                   "firstShortage": r["base"]["firstShortage"], "totalUnmet": r["base"]["totalUnmet"],
                   "replenishmentPoint": r["replenishmentPoint"], "reason": r["reason"],
                   "firstPO": r["firstPO"], "daysUntilPO": r["daysUntilPO"]}
                  for r in E.rank_risks(materials, orders, 14, start)],
        "scanTrimmed": None,
        "mitigateDelay3": [{"id": r["id"], "improvement": r["improvement"], "resolves": r["resolvesShortage"],
                            "totalUnmet": r["simulation"]["totalUnmet"], "firstShortage": r["simulation"]["firstShortage"],
                            "dataRequired": r.get("dataRequired")}
                           for r in E.mitigate(m1, orders, {"type": "deliveryDelay", "value": 3}, {"today": start, "horizon": 14})],
        "mitigateOutage": [{"id": r["id"], "improvement": r["improvement"], "resolves": r["resolvesShortage"],
                            "dataRequired": r.get("dataRequired")}
                           for r in E.mitigate(materials[1], orders, {"type": "supplierOutage", "supplier": "科联半导体"},
                                               {"today": start, "horizon": 14})],
        "overdue": E.simulate({"sku": "X", "stock": 0, "usage": 1, "safety": 0, "leadTime": 3, "supplier": "S"},
                              [{"id": "PO", "sku": "X", "arrival": "2026-10-24", "quantity": 99, "supplier": "S", "status": "已下单"}],
                              {"today": start, "horizon": 2}),
        "backtest": E.backtest_run(m1, orders, [9, 11, 10, 8, 12, 10, 10, 9, 11, 10, 10, 10, 9, 10], {"today": start}),
        "dates": {"add1": E.add_days("2026-12-31", 1), "addN1": E.add_days("2026-01-01", -1), "dayNum": E.day_number("2026-10-26")},
    }
    mix_orders = [
        {"id": "A", "sku": "M-001", "quantity": 50, "arrival": "2026-10-29", "supplier": "s", "status": "已取消"},
        {"id": "B", "sku": "M-001", "quantity": 50, "arrival": "2026-10-30", "supplier": "s", "status": "已收货"},
        {"id": "C", "sku": "M-001", "quantity": 50, "receivedQty": 20, "arrival": "2026-10-31", "supplier": "s", "status": "部分收货"},
        {"id": "D", "sku": "M-001", "quantity": 50, "arrival": "2026-11-01", "supplier": "s", "status": "计划"}]
    py["overrideEmpty"] = E.simulate(m1, orders, {"today": start, "horizon": 14, "overrideOrders": []})
    py["statusMix"] = E.simulate(m1, mix_orders, {"today": start, "horizon": 14})
    py["statusMixPlanned"] = E.simulate(m1, mix_orders, {"today": start, "horizon": 14,
                                                         "scenario": {"type": "purchaseDelay", "value": 1}})
    py["seriesGrowth"] = E.simulate(m1, orders, {"today": start, "horizon": 5,
                                                 "scenario": {"type": "demandIncrease", "value": 50},
                                                 "usageSeries": [10, 10, 10, 10, 10]})
    py["joint"] = E.simulate(m1, orders, {"today": start, "horizon": 14,
                                          "scenario": {"type": "joint", "demandPct": 20, "delayDays": 2}})
    py["thrJoint"] = E.find_joint_threshold(m1, orders, {"today": start, "horizon": 14,
                                                         "demandPct": 20, "maxDelay": 10})
    scan = E.autonomous_scan(materials, orders, {"today": start, "horizon": 14})
    py["scanTrimmed"] = {
        "totalRounds": scan["totalRounds"],
        "tested": [{"sku": t["risk"]["material"]["sku"], "rounds": t["rounds"], "focus": t["focusScenario"],
                    "thresholds": {k: {"status": v["status"], "threshold": v["threshold"], "rounds": v["rounds"]}
                                   for k, v in t["thresholds"].items()}} for t in scan["tested"]],
        "log": scan["log"],
    }
    failures = []
    for key in js:
        if key in ("ordersAfter",):
            continue
        if key not in py:
            failures.append("missing python key %s" % key)
            continue
        if _norm(js[key]) != _norm(py[key]):
            failures.append("MISMATCH %s\n  js=%s\n  py=%s" % (key, json.dumps(_norm(js[key]), ensure_ascii=False)[:500],
                                                               json.dumps(_norm(py[key]), ensure_ascii=False)[:500]))
    # immutability check
    if orders[0]["arrival"] != E.add_days(start, 6):
        failures.append("python orders were mutated")
    if failures:
        print("CROSS-CHECK FAILED")
        for f in failures:
            print(f)
        sys.exit(1)
    print("CROSS-CHECK OK: engine.py matches engine.js on %d keys" % len(js))


if __name__ == "__main__":
    main()
