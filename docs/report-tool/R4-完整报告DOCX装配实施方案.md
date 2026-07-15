# R4 完整报告 DOCX 装配实施方案

> 状态：未实施。本文档为开发实施基线，不代表功能已交付。

## 1. 阶段定位

R4 负责把结构化完整报告数据、现有附录 A 数据和 R0 运行时母版装配为一份可审阅或可正式交付的完整 DOCX。它是“数据事实源”到“报告投影”的核心阶段。

本阶段不把 Word 当数据库。DOCX 中重复出现的系统名称、统计数量、分数和结论必须从同一结构化字段或派生结果生成，禁止由各章节分别保存一份可独立修改的副本。

### 1.1 复杂度

- 复杂度：极高。
- 风险：动态表格破版、分节继承错误、图片关系冲突、书签冲突、字段缓存过期、正文与附录分数不一致、未完成内容误导出正式版。
- 实施方式：必须拆分工作包，先结构装配，再业务表，再附录，再字段刷新和最终闸门。

### 1.2 前置依赖

- R0 已交付并验收运行时模板、字段字典、manifest、rule hints、叙述模板和资产哈希。
- 完整报告项目数据域已至少覆盖报告信息、单位、系统、资产、范围、对象、单元汇总、风险和附录 B 元数据。
- `project_type`、`workflow_status` 和模板绑定已经落库。
- 当前附录 A 生成、评分、图片、题注、书签和交叉引用测试保持通过。
- Windows 正式版需要可用的 Microsoft Word 自动化；无 Word 环境只允许生成草稿。

## 2. 统一契约

```text
project_type    = appendix_a | full_report
workflow_status = draft | ready_for_review | confirmed
template_edition = 2023
template_revision = 2025-12-08
export_mode     = draft | final
import_mode     = migration | roundtrip
```

硬约束：

- 完整报告导出只接受 `project_type=full_report`。
- `draft` 可在数据未完全确认时生成，但必须有显式草稿标识和问题清单。
- `final` 只允许 `workflow_status=confirmed`。
- 对外正式输出仅包括本阶段生成的 DOCX 和现有独立接口生成的 XLSX，不提供 PDF 交付或下载。
- 批注提取规则只生成警告，不得单独阻止 final；数据完整性、结构、评分和未确认状态可以阻止 final。
- Ra/Rk 继续参与内部评分和 XLSX，但任何 Word 生成路径不得写入 Ra/Rk。
- R4 只生成报告，不回收用户在 Word 中的修改；回收属于 R7。

## 3. 经仓库验证的当前基线

- 当前 `generate_project_docx()` 从空白文档生成 8 个横向分节和 8 张附录 A 表。
- 当前导出模式为 `editable|final`，前者保留下拉内容控件，后者扁平化。
- 当前生成器可生成表格几何、内容控件、图片、题注、SEQ/REF 和书签，并在保存后检查 8 分节、8 表和 REF 目标。
- 当前完整报告基础模板为 17 分节、55 张表；不能沿用“8 分节、8 表”的验收常量。
- 55 张表中既有固定规范表，也有动态资产表、对象表、汇总表、风险表、附录 A 表和附录 B 证据表。
- 客户项目证明动态行数可能远超基础模板，例如 A-2 可达到 167 行、网络对象表可达到 34 行。
- 当前评分后端已权威计算技术和管理分数；XLSX 导出已有完整校验。
- 完整报告 DOCX 的结构分析、字段刷新、分页和交付验收只使用 Microsoft Word。既有 LibreOffice 预览能力不作为完整报告工具的判断依据。

## 4. 目标与非目标

### 4.1 目标

1. 从 R0 运行时模板装配 17 分节完整报告。
2. 对 manifest 中 55 张表逐表指定渲染策略并完成覆盖。
3. 复用附录 A 数据、评分、图片和叙述模板，不复制一套附录数据模型。
4. 保证正文单元汇总、整体评分、风险统计、附录 A 和 XLSX 同源。
5. 支持草稿版和最终版两种明确语义。
6. 对最终版执行强校验、Word 字段刷新、结构回读和导出快照固化。
7. 保留生成输入、模板、评分引擎和输出文件的可追溯哈希。

### 4.2 非目标

