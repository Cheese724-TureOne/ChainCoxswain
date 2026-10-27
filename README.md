# ChainCoxswain（链舵）v2 — 供应链智能风险决策系统

基于 **千问大模型（Function Calling）+ 确定性库存仿真引擎 + 千问办公（MCP/Skill）协作** 的供应链库存风险压力测试与决策系统。

- 千问 Agent 自主决定：测试哪些物料、测试哪种扰动、每轮用什么参数、何时停止；
- 所有缺货数量 / 日期 / 库存余额由**确定性引擎**（`engine.js` / `backend/engine.py` 同构实现）计算，大模型只做规划与解释，**不编造数字**；
- 千问办公通过可运行的 **ChainCoxswain MCP Server**（13 个工具）+ 专属 **Skill** 完成"分析风险 → 压力测试 → 推荐方案 → 生成 Excel/Word/PPT"的真实业务工作流；
- 真实数据 / 历史数据 / 假设数据全程区分标注；业务动作一律人工审核，v1 无自动下单能力。

---

## 1. 目录结构

```
ChainCoxswain_Pro/
├── index.html            # 单页入口（PC / 手机自适应）
├── app.js                # 前端：4 个核心模块 + Agent 面板 + 千问办公协作 + 对照实验
├── style.css             # 全部样式（含响应式断点 1180px / 760px）
├── engine.js             # 确定性库存仿真引擎（前端离线演示用）
├── assets/jszip.min.js   # 前端 Excel/CSV 解析
├── backend/
│   ├── server.py         # Flask 后端（REST + MCP HTTP 桥接 + 静态托管）
│   ├── engine.py         # 确定性仿真引擎（Python 同构实现，后端唯一算数来源）
│   ├── service.py        # ChainCoxswainService：共享业务服务（工具 + 日志审计）
│   ├── qwen_agent.py     # 千问 Agent：规划→调用→观察→再规划 闭环（含规则演示降级）
│   ├── mcp_server.py     # ChainCoxswain MCP Server（JSON-RPC 2.0，stdio + HTTP 桥接）
│   ├── data_a.py         # A.zip 真实历史数据适配（无泄漏 train/test 切分）
│   ├── compare.py        # 四组对照实验（G1 静态 / G2 固定 / G3 规则 / G4 Agent）
│   └── report_gen.py     # 可编辑 Excel / Word / PPT / HTML 报告生成
├── qwen-office-skill/    # 千问办公专属 Skill（SKILL.md、SOP、数据字典、工具参考、报告模板）
├── tests/                # pytest 自动化测试（53 项，全部通过）
├── data/A.zip            # 比赛真实历史数据（632 unit，快照 2021-03-01）
├── demo_data.xlsx        # 演示数据说明模板
├── docs/                 # 演示脚本、交付说明
├── start.bat / start.sh  # 一键启动
├── .env.example          # 环境变量样例（API Key 只放这里/系统环境，绝不下发前端）
└── requirements.txt
```

## 2. 快速开始

### 2.1 纯离线演示（无需任何 Key，无需后端）
直接用浏览器打开 `index.html` 即可：
- 首页仪表盘、数据导入（CSV/Excel 上传）、自主压力测试、手动情景模拟四模块全部可用（引擎在浏览器内运行）；
- 需要后端的功能（千问 Agent、A.zip、MCP、报告导出、对照实验）会自动提示"后端未连接"。

### 2.2 完整功能（后端）
```bash
# 1) 安装依赖（国内可用阿里镜像）
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 2) 配置千问 API Key（可选；不配 = 规则演示模式）
copy .env.example .env        # Windows
cp .env.example .env          # macOS / Linux
# 编辑 .env，填入 DASHSCOPE_API_KEY=sk-xxx

# 3) 启动
start.bat                     # Windows
sh start.sh                   # macOS / Linux
# 或直接：python backend/server.py
```
- 后端地址：`http://127.0.0.1:8765`（可用 `CHAINCOXSWAIN_PORT` 修改）
- 打开 `index.html`（file:// 即可，前端默认请求 127.0.0.1:8765；部署到其他地址时在页面里设置 `window.CHAINCOXSWAIN_API_BASE`）
- 健康检查：`GET /api/health` → 返回服务版本、千问状态、数据集、7 个 MCP 工具名

### 2.3 千问 API 配置说明
| 变量 | 说明 | 默认 |
|---|---|---|
| `DASHSCOPE_API_KEY` | 阿里云百炼 API Key（OpenAI 兼容模式） | 空 → 规则演示模式 |
| `QWEN_MODEL` | 模型名，如 `qwen-plus` / `qwen-turbo` / `qwen-max` | `qwen-plus` |
| `CHAINCOXSWAIN_PORT` | 后端端口 | `8765` |

