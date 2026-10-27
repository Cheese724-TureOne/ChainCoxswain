# -*- coding: utf-8 -*-
"""ChainCoxswain business-document generation: editable Excel risk table, Word
analysis report, PPT presentation. All figures come from the deterministic
engine; generation is fully offline (openpyxl / python-docx / python-pptx).
"""
from __future__ import annotations

import html as _html
import os
import time
from typing import Dict, List, Optional

try:
    from . import engine as E
    from . import inventory_policy as POL
    from . import demand_forecast as FC
    from . import risk_dimensions as RD
except ImportError:  # pragma: no cover
    import engine as E  # type: ignore
    import inventory_policy as POL  # type: ignore
    import demand_forecast as FC  # type: ignore
    import risk_dimensions as RD  # type: ignore

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports")
LEVEL_LABELS = {"critical": "缺货风险", "watch": "低于安全库存", "excess": "库存覆盖偏高", "normal": "状态正常"}
SCENARIO_LABELS = {"deliveryDelay": "采购到货延迟", "purchaseDelay": "补货下单推迟", "demandIncrease": "需求上涨"}


def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def _collect(service, limit_skus: int = 6, horizon: int = 14) -> Dict:
    risks = E.rank_risks(service.materials, service.orders, horizon, service._today())
    scan = E.autonomous_scan(service.materials, service.orders,
                             {"today": service._today(), "horizon": horizon, "maxSkus": limit_skus})
    focus = scan["tested"][0] if scan["tested"] else None
    interventions = []
    if focus:
        interventions = E.mitigate(focus["risk"]["material"], service.orders,
                                   focus["focusScenario"], {"today": service._today(), "horizon": horizon})
    policies, probs, tiers = {}, {}, {}
    ful = RD.supplier_fulfillment(getattr(service, "supplier_history", []) or [])
    for r in risks:
        sku = r["material"]["sku"]
        series = service._series_for(sku) if hasattr(service, "_series_for") else []
        policies[sku] = POL.policy_for_material(r["material"], service.orders, service._today(),
                                                demand_series=series,
                                                lead_times=service._leads_for(r["material"])
                                                if hasattr(service, "_leads_for") else [])
    for r in E.shortage_queue(risks)[:12]:
        sku = r["material"]["sku"]
        mc = service.tool_stockout_probability(sku, n=300, caller="report")
        probs[sku] = mc
        fc = FC.forecast(service._series_for(sku), horizon, fallback_usage=r["material"].get("usage")) \
            if hasattr(service, "_series_for") else None
        dims = RD.dimension_scores(r["material"], r, policies[sku], fc,
                                   mc if mc.get("available") else None, ful, None)
        tiers[sku] = RD.priority_tier(dims)
    return {"risks": risks, "scan": scan, "focus": focus, "interventions": interventions,
            "meta": service.meta(), "today": service._today(), "horizon": horizon,
            "policies": policies, "probs": probs, "tiers": tiers}


def generate(service, fmt: str = "xlsx") -> Dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    data = _collect(service)
    ts = time.strftime("%Y%m%d_%H%M%S")
    if fmt == "xlsx":
        path = _gen_xlsx(data, ts)
    elif fmt == "docx":
        path = _gen_docx(data, ts)
    elif fmt == "pptx":
        path = _gen_pptx(data, ts)
    elif fmt == "html":
        path = _gen_html(data, ts)
    else:
        raise ValueError("不支持的格式: %s" % fmt)
    return {"format": fmt, "path": path, "filename": os.path.basename(path),
            "sizeBytes": os.path.getsize(path),
            "content": {"riskCounts": _risk_counts(data),
                        "testedSkus": len(data["scan"]["tested"]),
                        "totalRounds": data["scan"]["totalRounds"]},
            "note": "报告内所有数字来自确定性库存仿真引擎；行动建议需人工审核后执行"}


def _risk_counts(data) -> Dict:
    return {k: sum(1 for r in data["risks"] if r["level"] == k) for k in LEVEL_LABELS}