- 不支持在 Word 中自由增加业务结构后自动回收。
- 不允许用户上传任意 DOCX 作为公共模板。
- 不在本阶段实现复杂模板设计器。
- 不把批注编写建议自动升级为专业结论。
- 不使用客户复核版替代脱敏运行时模板。
- 不改变 Ra/Rk 不进入 Word 的边界。

## 5. 装配架构

### 5.1 生成流水线

```text
读取项目和模板绑定
  -> 冻结数据库读取快照
  -> 校验模板包哈希
  -> 构建 ReportProjection
  -> 运行权威评分和派生统计
  -> 填充标量 SDT
  -> 渲染 55 张表及条件块
  -> 在同一 OOXML 包内生成附录 A
  -> 装配附录 B 证据块
  -> 图片、关系、书签和编号去冲突
  -> 写入草稿或最终版标识
  -> 保存临时 DOCX
  -> OOXML 结构校验
  -> Word 字段刷新和重新分页
  -> 重开回读校验
  -> 固化导出快照和文件哈希
  -> 原子发布文件
```

任何一步失败都不得覆盖上一次成功导出。

### 5.2 ReportProjection

装配器不直接在各处查询 SQLite。首先生成不可变 `ReportProjection`：

```text
ReportProjection
  project
  template_binding
  report_identity
  organizations
  assessment_team
  project_process
  system_profile
  topology_and_crypto_usage
  assets
  standards_and_metrics
  scope_objects_and_methods
  appendix_a
  unit_summaries
  corrections
  overall_score
  findings_and_recommendations
  risks
  conclusion_draft
  appendix_b_evidence
  validation_summary
```

投影对象必须包含字段来源、审核状态和稳定业务 ID。所有派生值在投影构建阶段一次计算，避免装配不同章节时重新计算出现漂移。

### 5.3 55 表渲染策略

manifest 必须对 55 张表逐表指定以下策略之一：

| 策略 | 用途 | 行处理 |
|---|---|---|
| `fixed_form` | 封面信息、固定证明表 | 保留结构，填固定槽位 |
| `reference_fixed` | 指标、威胁等标准清单 | 从版本化规范数据生成或核验 |
| `repeat_rows` | 资产、人员、工具、对象 | 克隆模板行并按数据量展开 |
| `matrix` | 层面/指标符合情况矩阵 | 动态行、固定指标列 |
| `derived_summary` | 单元、整体评分和统计 | 只读派生，不接受手填 |
| `risk_rows` | 问题和风险 | 按问题稳定 ID 展开 |
| `appendix_a` | A-1～A-8 | 复用现有生成逻辑 |
| `evidence_block` | 附录 B | 条件化证据表和图片 |

每张表必须定义：

- `table_id` 和所属章节。
- 数据查询或投影路径。
- 最小/最大列数。
- 表头行数和重复表头规则。
- 模板行 ID、行排序、空数据策略。
- 合并单元格规则、列宽和自动换页规则。
- 最终版必填列。
- 允许的内容控件和是否扁平化。
- 结构回读签名。

空数据不能通过保留 `XX` 示例表达；草稿使用统一“待补充”样式，最终版根据字段规则决定删除空块或阻止导出。

### 5.4 动态块装配

- 通过 R0 块级锚点定位，不使用表格序号作为运行时定位。
- 生成节点必须插入同一 OOXML package，不能把两个 DOCX 直接拼接。
- 新关系 ID、图片名、书签 ID、批注 ID、编号 ID 必须由统一分配器生成。
- 写入动态表前删除锚点区间内的模板示例行，只保留经过批准的表头和样式模板行。
- 大表允许跨页，首行/前两行表头按 manifest 设置重复。
- 禁止固定行高截断正文。

### 5.5 附录 A 复用

现有附录 A 服务应拆成：

```text
build_appendix_a_projection(project_id)
render_appendix_a_into(document_package, anchor, projection, mode)
validate_appendix_a_block(document_package)
```

要求：

- A-1～A-8 仍使用现有数据库表和权威评分。
- 技术表仍为 8 列，管理表仍为 5 列。
- draft 可保留下拉控件，final 扁平化。
- Ra/Rk 只能存在于投影和内部审计快照，不能写入 Word。
- 图片使用项目内稳定图片 ID 和题注，正文 REF 目标必须存在。
- 附录 A 的 8 个横向分节嵌入完整报告的 17 分节契约中，不再单独断言整份文档只有 8 分节。

### 5.6 同源派生

