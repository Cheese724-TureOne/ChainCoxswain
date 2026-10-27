---
name: chaincoxswain-supply-risk
description: >-
  供应链库存风险分析协作技能。当用户在千问办公中要求分析库存风险、找出脆弱物料、
  运行压力测试、搜索缺货临界点、比较干预方案或生成供应链风险分析报告时使用。
  必须通过 ChainCoxswain MCP Server 的工具取得确定性计算结果，不得自行编造库存数字。
  生成的 Excel/Word/PPT 报告均为可编辑业务成果。
---

# ChainCoxswain 供应链风险分析（千问办公版）

本技能让千问办公通过 **ChainCoxswain MCP Server** 完成真实的供应链库存风险工作流：
读取数据 → 识别风险 → 自主压力测试 → 定位临界点 → 比较干预方案 → 生成可编辑业务文档。

## 一、适用场景

- 用户要求"分析当前库存风险""找出最脆弱的物料""运行压力测试""推荐应对方案""生成供应链风险分析报告"；
- 用户上传了库存/用量/采购数据，或要求使用 ChainCoxswain 已加载的数据集；
- 需要产出 Excel 风险表、Word 分析报告或 PPT 汇报材料。

## 二、前置条件与授权

1. ChainCoxswain MCP Server 已接入千问办公（stdio 或 HTTP 桥接）。连接状态以 MCP 客户端配置页为准，不得臆造"已连接"。
2. 本技能**只读分析 + 导出报告**。以下动作一律禁止直接执行，必须交给用户人工确认：
   - 实际下单、修改采购订单、通知供应商、调整库存、调拨。
3. 如果 MCP 调用失败，必须如实报告失败原因，**禁止编造风险结论替代工具结果**。

## 三、标准工作流（SOP）

按顺序执行，每步记录工具调用与结果：

1. **读取风险清单**：调用 `get_inventory_risk_list`，获取整体统计与 top N 脆弱物料。
2. **查询重点物料**：对清单前 2–4 项调用 `query_material`，确认库存、覆盖天数、下一笔到货、数据缺失项。
3. **执行自主压力测试**：调用 `run_autonomous_stress_test`（limit_skus 建议 5，horizon 14），获得：
   - 每个物料的测试顺序依据；
   - 到货延期 / 下单推迟 / 需求上涨三种扰动的逐轮搜索日志；
   - 每种扰动的缺货临界点（status: found / already / not-found / not-applicable）。
4. **查询风险临界点**（可选，对单个物料复核）：调用 `query_risk_threshold`。
5. **比较干预方案**：对最脆弱物料的重点情景调用 `compare_interventions`，比较催货、临时补货、需求平滑的效果（改善缺口量、是否消除缺货）。
6. **导出报告**：调用 `export_analysis_report` 生成 xlsx / docx / pptx，把文件路径交付用户；如用户需要正文内容，按 `report_template.md` 组织文字，所有数字必须引用工具结果。
7. **审计追溯**：如用户质疑结论，调用 `get_agent_tool_logs` 提供工具调用记录。

## 四、硬性规则（防幻觉）

- 缺货数量、缺货日期、库存余额、覆盖天数、临界点**只能引用工具返回结果**，禁止心算或编造。
- 工具返回 `not-applicable`（例如数据集无采购订单）时，如实说明"该数据集缺少采购数据，延期类测试不适用"，不得强行补一个数字。
- 数据性质必须区分：演示数据（虚构）/ 真实历史数据（保留历史业务日期，不冒充实时）/ 模拟采购订单（A.zip 无真实采购单时明确标记"模拟"）。
- 干预方案一律标注"需人工审核后执行"。
- 每轮测试必须说明**上一轮结果如何影响下一轮参数**（例如：延期 2 天未缺货、4 天缺货 → 二分取 3 天）。
- 报告中的数字必须可追溯到具体工具调用（工具名 + 参数 + 返回摘要）。

## 五、工具一览（ChainCoxswain MCP）

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `get_inventory_risk_list` | 库存风险清单 | limit |
| `query_material` | 查询物料明细 | sku |
| `run_autonomous_stress_test` | 自主压力测试 | limit_skus, horizon |
| `query_risk_threshold` | 单物料临界点 | sku, scenario_type, max_value |
| `compare_interventions` | 干预方案对比 | sku, scenario_type, value |
| `query_stockout_probability` | Monte Carlo 缺货概率（7/14/30 天、P95 缺口、首次缺货日分布） | sku, n, seed |
| `query_material_policy` | 动态安全库存/再订货点/库存位置/补货预警 | sku, service_level |
| `query_recovery_plan` | 反向干预：最小补货量/最晚到货/最小削减/组合方案 | sku, scenario_type, value |
| `query_joint_threshold` | 联合压力临界点（需求增长×供应延期，线性扫描） | sku, demand_pct, max_delay |
| `get_risk_dimensions` | 六维风险指标 + 规则分层优先级 P0-P3 | sku |
| `get_warehouse_risk` | 分仓（SKU+warehouse）风险与覆盖离散度 | sku, transship |
| `get_agent_tool_logs` | 工具调用审计 | limit |
| `export_analysis_report` | 导出报告 | format: xlsx/docx/pptx/html |

工具 schema 与参数校验由 MCP Server 提供，详见 `tools_reference.md`。

## 六、输出要求

- 结论段落遵循：**数据依据 → 测试过程 → 临界点 → 干预建议 → 边界声明**。
- 每个数字后标注出处，例如：`（get_inventory_risk_list 返回，asOf 2021-03-01）`。
- 生成文档必须可编辑：xlsx 用 openpyxl 真实生成（多工作表），docx/pptx 同理；不要交付占位链接。
- 结束语必须包含："以上为条件推演，非确定性预测；所有行动需人工审核。"

## 七、演示任务示例

用户原话：
> 分析当前库存风险，找出最脆弱的物料，自动运行压力测试，推荐应对方案，并生成供应链风险分析报告。

推荐执行路径见 `example_task.md`。