- 调用端点（代码内置）：`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- **Key 只存在于服务器环境变量**，`/api/qwen/status` 只返回"已配置/未配置 + 掩码"，绝不返回明文，也不下发到浏览器；
- 未配置 Key 时，Agent 面板自动切换为**明确标记的"规则演示模式"**（同一套引擎、同样的逐轮记录，推理文字由固定规则生成，页面有醒目标注）；
- 千问调用失败（超时/限流/网络）时自动降级为"规则演示模式（千问调用失败自动降级）"并在结果中保留 `agentError` 原文。

## 3. 功能模块

### 模块 A：真实数据处理
- 保留 CSV / Excel 上传；新增 **A.zip 适配**：正确识别 `inventory_info_A`（库存快照）/ `use_train_A` / `use_test_A` / `product_topo_A` / `geo_topo_A` / `weight_A` 及其关联键（unit 编码 ≠ 物料 SKU，产品/地理层级独立映射）；
- **通用映射导入向导**（数据工作台 06 卡片）：任意公司格式无需改代码——自动识别每列含义（中/英文、混合表头、别名 + 值特征打分，一列只认领一次），带**辅助确认**：必填字段才需确认（带置信度徽章），可选字段折叠收起，未识别字段给出"建议列"并可一键「按建议补全」，下拉选项按匹配度排序（第一项即最佳猜测）；支持三种形态：物料宽表 / 采购单表 / **明细长表**（按物料自动聚合：库存取结存列最后一天值、日均用量 = 出库量 ÷ 日期跨度，缺省假设全部显式标注）；**支持 ZIP 数据包**：拖入后本地自动拆包（不上传服务器），逐个文件识别角色（物料表/采购单表/明细长表），说明/字典/模板与不支持的文件自动跳过，自动勾选最佳组合（物料表+采购单表）后一键导入，可勾选调整；传统模板导入失败时会提示改走向导；
- **保留真实业务日期 2021-03-01**，页面明确标注"历史数据快照"，不冒充实时库存；
- A.zip 不含采购订单/供应商/到货日期：相关功能提示缺失，或使用**单号 `SIM-PO-A*`、字段 `simulated:true` 显式标记**的模拟订单，**不编造真实供应商与到货信息**；
- 历史回测无未来泄漏：用量估计只用 train 期（≤2021-03-01），回测重放只用 test 期（2021-03-02 起）——由 `tests/test_backtest.py` 断言；
- **关系型数据集适配**（`data/relational/`，inventory-supply-chain v1.0.0 合成教学数据）：8 张规范化表（products/suppliers/warehouses/opening_balances/movements/purchase_orders/purchase_order_lines）经**确定性 join+聚合**（`backend/data_rel.py`，不依赖 LLM 清洗）适配为 2500 物料 + 1163 条真实在途采购单：快照日 = 2025-12-15（数据集最后一笔下单日）、库存 = 期初+截至快照日流水净变化（无未来泄漏）、用量 = 快照日前 91 天 sale 日均、交期/供应商/单价来自真实 join；数据集为 synthetic 教学数据，**dataKind 标 demo，不冒充真实数据**。

### 模块 B：真实千问 Agent（Function Calling 闭环）
- 8 个工具：`get_risk_list` / `query_material` / `run_simulation` / `search_threshold` / `compare_interventions` / `get_tool_history` / `explain`（+ 系统注入的 7 个服务工具）；
- 实现真实的 **规划 → 调用工具 → 观察结果 → 调整计划 → 再次调用** 循环：每一轮记录模型推理文字、工具参数、工具结果；工具结果作为 `tool` 消息回传给模型，因此**下一轮参数可以依赖上一轮结果**（`tests/test_agent.py::test_adaptive_mock_next_round_depends_on_previous_result` 验证了这一点）；
- 运行护栏：`maxRounds` / `maxToolCalls` / `timeoutSec` / `budgetTokens` 硬约束 + 工具参数校验 + 错误回喂（`约束满足率` 在结果中如实展示）；
- 临界点搜索由引擎完成（倍增试探 + 整数二分，逐轮日志可审计），千问只决定"测谁、测什么、下一步怎么调"。

### 模块 C：千问办公深度协作（MCP + Skill）
> 千问办公 QwenWork ≠ 千问大模型 API。本模块是独立的可运行集成。

1. **ChainCoxswain MCP Server**（`backend/mcp_server.py`，JSON-RPC 2.0，协议 2024-11-05），13 个工具：

| 工具 | 作用 |
|---|---|
| `get_inventory_risk_list` | 库存风险清单（排序 + 总体统计 + 首次缺货/累计缺口） |
| `query_material` | 查询重点物料（库存/用量/覆盖/交期假设/下一笔到货） |
| `run_autonomous_stress_test` | 自主压力测试（自动筛选 + 逐轮临界点搜索日志） |
| `query_risk_threshold` | 查询指定扰动的缺货临界点（倍增 + 二分逐轮日志） |
| `compare_interventions` | 比较催货/临时补货/需求平滑等干预预案 |
| `get_agent_tool_logs` | Agent 工具调用审计记录（调用方/参数/结果/耗时） |
| `export_analysis_report` | 导出 xlsx / docx / pptx / html 报告 |

   两种接入传输：**stdio**（`python backend/mcp_server.py data`）与 **HTTP 桥接**（`POST /api/mcp`）。业务动作（下单/通知供应商/调库存）**刻意不暴露**。

2. **千问办公专属 Skill**（`qwen-office-skill/`）：`SKILL.md`（技能定义与 SOP）、`data_dictionary.md`（数据字典）、`tools_reference.md`（MCP 工具用法与接入配置）、`example_task.md`（完整任务路径）、`report_template.md`（报告模板）。

3. **工作流**：用户在千问办公提出
   > "分析当前库存风险，找出最脆弱的物料，自动运行压力测试，推荐应对方案，并生成供应链风险分析报告。"
   
   千问办公调用 MCP 工具取得**真实计算结果**（不是模型自己编数），再用 `export_analysis_report` 生成**可编辑的 Excel 风险表 / Word 分析报告 / PPT 汇报材料**（openpyxl / python-docx / python-pptx 生成，落盘 `exports/`，可二次编辑）。

4. **前端"千问办公协作"模块**：MCP/Skill 配置说明与连接状态、分析任务与运行记录、可复制的任务指令、导出结构化任务包（`GET /api/task/package`）、数据来源/假设/审核状态标注。

> 如实说明：**没有真实千问办公账号时，已完成全部 MCP 工具的本地自动化测试（stdio + HTTP 桥接），真实客户端联调状态标注为"尚未完成"，系统不假装已连接。**

### 模块 D：前端体验
保留数据总览 / 数据导入 / 自主压力测试 / 手动情景模拟四个核心模块，新增：
- 千问 Agent 决策面板：目标/预算参数、逐轮时间线（计划 → 工具调用 → 观察 → 下一步）、工具调用日志（参数/结果/耗时）、结论卡片（最脆弱物料 / 临界点 / 干预方案）——评委能看到**为什么测这个物料、上一轮发现了什么、为什么下一轮改参数、每个结论来自哪个工具**；
- 风险临界点可视化（阈值条带 + SKU 标签）；
- 预防/应急方案效果对比；四组对照实验页（G1/G2/G3/G4 检测率/临界点误差/调用数/耗时/约束满足率/可复现性，含 6 个物料的地面真值表）；
- 数据质量与来源标识（演示/历史/模拟 pill、缺失字段、质量提示）；人工审核与报告导出（HTML 打印 / 后端 xlsx/docx/pptx）；
- PC 与手机自适应（1180px / 760px 断点）。

### 模块 E：验证与对照实验
- 经典样例（回归基准）：库存 80、日用量 10、第 7 天到货 100 → 延期 2 天不缺货；延期 3 天第 9 天首次缺货 10 件；临界点 = 3（`tests/test_engine.py`）；
- 四组对照：G1 静态预警 / G2 固定参数单次测试 / G3 规则自适应 / G4 千问 Agent 多轮；G4 的调用预算 = G3 实测调用数 + 每物料固定开销 + 余量（公平预算）；临界点误差以穷举搜索为地面真值；
- 测试矩阵：Mock Agent、错误参数、无采购数据、MCP 调用、A.zip 历史数据（见下节）。

## 4. 自动化测试

```bash
PYTHONIOENCODING=utf-8 python -m pytest tests -q
# 80 passed（含 A.zip 加载、关系型数据集适配、MCP 子进程 stdio 往返、四组对照、14 项升级测试、V1/V2/V3 评估）
```

| 文件 | 覆盖 |
|---|---|
| `test_engine.py`（13） | 经典样例、延期 2/3 天、临界点=3、M-002 需求 +22%、逾期订单、断供、干预方案、日期边界 |
| `test_backtest.py`（7） | A.zip 加载、632 unit、模拟订单标记、无 NaN、train/test 无泄漏、回测重放、风险摘要诚实性 |
| `test_agent.py`（12） | MockLLM 闭环、下一轮依赖上一轮结果、未知工具/坏参数/非法 JSON 容错、无采购数据 not-applicable、无 Key 规则演示、强制模式、预算硬约束、异常自动降级 |
| `test_mcp.py`（11） | initialize 握手、恰好 13 个工具、真实工具调用、未知工具/缺参 isError、审计日志、JSON-RPC 错误码、**stdio 子进程往返** |
| `test_report.py`（5） | xlsx 5 张表（含概率与政策）/ docx / pptx / html 可生成且可重新打开、人工审核字样、非法格式报错 |
| `test_compare.py`（5） | 四组结构、G3/G4 检测率 100% 且临界点误差 0、G1/G2 无临界点、公平预算、完全可复现 |
| `test_rel.py`（6） | 关系型数据集适配：2500 物料、快照 2025-12-15、无未来泄漏、无 NaN/负库存、在途/逾期单拆分、供应商真实 join、demo 标注、服务集成 |
| `test_upgrades.py`（14） | 任务书第五节 1–14：计划采购不进基准、取消单排除、部分收货余量、多明细不丢、overrideOrders=[]、双端一致、序列叠加增长、SS/ROP 公式、MC 种子复现、缺历史回退、历史回放无未来收货、基准缺货找最小干预、数据版本一致、经典回归 |
| `test_eval_versions.py`（2） | V1/V2/V3 时间顺序评估（真实流水标签，正标签为 0 时如实不计算召回）+ 零售 7 天窗回退一致性 |

可选 JS 侧检查（需要 `tools-node.exe` 或本机 Node）：
```bash
tools-node.exe --check app.js engine.js
```

## 5. 千问办公接入说明（真实客户端联调步骤）

1. **启动后端**（`start.bat`），确认 `GET /api/health` 返回 `mcpTools` 7 项；
2. **安装 Skill**：将 `qwen-office-skill/` 目录导入千问办公的技能库（或按千问办公的 Skill 管理入口上传）；
3. **接入 MCP Server**，两种方式：
   - stdio（推荐）：
     ```json
     {
       "mcpServers": {
         "chaincoxswain-mcp": {
           "command": "python",
           "args": ["backend/mcp_server.py", "data"],
           "cwd": "<本仓库根目录绝对路径>"
         }
       }
     }
     ```
   - HTTP 桥接（无 stdio 权限时）：`POST http://127.0.0.1:8765/api/mcp`，JSON-RPC 2.0 请求体；