以下内容禁止独立手填：

- 各层面符合、部分符合、不符合、不适用数量。
- 单元分、层面分和综合得分。
- 风险数量统计。
- 正文中引用的指标数量和不适用数量。
- 附录 A、正文单元汇总、整体测评表和 XLSX 中的同一分值。

叙述性结论可以由派生值生成草稿，但必须保留人工确认字段；生成器不得仅凭分数自动下最终专业结论。

## 6. 草稿与最终版契约

### 6.1 草稿版

`export_mode=draft`：

- 允许 `workflow_status=draft|ready_for_review|confirmed`。
- 首页、页眉或水印显示“草稿—未完成复核”，不得只依赖文件名。
- 文件名包含“草稿”。
- 未完成字段以统一占位样式显示，并在文末或伴随 JSON 生成问题清单。
- 允许规则提示、缺失项警告和字段刷新降级。
- 不得伪装成最终报告。

### 6.2 最终版

`export_mode=final` 必须满足：

- 项目为 `full_report`。
- `workflow_status=confirmed`。
- 模板资产哈希通过。
- 所有强制数据、附件和专业确认通过。
- A-1～A-8 评分完成且后端重算一致。
- 正文、附录 A 和 XLSX 同源向量测试通过。
- 无 `XX`、大括号示例、“选择一项。”、“待补充”等占位符。
- 无批注、未接受修订、外链、宏和 OLE。
- Word 字段刷新成功，TOC、PAGE、NUMPAGES、SEQ、REF、PAGEREF 可回读。
- Word 打开不产生修复提示。
- 最终版移除草稿标识，内容控件按 manifest 扁平化或锁定。

批注规则提示即使未满足也只列入 warning；它们不能单独阻止最终版。真正阻断项必须来自字段字典强约束、结构校验、同源计算或人工确认状态。

## 7. 字段刷新与可视化验证

### 7.1 刷新顺序

1. 生成时写入 `w:updateFields=true`，但不把它视为完成证明。
2. 对可确定的 SEQ/REF 缓存值进行结构级物化。
3. 使用隔离 Word 进程打开临时副本。
4. 更新所有 story ranges、目录、正文、页眉、页脚和文本框字段。
5. 重新分页并保存 DOCX。
6. 关闭文档和进程后回读 DOCX。
7. 使用 Microsoft Word 原生导出临时 PDF/PNG 进行内部视觉 QA；该中间产物不公开、不归档，也不影响 DOCX 导出成功状态。

### 7.2 异常处理

- Word 自动化超时必须结束且只结束本任务创建的进程。
- Word 弹出对话框、文件被占用、保护视图、修复提示或 COM 断开均视为失败。
- draft 可保留结构通过但未完成分页的结果，并明确标识。
- final 不允许以 LibreOffice、WPS 或未刷新缓存替代 Word 验收。

## 8. 快照基座

建议新增：

```text
report_export_jobs
  id
  project_id
  export_mode
  status
  template_package_id
  template_hash
  data_snapshot_path
  data_snapshot_hash
  scoring_engine_version
  field_dictionary_hash
  manifest_hash
  validation_json
  output_docx_path
  output_docx_hash
  page_count
  created_at
  started_at
  finished_at
  error_code
  error_message

report_export_snapshots
  snapshot_id
  project_id
  workflow_status
  export_mode
  projection_json_path
  projection_hash
  export_job_id
  confirmed_at
```

投影快照必须包含完整报告业务值和派生值，但附件保存稳定 ID、文件哈希和相对路径，不重复嵌入大二进制。

快照使用临时文件写入、`fsync`、原子改名。只有输出 DOCX 和快照全部成功后，导出任务才变为 `succeeded`。临时视觉 QA 产物不属于导出快照。

## 9. API 与 UI 契约

### 9.1 API

新增或扩展：

```text
POST /api/projects/{project_uuid}/report-validations
GET  /api/projects/{project_uuid}/report-validations/latest

POST /api/projects/{project_uuid}/report-export-jobs
  { "mode": "draft" | "final" }

GET  /api/report-export-jobs/{job_id}
GET  /api/report-export-jobs/{job_id}/docx
GET  /api/report-export-jobs/{job_id}/issues
```

兼容策略：

