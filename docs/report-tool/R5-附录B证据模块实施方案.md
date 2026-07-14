# R5 附录 B 证据模块实施方案

> 状态：未实施。本文档为开发实施基线，不代表功能已交付。

## 1. 阶段定位

R5 在完整报告项目中新增“附录 B 密评活动有效性证明记录”模块，将九类证明材料从散落文件和 Word 手工贴图改为项目内结构化管理，并在完整报告 DOCX 导出时按 2023 版、2025-12-08 修订模板的固定顺序生成附录 B。

本阶段复杂度为高。难点不在图片上传，而在九类证据的元数据、条件适用性、顺序、跨项目隔离、Word 分页以及“缺失只警告”的一致契约。

依赖：

- 完整报告项目基础模型已引入 `project_type=appendix_a|full_report`。
- 项目状态已引入 `workflow_status=draft|ready_for_review|confirmed`。
- 运行时模板已登记 `template_edition=2023`、`template_revision=2025-12-08`。
- 完整报告 DOCX 生成器能够定位附录 B 的九个模板槽位。
- R5 不依赖 PDF 输出；阶段交付仍为 DOCX 和既有 XLSX。

统一边界：Ra、Rk 只参与内部评分和 XLSX，不进入 Word；模板批注提炼出的规则在本阶段全部为警告，不得阻止保存、状态流转或导出。

## 2. 经仓库验证的当前基线

- `backend/app/runtime.py` 当前 `SCHEMA_VERSION = "3"`。
- `projects` 仅有 `id/name/created_at/updated_at`，还没有项目类型、工作流状态和模板版本字段。
- `evidence_images` 当前按 `project_id + section_code` 服务 A-1～A-8，保存图片路径、题注、alt、顺序、像素、DPI 和显示尺寸。
- `backend/app/services/evidence.py` 已同时校验扩展名和 MIME，只允许 PNG/JPEG，并能读取图片尺寸及 DPI。
- 现有 API 已支持单张/批量上传、替换文件、修改题注、删除和章节内重排；前端 `EvidencePanel.tsx` 已支持预览、排序和质量提示。
- 当前证据图片与 `assessment_rows` 通过 `cross_references` 建立 A 表结果记录引用，不适合直接复用为附录 B 的证明材料模型。
- 当前 DOCX 生成器只生成 A-1～A-8、8 个横向分节和 8 张表；没有完整报告或附录 B 拼装能力。
- 基础报告模板和客户复核版都包含附录 B 的 9 张表；基础模板附录 B 位于独立横向分节，客户版存在分节合并漂移。
- 基础模板附录 B 的内部批注描述了材料要求，但这些批注不能进入最终报告，也不能升级为阻断性校验。

## 3. 目标与非目标

### 3.1 目标

- 对 `full_report` 项目提供九类附录 B 证据的独立管理页。
- 每类证据支持结构化元数据、多张 PNG/JPEG、题注、排序、替换、删除和大图预览。
- 所有图片必须经工具上传和管理；不接受文件系统路径、URL、粘贴的 base64 或直接编辑 DOCX 内图片。
- 按模板顺序和条件规则生成附录 B；图片尽量一页一张，必要时横向适配，但不拉伸。
- 缺少证据、元数据或题注只产生 warning；图片损坏、越权引用和文件不存在属于技术错误。
- `draft|final` 两种 DOCX 导出均能生成附录 B；`final` 清除批注、占位符和内容控件。

### 3.2 非目标

- 不支持 PDF、TIFF、BMP、HEIC、DOCX、ZIP 或合同原文件上传。
- 不做 OCR、自动识别金额、日期、签章或人员姓名。
- 不连接外部网盘，不允许外链图片。
- 不在本阶段实现电子签章真实性验证。
- 不改变 A-1～A-8 证据图和交叉引用的现有语义。
- 不让附录 B 缺失阻止最终 DOCX 导出。

## 4. 九类证据契约

固定枚举不得由客户端自由扩展：

| 顺序 | `category_code` | 中文名称 | 结构化元数据 |
|---:|---|---|---|
| 1 | `engagement_proof` | 密评委托证明 | 文件类型、签订时间、委托单位、委托金额、系统密评单价 |
| 2 | `travel_accommodation` | 差旅与住宿证明 | 入场时间、离场时间、责任单位、现场人员、本地测评标志 |
| 3 | `onsite_process` | 现场测评过程证明 | 入离场时间、责任单位、地点、现场人员、材料子类型 |
| 4 | `authorization_notice` | 授权书与风险告知书 | 授权书/风险告知书子类型、签署日期 |
| 5 | `plan_review` | 测评方案评审与确认 | 方案名称、评审时间、评审/确认子类型 |
| 6 | `report_review` | 密评报告评审记录 | 评审时间 |
| 7 | `assessor_roster` | 密评人员资格情况 | 姓名、角色、考试通过时间；采用结构化人员行，不以图片替代名单 |
| 8 | `assessor_exam_proof` | 密评人员成绩证明 | 关联人员 ID |
| 9 | `grading_filing` | 系统定级匹配证明 | 等保备案名称、备案时间 |

