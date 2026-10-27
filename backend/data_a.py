# -*- coding: utf-8 -*-
"""ChainCoxswain A.zip real-data adapter.

A.zip (阿里云基础设施供应链数据) contains REAL historical business data:
  inventory_info_A.csv  库存快照（632 行，单一快照日 2021-03-01）
  use_train_A.csv       历史消耗（2018-06-04 ~ 2021-03-01，1002 天）
  use_test_A.csv        留出测试数据（2021-03-02 ~ 2021-06-07，98 天）
  product_topo_A.csv    产品层级（product_level_1 -> product_level_2）
  geo_topo_A.csv        地理层级（L1 -> L2 -> L3）
  weight_A.csv          辅助数据（unit -> 单件重量）

Granularity key is `unit` (632 units). A unit belongs to one product_level_2
and one geography_level_3.

Rules enforced here:
  * Real business dates are preserved (snapshot = 2021-03-01). This is
    HISTORICAL data and is never presented as real-time inventory.
  * A.zip contains NO purchase orders, suppliers or expected arrival dates.
    PO-dependent features are reported as missing, or use clearly flagged
    "模拟采购订单" (simulated: true) when explicitly requested.
  * Backtests never leak future data: usage estimates come only from
    use_train_A (through 2021-03-01); use_test_A is consumed only in
    replay/evaluation.
"""
from __future__ import annotations

import io
import math
import os
import zipfile
from typing import Dict, List, Optional

import pandas as pd

try:
    from . import engine as E
except ImportError:  # pragma: no cover - script execution
    import engine as E  # type: ignore

SNAPSHOT_DATE = "2021-03-01"  # the only ts in inventory_info_A.csv
RECENT_WINDOW = 30            # days of train data used to estimate daily usage
DEFAULT_HORIZON = 30          # default risk-scan window for the historical snapshot


def _read_csv_from_zip(z: zipfile.ZipFile, name: str) -> pd.DataFrame:
    # tolerate "A/xxx.csv" prefixes and __MACOSX duplicates
    candidates = [n for n in z.namelist() if n.endswith("/" + name) or n == name]
    if not candidates:
        raise KeyError("数据包中找不到 %s" % name)
    real = [c for c in candidates if "__MACOSX" not in c][0]
    with z.open(real) as f:
        return pd.read_csv(io.BytesIO(f.read()), encoding="utf-8-sig")


