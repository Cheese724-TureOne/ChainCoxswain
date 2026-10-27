# -*- coding: utf-8 -*-
"""ChainCoxswain 关系型数据集适配器（inventory-supply-chain v1.0.0，合成教学数据）。

数据形态：8 张规范化 CSV 表（外键关联），非扁平模板：
  products / suppliers / warehouses / inventory_opening_balances /
  inventory_movements / purchase_orders / purchase_order_lines

适配策略（确定性 join + 聚合，全程不依赖 LLM）：
  * 业务当前日 TODAY = 2025-12-15（数据集中最后一笔下单日期，原样保留）；
  * 物料 = 按 product 聚合（跨 12 仓）：
      stock  = 期初(opening_units) + 截至 TODAY 的全部流水净变化（不含 TODAY 之后，无未来泄漏）
      usage  = TODAY 前 91 天窗口内 sale 流出量的日均值
      leadTime / supplier / unitCost 来自 suppliers/products 真实字段
  * 采购单 = open/partial 且 expected_at > TODAY 的未收完订单行（真实在途单）；
  * 该数据集为 synthetic 教学数据 -> dataKind=demo，绝不标注为真实历史数据；
  * 缺失项显式标注：安全库存按 0 假设、无 BOM/生产计划、跨仓聚合说明。
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REL_DEFAULT_DIR = os.path.join(BASE_DIR, "data", "relational")
TODAY = "2025-12-15"          # 数据集最后一笔下单日期，作为业务推演基准日（历史业务日期）
USAGE_WINDOW_DAYS = 91        # 用量估计窗口（TODAY 往前 91 天）
HORIZON_NOTE = "推演窗口默认 14 天；在途采购单集中在 2025-12-16 ~ 2025-12-31"


def _f(v: Any) -> Optional[float]:
    """float-safe：NaN/Inf -> None，避免进入 JSON。"""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or math.isinf(x)) else x


def _clean_str(v: Any) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v)


class RelationalAdapter:
    """Relational -> materials/orders 确定性适配。"""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or REL_DEFAULT_DIR

    def _csv(self, name: str) -> pd.DataFrame:
        p = os.path.join(self.data_dir, name)
        if not os.path.exists(p):
            raise FileNotFoundError("关系型数据集缺少 %s（数据目录 %s）" % (name, self.data_dir))
        return pd.read_csv(p)

    def load(self) -> Dict:
        products = self._csv("products.csv")
        suppliers = self._csv("suppliers.csv")
        balances = self._csv("inventory_opening_balances.csv")
        movements = self._csv("inventory_movements.csv")
        orders_po = self._csv("purchase_orders.csv")
        order_lines = self._csv("purchase_order_lines.csv")

        today_ts = pd.Timestamp(TODAY)
        window_start = today_ts - pd.Timedelta(days=USAGE_WINDOW_DAYS)

        # ---- 流水：统一时间类型（原文件列存在混合类型）----
        movements["movement_at"] = pd.to_datetime(movements["movement_at"], errors="coerce")
        orders_po["expected_at"] = pd.to_datetime(orders_po["expected_at"], errors="coerce")
        orders_po["ordered_at"] = pd.to_datetime(orders_po["ordered_at"], errors="coerce")
        orders_po["received_at"] = pd.to_datetime(orders_po["received_at"], errors="coerce")

        # ---- 库存：期初 + 截至 TODAY 的净流水（TODAY 之后不参与，无未来泄漏）----
        opening = balances.groupby("product_id", as_index=False)["opening_units"].sum()
        opening = opening.rename(columns={"opening_units": "opening"})
        hist = movements[movements["movement_at"] <= today_ts]
        net = hist.groupby("product_id", as_index=False)["quantity_change"].sum()
        net = net.rename(columns={"quantity_change": "net_flow"})
        stock_df = opening.merge(net, on="product_id", how="left").fillna(0.0)
        stock_df["stock"] = (stock_df["opening"] + stock_df["net_flow"]).round(0)
        negative = int((stock_df["stock"] < 0).sum())
        stock_df.loc[stock_df["stock"] < 0, "stock"] = 0.0  # 合成数据的账实差异 -> 按 0 展示并标注

        # ---- 用量：窗口内 sale 流出量日均 ----
        sales = movements[(movements["movement_type"] == "sale")
                          & (movements["movement_at"] > window_start)
                          & (movements["movement_at"] <= today_ts)]
        usage_df = sales.groupby("product_id", as_index=False)["quantity_change"].agg(
            sold=lambda s: -float(s.sum()))
        usage_df["usage"] = (usage_df["sold"] / USAGE_WINDOW_DAYS).round(4)
        zero_usage = 2500 - usage_df["product_id"].nunique()

        # ---- 逐日需求统计（窗口内，缺失日按 0 需求）：usageStd 供波动/概率模型 ----
        g = sales.groupby("product_id")["quantity_change"].agg(
            s=lambda s: -float(s.sum()), ss=lambda s: float((s ** 2).sum()), k="count")
        std_map: Dict[str, float] = {}
        for pid, row in g.iterrows():
            n = USAGE_WINDOW_DAYS
            mean = row["s"] / n
            var = (row["ss"] - n * mean * mean) / (n - 1) if n > 1 else 0.0
            std_map[_clean_str(pid)] = round(math.sqrt(max(0.0, var)), 4)

        # ---- 供应商 / 产品主数据 ----
        sup = suppliers.set_index("supplier_id")
        prod = products.merge(sup[["supplier_name", "lead_time_days"]],
                              left_on="supplier_id", right_index=True, how="left")

        # ---- 组装 materials（每 product 一条，跨仓聚合）----
        merged = prod.merge(stock_df[["product_id", "stock"]], on="product_id", how="left") \
                     .merge(usage_df[["product_id", "usage"]], on="product_id", how="left")
        merged["stock"] = merged["stock"].fillna(0.0)
        merged["usage"] = merged["usage"].fillna(0.0)
        materials: List[Dict] = []
        for _, r in merged.iterrows():
            pid = _clean_str(r["product_id"])
            materials.append({
                "sku": _clean_str(r["sku"]),
                "name": "%s · %s" % (_clean_str(r["sku"]), _clean_str(r["category"])),
                "category": _clean_str(r["category"]),
                "stock": float(r["stock"]),
                "usage": float(r["usage"]),
                "usageStd": std_map.get(pid, 0.0),
                "usageObsDays": USAGE_WINDOW_DAYS,
                "safety": 0.0,                     # 假设：数据未提供安全库存
                "leadTime": int(_f(r["lead_time_days"]) or 0),
                "supplier": _clean_str(r["supplier_name"]) or "未提供",
                "unitCost": _f(r["unit_cost"]),
                "reorderPoint": _f(r["reorder_point"]),
            })

        # ---- 采购单：以快照日 TODAY 重构在途状态（不使用数据集最终状态/最终收货量）----
        # 截至 TODAY 的累计实际收货来自流水表 receipt 记录（purchase_order_line_id 关联），
        # 因此 TODAY 之后才发生的收货不会进入历史在途量（无未来泄漏）。
        receipts_hist = movements[(movements["movement_type"] == "receipt")
                                  & (movements["movement_at"] <= today_ts)
                                  & (movements["purchase_order_line_id"].notna())]
        recv_by_line = receipts_hist.groupby("purchase_order_line_id")["quantity_change"].sum()
        placed = orders_po[orders_po["ordered_at"] <= today_ts]
        sku_map = dict(zip(products["product_id"], products["sku"]))
        lines = order_lines[order_lines["purchase_order_id"].isin(placed["purchase_order_id"])].copy()
        lines["recv_today"] = lines["purchase_order_line_id"].map(recv_by_line).fillna(0.0)
        lines["remaining"] = lines["quantity_ordered"] - lines["recv_today"]
        lines = lines[lines["remaining"] > 0]
        po_info = placed[["purchase_order_id", "expected_at", "supplier_id", "warehouse_id"]]
        lines = lines.merge(po_info, on="purchase_order_id", how="left")
        orders: List[Dict] = []
        n_overdue = 0
        for _, r in lines.iterrows():
            pid = _clean_str(r["product_id"])
            if pid not in sku_map:
                continue
            expected = pd.Timestamp(r["expected_at"])
            overdue = expected <= today_ts      # 快照日已逾期：单独标记，引擎不计入未来到货
            if overdue:
                n_overdue += 1
            orders.append({
                "id": _clean_str(r["purchase_order_line_id"]),
                "poId": _clean_str(r["purchase_order_id"]),
                "sku": sku_map[pid],
                "warehouseId": _clean_str(r["warehouse_id"]),
                "quantity": int(r["remaining"]),
                "receivedQty": 0,
                "arrival": expected.strftime("%Y-%m-%d"),
                "supplier": _clean_str(sup.loc[r["supplier_id"], "supplier_name"]) if r["supplier_id"] in sup.index else "",
                "status": "已下单",               # 快照日重构状态：已下单未收完（在途）
                "overdue": bool(overdue),
            })

        # ---- 需求历史（逐日 sale 流出，训练窗口内）：供预测/Monte Carlo/回测 ----
        sales_all = movements[(movements["movement_type"] == "sale")
                              & (movements["movement_at"] <= today_ts)]
        daily = (sales_all.groupby([sales_all["product_id"],
                                    sales_all["movement_at"].dt.strftime("%Y-%m-%d")])["quantity_change"]
                 .sum().reset_index())
        demand_history: Dict[str, Dict[str, float]] = {}
        for pid, day, q in daily.itertuples(index=False):
            sku = sku_map.get(pid)
            if sku:
                demand_history.setdefault(sku, {})[day] = round(-float(q), 4)

        # ---- 交期与履约历史（仅快照日前已完成的采购，无未来泄漏）----
        completed = orders_po[(orders_po["ordered_at"] <= today_ts)
                              & (orders_po["received_at"].notna())
                              & (orders_po["received_at"] <= today_ts)]
        lead_samples: Dict[str, List[float]] = {}
        supplier_history: List[Dict] = []
        for _, r in completed.iterrows():
            sup_name = _clean_str(sup.loc[r["supplier_id"], "supplier_name"]) if r["supplier_id"] in sup.index else ""
            lead = float((pd.Timestamp(r["received_at"]) - pd.Timestamp(r["ordered_at"])).days)
            lead_samples.setdefault(sup_name, []).append(max(0.0, lead))
            line_rows = order_lines[order_lines["purchase_order_id"] == r["purchase_order_id"]]
            q_ord = float(line_rows["quantity_ordered"].sum())
            q_recv = float(line_rows["quantity_received"].sum())
            supplier_history.append({"supplier": sup_name,
                                     "expected_at": pd.Timestamp(r["expected_at"]).strftime("%Y-%m-%d"),
                                     "received_at": pd.Timestamp(r["received_at"]).strftime("%Y-%m-%d"),
                                     "quantity_ordered": q_ord, "quantity_received": q_recv})

        # ---- 分仓单元（SKU + warehouse_id）：库存/用量/在途按仓拆分 ----
        wh_open = balances.groupby(["product_id", "warehouse_id"], as_index=False)["opening_units"].sum()
        wh_net = hist.groupby(["product_id", "warehouse_id"], as_index=False)["quantity_change"].sum()
        wh_stock = wh_open.merge(wh_net, on=["product_id", "warehouse_id"], how="outer").fillna(0.0)
        wh_stock["stock"] = (wh_stock["opening_units"] + wh_stock["quantity_change"]).clip(lower=0).round(0)
        wh_sales = (sales_all.groupby(["product_id", "warehouse_id"])["quantity_change"].sum().reset_index())
        wh_sales["usage"] = (-wh_sales["quantity_change"] / USAGE_WINDOW_DAYS).clip(lower=0).round(4)
        wh = wh_stock.merge(wh_sales[["product_id", "warehouse_id", "usage"]],
                            on=["product_id", "warehouse_id"], how="left")
        wh_inbound = lines.groupby(["product_id", "warehouse_id"])["remaining"].sum().reset_index()
        wh = wh.merge(wh_inbound, on=["product_id", "warehouse_id"], how="left")
        warehouse_units: List[Dict] = []
        for _, r in wh.iterrows():
            pid = _clean_str(r["product_id"])
            if pid not in sku_map:
                continue
            warehouse_units.append({"sku": sku_map[pid], "warehouseId": _clean_str(r["warehouse_id"]),
                                    "stock": float(r["stock"]), "usage": float(r["usage"] or 0.0),
                                    "inbound": float(r["remaining"] or 0.0)})

        future_lines = lines[lines["expected_at"] > today_ts]
        overdue_lines = lines[lines["expected_at"] <= today_ts]
        flags = [
            "合成教学数据集（synthetic，README 声明为虚构业务数据），不构成真实企业基准",
            "物料按 product 跨 12 仓聚合；分仓粒度另提供 warehouse_units 视图",
            "库存 = 期初 + 截至 %s 的流水净变化；%s 之后的流水不参与（无未来泄漏）" % (TODAY, TODAY),
            "日均用量 = %s 前 %d 天窗口内 sale 流出量日均值（无销售记录的 %d 个产品用量为 0）" % (
                TODAY, USAGE_WINDOW_DAYS, zero_usage),
            "在途采购按快照日重构：截至 %s 的累计收货取自流水 receipt 记录，不使用数据集最终收货量/最终状态（无未来泄漏）" % TODAY,
            "有 %d 笔在途单在快照日已逾期，单独标记 overdue，引擎不计入未来到货" % n_overdue,
        ]
        if negative:
            flags.append("有 %d 个产品按流水推算出现负库存（合成数据的账实差异），按 0 展示并计入此提示" % negative)
        flags.append("安全库存数据缺失，按 0 假设；动态安全库存由历史需求/交期样本计算（见 policy）")

        return {
            "materials": materials,
            "orders": orders,
            "today": TODAY,
            "units": len(materials),
            "ordersCount": len(orders),
            "usageWindow": {"start": window_start.strftime("%Y-%m-%d"), "end": TODAY,
                            "days": USAGE_WINDOW_DAYS},
            "flags": flags,
            "missingFields": {
                "安全库存": "未提供，按 0 假设（已在 flags 标注）",
                "BOM/生产计划": "未提供，不把物料缺口直接认定为停产",
                "分仓调拨能力": "未提供，分仓视图默认不把他仓库存计入可用",
            },
            "poStats": {"openPOs": int(len(placed)), "futureLines": int(len(future_lines)),
                        "futureQuantity": int(future_lines["remaining"].sum()) if len(future_lines) else 0,
                        "overdueLines": int(len(overdue_lines)),
                        "overdueQuantity": int(overdue_lines["remaining"].sum()) if len(overdue_lines) else 0,
                        "arrivalRange": [future_lines["expected_at"].min().strftime("%Y-%m-%d"),
                                         future_lines["expected_at"].max().strftime("%Y-%m-%d")]
                        if len(future_lines) else []},
            "demandHistory": demand_history,
            "leadTimeSamples": {k: [round(x, 1) for x in v] for k, v in lead_samples.items()},
            "supplierHistory": supplier_history,
            "warehouseUnits": warehouse_units,
        }