4. **本地自检**（无千问办公账号也能做）：`python -m pytest tests/test_mcp.py -q` 或打开前端"千问办公协作"模块逐工具点测；
5. **发起任务**：粘贴任务指令（前端可一键复制，或 `qwen-office-skill/example_task.md`）；
6. 千问办公执行 → 调 MCP 工具取数 → `export_analysis_report` 生成 `exports/ChainCoxswain_*.xlsx/.docx/.pptx` → 下载二次编辑；
7. **联调状态如实标注**：未在真实千问办公客户端验证前，页面上保持"真实客户端联调尚未完成"标记。

## 6. 数据与安全边界（务必遵守）

- 真实数据（A.zip 历史快照 2021-03-01）、演示数据（虚构）、假设数据（安全库存/交期默认值）在界面与报告中全程区分标注；
- 千问模型**只做决策规划与解释**，任何数字都有工具结果出处；结论页声明"结论数字全部来自工具返回结果"；
- 所有下单/通知供应商/调库存动作必须人工确认，**v1 不自动执行**（MCP 不暴露业务动作工具）；
- API Key 不出后端；报告落盘 `exports/`（已在 `.gitignore` 忽略）。

## 7. 常见问题

- **端口被占用**：`netstat -ano | findstr :8765` 找到 PID 后 `taskkill //F //PID <pid>`，或改 `CHAINCOXSWAIN_PORT`；
- **前端提示后端未连接**：确认后端已启动、`window.CHAINCOXSWAIN_API_BASE` 指向正确地址（默认 `http://127.0.0.1:8765`）；
- **Agent 显示规则演示模式**：`.env` 未配置 `DASHSCOPE_API_KEY`，或千问调用失败自动降级（页面会显示原因）；
- **A.zip 加载失败**：确认 `data/A.zip` 存在；该数据集无采购订单，延期类测试会返回 `not-applicable` 或 `already(0)`，这是真实数据状态而非 bug。
