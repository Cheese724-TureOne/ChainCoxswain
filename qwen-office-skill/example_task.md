# ChainCoxswain 示例任务：完整执行路径（千问办公）

## 用户指令

> 分析当前库存风险，找出最脆弱的物料，自动运行压力测试，推荐应对方案，并生成供应链风险分析报告。

## 千问办公执行路径（含工具调用序列）

### 第 1 步：读风险清单
```
tools/call get_inventory_risk_list {"limit": 5}
```
→ 得到 counts 与 top5。记住 asOf（若为 A.zip 数据集则 asOf=2021-03-01，属历史快照）。

### 第 2 步：查询前 2 个最脆弱物料
```
tools/call query_material {"sku": "<top1.sku>"}
tools/call query_material {"sku": "<top2.sku>"}
```
→ 确认覆盖天数、下一笔到货、缺失字段（如 supplier="未提供"）。

### 第 3 步：自主压力测试
```
tools/call run_autonomous_stress_test {"limit_skus": 5, "horizon": 14}
```
→ 返回 log（选择依据 + 每轮参数 + 是否缺货）与 tested（每种扰动的临界点）。

### 第 4 步：对最脆弱物料复核临界点（可选）
```
tools/call query_risk_threshold {"sku": "<top1.sku>", "scenario_type": "deliveryDelay"}
```

### 第 5 步：比较干预方案
```
tools/call compare_interventions {"sku": "<top1.sku>", "scenario_type": "<focusScenario.type>", "value": <focusScenario.value>}
```
→ 催货 / 临时补货 / 需求平滑的效果对比。

### 第 6 步：生成业务文档
```
tools/call export_analysis_report {"format": "xlsx"}   # 可编辑 Excel 风险表
tools/call export_analysis_report {"format": "docx"}   # Word 分析报告
tools/call export_analysis_report {"format": "pptx"}   # PPT 汇报材料
```
→ 将生成的文件路径/下载链接交给用户；正文按 report_template.md 组织，数字全部引用上述工具返回。

### 第 7 步：审计（用户质疑时）
```
tools/call get_agent_tool_logs {"limit": 50}
```

## 结果演示要点（比赛展示用）

- 展示第 3 步 log 中"延期 1→2→4→3 天"的逐轮记录，证明参数由上一轮结果决定；
- 展示临界点：延期 2 天不缺货、3 天缺货（经典样例：库存 80、日耗 10、第 7 天到货 100）；
- 展示第 6 步生成的真实 xlsx/docx/pptx 文件（可打开、可编辑）；
- 明确说明：真实客户端联调状态以实际配置为准；本地 MCP 工具已通过自动化测试。