# ----------------------------------------------------------------------- xlsx
def _gen_xlsx(data, ts) -> str:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    head_fill = PatternFill("solid", fgColor="1AB6A7")
    head_font = Font(color="FFFFFF", bold=True)
    thin = Alignment(vertical="top")

    # Sheet 1: risk list
    ws = wb.active
    ws.title = "库存风险清单"
    ws.append(["生成时间", ts, "数据来源", data["meta"]["source"], "数据性质", data["meta"]["dataKind"]])
    ws.append(["测试窗口（天）", data["horizon"], "快照/推演基准日", data["today"]])
    ws.append([])
    headers = ["物料编码", "物料名称", "快照库存", "日均用量", "覆盖天数", "安全库存", "风险等级",
               "首次缺货日期", "累计缺口", "优先级", "推荐安全库存", "推荐再订货点", "库存位置",
               "14天缺货概率", "原因"]
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=ws.max_row, column=c)
        cell.fill, cell.font = head_fill, head_font
    for r in data["risks"]:
        sku = r["material"]["sku"]
        pol = data["policies"].get(sku) or {}
        prob = (data["probs"].get(sku) or {})
        p14 = prob.get("horizons", {}).get("14", {}).get("stockoutProbability") \
            if prob.get("available") else None
        ws.append([sku, r["material"].get("name"), r["material"]["stock"],
                   r["material"]["usage"], _fmt(r["coverage"]), r["material"]["safety"],
                   LEVEL_LABELS[r["level"]], r["base"]["firstShortage"] or "—",
                   _fmt(r["base"]["totalUnmet"]),
                   (data["tiers"].get(sku) or {}).get("tier", "—"),
                   _fmt(pol.get("recommendedSafety")) if pol.get("available") else "回退:%s" % _fmt(pol.get("recommendedSafety")),
                   _fmt(pol.get("recommendedROP")), _fmt(pol.get("inventoryPosition")),
                   _fmt(p14) if p14 is not None else "历史不足",
                   r["reason"]])
    for row in ws.iter_rows(min_row=4, max_row=ws.max_row, max_col=len(headers)):
        for cell in row:
            cell.alignment = thin
    for i, w in enumerate([14, 22, 10, 10, 10, 10, 14, 14, 10, 8, 12, 12, 10, 12, 60], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # Sheet: 概率与政策（Top 缺货候选）
    wsp = wb.create_sheet("概率与政策")
    wsp.append(["物料", "方法", "7天缺货概率", "14天缺货概率", "30天缺货概率", "预计未满足(14d)",
                "P95缺口(14d)", "首次缺货日p50", "推荐SS", "推荐ROP", "库存位置", "需补货", "优先级规则"])
    for c in range(1, 14):
        wsp.cell(row=1, column=c).fill, wsp.cell(row=1, column=c).font = head_fill, head_font
    for sku, prob in data["probs"].items():
        pol = data["policies"].get(sku) or {}
        h = prob.get("horizons", {}) if prob.get("available") else {}
        wsp.append([sku, prob.get("method", "情景分析（历史不足，非概率）"),
                    _fmt((h.get("7") or {}).get("stockoutProbability")),
                    _fmt((h.get("14") or {}).get("stockoutProbability")),
                    _fmt((h.get("30") or {}).get("stockoutProbability")),
                    _fmt((h.get("14") or {}).get("expectedUnmet")),
                    _fmt((h.get("14") or {}).get("p95Unmet")),
                    _fmt((prob.get("firstShortageDay") or {}).get("p50")),
                    _fmt(pol.get("recommendedSafety")), _fmt(pol.get("recommendedROP")),
                    _fmt(pol.get("inventoryPosition")),
                    "是" if pol.get("needsReorder") else ("否" if pol.get("needsReorder") is False else "—"),
                    "；".join((data["tiers"].get(sku) or {}).get("rules", []))])
    for i, w in enumerate([16, 30, 12, 12, 12, 14, 12, 14, 10, 10, 10, 8, 60], 1):
        wsp.column_dimensions[get_column_letter(i)].width = w

    # Sheet 2: thresholds
    ws2 = wb.create_sheet("风险临界点")
    ws2.append(["物料", "到货延期临界点(天)", "下单推迟临界点(天)", "需求上涨临界点(%)", "基线风险", "总轮次"])
    for t in data["scan"]["tested"]:
        th = t["thresholds"]
        ws2.append([t["risk"]["material"]["sku"],
                    _fmt(th["deliveryDelay"]["threshold"]) if th["deliveryDelay"]["status"] == "found"
                    else {"already": "基线已缺货", "not-found": "未触发", "not-applicable": "不适用（无采购数据）"}.get(th["deliveryDelay"]["status"], "—"),
                    _fmt(th["purchaseDelay"]["threshold"]) if th["purchaseDelay"]["status"] == "found"
                    else {"already": "基线已缺货", "not-found": "未触发", "not-applicable": "不适用（无计划采购）"}.get(th["purchaseDelay"]["status"], "—"),
                    _fmt(th["demandIncrease"]["threshold"]) if th["demandIncrease"]["status"] == "found"
                    else {"already": "基线已缺货", "not-found": "未触发", "not-applicable": "不适用"}.get(th["demandIncrease"]["status"], "—"),
                    LEVEL_LABELS[t["risk"]["level"]], t["rounds"]])
    for c in range(1, 7):
        ws2.cell(row=1, column=c).fill, ws2.cell(row=1, column=c).font = head_fill, head_font
    for i, w in enumerate([36, 20, 20, 18, 12, 10], 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # Sheet 3: interventions
    ws3 = wb.create_sheet("干预方案")
    ws3.append(["物料", "重点情景", "方案", "动作说明", "缺口改善(件)", "是否消除缺口", "数据是否充分", "备注"])
    if data["focus"]:
        f = data["focus"]
        for o in data["interventions"]:
            ws3.append([f["risk"]["material"]["sku"],
                        "%s %s" % (SCENARIO_LABELS.get(f["focusScenario"]["type"], ""), f["focusScenario"]["value"]),
                        o["title"], o["action"], _fmt(o["improvement"]),
                        "是" if o["resolvesShortage"] else "否",
                        "否（需人工补充）" if o.get("dataRequired") else "是", o["note"]])
    for c in range(1, 9):
        ws3.cell(row=1, column=c).fill, ws3.cell(row=1, column=c).font = head_fill, head_font
    for i, w in enumerate([36, 16, 24, 46, 12, 12, 18, 50], 1):
        ws3.column_dimensions[get_column_letter(i)].width = w

    # Sheet 4: data source & assumptions
    ws4 = wb.create_sheet("数据来源与假设")
    ws4.append(["数据来源", data["meta"]["source"]])
    ws4.append(["数据性质", {"demo": "演示数据（虚构）", "real-historical": "真实历史数据"}.get(data["meta"]["dataKind"], data["meta"]["dataKind"])])
    ws4.append([])
    ws4.append(["假设与边界"])
    for i, line in enumerate([
        "1. 收货在每日开始时入库，当日消耗在入库后扣除，每日结束记录剩余库存。",
        "2. 日均用量在测试窗口内恒定；需求上涨情景按固定倍率放大。",
        "3. 缺货数量由逐日确定性仿真计算，不由大模型生成。",
        "4. 逾期且未确认收货的采购单不计入未来到货。",
        "5. 缺少 BOM/生产计划/跨仓调拨数据，不把物料缺口直接认定为停产。",
        "6. 所有催货、补货、供应商替换方案均需业务人员人工审核后执行，本系统无下单权限。",
    ], 2):
        ws4.cell(row=i, column=1, value=line)
    if data["meta"]["datasetMeta"].get("flags"):
        ws4.append([])
        ws4.append(["数据质量提示"])
        for i, f in enumerate(data["meta"]["datasetMeta"]["flags"], ws4.max_row + 1):
            ws4.cell(row=i, column=1, value="· " + f)
    if data["meta"]["datasetMeta"].get("missingFields"):
        ws4.append([])
        ws4.append(["缺失字段"])
        for i, (k, v) in enumerate(data["meta"]["datasetMeta"]["missingFields"].items(), ws4.max_row + 1):
            ws4.cell(row=i, column=1, value="%s：%s" % (k, v))
    ws4.column_dimensions["A"].width = 110

    path = os.path.join(OUT_DIR, "ChainCoxswain_风险分析_%s.xlsx" % ts)
    wb.save(path)
    return path


# ----------------------------------------------------------------------- docx
def _gen_docx(data, ts) -> str:
    from docx import Document
    from docx.shared import Pt
    doc = Document()
    doc.add_heading("供应链库存风险分析报告", 0)
    p = doc.add_paragraph()
    p.add_run("生成时间：%s\n数据来源：%s\n数据性质：%s\n测试窗口：%d 天 · 基准日：%s" % (
        ts, data["meta"]["source"], data["meta"]["dataKind"], data["horizon"], data["today"]))

    doc.add_heading("一、风险发现", level=1)
    counts = _risk_counts(data)
    doc.add_paragraph("缺货风险 %d 项 / 低于安全库存 %d 项 / 覆盖偏高 %d 项 / 正常 %d 项" % (
        counts["critical"], counts["watch"], counts["excess"], counts["normal"]))
    table = doc.add_table(rows=1, cols=6)
    table.style = "Light Grid Accent 1"
    for i, h in enumerate(["物料", "快照库存", "日均用量", "覆盖天数", "风险等级", "首次缺货"]):
        table.rows[0].cells[i].text = h
    for r in data["risks"][:12]:
        row = table.add_row().cells
        row[0].text = r["material"]["sku"]
        row[1].text = _fmt(r["material"]["stock"])
        row[2].text = _fmt(r["material"]["usage"])
        row[3].text = _fmt(r["coverage"])
        row[4].text = LEVEL_LABELS[r["level"]]
        row[5].text = r["base"]["firstShortage"] or "—"

    doc.add_heading("二、自主压力测试与临界点", level=1)
    doc.add_paragraph("共测试 %d 个物料、执行 %d 轮确定性仿真。临界点 = 首次触发缺货的最小整数扰动。" % (
        len(data["scan"]["tested"]), data["scan"]["totalRounds"]))
    t2 = doc.add_table(rows=1, cols=5)
    t2.style = "Light Grid Accent 1"
    for i, h in enumerate(["物料", "到货延期(天)", "下单推迟(天)", "需求上涨(%)", "基线风险"]):
        t2.rows[0].cells[i].text = h
    for t in data["scan"]["tested"]:
        th = t["thresholds"]
        row = t2.add_row().cells
        row[0].text = t["risk"]["material"]["sku"]
        row[1].text = _fmt(th["deliveryDelay"]["threshold"]) if th["deliveryDelay"]["status"] == "found" else th["deliveryDelay"]["status"]
        row[2].text = _fmt(th["purchaseDelay"]["threshold"]) if th["purchaseDelay"]["status"] == "found" else th["purchaseDelay"]["status"]
        row[3].text = _fmt(th["demandIncrease"]["threshold"]) if th["demandIncrease"]["status"] == "found" else th["demandIncrease"]["status"]
        row[4].text = LEVEL_LABELS[t["risk"]["level"]]

    doc.add_heading("三、概率风险与动态库存政策（Monte Carlo + 安全库存模型）", level=1)
    if data["probs"]:
        for sku, prob in data["probs"].items():
            pol = data["policies"].get(sku) or {}
            tier = (data["tiers"].get(sku) or {}).get("tier", "—")
            if prob.get("available"):
                h = prob["horizons"]
                doc.add_paragraph(
                    "%s：7/14/30 天缺货概率 %s / %s / %s；14 天预计未满足 %s 件（P95 %s）；首次缺货日 p50=%s。%s 次抽样，种子 %s。" % (
                        sku, _fmt(h["7"]["stockoutProbability"]), _fmt(h["14"]["stockoutProbability"]),
                        _fmt(h["30"]["stockoutProbability"]), _fmt(h["14"]["expectedUnmet"]),
                        _fmt(h["14"]["p95Unmet"]), _fmt(prob["firstShortageDay"]["p50"]),
                        prob["n"], prob["seed"]), style="List Bullet")
            else:
                doc.add_paragraph("%s：%s（不输出虚假概率）" % (sku, prob.get("reason")), style="List Bullet")
            doc.add_paragraph(
                "　政策：%s；推荐安全库存 %s、再订货点 %s、库存位置 %s%s。优先级 %s。" % (
                    "动态模型可用" if pol.get("available") else "回退（%s）" % pol.get("fallbackReason"),
                    _fmt(pol.get("recommendedSafety")), _fmt(pol.get("recommendedROP")),
                    _fmt(pol.get("inventoryPosition")),
                    "，低于再订货点需补货" if pol.get("needsReorder") else "", tier), style="List Bullet")
    else:
        doc.add_paragraph("当前数据集无缺货/安全库存预警物料，未触发概率仿真。")

    doc.add_heading("四、干预方案（需人工审核）", level=1)
    if data["focus"]:
        f = data["focus"]
        doc.add_paragraph("重点情景：%s %s，影响物料 %s" % (
            SCENARIO_LABELS.get(f["focusScenario"]["type"], ""), f["focusScenario"]["value"],
            f["risk"]["material"]["sku"]))
        for o in data["interventions"]:
            doc.add_paragraph("%s：%s。模拟缺口改善 %s 件；%s。%s" % (
                o["title"], o["action"], _fmt(o["improvement"]),
                "测试窗口内消除缺口" if o["resolvesShortage"] else "仍有缺口", o["note"]), style="List Bullet")

    doc.add_heading("五、假设与边界", level=1)
    for line in [
        "收货按每日开始入库，同日消耗在入库后计算。",
        "日均用量在窗口内恒定；需求上涨按倍率放大。",
        "缺货数量由确定性仿真引擎计算，不由大模型生成。",
        "逾期未确认收货的采购单不计入未来到货。",
        "缺少 BOM/生产计划/调拨数据，不把物料缺口直接认定为停产。",
        "所有催货、补货等方案需人工审核后执行，系统无自动下单权限。",
    ]:
        doc.add_paragraph(line, style="List Number")
    path = os.path.join(OUT_DIR, "ChainCoxswain_分析报告_%s.docx" % ts)
    doc.save(path)
    return path


# ----------------------------------------------------------------------- pptx
def _gen_pptx(data, ts) -> str:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "供应链库存风险分析"
    slide.placeholders[1].text = "ChainCoxswain 链舵 · 千问办公协作产出\n数据来源：%s\n生成时间：%s" % (
        data["meta"]["source"], ts)

    counts = _risk_counts(data)
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "风险概览"
    body = s2.placeholders[1].text_frame
    body.text = ("缺货风险 %d 项 · 低于安全库存 %d 项 · 覆盖偏高 %d 项\n"
                 "测试窗口 %d 天 · 共 %d 轮确定性仿真") % (
        counts["critical"], counts["watch"], counts["excess"], data["horizon"], data["scan"]["totalRounds"])
    for r in data["risks"][:5]:
        body.add_paragraph()
        body.paragraphs[-1].text = "· %s：%s（首次缺货 %s）" % (
            r["material"]["sku"], LEVEL_LABELS[r["level"]], r["base"]["firstShortage"] or "无")

    s3 = prs.slides.add_slide(prs.slide_layouts[1])
    s3.shapes.title.text = "风险临界点（自主搜索）"
    lines = ["首次触发缺货的最小整数扰动："]
    for t in data["scan"]["tested"]:
        th = t["thresholds"]
        lines.append("· %s：到货延期 %s / 下单推迟 %s / 需求上涨 %s" % (
            t["risk"]["material"]["sku"],
            _fmt(th["deliveryDelay"]["threshold"]) if th["deliveryDelay"]["status"] == "found" else th["deliveryDelay"]["status"],
            _fmt(th["purchaseDelay"]["threshold"]) if th["purchaseDelay"]["status"] == "found" else th["purchaseDelay"]["status"],
            _fmt(th["demandIncrease"]["threshold"]) if th["demandIncrease"]["status"] == "found" else th["demandIncrease"]["status"]))
    s3.placeholders[1].text = "\n".join(lines)

    s3b = prs.slides.add_slide(prs.slide_layouts[1])
    s3b.shapes.title.text = "概率风险与动态库存政策"
    plines = ["Monte Carlo 条件推演（固定种子可复现）+ SS=z*sqrt(L̄σD²+μD²σL²)："]
    if data["probs"]:
        for sku, prob in data["probs"].items():
            pol = data["policies"].get(sku) or {}
            if prob.get("available"):
                h = prob["horizons"]
                plines.append("· %s：14天缺货概率 %s（7d %s / 30d %s），预计未满足 %s 件" % (
                    sku, _fmt(h["14"]["stockoutProbability"]), _fmt(h["7"]["stockoutProbability"]),
                    _fmt(h["30"]["stockoutProbability"]), _fmt(h["14"]["expectedUnmet"])))
            else:
                plines.append("· %s：历史不足，仅情景分析（不输出虚假概率）" % sku)
            plines.append("　推荐SS %s / ROP %s / 库存位置 %s；优先级 %s" % (
                _fmt(pol.get("recommendedSafety")), _fmt(pol.get("recommendedROP")),
                _fmt(pol.get("inventoryPosition")), (data["tiers"].get(sku) or {}).get("tier", "—")))
    else:
        plines.append("· 当前无缺货/安全库存预警物料")
    s3b.placeholders[1].text = "\n".join(plines)

    s4 = prs.slides.add_slide(prs.slide_layouts[1])
    s4.shapes.title.text = "干预方案（待人工审核）"
    if data["focus"]:
        f = data["focus"]
        out = ["重点情景：%s %s → %s" % (SCENARIO_LABELS.get(f["focusScenario"]["type"], ""),
                                          f["focusScenario"]["value"], f["risk"]["material"]["sku"])]
        for o in data["interventions"]:
            out.append("· %s：缺口改善 %s 件（%s）" % (
                o["title"], _fmt(o["improvement"]), "消除缺口" if o["resolvesShortage"] else "仍有缺口"))
        s4.placeholders[1].text = "\n".join(out)

    s5 = prs.slides.add_slide(prs.slide_layouts[1])
    s5.shapes.title.text = "数据边界与审核"
    s5.placeholders[1].text = ("· 数字全部来自确定性库存仿真，不由大模型生成\n"
                               "· 无 BOM/生产计划时不推断停产\n"
                               "· 所有方案需人工确认供应商/客户可行性\n"
                               "· 系统无自动下单权限")
    path = os.path.join(OUT_DIR, "ChainCoxswain_汇报材料_%s.pptx" % ts)
    prs.save(path)
    return path


# ----------------------------------------------------------------------- html
def _gen_html(data, ts) -> str:
    rows = "".join("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
        _html.escape(r["material"]["sku"]), _html.escape(str(r["material"].get("name"))),
        _fmt(r["material"]["stock"]), _fmt(r["material"]["usage"]), _fmt(r["coverage"]),
        LEVEL_LABELS[r["level"]], r["base"]["firstShortage"] or "—")
        for r in data["risks"][:30])
    prob_rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            _html.escape(sku),
            _html.escape("Monte Carlo" if prob.get("available") else "历史不足（情景分析）"),
            _fmt((prob.get("horizons", {}).get("7") or {}).get("stockoutProbability")),
            _fmt((prob.get("horizons", {}).get("14") or {}).get("stockoutProbability")),
            _fmt((prob.get("horizons", {}).get("30") or {}).get("stockoutProbability")),
            _fmt((prob.get("horizons", {}).get("14") or {}).get("expectedUnmet")),
            _fmt((prob.get("horizons", {}).get("14") or {}).get("p95Unmet")),
            _fmt((data["policies"].get(sku) or {}).get("recommendedSafety")),
            _fmt((data["policies"].get(sku) or {}).get("recommendedROP")),
            _fmt((data["policies"].get(sku) or {}).get("inventoryPosition")),
            _html.escape((data["tiers"].get(sku) or {}).get("tier", "—")))
        for sku, prob in data["probs"].items())
    path = os.path.join(OUT_DIR, "ChainCoxswain_风险报告_%s.html" % ts)
    with open(path, "w", encoding="utf-8") as f:
        f.write("""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>ChainCoxswain 风险分析报告</title><style>
body{max-width:1100px;margin:40px auto;font:14px/1.7 -apple-system,'Microsoft YaHei',sans-serif;color:#22344a}
h1{font-size:26px}h2{margin-top:32px;border-bottom:2px solid #1ab6a7;padding-bottom:8px}
table{border-collapse:collapse;width:100%%}td,th{border:1px solid #dce7ee;padding:8px;font-size:12px}
th{background:#f1f7f7}small{color:#647990}</style></head><body>
<h1>供应链库存风险分析报告</h1>
<p>生成时间 %s · 数据来源 %s · 窗口 %d 天</p>
<h2>风险清单（前 30 项）</h2>
<table><tr><th>物料</th><th>名称</th><th>库存</th><th>日均用量</th><th>覆盖天数</th><th>等级</th><th>首次缺货</th></tr>%s</table>
<h2>概率风险与动态库存政策</h2>
<table><tr><th>物料</th><th>方法</th><th>7天概率</th><th>14天概率</th><th>30天概率</th><th>14天预计未满足</th><th>P95缺口</th><th>推荐SS</th><th>推荐ROP</th><th>库存位置</th><th>优先级</th></tr>%s</table>
<small>概率为 Monte Carlo 条件推演（固定种子可复现）；历史不足时标注"历史不足"，不提供虚假概率。优先级为规则分层（P0-P3），非加权综合分。</small>
<h2>假设与边界</h2><small>数字全部来自确定性库存仿真引擎；到货按日初入库；逾期未确认订单不计入到货；无 BOM/生产计划时不推断停产；所有行动需人工审核。</small>
</body></html>""" % (ts, _html.escape(data["meta"]["source"]), data["horizon"], rows, prob_rows))
    return path