- 现有附录 A `POST .../exports/docx?mode=editable|final` 在迁移期继续可用。
- 完整报告只使用新异步任务接口和 `draft|final`。
- 后续可将附录 A `editable` 映射为 `draft`，但必须先通过 API 版本迁移，不得静默改变客户端语义。

结构化错误：

```json
{
  "message": "完整报告尚未满足最终版条件",
  "issues": [
    {
      "severity": "error",
      "code": "REPORT_FIELD_MISSING",
      "field_id": "report.system.name",
      "section_id": "front.basic_info",
      "reason": "必填字段为空"
    }
  ]
}
```

### 9.2 UI

- 完整报告项目显示章节导航、完成度、来源和审核状态。
- 导出区域分别提供“生成草稿”和“生成最终版”。
- 最终版按钮仅在 `confirmed` 时可点击，但后端仍独立校验。
- 展示模板 edition/revision、数据快照时间和导出任务状态。
- 问题列表区分 error、warning；模板批注规则统一标记为“编写提示”。
- DOCX 仅在任务成功后显示下载入口；不提供 PDF 下载接口。
- 导出进行中锁定重复提交，允许查看日志但不暴露敏感正文。

## 10. 可提交工作包

### WP-R4-1：报告投影与装配上下文

**输入**

- 完整报告结构化数据。
- R0 模板包。

**具体改动**

- 新增不可变 `ReportProjection`。
- 新增模板包复制、锚点解析、关系 ID 和书签 ID 分配器。
- 新增装配临时目录和原子发布机制。

**失败行为**

- 模板哈希、字段字典或 manifest 不一致时在写文件前失败。
- 同一业务 ID 重复或引用断链时投影构建失败。

**测试**

- 投影确定性测试。
- 锚点重复/缺失测试。
- 关系和书签 ID 冲突测试。
- 临时文件失败清理测试。

**验收**

- 相同数据库快照产生相同投影哈希。

**建议提交**

`R4-1: 建立完整报告投影和装配上下文`

### WP-R4-2：55 表渲染器

**输入**

- manifest 的 55 表策略。
- ReportProjection。

**具体改动**

- 实现固定表、重复行、矩阵、派生汇总、风险和证据表渲染器。
- 对 55 个 table_id 建立显式注册，不允许默认猜测。
- 实现表头重复、合并、列宽和空块策略。

**失败行为**

- manifest 中任一表无渲染器时构建失败。
- 行数据不满足列 schema 时返回 table_id 和业务行 ID。

**测试**

- 55/55 注册覆盖测试。
- 0/1/多行和极长文本测试。
- 大规模对象、资产和风险表测试。
- 表格网格、合并和重复表头测试。

**验收**

- 55 张表均能由结构化投影生成并回读。

**建议提交**

`R4-2: 实现完整报告55表动态渲染`

### WP-R4-3：附录 A 嵌入与同源评分

**输入**

- 现有 A-1～A-8 服务和评分引擎。
- 完整报告附录锚点。

**具体改动**

- 抽取可嵌入式附录 A 渲染接口。
- 在完整报告 package 内生成图片关系、题注和书签。
- 建立正文汇总、附录 A、XLSX 同源向量校验。

**失败行为**

- 缺指标、未完成评分、断图、断引用或同源差异时 final 失败。
- 检测到 Ra/Rk Word 节点时失败。

**测试**

- A-2 165 对象、A-7 双对象回归。
- 8 列/5 列结构测试。
- Ra/Rk 不进 Word 测试。
- 三方分数一致性测试。

**验收**

- 完整报告附录 A 与现有独立导出内容语义一致。

**建议提交**

`R4-3: 将附录A和权威评分接入完整报告`

### WP-R4-4：草稿、最终版闸门和字段刷新

**输入**

- 初步完整 DOCX。
- workflow status 和验证结果。

**具体改动**

- 增加草稿标识和最终版清理。
- 实现 final 强校验。
- 实现受控 Word 字段刷新和重新分页。
- 增加字段回读和占位符扫描。

**失败行为**

- final 任一强错误、Word 超时、修复提示或字段断链均失败。
- 上一次成功产物保持不变。

**测试**

- 工作流状态矩阵测试。
- 草稿标识测试。
- 字段刷新成功/超时/异常 Word 测试。
- 未替换占位符、批注和修订阻断测试。

**验收**

- final 文件无草稿标识、无占位符、字段回读完整。

**建议提交**

`R4-4: 完成完整报告最终版闸门和字段刷新`

