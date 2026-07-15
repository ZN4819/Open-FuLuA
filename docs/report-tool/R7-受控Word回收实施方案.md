# R7 受控 Word 回收实施方案

> 状态：未实施。本文档为开发实施基线，不代表功能已交付。

## 1. 阶段定位

R7 为完整报告提供受控 Word 往返能力：用户从工具导出明确标记为 `roundtrip_capable` 的可编辑草稿，在 Microsoft Word 中修改白名单内的现有事实字段或现有表格行，再将 DOCX 上传回工具；系统通过受控签名 manifest、不可变导出快照、结构校验和三方 diff 生成回收预览，用户确认后原子写回结构化数据。

Word 回收不是“导入任意 Word”。系统必须严格证明上传文件与当前项目、指定导出快照和指定模板同源。不能证明同源的文件只能进入 `migration` 预览，不能以 `roundtrip` 更新现有项目。

### 1.1 复杂度

- 复杂度：极高。
- 主要难点：Word 对 OOXML 的重写、内容控件丢失、域拆分、表格合并变化、并发编辑冲突、修订状态、三方 diff、事务原子性和审计追踪。
- 风险等级：极高。错误回收可能覆盖工具内新数据、破坏权威评分或把外部文档注入项目。

### 1.2 前置依赖

- R0 已提供稳定字段字典、块锚点、运行时模板和 manifest。
- R2 已提供《报告字段及参数关联矩阵》，其中可编辑性、权威字段 ID、重复投影和约束状态是回收白名单的唯一来源。
- R3 已提供《派生计算与文案生成规则矩阵》，可以在事实字段变化后重算派生结果并使相关人工正文失效或待重新确认。
- R4 已提供不可变导出快照、DOCX 哈希、完整报告装配和仅用于受控草稿的 roundtrip 输出档位。
- 项目具有稳定业务记录 ID、项目修订号和 `project_type/workflow_status`。
- 附录 A 的每个可回收现有行具有稳定 row ID；后端评分可在写回后权威重算。
- UI 能显示字段级和行级 diff，并要求用户明确处理冲突。

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

- `roundtrip` 只接受本项目由工具生成、`export_mode=draft`、`roundtrip_capable=true` 且 manifest 验证通过的同源 DOCX；`final` DOCX 永远不是回收输入。
- `migration` 用于外部既有报告或旧版本报告，只生成迁移预览，不直接覆盖现有项目。
- Word 只允许修改 R2 矩阵明确可编辑、且经 manifest 固化的已有事实字段和已有业务行；manifest 不能把只读字段临时升级为可写。
- 不允许通过 Word 新增、删除、复制、拆分、合并或重新排序业务行。
- 图片、图片文件、图片排序、题注和图片引用一律不从 Word 回收；必须回到工具内修改。
- 未接受修订会阻止回收。检测到 `w:ins`、`w:del`、`w:moveFrom`、`w:moveTo` 或其他未决修订时任务失败。
- Ra/Rk 不进入 Word，因此不存在 Word 回收路径；上传文档中出现 Ra/Rk 业务字段时视为结构异常。
- 批注不作为业务数据回收；模板规则仍只产生 warning。
- 模板包常量、R3 派生值、只读重复投影、Word 字段缓存以及附录 A 对象分/单元分/修正后分值均不得直接回收。
- 写回事实字段后，Word/客户端显示值均不可信，必须由后端重新计算对象分、单元分、最终投影和 R3 派生结果，并按依赖关系使人工正文重新待确认。

## 3. 经仓库验证的当前基线

- 当前 DOCX 导入只面向附录 A，流程为上传、解析预览、确认后创建新项目。
- 当前导入不会更新已有项目，也没有来源 manifest、基线快照、三方 diff 或冲突解决。
- 当前 `docx_import_jobs` 保存原文件、解析 JSON、问题和创建项目 ID，但没有导出快照 ID、项目修订号或回收状态。
- 当前可编辑附录 A DOCX 已为 D/A/K 和管理符合情况生成带 tag 的下拉内容控件。
- 当前 DOCX 导入可解析表格合并、图片、题注和 REF，但 R7 必须收窄回收边界，不复用“图片导入”作为 roundtrip 行为。
- 当前项目更新缺少统一乐观并发版本；R7 需要增加项目修订号或等价 ETag。
- 当前后端已能权威重算技术和管理分数，适合在回收事务中复用。