`onsite_process` 的图片子类型限定为 `sign_in|onsite_photo|handover_record|room_access_record`；`authorization_notice` 限定为 `authorization|risk_notice`；`plan_review` 限定为 `review|confirmation`。未知值返回 422，不静默归入“其他”。

## 5. 架构及数据契约

### 5.1 数据模型

新增表：

```text
report_evidence_categories
  project_id, category_code, metadata_json, is_not_applicable,
  not_applicable_reason, created_at, updated_at
  UNIQUE(project_id, category_code)

report_evidence_items
  id, evidence_uuid, project_id, file_path, original_name, caption, alt_text,
  pixel_width, pixel_height, dpi_x, dpi_y,
  display_width_in, display_height_in, sha256,
  created_at, updated_at

report_evidence_usages
  id, usage_uuid, project_id, evidence_item_id, category_code, subtype,
  related_member_id, sort_order,
  created_at, updated_at
```

约束：

- `project_id` 必须指向 `project_type=full_report`。
- 证据条目和用途表通过数据库外键与服务层双重校验项目归属；同一证据可通过多条 usage 在不同证明位置复用，禁止复制二进制制造重复证据。
- `related_member_id` 复用 R2 的 `report_members`，不在附录B建立第二套人员事实源。
- `file_path` 只能是 `storage/report_evidence/{project_id}/...` 下的相对路径。
- 上传后计算 SHA-256；相同项目、类别、摘要相同只警告重复，不自动去重。
- 九个类别在创建 full_report 项目时一次性初始化；迁移重复执行必须幂等。
- `metadata_json` 的解析由类别专用 Pydantic 模型完成，数据库不接受任意未校验 JSON。

### 5.2 API 契约

```text
GET    /api/projects/{project_uuid}/report/appendix-b
PUT    /api/projects/{project_uuid}/report/appendix-b/{category_code}
POST   /api/projects/{project_uuid}/report/appendix-b/{category_code}/images
PUT    /api/report-evidence/{evidence_uuid}
POST   /api/report-evidence/{evidence_uuid}/file
DELETE /api/report-evidence/{evidence_uuid}
POST   /api/projects/{project_uuid}/report/appendix-b/{category_code}/reorder
```

读取响应包含九类完整清单、结构化元数据、图片、关联的 `report_members`、warning 和完成计数。上传使用 multipart，仅允许 PNG/JPEG。非法图片返回 400；项目或类别不存在返回 404；项目类型不匹配返回 409；非法枚举或元数据返回 422。

warning 示例：`APPENDIX_B_CATEGORY_EMPTY`、`APPENDIX_B_METADATA_MISSING`、`APPENDIX_B_CAPTION_MISSING`、`APPENDIX_B_LOCAL_TRAVEL_OPTIONAL`。warning 不改变 HTTP 成功状态。

### 5.3 UI 契约

- 完整报告项目导航新增“附录 B 证明材料”；`appendix_a` 项目不显示入口。
- 页面按九类固定顺序显示折叠卡片，显示图片数、元数据完整度和 warning 数。
- 每类卡片提供类别专用字段、批量上传、替换、删除、拖拽/按钮排序和大图预览。
- 只显示 PNG/JPEG 选择器；前端限制是体验层，后端仍独立校验。
- warning 使用黄色提示，不使用红色阻断样式；用户可在 warning 存在时保存、确认和导出。
- 图片来源只显示“由工具管理”，不提供路径编辑框、URL 输入框或打开源 DOCX 编辑入口。

### 5.4 DOCX 输出契约

- 使用 `template_edition=2023`、`template_revision=2025-12-08` 的脱敏运行时模板。
- 附录 B 单独横向分节，九类表顺序固定。
- 元数据写入对应单元格；未填保持空白，不输出 `XXX`、`20XX`、`贴图` 或 `{扫描件}`。
- 图片从工具存储读取，保持宽高比；每张图片生成稳定书签、SEQ 题注和必要的 REF。
- `export=draft` 可保留可编辑内容控件，但不保留模板批注；`export=final` 扁平化控件并清理所有批注。
- Ra、Rk 不写入任何 Word XML。

## 6. 可提交工作包

### R5-WP1：数据库与类别模型

- 输入：九类枚举、项目类型统一契约、schema 3 基线。
- 具体改动：新增三张表、索引、外键、Pydantic 模型和幂等 schema 迁移；full_report 项目初始化九类记录。
- 失败行为：迁移事务失败则回滚并保持旧 schema 可启动；不创建半套类别。
- 测试：全新库、schema 3 升级、重复升级、appendix_a 拒绝、级联删除、非法 JSON。
- 验收：九类顺序稳定；旧项目和 A 数据无变化。
- 建议提交：`R5-WP1: 建立附录B九类证据数据模型`。

### R5-WP2：安全文件服务与 API