### WP-R4-5：导出快照、API 和 UI

**输入**

- 完整装配服务。

**具体改动**

- 新增导出任务和快照表。
- 新增异步 API、下载和问题接口。
- 前端增加草稿/最终版导出、进度和问题展示。

**失败行为**

- 快照或文件哈希写入失败时任务失败。
- 不返回半成品下载地址。

**测试**

- API 状态机和并发测试。
- 原子提交测试。
- 前端重复提交、失败重试和下载测试。

**验收**

- 每个成功文件都能追溯到模板、投影和评分版本。

**建议提交**

`R4-5: 增加完整报告导出任务和快照`

### WP-R4-6：打包与专项验收

**输入**

- R4 全部能力。

**具体改动**

- 更新桌面模板资源和 Word 自动化依赖检查。
- 建立脱敏完整报告黄金样本。
- 增加结构、公式和字段回归；在渲染器可用时增加临时视觉回归。

**失败行为**

- 打包侧车缺模板、无法启动 Word 刷新或输出不完整时验收失败。

**测试**

- 全量检查。
- 干净数据目录桌面验收。
- Word 打开无修复提示测试。
- 17 分节/55 表逐页视觉抽检和关键页全检。

**验收**

- 安装版可独立生成草稿；满足环境和数据闸门时可生成最终版。

**建议提交**

`R4-6: 完成完整报告DOCX装配专项验收`

## 11. 迁移与回滚

### 11.1 数据迁移

- 新增导出任务和快照表前先备份数据库。
- 不迁移或覆盖现有 `render_jobs`；完整报告使用独立任务表。
- 旧附录 A 导出记录保持可读。

### 11.2 API 迁移

- 保留旧附录 A 同步导出接口。
- 新完整报告异步接口独立上线。
- 前端根据 `project_type` 选择导出接口。

### 11.3 回滚

- R4 功能通过 feature flag 禁用时，附录 A 项目仍可正常运行。
- 失败导出只删除本次临时目录，不删除历史成功快照。
- 模板 package 和快照不可被新版本原位覆盖。

## 12. 风险与安全

- 报告含个人信息、联系方式和安全架构信息，日志不得记录正文、附件内容或完整投影。
- 临时 DOCX 及可选视觉 QA 文件必须保存在任务隔离目录并采用不可预测文件名；QA 文件任务结束后清理。
- 下载接口必须校验项目和任务归属，防止路径穿越。
- Word 自动化禁用宏、外链更新和交互对话框。
- 生成器只使用已注册模板包，不接受请求传入任意本地路径。
- 风险等级、整改建议和最终结论必须保留人工确认，不得由语言模板静默盖章。

## 13. 阶段验收闸门

- [ ] 17 分节顺序、方向、边距和页眉页脚关系符合 manifest。
- [ ] 55/55 张表均有显式渲染策略和回读测试。
- [ ] 动态长表无固定行高截断，表头正确重复。
- [ ] 附录 A 复用现有权威数据和评分。
- [ ] Word 中不存在 Ra/Rk。
- [ ] 正文、附录 A、XLSX 和快照分数完全一致。
- [ ] draft 始终有显式草稿标识。
- [ ] final 只对 confirmed 项目开放并通过全部强校验。
- [ ] 批注规则提示只产生 warning。
- [ ] TOC、PAGE、NUMPAGES、SEQ、REF、PAGEREF 更新并可回读。
- [ ] 输出无批注、未接受修订、占位符、外链、宏和 OLE。
- [ ] 每个成功导出都有不可变投影快照和文件哈希。
- [ ] Word 打开无修复提示；有可用渲染器时完成临时视觉 QA，无渲染器时如实记录但不把 PDF 作为交付前置。
- [ ] 桌面打包验收和独立代码审查通过。

## 14. 建议分支、提交与 PR

- 建议分支：`codex/r4-full-report-docx-assembly`
- 每个 WP 独立提交，二进制黄金样本单独提交并注明脱敏来源。
- PR 标题：`R4：实现完整报告DOCX装配、快照与最终版闸门`
- PR 描述必须列出 55 表覆盖矩阵、17 分节验证、Word 环境要求、同源评分证据、失败回滚和敏感数据检查。
- 合并前不得暂存客户报告、真实项目快照、本地导出 DOCX、临时视觉 QA 文件、日志或数据库。
