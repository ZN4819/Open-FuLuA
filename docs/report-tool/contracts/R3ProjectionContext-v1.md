# R3ProjectionContext v1 交接契约

## 1. 契约身份

| 项目 | 固定值 |
| --- | --- |
| 上下文版本 | `1.0` |
| 规则集 | `report-derived-2023-2025.12.08-v1` |
| 模板包 | `report-2023-2025.12.08` |
| 机器可读 schema | `templates/report/contracts/2023-2025.12.08/r3_projection_context.v1.schema.json` |
| 取得接口 | `GET /api/projects/{project_uuid}/report/projection-context` |

本契约是 R3 向 R4 提供派生结果的唯一稳定边界。R4 不得直接查询 R3 内部表，也不得重新计算评分、结果修正、指标结论、风险数量、综合得分或评估结论。

## 2. 可用前置条件

接口只在以下条件同时成立时返回上下文：

1. 当前项目为 `full_report`，且 R2 权威数据通过校验。
2. 当前生成运行状态为 `current`，规则集 ID、规则集哈希和输入哈希仍匹配。
3. 固定 33 个派生正文块全部存在、未过期且已确认。
4. 风险记录数量和等级数量守恒，所有风险均已关联母版威胁并确认。
5. 当前项目 revision 已执行一致性校验，校验结果为 `valid`。
6. 上游六组源哈希、一致性上下文哈希和数据库保存的上下文哈希完全一致。
7. 输出中不存在 Ra、Rk 字段。

任何条件不满足都返回结构化错误，不沿用旧上下文。

## 3. 顶层结构

以下为可读结构示例；数组内容以机器 schema 和 R3 集成测试生成的完整 41 项上下文为准。

```jsonc
{
  "schema_version": "1.0",
  "generation_run_uuid": "...",
  "generation_state_revision": 2,
  "rule_set_id": "report-derived-2023-2025.12.08-v1",
  "rule_set_hash": "<sha256>",
  "input_hash": "<sha256>",
  "source_hashes": {
    "system_summary": "<sha256>",
    "report_facts": "<sha256>",
    "appendix_a": "<sha256>",
    "correction_relations": "<sha256>",
    "risks": "<sha256>",
    "special_indicators": "<sha256>"
  },
  "original_projection": {"rows": [], "indicators": ["固定41项"]},
  "correction_projection": {"rows": [], "render_empty_as_slash_row": true},
  "final_projection": {
    "rows": [],
    "indicators": ["固定41项"],
    "statistics": {"layers": ["固定8层"], "total": {"indicator_total": 41}},
    "score": {"raw_score": "100.0000", "display_score": "100.00", "rounding": "ROUND_HALF_UP", "layers": []}
  },
  "projection_hash": "<sha256>",
  "findings": [],
  "risk_snapshot": {
    "risk_total": 0,
    "counts": {"high": 0, "medium": 0, "low": 0},
    "overall_risk": "未发现安全风险",
    "high_risk_judgment": "判定系统不存在高风险",
    "rows": []
  },
  "assessment_conclusion": {
    "display_score": "100.00",
    "conclusion": "符合",
    "overall_risk": "未发现安全风险",
    "high_risk_judgment": "判定系统不存在高风险"
  },
  "blocks": ["固定33个已确认正文块"],
  "threat_catalog": ["固定24项母版威胁目录"],
  "consistency": {
    "status": "valid",
    "issues": [],
    "context_hash": "<sha256>",
    "state_revision": 35
  },
  "project_revision": 35
}
```

`consistency.context_hash` 的计算范围为除 `consistency`、`project_revision` 外的全部上下文字段。R4 应保存该哈希和 `generation_run_uuid`，用于导出快照及同源回收。

## 4. 三份结果投影

- `original_projection`：修正前对象、单元和指标结果；R4 用于表 4-1～表 4-11。
- `correction_projection`：只包含最终分值大于原始分值的实际修正对象；空集合时 `render_empty_as_slash_row=true`，R4 仍输出标题行和内容为 `/` 的一行。
- `final_projection`：修正后对象、单元、指标、8 层/41 项统计和综合得分；R4 用于表 5-2、最终附录 A、总体评价、风险和第 7 章结论。

发生实际修正的最终对象行携带 `was_corrected=true`、`original_object_score`、`final_object_score` 和修正来源。R4 在附录 A 中将该对象的 D/A/K 显示为 `/`，对象评分显示为四位小数加 `*`；上下文本身不追加 Word 显示字符。

## 5. 固定正文块

上下文固定输出 33 个块：