## 4. 目标与非目标

### 4.1 目标

1. 通过唯一受控签名部件、服务端不可变快照和项目归属严格证明同源。
2. 对允许字段和已有行生成“导出基线/数据库当前值/Word 值”三方 diff。
3. 自动合并无冲突修改，并让用户逐项解决真正冲突。
4. 拒绝新增、删除、复制、重排、拆分或合并业务行。
5. 明确忽略图片变化，并在 UI 中提示用户回到工具修改。
6. 阻止未接受修订、结构异常 Word、来源不明文件、任意 custom XML、模板常量/派生字段覆盖和 Ra/Rk 注入。
7. 使用事务、项目修订号和原子归档实现全有或全无写回。
8. 保存回收 manifest、diff、用户选择、写回前后哈希和审计记录。

### 4.2 非目标

- 不支持导入任意格式的 Word 表格覆盖项目。
- 不回收图片、题注、图号、图片顺序或图片内文字。
- 不允许 Word 新建业务行。
- 不回收目录、页码、字段显示缓存、格式、样式、页眉页脚或分节变化。
- 不把 Word 中手工修改的对象分、单元分、最终分值、统计数量、风险汇总、综合得分或评估结论视为权威值。
- 不在 roundtrip 中执行项目间复制或合并。

## 5. 同源 Manifest

### 5.1 嵌入位置

R4 仅在用户显式选择受控回收草稿时写入：

- 唯一受控 custom XML part：完整机器 manifest；使用固定 content type、固定 namespace、固定 relationship type 和固定 schema。
- 自定义文档属性：最小可诊断身份，不含敏感正文。
- 受控 SDT tag：字段 ID、块 ID、业务行 ID 和列 ID。
- 服务端 `report_export_snapshots`：manifest 的权威副本及哈希。

R0 运行时母版、普通草稿和最终版继续禁止任意 custom XML。受控回收草稿中必须恰好存在一个由本工具生成的签名部件；缺失、重复、schema/namespace/关系类型不符或存在任何额外 custom XML 均为结构错误。仅在 Word 中存在 manifest 不足以证明同源；必须与服务端快照匹配。

### 5.2 Manifest 字段

```json
{
  "manifest_version": "1",
  "document_instance_id": "uuid",
  "project_uuid": "uuid",
  "project_type": "full_report",
  "export_job_id": 100,
  "snapshot_id": "uuid",
  "project_revision": 12,
  "template_package_id": "report-2023-2025.12.08",
  "template_edition": "2023",
  "template_revision": "2025-12-08",
  "template_hash": "sha256",
  "field_dictionary_hash": "sha256",
  "manifest_hash": "sha256",
  "snapshot_hash": "sha256",
  "writable_contract_hash": "sha256",
  "scoring_engine_version": "...",
  "issued_at": "ISO-8601",
  "writable_fields": [],
  "writable_rows": [],
  "baseline_value_hashes": {},
  "signature": "HMAC-or-local-signature"
}
```

`writable_rows` 对每一行记录：

- 稳定业务 row ID。
- 所属块和表。
- 原始排序。
- 可写列。
- 关键不可变列。
- 基线值哈希。

manifest 只保存身份、哈希、允许字段/行 ID 和规范化契约，不保存字段明文基线；可用于三方比较的完整 B 值只保存在不可变服务端快照中。manifest 不得包含访问令牌、数据库路径、附件绝对路径或客户正文。

### 5.3 签名和密钥

- manifest 按固定 canonical JSON 规则排除 `signature` 后计算 HMAC；应用数据目录中的本机密钥不得打包进安装目录。
- 签名同时覆盖 snapshot hash、模板包、项目 UUID、document instance、可写契约和全部基线值哈希，禁止只签 package ID。
- 恢复备份时必须同时恢复密钥或明确使旧 roundtrip 文件失效。
- 签名不通过时 roundtrip 立即失败，不能降级为“仅警告后继续”。
- 用户可显式改用 migration 重新预览，但 migration 不覆盖现有项目。

## 6. 可回收边界

### 6.1 允许回收

