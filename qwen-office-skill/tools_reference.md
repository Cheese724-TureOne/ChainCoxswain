# ChainCoxswain MCP 工具参考（千问办公）

MCP Server 名称：`chaincoxswain-mcp` v1.0.0，协议 2024-11-05。
两种接入方式：

1. **stdio（推荐）**：由千问办公/客户端启动子进程
   ```
   python backend/mcp_server.py <数据目录绝对路径>
   ```
   可选环境变量 `CHAINCOXSWAIN_DATASET=demo|azip|relational` 指定启动时加载的数据集（demo=演示数据，azip=A.zip 历史数据，relational=关系型合成数据集；默认 demo）。
2. **HTTP 桥接**：向 ChainCoxswain 后端 `POST http://127.0.0.1:8765/api/mcp` 发送 JSON-RPC 2.0 请求（用于无 stdio 权限的客户端与本地联调测试）。HTTP 桥接与页面共享同一内存服务：页面加载的数据集（演示/A.zip/关系型），MCP 工具立即读到同一份数据。

## JSON-RPC 流程

```
-> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"qwen-office","version":"1.0"}}}
<- {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"chaincoxswain-mcp","version":"1.0.0"}}}

-> {"jsonrpc":"2.0","method":"notifications/initialized"}
-> {"jsonrpc":"2.0","id":2,"method":"tools/list"}
-> {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_inventory_risk_list","arguments":{"limit":5}}}
<- {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{...JSON 结果...}"}],"isError":false}}
```

## 十三个工具的参数与返回（7 经典 + 6 升级算法）

### 1. get_inventory_risk_list
- 入参：`limit`（int，1–200，默认 20）
- 返回：`{asOf, horizonDays, total, counts:{critical,watch,excess,normal}, top:[{sku,name,level,stock,dailyUsage,coverageDays,firstShortage,totalUnmet,reason}]}`
- 数字由确定性逐日库存仿真计算。

### 2. query_material
- 入参：`sku`（必填）
- 返回：`{sku,name,stock,dailyUsage,safety,leadTimeDays,supplier,coverageDays,baselineLevel,baselineReason,nextExpectedArrival,replenishmentPoint,dataKind,sourceNote}`
- 缺失字段（如供应商）返回 "未提供"，不编造。

### 3. run_autonomous_stress_test
- 入参：`limit_skus`（1–15，默认 5）、`horizon`（1–365，默认 14）
- 返回：`{today,horizon,totalRounds,log:[...],tested:[{sku,name,level,focusScenario,thresholds:{deliveryDelay|purchaseDelay|demandIncrease:{status,threshold,rounds}},impact:{firstShortage,totalUnmet}}]}`
- `log` 记录每轮：物料选择依据、测试顺序、每轮参数与"是否触发缺货"。

### 4. query_risk_threshold
- 入参：`sku`、`scenario_type`（deliveryDelay | purchaseDelay | demandIncrease）、`max_value`（1–500，默认 30 天 / 200%）
- 返回：`{sku,scenarioType,unit,status(found|already|not-found|not-applicable),threshold,rounds,roundLog,firstShortageAtThreshold,totalUnmetAtThreshold}`

### 5. compare_interventions
- 入参：`sku`、`scenario_type`、`value`、`horizon`（默认 14）
- 返回：`{sku,scenario,interventions:[{id,title,action,note,improvementUnits,resolvesShortage,dataRequired,simulatedTotalUnmet,simulatedFirstShortage}]}`
- `dataRequired=true` 表示缺备用供应数据，方案不量化。

### 6. get_agent_tool_logs
- 入参：`limit`（1–500，默认 50）
- 返回：`{total,records:[{id,ts,tool,caller,args,ok,error,durationMs,summary}]}`

### 7. export_analysis_report
- 入参：`format`（xlsx | docx | pptx | html，默认 xlsx）
- 返回：`{format,path,filename,sizeBytes,content:{riskCounts,testedSkus,totalRounds},note}`
- xlsx 含 5 个工作表：库存风险清单 / 概率与政策 / 风险临界点 / 干预方案 / 数据来源与假设。

### 8. query_stockout_probability
- 入参：`sku`、`n`（默认 1000）、`seed`（默认 42）
- 返回：`{available,sku,n,seed,method,horizons:{"7"/"14"/"30":{stockoutProbability,expectedUnmet,p95Unmet}},firstShortageDay:{p10,p50,p90,probability},belowSafetyProbability,note}`
- `available=false` 时仅返回 `scenarioOnly` 情景分析与 `reason`，**禁止把情景当概率引用**。

### 9. query_material_policy
- 入参：`sku`、`service_level`（0.90/0.95/0.99，默认 0.95）
- 返回：`{available,muD,sigmaD,leadMean,leadSigma,z,recommendedSafety,recommendedROP,inventoryPosition,onHand,inboundConfirmed,needsReorder,belowROP,notes,fallbackReason}`
- 公式 SS=z*sqrt(L̄σD²+μD²σL²)、ROP=μD*L̄+SS；历史不足 14 天时 `available=false` 并回退。

### 10. query_recovery_plan
- 入参：`sku`、`scenario_type`、`value`、`horizon`
- 返回：`{replenishment,expedite,demandReduction,combined,feasibleOptions,recommended,note}`；不可行方案带 `feasible=false` 与原因，不得引用为可行建议。

### 11. query_joint_threshold
- 入参：`sku`、`demand_pct`、`max_delay`、`horizon`
- 返回：`{status,threshold,demandPct,rounds,monotone,result,note}`；联合情景线性扫描，`monotone` 为实测标记。

### 12. get_risk_dimensions
- 入参：`sku`
- 返回：`{sku,dimensions:{shortage,volatility,fulfillment,excess,warehouse,criticality},priority:{tier,rules,note},dataVersion}`。

### 13. get_warehouse_risk
- 入参：`sku`（可选）、`transship`（可选 {enabled,leadDays,costPerUnit}）
- 返回：`{sku,units:[{sku,warehouseId,level,stock,usage,coverageDays,firstShortage,totalUnmet,otherWarehouseStock,otherStockUsable,note}],dispersion,note,dataVersion}`。

## 错误处理约定

- 参数不合法 → 返回 `{"error":"工具调用失败: ..."}`，`isError=true`；
- 数据集未加载 → 使用内置演示数据并在返回中标明 `dataKind=demo`；
- 无采购订单数据集执行延期类搜索 → `status=not-applicable`（非错误）。