- 评估结论页：`conclusion.system_summary`、`conclusion.assessment_summary`。
- 总体评价：`overall_evaluation.intro`、`overall_evaluation.layer.1..8`、`overall_evaluation.outro`。
- 安全问题：`security_issues.intro`、`security_issues.layer.1..8`。
- 改进建议：`recommendations.intro`、`recommendations.layer.1..8`。
- 风险分析：`risk_analysis.summary`、`risk_analysis.rows`。
- 第 7 章：`assessment_conclusion`。

每个块只向 R4 暴露 `block_uuid`、`block_key`、`effective`、`rule_id` 和 `source_hash`。`effective` 已合并经确认的人工版本；R4 不读取基线和覆盖历史。没有问题指标的安全层面仍保留稳定块，但 `effective.visible=false`，装配时不显示该层面。

## 6. 源哈希与失效边界

| 源组 | 内容 | 主要下游 |
| --- | --- | --- |
| `system_summary` | 第 2 章系统完整描述 | 评估结论页系统简介 |
| `report_facts` | 系统名称、有效委托单位、被测单位、测评日期、等级 | 引用相应事实的固定正文 |
| `appendix_a` | 附录 A 原始记录、对象、D/A/K/Ra/Rk 或管理符合情况 | 三投影、统计、问题、风险、得分和结论 |
| `correction_relations` | A-2/A-4 原始修正关系 | 修正投影及全部最终结果下游 |
| `risks` | 风险等级、威胁关联、人工分析和确认状态 | 风险统计、风险正文、高风险判断和结论 |
| `special_indicators` | 人工标准和特殊指标 | 总体评价中特殊指标引用 |

Ra、Rk 只参与 `appendix_a` 内部摘要和后端权威评分，不作为公开字段返回。

## 7. 稳定错误码

### 7.1 R4 取得上下文时的阻断码

| 错误码 | HTTP | 含义与处理 |
| --- | --- | --- |
| `RULE_SET_UNAVAILABLE` | 503 | 规则资产缺失、哈希或黄金向量失败；停止装配，不返回本地路径。 |
| `R3_CONTEXT_NOT_AVAILABLE` | 422 | 尚未成功生成上下文；回到 R3 生成。 |
| `R3_CONTEXT_STALE` | 422 | 上游事实、规则或生成运行已变化；重新生成并复核。 |
| `R3_CONTEXT_NOT_CONFIRMED` | 422 | 正文块未确认、已过期，或当前 revision 未通过一致性校验。 |
| `R3_CONTEXT_HASH_MISMATCH` | 500 | 保存的上下文哈希不一致；停止导出并保留诊断证据。 |
| `R3_CONTEXT_SCHEMA_INVALID` | 500 | 输出不符合 v1 契约；停止导出。 |
| `R3_PRIVATE_FACTOR_LEAK` | 500 | 检测到 Ra/Rk 公开字段；停止导出。 |

### 7.2 生成运行的 `needs_input` 问题码

生成运行可返回 `SCORING_INPUT_INVALID`、`INDICATOR_DATA_MISSING`、`INDICATOR_CATALOG_MISMATCH`、`ASSESSMENT_OBJECT_BINDING_MISSING`、`CORRECTION_METRIC_PAIR_INVALID`、`CORRECTION_RELATION_CARDINALITY`、`CORRECTION_ORIGINAL_REFERENCE_STALE`、`RISK_LEVEL_REQUIRED`、`RISK_THREAT_REQUIRED`、`RISK_CONFIRMATION_REQUIRED`、`RISK_COUNT_INVARIANT_FAILED` 或 `NARRATIVE_FACT_MISSING`。这些问题不会提交半成品为当前上下文。

并发写入使用 `PROJECT_REVISION_CONFLICT` 或 `REVISION_CONFLICT`；人工正文使用 `BLOCK_OVERRIDE_NOT_ALLOWED`、`BLOCK_OVERRIDE_REASON_REQUIRED`、`BLOCK_OVERRIDE_SCHEMA_INVALID`、`BLOCK_OVERRIDE_TEXT_INVALID` 和 `DERIVED_BLOCK_STALE`。

## 8. R4 消费规则和测试夹具

- R4 请求上下文后，应校验 `schema_version`、规则集身份、`consistency.status=valid`、`consistency.state_revision=project_revision` 及 `context_hash`。
- 装配期间不得再次读取可变 R3 业务表；上下文和同 revision 的 R2 上下文共同组成不可变 `ReportAssemblyContext`。
- JSON Schema 随 PyInstaller 资源白名单打包；桌面端缺失该资产视为构建失败。
- `tests/test_r3_report_generation.py::test_complete_projection_score_and_context_exclude_private_factors` 是完整 41 项接口夹具，实际生成、确认、校验并回读 v1 上下文。
- `tests/test_r3_report_generation.py::test_projection_context_schema_asset_matches_runtime_contract` 保证机器 schema 与运行时顶层字段、源哈希组、33 块和 24 项威胁目录同步。