- R2 矩阵标记 `editable=true`、数据性质为事实输入，并同时出现在受签名 manifest 白名单中的标量文本、日期、枚举和长文本字段。
- 附录 A 已有行中经 R2 允许的对象名称、子系统、结果记录、D/A/K 和管理符合情况；只允许更新原始输入，不接受 Word 显示分值。
- 完整报告已存在重复行中经 R2 允许编辑的非关键事实列。
- 叙述内容只允许写入与 R3 生成基线分离的“人工版本”字段；回收后状态为待确认，不得覆盖生成基线、规则 ID 或派生结果。

白名单在导出快照创建时由 R2 矩阵确定并签名冻结。上传时必须再次用当前兼容矩阵校验：当前矩阵收窄权限时按更严格边界处理；不得因旧 manifest 声明可写就恢复已撤销权限。

### 6.2 永远不从 Word 回收

- 项目 ID、模板版本、行 ID、字段 ID、排序键和外键。
- 密评机构名称、地址、邮政编码、联系人、固定威胁目录、模板固定标准和其他模板包常量。
- 新增或删除的行。
- R3 规则 ID、生成基线、确认/过期状态，以及指标结论、结果修正、对象分、单元分、最终分值、层面/总体统计、风险数量和汇总等级、高风险判断、综合得分、评估结论等派生值。
- Ra/Rk。
- 图片、题注、图号、图片关系、alt 文本和引用关系。
- 目录、页码、SEQ/REF/PAGEREF 缓存。
- 样式、分节、页眉页脚、书签名称和表格几何。
- Word 批注和审阅人信息。

### 6.3 图片变化行为

- 仅新增、替换或删除普通图片时，回收预览生成 `WORD_IMAGE_CHANGE_IGNORED` warning。
- 图片变化导致业务块结构、关系或正文引用损坏时升级为结构 error。
- UI 明确说明“图片不会回收，请在工具中上传、替换、排序或修改题注”。
- 回收提交不读取或复制 `word/media` 到项目存储。

## 7. 上传和结构预检

上传文件先进入隔离区，不直接打开或写入项目目录。

预检顺序：

1. ZIP 安全限制和内容类型校验。
2. 禁止宏、OLE、ActiveX、外链、嵌入包和远程模板。
3. 断言 custom XML 恰好为一个固定 schema 的受控签名部件，拒绝任意其他 custom XML，再验证 canonical payload 和签名。
4. 校验 project ID、snapshot ID、package ID 和服务端快照。
5. 扫描所有文档部件中的未接受修订。
6. 校验 17 分节、55 表块、锚点、标签和业务行集合。
7. 校验字段和行 tag 唯一。
8. 检测新增、删除、复制、重排、拆分和合并行。
9. 检测图片变化并生成 warning。
10. 按 R2 白名单提取事实字段，归并同一权威字段的重复投影，并生成三方 diff。

任何结构 error 均停止在预览阶段，不产生可确认写回计划。

### 7.1 未接受修订

必须扫描：

- `word/document.xml`
- 页眉、页脚、脚注、尾注、文本框和批注相关部件
- `w:ins`、`w:del`、`w:moveFrom`、`w:moveTo`
- 属性修订，如 `w:pPrChange`、`w:rPrChange`、`w:tblPrChange`、`w:trPrChange`、`w:tcPrChange`

检测到任一未接受修订时返回 `WORD_TRACKED_CHANGES_NOT_ACCEPTED`，提示用户在 Word 中接受或拒绝全部修订后重新上传。

## 8. 三方 Diff 与冲突规则

三方值：

- `B`：导出快照基线值。
- `D`：上传时数据库当前值。
- `W`：上传 Word 中提取的值。

`B` 必须来自 `report_export_snapshots` 中保存的不可变明文基线及其哈希，不能从上传 Word 反推，也不能只有 hash 而没有可比较值。`W` 只能由稳定 tag 和 R2 权威字段 ID 提取；同一权威字段存在多个允许投影时，所有 Word 槽位规范化后必须一致，否则生成 `WORD_REPEATED_SLOT_CONFLICT` 并阻断该字段自动合并。

### 8.1 自动判定

| 条件 | 结果 |
|---|---|
| `W == B` 且 `D == B` | 未修改 |
| `W == B` 且 `D != B` | 保留数据库当前值 |
| `W != B` 且 `D == B` | 可自动应用 Word 值 |
| `W == D` | 已一致，无需写入 |
| `W != B`、`D != B` 且 `W != D` | 冲突，必须人工选择 |