- 输入：现有 `services/evidence.py` 的 PNG/JPEG 检查和图片元数据能力。
- 具体改动：抽取可复用图片校验；实现附录 B CRUD、批量上传、替换、排序、摘要和项目归属检查。
- 失败行为：批量上传任一失败时回滚本批数据库行和新文件；替换失败保留旧文件；越权统一返回 404。
- 测试：伪扩展名、伪 MIME、损坏图片、路径穿越、跨项目 ID、重复文件、并发排序。
- 验收：磁盘和数据库不留孤儿文件；仅 PNG/JPEG 可进入存储。
- 建议提交：`R5-WP2: 实现附录B证据图片API`。

### R5-WP3：九类 UI

- 输入：API 响应、现有 EvidencePanel 交互模式。
- 具体改动：新增附录 B 页面、类别卡片、专用元数据表单、人员表格、图片上传和预览；提取通用图片卡片但不混淆 A/B 模型。
- 失败行为：未保存元数据切换页面时提示；上传失败保留本地表单；读取失败不显示伪完成状态。
- 测试：九类顺序、项目类型可见性、PNG/JPEG accept、warning 非阻断、键盘操作和移动端布局。
- 验收：用户无需打开文件夹或 Word 即可完成全部证据管理。
- 建议提交：`R5-WP3: 完成附录B证据管理页面`。

### R5-WP4：规则警告与状态联动

- 输入：模板批注提炼规则、workflow_status 契约。
- 具体改动：新增 warning 计算器；项目进入 ready_for_review 或 confirmed 时刷新提示，但不因附录 B 缺失拒绝状态变更。
- 失败行为：规则计算异常记录日志并返回通用 warning，不把项目误标为完整。
- 测试：本地测评免差旅、人员成绩关联、九类全空、部分缺失、全部完整。
- 验收：warning 数和目标类别稳定，可从 UI 直接定位。
- 建议提交：`R5-WP4: 增加附录B非阻断规则提示`。

### R5-WP5：完整报告 DOCX 拼装

- 输入：脱敏完整报告模板、九类数据、图片文件。
- 具体改动：实现九类表填充、动态人员行、图片分页、题注和书签；加入 draft/final 行为；确保 Ra/Rk 缺席。
- 失败行为：缺证据继续导出并返回 warning；文件损坏或数据库引用文件不存在时导出失败并给出类别和图片 ID。
- 测试：空附录 B、九类完整、多图、一页一图、横图、长题注、重复导出、DOCX 重新解析、字段目标完整性。
- 验收：Word 打开无修复提示；最终版无批注和示例占位符。
- 建议提交：`R5-WP5: 生成完整报告附录B`。

### R5-WP6：打包与阶段回归

- 输入：桌面运行时、备份恢复、全量测试。
- 具体改动：将新表纳入迁移完整性和备份审计；补充前后端与桌面打包契约测试。
- 失败行为：打包资源缺失时构建失败，不降级生成空附录。
- 测试：全量 `scripts/run_checks.ps1`、客户端安装态上传/导出/备份恢复。
- 验收：旧 A 项目可正常打开、导出；full_report 附录 B 可用。
- 建议提交：`R5-WP6: 完成附录B阶段验收`。

## 7. 迁移与回滚

- 升级前使用现有 SQLite 一致性备份机制创建备份。
- 迁移只新增表和索引，不改写 `evidence_images`、A 行或评分。
- 回滚应用版本时旧版本忽略新表；数据库不得降版本写入。
- 阶段内代码回滚可保留新表；再次升级时幂等复用。
- 删除项目时先在事务内删除记录，再安全删除该项目附录 B 目录；路径必须解析并确认仍位于 data root。

## 8. 风险与安全

- 证明材料可能含合同金额、手机号、签名、票据和人员信息，只存本地 data root，不上传远端。
- API 文件读取必须通过记录 ID 和项目归属解析，禁止客户端提交任意路径。
- 图片解码设置像素上限，防止压缩炸弹；上传大小限制与现有 DOCX 导入限制分开配置。
- 诊断日志不打印图片内容、绝对客户路径或完整人员信息。
- 删除和替换采用先数据库校验、后文件操作、失败补偿，避免跨项目删除。
- 示例真实报告及其图片不得提交 Git。

## 9. 阶段验收闸门

- schema 迁移、重复迁移和恢复测试通过。
- 九类枚举、顺序、条件字段与模板一致。
- 仅 PNG/JPEG；路径穿越、越权和损坏图片测试通过。
- 缺失证据只产生 warning，任何导出模式都不因此被阻止。
- 完整报告 DOCX 的九类表、图片、题注和书签结构回归通过。
- final DOCX 无批注、无示例占位符、无 Ra/Rk。
- 旧 appendix_a 项目行为和 XLSX 导出无回归。
- 桌面安装态完成一次上传、重排、替换、备份恢复和 DOCX 导出验收。

## 10. 分支、提交与 PR

- 建议分支：`codex/r5-appendix-b-evidence`，从最新 `main` 创建。
- 每个工作包独立提交，禁止把真实报告、上传图片、数据库、导出件或临时渲染产物加入提交。
- PR 标题：`R5: 增加附录B九类证据管理与导出`。
- PR 描述必须列出 schema 迁移、warning 非阻断、PNG/JPEG 限制、无 Ra/Rk Word 输出和安装态验收结果。