class DataAAdapter:
    """Loads A.zip and derives unit-level materials + replay series for the engine."""

    def __init__(self, zip_path: str):
        if not os.path.exists(zip_path):
            raise FileNotFoundError("找不到数据包: %s" % zip_path)
        self.zip_path = zip_path
        self._cache: Dict = {}

    def load(self, recent_window: int = RECENT_WINDOW, horizon: int = DEFAULT_HORIZON,
             simulate_pos: bool = False) -> Dict:
        key = (recent_window, horizon, simulate_pos)
        if key in self._cache:
            return self._cache[key]
        with zipfile.ZipFile(self.zip_path) as z:
            names = set(z.namelist())
            expected = {"inventory_info_A.csv", "use_train_A.csv", "use_test_A.csv",
                        "product_topo_A.csv", "geo_topo_A.csv", "weight_A.csv"}
            found = {n.split("/")[-1] for n in names if n.endswith(".csv")}
            missing = expected - found
            inv = _read_csv_from_zip(z, "inventory_info_A.csv")
            train = _read_csv_from_zip(z, "use_train_A.csv")
            test = _read_csv_from_zip(z, "use_test_A.csv")
            topo = _read_csv_from_zip(z, "product_topo_A.csv")
            geo = _read_csv_from_zip(z, "geo_topo_A.csv")
            weight = _read_csv_from_zip(z, "weight_A.csv")

        flags: List[str] = []
        if missing:
            flags.append("数据包缺少文件: %s" % ", ".join(sorted(missing)))

        # ---- validation of real schema ----
        snapshot_dates = sorted(set(str(x) for x in inv["ts"].dropna()))
        if len(snapshot_dates) != 1:
            raise ValueError("inventory_info_A.csv 应只有一个快照日期，实际 %s" % snapshot_dates)
        if snapshot_dates[0] != SNAPSHOT_DATE:
            raise ValueError("快照日期与已知值不一致: %s" % snapshot_dates[0])

        train_ts = pd.to_datetime(train["ts"])
        test_ts = pd.to_datetime(test["ts"])
        train_max = str(train_ts.max().date())
        test_min = str(test_ts.min().date())
        if test_min <= train_max:
            raise ValueError("use_test_A 与 use_train_A 时间重叠，无法做无泄漏回测")
        if train_max != SNAPSHOT_DATE:
            flags.append("use_train_A 末日在 %s，与快照日 %s 存在间隔" % (train_max, SNAPSHOT_DATE))

        # ---- hierarchies ----
        def _clean(v):
            """拓扑文件自身可能含空值；统一转成 None，避免 NaN 进入 JSON。"""
            return None if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)

        prod_l1 = {_clean(k): _clean(v) for k, v in zip(topo["product_level_2"], topo["product_level_1"])}
        geo_l2 = {_clean(k): _clean(v) for k, v in zip(geo["geography_level_3"], geo["geography_level_2"])}
        geo_l1 = {_clean(k): _clean(v) for k, v in zip(geo["geography_level_3"], geo["geography_level_1"])}
        weight_map = {_clean(k): v for k, v in zip(weight["unit"], weight["weight"])}

        # ---- snapshot stock ----
        inv["qty"] = pd.to_numeric(inv["qty"], errors="coerce").fillna(0.0)
        snapshot = {}
        for _, r in inv.iterrows():
            snapshot[_clean(r["unit"])] = {"stock": float(r["qty"]),
                                          "product": _clean(r["product"]),
                                          "geography": _clean(r["geography"])}

        # ---- usage estimation from train (no future data) ----
        train = train[train_ts <= pd.Timestamp(SNAPSHOT_DATE)]
        train["ts"] = train["ts"].astype(str)
        train["qty"] = pd.to_numeric(train["qty"], errors="coerce").fillna(0.0)
        last_days = sorted(set(train["ts"]))[-recent_window:]
        window = train[train["ts"].isin(last_days)]
        usage_est = window.groupby("unit")["qty"].agg(["mean", "std", "count"])
        train_agg = train.groupby("unit")["qty"].agg(["mean", "std", "sum", "count", "min"])
        neg_units = sorted(set(train.loc[train["qty"] < 0, "unit"]))

        # ---- materials for the engine ----
        materials: List[Dict] = []
        for unit, meta in snapshot.items():
            product = meta["product"] or "未知产品"
            geo_unit = meta["geography"] or "未知地理"
            u = usage_est.loc[unit] if unit in usage_est.index else None
            mean_use = float(u["mean"]) if u is not None else 0.0
            std_use = float(u["std"]) if u is not None and not pd.isna(u["std"]) else 0.0
            obs_days = int(u["count"]) if u is not None else 0
            all_row = train_agg.loc[unit] if unit in train_agg.index else None
            materials.append({
                "sku": unit,
                "name": "产品 %s @ 地理 %s" % (product[:8], geo_unit[:8]),
                "unit": unit,
                "product": product,
                "productLevel1": prod_l1.get(product) or "未映射",
                "geography": geo_unit,
                "geographyLevel1": geo_l1.get(geo_unit) or "未映射",
                "geographyLevel2": geo_l2.get(geo_unit) or "未映射",
                "stock": meta["stock"],
                "usage": round(mean_use, 4),
                "usageStd": round(std_use, 4),
                "usageObsDays": obs_days,
                "weightPerUnit": float(weight_map[unit]) if unit in weight_map else None,
                # Fields A.zip does not contain. They are marked as assumptions,
                # never presented as real business facts.
                "safety": 0.0,                     # 假设：未提供安全库存
                "leadTime": 7,                     # 假设：未提供交期，取 7 天
                "supplier": "",                    # 未提供
                "unitCost": None,
                "trainMean": float(all_row["mean"]) if all_row is not None else 0.0,
                "trainDays": int(all_row["count"]) if all_row is not None else 0,
            })

        # ---- simulated purchase orders (ONLY when explicitly requested) ----
        orders: List[Dict] = []
        if simulate_pos:
            rng_hash = lambda s: int(__import__("hashlib").md5(s.encode()).hexdigest(), 16)  # noqa: E731
            for i, m in enumerate(materials):
                if m["usage"] <= 0:
                    continue
                coverage = m["stock"] / m["usage"] if m["usage"] > 0 else None
                if coverage is not None and coverage <= 60:
                    qty = math.ceil(max(m["usage"] * 14, 1))
                    arrival_offset = max(1, math.ceil(min(coverage, 14) * 0.7))
                    orders.append({
                        "id": "SIM-PO-A-%04d" % (i + 1),
                        "sku": m["sku"], "quantity": qty,
                        "arrival": E.add_days(SNAPSHOT_DATE, arrival_offset),
                        "supplier": "", "status": "已下单",
                        "simulated": True,
                        "note": "A.zip 无真实采购订单；本条为按历史消耗量生成的模拟采购单，仅用于演示延期测试",
                    })
            flags.append("已按请求生成 %d 条【模拟】采购订单（A.zip 不含真实采购数据）" % len(orders))
        else:
            flags.append("A.zip 不含采购订单/供应商/预计到货日期字段；延期类测试将标记为“不适用（无采购数据）”")

        if neg_units:
            flags.append("use_train_A 中存在负消耗（退货/冲销）记录，涉及 %d 个 unit，已如实保留" % len(neg_units))
        if snapshot and len(set(geo["geography_level_3"])) > len(snapshot):
            flags.append("geo_topo 有 %d 个 L3 地理单元，其中 %d 个无库存快照" % (
                len(set(geo["geography_level_3"])), len(set(geo["geography_level_3"])) - len(snapshot)))

        # ---- risk scan over the historical snapshot ----
        risks = E.rank_risks(materials, orders, horizon, SNAPSHOT_DATE)
        dataset = {
            "source": "A.zip（阿里云基础设施供应链数据）",
            "dataKind": "历史业务数据",
            "snapshotDate": SNAPSHOT_DATE,
            "trainRange": [str(train_ts.min().date()), train_max],
            "testRange": [test_min, str(test_ts.max().date())],
            "units": len(materials),
            "products": len(set(m["product"] for m in materials)),
            "geographies": len(set(m["geography"] for m in materials)),
            "orders": len(orders),
            "ordersSimulated": simulate_pos,
            "flags": flags,
            "missingFields": {"采购订单": "未提供", "供应商": "未提供", "预计到货日期": "未提供",
                              "采购交期": "未提供（引擎按 7 天假设）", "安全库存": "未提供（引擎按 0 假设）"},
            "materials": materials,
            "orders": orders,
            "riskSummary": {
                "critical": sum(1 for r in risks if r["level"] == "critical"),
                "watch": sum(1 for r in risks if r["level"] == "watch"),
                "excess": sum(1 for r in risks if r["level"] == "excess"),
                "normal": sum(1 for r in risks if r["level"] == "normal"),
            },
        }
        self._cache[key] = dataset
        return dataset

    # ------------------------------------------------------------------ backtest
    def replay_series(self, units: Optional[List[str]] = None) -> Dict[str, List[float]]:
        """Actual daily consumption per unit from use_test_A, aligned to
        SNAPSHOT_DATE + 1 .. end of test period (evaluation only)."""
        with zipfile.ZipFile(self.zip_path) as z:
            test = _read_csv_from_zip(z, "use_test_A.csv")
        test = test[test["ts"] > SNAPSHOT_DATE].copy()
        all_days = sorted(set(test["ts"]))
        horizon = len(all_days)
        idx = {d: i for i, d in enumerate(all_days)}
        series: Dict[str, List[float]] = {}
        grouped = test.groupby("unit")
        target = units if units else list(grouped.groups.keys())
        for unit in target:
            row = [0.0] * horizon
            if unit in grouped.groups:
                g = grouped.get_group(unit)
                for _, r in g.iterrows():
                    row[idx[r["ts"]]] += float(r["qty"])
            series[unit] = row
        return series, all_days, horizon

    def backtest(self, unit_limit: int = 50, top_n: Optional[int] = None) -> Dict:
        """Replay use_test_A actual consumption against the 2021-03-01 snapshot.

        No leakage by construction: usage estimation uses only use_train_A
        (through 2021-03-01); use_test_A is only consumed here, in the replay.

        Convention: the 2021-03-01 snapshot is treated as the stock at the start
        of the first test day (2021-03-02), i.e. end of business on 2021-03-01.
        """
        ds = self.load()
        materials = ds["materials"]
        series, days, horizon = self.replay_series()
        # Choose the most at-risk units first (deterministic ordering).
        risks = E.rank_risks(materials, [], horizon, SNAPSHOT_DATE)
        order = [r["material"]["sku"] for r in risks]
        if top_n:
            order = order[:top_n]
        order = order[:unit_limit]
        results = []
        for sku in order:
            m = next(x for x in materials if x["sku"] == sku)
            actual = series.get(sku, [0.0] * horizon)
            replay = E.backtest_run(m, [], actual, {"today": days[0], "horizon": horizon})
            results.append({
                "sku": sku,
                "product": m["product"],
                "geography": m["geography"],
                "snapshotStock": m["stock"],
                "estDailyUsage": m["usage"],
                "actualMeanUsage": replay["actualDailyDemand"],
                "firstShortage": replay["firstShortage"],
                "totalUnmet": replay["totalUnmet"],
                "endStock": replay["endStock"],
                "shortageDays": sum(1 for p in replay["points"] if p["unmet"] and p["unmet"] > 0),
            })
        return {
            "method": "backtest-replay",
            "note": "用 use_test_A 的真实逐日消耗回放 2021-03-01 库存快照；只用于评估，不参与任何参数估计",
            "startDate": days[0] if days else None,
            "endDate": days[-1] if days else None,
            "horizon": horizon,
            "unitsEvaluated": len(results),
            "unitsWithShortage": sum(1 for r in results if r["firstShortage"]),
            "totalUnmetAll": round(sum(r["totalUnmet"] for r in results), 2),
            "results": results,
        }