比较前只执行字段字典允许的规范化，例如换行、受控空白和枚举显示值映射；不得用模糊相似度自动覆盖。

### 8.2 行集合约束

- Word 行 ID 集合必须等于导出快照行 ID 集合。
- 行顺序必须等于 manifest 顺序。
- 行 ID 重复、缺失或新增均为 error。
- 关键不可变字段变化为 error，不提供“应用 Word”选项。
- 普通可写列按字段级进行三方 diff。
- Word 中新增的字段 tag、R2 未声明可写的列、模板常量、派生字段或附录 A 分值字段均视为越界，不因值碰巧等于数据库而忽略。

### 8.3 冲突解决

允许选择：

- `keep_database`
- `apply_word`

不提供“合并文本”自动算法。用户需要合并长文本时，应复制内容后在工具内编辑，再重新预览。

解决计划绑定 `import_job_id`、diff hash 和当前项目 revision。任一变化都会使旧解决计划失效。

## 9. 数据模型

在 R6 的 `report_import_jobs`、`report_import_issues` 和 `report_import_resolutions` 基础上扩展 roundtrip 字段，并新增结构化冲突及审计表：

```text
report_import_jobs
  id
  project_id
  import_mode
  status
  source_docx_path
  source_docx_hash
  manifest_json_path
  manifest_hash
  source_snapshot_id
  source_snapshot_hash
  writable_contract_hash
  base_project_revision
  observed_project_revision
  diff_json_path
  diff_hash
  resolution_json_path
  resolution_hash
  created_at
  started_at
  finished_at
  committed_at
  error_code
  error_message

report_sync_conflicts
  id
  import_job_id
  field_path
  entity_uuid
  base_value_hash
  database_value_json
  word_value_json
  conflict_kind
  resolution
  resolved_value_json
  resolved_at

report_import_audits
  id
  import_job_id
  project_id
  before_revision
  after_revision
  applied_fields_json
  kept_fields_json
  ignored_changes_json
  before_state_hash
  after_state_hash
  actor
  created_at
```

结构预检和导入诊断继续写入 R6 的 `report_import_issues`；字段冲突写入 `report_sync_conflicts`，不得只保存在不可查询的 diff JSON 中。项目增加单调递增 `revision`。所有会改变完整报告事实源的保存操作必须增加 revision，不能只在 R7 提交时增加。服务端快照必须保留 B 的规范化值、字段类型、行集合、R2 association ID 和哈希；清理导出文件时不得删除仍被 roundtrip 任务或审计引用的快照。

## 10. API 契约

```text
POST /api/projects/{project_uuid}/report-import-jobs
  multipart: file, mode=roundtrip

GET /api/report-import-jobs/{job_id}
GET /api/report-import-jobs/{job_id}/diff
GET /api/report-import-jobs/{job_id}/issues

PUT /api/report-import-jobs/{job_id}/resolution
  { diff_hash, expected_project_revision, resolutions[] }

POST /api/report-import-jobs/{job_id}/commit
  { resolution_hash, expected_project_revision }
```

状态机：

```text
uploaded
  -> validating
  -> invalid
  -> diff_ready
  -> conflicts_pending
  -> ready_to_commit
  -> committing
  -> succeeded | failed | stale
```

失败响应必须包含稳定 code、字段/行 ID、三方摘要和用户可执行的修复建议，不返回本地绝对路径或完整敏感值。

### 10.1 migration 模式

- migration 完全由 R6 的 `/api/report-imports/docx?mode=migration` 流程处理，不进入 R7 的目标项目回收端点。
- R7 检测到来源缺失时只能返回“改用迁移预览”的显式建议，不得在同一任务内降级、复用 resolution 或覆盖现有项目。

### 10.2 roundtrip 模式

- manifest、签名、快照和项目必须全部匹配。
- 文档必须是 `export_mode=draft` 且 `roundtrip_capable=true`；final、普通草稿和外部报告均拒绝。
- 只更新目标项目。
- 必须经过 diff、冲突解决和 commit 三步。
- 不允许通过修改请求中的 project ID 改变归属。

## 11. UI 契约

- 项目页提供“回收 Word 修改”，默认 mode 为 roundtrip。
- 上传前提示：仅已有行可改；不能新增/删除行；图片不会回收；必须先接受全部修订。
- 上传前同时提示：模板机构常量、对象/单元/综合分值、统计、风险汇总、结论和 Word 页码/域缓存不会回收；如需更改事实输入必须编辑白名单槽位。
- diff 页面按章节和业务对象分组，显示基线、工具当前值、Word 值和判定。
- 无冲突项显示自动处理结果；冲突项必须逐项选择。
- 关键字段变化、行集合变化和未接受修订以阻断错误显示。
- 图片变化放在单独“不会回收的修改”区域。
- 提交按钮显示预计更新字段数、保留数据库字段数和忽略项数。
- 提交成功后刷新项目数据，并提示分数已经由后端重新计算。
- 不允许用户通过 UI 强制忽略来源、结构或修订错误。

## 12. 原子提交

提交过程：

1. 验证 job 为 `ready_to_commit`。
2. 验证 resolution hash。
3. 开启 `BEGIN IMMEDIATE`。
4. 重新读取项目 revision；与 expected revision 不同则标记 `stale` 并回滚。
5. 重新读取受影响字段并核对 diff 前提仍成立。
6. 只更新解决计划中的允许字段和已有行。
7. 后端重新计算技术/管理评分、结果修正、最终投影，并按 R3 规则重新计算统计、风险汇总、综合得分和结论；不得采用 Word 显示值。
8. 对受依赖影响的人工正文标记为过期或待重新确认，不静默覆盖其文本。
9. 运行 R2/R3 强一致性校验。
10. 写入 import audit 和新项目 revision。
11. 提交数据库事务。
12. 将上传源文件和受控 manifest 按哈希归档；归档失败时记录可恢复任务，不重复写业务数据。

业务写回必须全有或全无。不得逐章节提交后在中途失败。

## 13. 可提交工作包

### WP-R7-1：Roundtrip Manifest 生成与验证

**输入**

- R4 导出快照。
- R0 字段字典和 manifest。
- R2 字段关联矩阵与 R3 派生规则矩阵。

**具体改动**

- 仅在 `roundtrip_capable` 草稿中写入一个固定 content type/namespace/relationship/schema 的受控 custom XML manifest、最小自定义属性和稳定 tag；普通草稿、final 和运行时母版不得含该部件。
- 从 R2 `editable=true` 的事实字段生成白名单并排除模板常量、派生值、只读投影和附录 A 权威分值，保存并签名 writable contract hash。
- 新增本机 HMAC 密钥管理和签名验证。
- 服务端保存 manifest 权威副本。

**失败行为**

- 密钥不可用、签名失败、快照缺失或 R2 白名单无法确定时不生成可 roundtrip 草稿。
- final 文件永远不声明为可回收；不得通过配置开关放宽。

**测试**

- manifest canonical JSON、唯一 custom XML、签名、快照/白名单篡改和备份恢复测试。
- 普通草稿/final 无 custom XML、额外 custom XML 拒绝测试。
- 不泄露令牌、绝对路径和 Ra/Rk 测试。

**验收**

- 草稿可由服务端精确定位到项目和导出快照。

**建议提交**

`R7-1: 增加受控Word回收manifest`

### WP-R7-2：上传隔离与异常 Word 预检

**输入**

- 用户上传 DOCX。

**具体改动**

- 新增隔离存储、ZIP 安全检查和 OOXML 部件检查。
- 扫描宏、OLE、外链、未接受修订、重复标签和结构变化。
- 只对 Microsoft Word 保存产生的受控重写差异建立兼容解析，不把 WPS 或 LibreOffice 重写结果作为受支持回收输入。

**失败行为**

- 非法包、来源不明、未接受修订或危险部件立即失败。
- 受控 custom XML 被清除、重复、改名或出现任意额外 custom XML 时 roundtrip 失败，可提示新建 migration 任务。

**测试**

- ZIP bomb、路径穿越、XXE、外链、宏和 OLE 测试。
- 全类修订节点测试。
- 内容控件被删除、复制和重排测试。
- Word 异常关闭产生临时/损坏文件测试。

**验收**

- 所有异常均在写数据库前被识别。

**建议提交**

`R7-2: 实现Word回收隔离预检`

### WP-R7-3：允许字段提取与行边界

**输入**

- 预检通过的同源 DOCX。
- writable fields/rows manifest。

**具体改动**

- 按 tag 提取允许值。
- 实现已有行集合、顺序和关键列校验。
- 归并同一权威字段的重复投影，槽位不一致时阻断。
- 明确忽略图片、字段缓存和格式变化；显式拒绝模板常量、R3 派生值和附录 A 权威分值字段。

**失败行为**

- 新增/删除/复制/重排行、关键字段变化、重复投影不一致、Ra/Rk/模板常量/派生值/分值节点出现时失败。
- 图片改变只产生 warning，除非破坏结构。

**测试**

- 标量、人工叙述版本和长文本提取测试。
- 仅编辑已有行成功测试。
- 行增删改序、重复槽位冲突、模板常量/派生值/对象分伪造和图片变化测试。

**验收**

- 提取结果只包含 manifest 白名单字段。

**建议提交**

`R7-3: 固化Word回收字段和已有行边界`

### WP-R7-4：三方 Diff 与冲突解决

**输入**

- 导出快照 B、数据库 D、Word W。

**具体改动**

- 实现确定性三方 diff。
- 生成字段级/行级冲突。
- 保存 diff 和 resolution 哈希。

**失败行为**

- 基线缺失、值类型非法或规范化不确定时不自动合并。
- 项目 revision 变化使预览标记 stale。

**测试**

- 三方组合全覆盖参数化测试。
- 空值、换行、枚举和长文本测试。
- 冲突解决 hash 防篡改测试。

**验收**

- 自动合并结果可由规则表逐项解释。

**建议提交**

`R7-4: 实现Word与数据库三方diff`

### WP-R7-5：原子写回、重算和审计

**输入**

- 已确认解决计划。

**具体改动**

- 新增项目 revision 和乐观并发控制。
- 在单事务内写回允许字段。
- 调用后端权威评分、结果修正、R3 派生重算和 R2/R3 一致性校验；相关人工正文仅失效，不静默覆盖。
- 写入审计记录和前后状态哈希。

**失败行为**

- revision 不一致标记 stale。
- 任一字段写入、重算或校验失败则整单回滚。
- 不出现部分章节成功。

**测试**

- 并发更新、事务回滚、幂等提交测试。
- 对象分/单元分/最终分值/统计/结论伪造拒绝、事实变化后端重算及人工正文失效测试。
- 审计哈希和重复请求测试。

**验收**

- 成功提交只改变解决计划列出的字段，派生值由后端重算。

**建议提交**

`R7-5: 完成Word回收原子写回和审计`

### WP-R7-6：API 与三方 Diff UI

**输入**

- R7 服务能力。

**具体改动**

- 新增上传、任务、diff、resolution 和 commit API。
- 前端增加上传前提示、三方对照、冲突选择和忽略图片说明。

**失败行为**

- UI 不提供绕过结构错误的入口。
- 提交过程中项目变化时要求重新生成 diff。

**测试**

- API 状态机、项目归属和权限边界测试。
- UI 冲突全选、逐项选择、stale 和失败恢复测试。
- 未接受修订阻断和图片不回收提示测试。

**验收**

- 用户能清楚知道哪些改动将应用、保留或忽略。

**建议提交**

`R7-6: 增加受控Word回收交互`

### WP-R7-7：异常 Word 与专项验收

**输入**

- Microsoft Word 不同受支持版本保存的脱敏测试文档。

**具体改动**

- 建立格式重写、异常关闭、保护文档、损坏域、删除 custom XML 等样本。
- 补充迁移模式和 roundtrip 模式端到端验收。

**失败行为**

- 无法证明同源或无法确定字段身份时宁可拒绝，不按位置猜测。

**测试**

- 全量检查和桌面客户端验收。
- 导出草稿、Word 修改、上传、diff、冲突解决、提交、重导出闭环。

**验收**

- 异常 Word 不造成数据库部分写入或错误覆盖。

**建议提交**

`R7-7: 完成受控Word回收专项验收`

## 14. 迁移与回滚

### 14.1 数据迁移

- 升级前执行 SQLite 一致性备份。
- 新增项目 revision、回收任务和审计表。
- 既有项目 revision 初始化为 1，不改变业务字段。
- 旧 `docx_import_jobs` 保留，用于现有附录 A 导入兼容。

### 14.2 模式迁移

- 旧外部报告必须选择 `migration`。
- 只有带 R7 manifest 的 R4 草稿才能选择 `roundtrip`。
- 该草稿还必须声明 `roundtrip_capable=true`；final 和普通 draft 不因存在相似 tag 获得回收资格。
- 不自动把 migration 文件伪装为同源 roundtrip 文件。

### 14.3 回滚

- schema 回滚使用升级前备份。
- 功能关闭后保留审计记录，禁用新回收任务。
- 已成功回收的数据不通过删除审计记录回滚；应基于审计 before snapshot 创建显式恢复操作。
- 上传隔离文件和 diff 可安全清理，但成功审计引用的哈希记录必须保留。
- 服务端导出快照和本机签名密钥不得在仍有有效草稿或审计引用时清理；应用回滚若不支持 manifest 版本，必须显式拒绝而不是降级为位置匹配。

## 15. 风险与安全

- DOCX 是不可信输入，必须防 ZIP bomb、路径穿越、XXE、宏、OLE、外链和嵌入包。
- 不执行任何上传文档中的宏、字段、链接或嵌入对象。
- HMAC 密钥放在用户数据目录并限制访问，不写日志、不上传、不打包。
- diff API 默认返回必要摘要；敏感字段可掩码显示。
- 上传原文件、manifest、diff 和审计按项目隔离。
- 任务日志不得记录完整正文、证件号码、手机号、邮箱或附件内容。
- 同源签名、项目 revision 和三方 diff 是三道独立防线，不能互相替代。
- custom XML 例外仅限一个固定 schema 的签名部件；禁止以“支持 roundtrip”为由接受其他自定义 XML。
- “只编辑已有行”和“图片不回收”必须同时由后端强制，不能只靠 UI 提示。

## 16. 阶段验收闸门

- [ ] roundtrip 必须通过 manifest 签名、服务端快照和项目归属三重同源校验。
- [ ] 只有 `roundtrip_capable` 同源草稿可回收；final、普通草稿和外部 DOCX 全部拒绝。
- [ ] 受控草稿恰好包含一个固定 schema 的签名 custom XML；其他档位不含 custom XML，额外部件全部拒绝。
- [ ] migration 与 roundtrip 行为明确隔离。
- [ ] 只回收白名单字段和已有业务行。
- [ ] 新增、删除、复制、重排、拆分或合并行全部阻止。
- [ ] 图片变化不回收，并有明确 warning。
- [ ] Word 中出现 Ra/Rk 业务字段时阻止。
- [ ] 模板包常量、R3 派生值、只读投影及附录 A 对象分/单元分/最终分值不得直接回收。
- [ ] 同一权威字段的多个 Word 槽位不一致时明确报冲突，不按位置选值。
- [ ] 所有未接受修订类型均能检测并阻止。
- [ ] 三方 diff 规则组合测试全覆盖。
- [ ] 冲突必须人工选择，解决计划绑定 diff hash 和项目 revision。
- [ ] 原子事务失败时没有部分写入。
- [ ] 写回后评分、结果修正和 R3 派生结果由后端重算，受影响人工正文按契约失效或待重新确认。
- [ ] 每次提交都有 before/after hash 和可追溯审计。
- [ ] 异常 Word 或丢失 custom XML 不会触发位置猜测；WPS/LibreOffice 重写文件明确拒绝回收。
- [ ] 桌面端端到端闭环和独立代码审查通过。

## 17. 建议分支、提交与 PR

- 建议分支：`codex/r7-controlled-word-roundtrip`
- WP-R7-1～WP-R7-7 分别提交，manifest、解析、diff、事务和 UI 不混为一个提交。
- PR 标题：`R7：实现严格同源的受控Word回收`
- PR 描述必须列出仅同源草稿边界、受控 custom XML schema 与签名、服务端快照保留策略、R2 白名单、模板常量/派生值/附录 A 权威分值排除、重复投影处理、已有行限制、图片不回收、修订阻断、三方 diff、R3 重算、并发控制和原子回滚证据。
- 合并前不得暂存真实客户报告、上传 Word、diff 内容、审计导出、本地数据库、密钥或日志。
