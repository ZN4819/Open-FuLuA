# 附录A编写工具实施计划

## 1. 总体实施策略

实施采用分阶段交付。先搭建可运行的本地 Web 应用，再逐步加入结构化编辑、图片管理、DOCX 生成、字段引用、预览校验和回归测试。

推荐开发顺序：

1. 项目骨架与本地运行环境。
2. 模板 profile 与样本文档结构分析器。
3. A-1 至 A-8 结构化编辑。
4. 图片管理和引用 token。
5. DOCX 生成器。
6. 校验与异步预览。
7. 回归测试与打包说明。

阶段交付要求：

- 每个阶段完成后，必须将代码提交并推送到远程仓库 [ZN4819/FuLuA](https://github.com/ZN4819/FuLuA)。
- 阶段提交前应运行该阶段相关测试或完成手动验收。
- 如果测试无法运行，应在阶段总结中说明原因和风险。
- 提交信息建议包含里程碑编号，例如 `M1: 完成项目骨架`。
- 推送后确认远程仓库中能看到对应提交。

## 2. 目录结构

建议项目结构：

```text
F:\Codex\FLA
├─ 附录A编写.docx
├─ 附录A编写工具开发方案.md
├─ 附录A编写工具实施计划.md
├─ AGENTS.md
├─ frontend/
├─ backend/
├─ templates/
│  └─ appendix_a/
│     ├─ template_profile.json
│     ├─ record_templates.json
│     └─ README.md
├─ storage/
│  ├─ projects/
│  ├─ uploads/
│  ├─ exports/
│  └─ previews/
└─ tests/
```

`storage/` 用于运行时文件，后续可加入 `.gitignore`。原始 `附录A编写.docx` 不移动、不覆盖。

## 3. 阶段一：项目骨架

### 3.1 后端初始化

任务：

- 创建 `backend/`。
- 初始化 FastAPI 应用。
- 配置 SQLite。
- 定义基础配置文件。
- 增加健康检查接口。

建议文件：

```text
backend/app/main.py
backend/app/config.py
backend/app/database.py
backend/app/models.py
backend/app/schemas.py
backend/app/api/
backend/app/services/
backend/requirements.txt
```

基础接口：

```text
GET /api/health
GET /api/projects
POST /api/projects
GET /api/projects/{project_id}
PUT /api/projects/{project_id}
DELETE /api/projects/{project_id}
```

验收标准：

- 后端可启动。
- 健康检查返回正常。
- 可以创建项目并保存到 SQLite。

### 3.2 前端初始化

任务：

- 创建 `frontend/`。
- 初始化 `React + TypeScript + Vite`。
- 配置 API client。
- 建立基本布局。

建议页面结构：

```text
frontend/src/App.tsx
frontend/src/api/client.ts
frontend/src/pages/ProjectPage.tsx
frontend/src/components/Layout.tsx
frontend/src/components/SectionNav.tsx
```

验收标准：

- 前端可启动。
- 能显示项目页面。
- 能调用后端健康检查。
- 能创建并打开项目。

## 4. 阶段二：模板 Profile

### 4.1 创建 profile

任务：

- 创建 `templates/appendix_a/template_profile.json`。
- 固化页面设置、章节、表格 schema、列宽、字体、下拉选项、图片规则。

必须包含：

- A4 横向页面设置。
- A-1 至 A-8 章节定义。
- 技术测评表 schema。
- 管理测评表 schema。
- 技术指标下拉选项。
- 管理符合情况下拉选项。
- 图号和表号前缀。
- 图片最大宽度和 DPI 阈值。

验收标准：

- 后端可读取 profile。
- 单元测试确认 8 个章节和 2 类表格 schema 存在。

完成记录（2026-06-17）：

- 已创建 `templates/appendix_a/template_profile.json`。
- 已固化横向 A4 页面设置、A-1 至 A-8 章节、两类表格 schema、下拉选项、图片规则和样本文档基准指标。
- 已实现后端 profile 读取服务。
- 已提供 `GET /api/template-profile` 接口。

### 4.2 样本文档分析器

任务：

- 实现一个只读分析脚本，用于回归检查样本文档结构。
- 分析对象包括表格、字段、书签、图片、内容控件、分节、页面设置。

建议文件：

```text
backend/app/services/docx_analyzer.py
tests/test_sample_docx_analysis.py
```

样本文档基准指标：

- 8 个分节。
- 8 张核心表。
- 307 个下拉内容控件。
- 292 个 REF 字段。
- 179 个 SEQ 字段。
- 196 个图片对象。

验收标准：

- 测试可以读取原始样本文档。
- 分析结果与基准指标一致或在可解释范围内。

完成记录（2026-06-17）：

- 已实现 `backend/app/services/docx_analyzer.py`。
- 已支持统计分节、表格、内容控件、下拉控件、REF、SEQ、书签、图片和表格形态。
- 已新增 `tests/test_template_profile.py` 和 `tests/test_sample_docx_analysis.py`。
- 本地样本文档回归测试通过，确认 8 分节、8 表、307 下拉、292 REF、179 SEQ、196 图片对象。

## 5. 阶段三：结构化数据模型

### 5.1 数据库模型

实现实体：

- `Project`
- `AppendixSection`
- `AssessmentRow`
- `MetricResult`
- `EvidenceImage`
- `CrossReference`
- `RenderJob`
- `ValidationIssue`

任务：

- 建表。
- 提供 CRUD service。
- 项目创建时自动初始化 A-1 至 A-8 章节。

验收标准：

- 新建项目后自动生成 8 个章节。
- 每个章节包含默认标题和表题。
- 数据可保存、读取、更新。

完成记录（2026-06-17）：

- 已新增 `assessment_rows`、`metric_results`、`evidence_images`、`cross_references`、`render_jobs`、`validation_issues` 数据表。
- 已让数据库连接支持 `FULUA_DATABASE_PATH` 环境变量，便于测试使用临时 SQLite。
- 已实现测评行、评分结果和交叉引用的替换式保存。
- 已补充项目列表读取和删除能力，前端可在刷新浏览器后从已有项目列表重新打开或删除项目。
- 已新增结构化数据模型测试，确认新项目 8 个章节初始化、测评行和评分结果可保存读取。

### 5.2 章节 API

实现接口：

```text
GET /api/projects/{project_id}/sections/{code}
PUT /api/projects/{project_id}/sections/{code}
```

请求和响应应包含：

- 章节信息。
- 测评行列表。
- 每行评分或符合情况。
- 该章节图片列表。
- 引用关系列表。

验收标准：

- 前端可读取某一章节。
- 前端修改表格内容后可保存。

完成记录（2026-06-17）：

- 已新增 `GET /api/projects/{project_id}/sections/{code}`。
- 已新增 `PUT /api/projects/{project_id}/sections/{code}`。
- 章节详情响应包含章节信息、测评行列表、证据图片列表和交叉引用列表。
- 前端 API client 已补充章节详情读取和保存函数。
- 后端测试确认章节详情契约可返回管理类和技术类数据结构。

## 6. 阶段四：前端结构化编辑

### 6.1 章节导航

任务：

- 左侧展示 A-1 至 A-8。
- 显示章节标题。
- 显示校验状态摘要。

验收标准：

- 点击章节可切换。
- 未保存内容不丢失。

完成记录（2026-06-17）：

- 已在项目页按章节加载详情。
- 已为每个章节维护本地草稿缓存，切换章节不会丢失未保存内容。
- 章节导航已显示未保存状态。

### 6.2 测评表编辑器

组件建议：

```text
AssessmentTable.tsx
TechnicalAssessmentTable.tsx
ManagementAssessmentTable.tsx
AssessmentRowEditor.tsx
MetricSelect.tsx
ScoreInput.tsx
```

技术测评行字段：

- 测评单元
- 测评对象
- 结果记录
- D
- A
- K
- 对象评分
- 单元得分

管理测评行字段：

- 测评单元
- 测评对象
- 结果记录
- 符合情况
- 单元得分

验收标准：

- A-1 至 A-4 显示技术测评表。
- A-5 至 A-8 显示管理测评表。
- 下拉选项与 profile 一致。
- 文本和评分保存后可恢复。

完成记录（2026-06-17）：

- 已新增 `AssessmentTable` 组件。
- A-1 至 A-4 显示 D/A/K、对象评分、单元得分。
- A-5 至 A-8 显示符合情况和单元得分。
- 下拉选项来自模板 profile。
- 已补充结果记录模板选择，用户可套用当前测评单元模板后继续手动修改。
- 章节保存会调用 `PUT /api/projects/{project_id}/sections/{code}`，保存后可重新读取恢复。

补充完成记录（2026-06-17）：

- 已新增 `scripts/extract_record_templates.py`，从 `附录A编写.docx` 只读抽取结果记录模板。
- 已生成 `templates/appendix_a/record_templates.json`，覆盖 A-1 至 A-8 共 117 条模板。
- 已新增 `GET /api/record-templates` 接口，支持按 `section_code` 筛选。
- 已在前端结果记录输入区增加“套用结果模板”下拉。
- 已修正图片引用插入交互，引用 token 会按结果记录输入框的光标位置或选区插入，并可直接替换 `[插入图片引用]` 占位。
- 已新增结果记录模板测试，确认模板覆盖全部章节，并清理样本文档固定图号。
- 已将前端测评表录入改为固定测评单元分组：测评单元从当前章节结果记录模板去重提取，用户只在对应单元下新增测评对象。

### 6.3 引用 token 编辑

任务：

- 结果记录文本支持插入图片引用。
- 内部保存 `[[FIG:imageId]]`。
- UI 显示为当前计算出的 `图A-x-y`。

验收标准：

- 插入引用后保存。
- 图片排序变化后显示编号自动变化。
- 删除图片后引用检查显示断链问题。

完成记录（2026-06-17）：

- 当前阶段已支持在结果记录中插入 `[[FIG:pending-*]]` 引用 token 并保存为交叉引用草稿。
- 引用与真实图片排序、断链检查将在图片管理阶段继续完善。

## 7. 阶段五：图片管理

### 7.1 上传与元数据

接口：

```text
POST /api/projects/{project_id}/evidence
PUT /api/evidence/{image_id}
DELETE /api/evidence/{image_id}
```

上传后读取：

- 文件名。
- MIME 类型。
- 像素宽高。
- DPI。
- 建议显示宽高。

验收标准：

- 可上传 PNG/JPEG。
- 可显示缩略图。
- 可保存题注。

完成记录（2026-06-17）：

- 已新增 `POST /api/projects/{project_id}/evidence`、`PUT /api/evidence/{image_id}`、`DELETE /api/evidence/{image_id}`。
- 已使用 Pillow 读取图片像素、DPI 和建议显示尺寸。
- 已将上传文件保存到 `storage/uploads/`，并通过 `/api/files/` 提供本地预览访问。
- 前端已新增证据图片面板，支持 PNG/JPEG 上传、缩略图预览和题注编辑。

### 7.2 排序与编号

任务：

- 同一章节内图片可拖拽排序。
- 排序决定图号。
- 图号预览规则为 `图A-章节号-序号`。

验收标准：

- A-3 第 4 张图片显示为 `图A-3-4`。
- 排序后正文引用同步更新显示。

完成记录（2026-06-17）：

- 已新增 `PUT /api/projects/{project_id}/sections/{section_code}/evidence-order`。
- 同一章节图片支持拖拽排序，同时保留上移/下移按钮。
- 后端按章节内顺序返回 `图A-x-y` 图号预览。
- 结果记录插入引用时使用真实图片 ID 保存 `[[FIG:imageId]]`。
- 已补充测试确认 `A-3` 第 4 张图片显示为 `图A-3-4`。

### 7.3 图片质量校验

规则：

- 宽度超过 9.69 in 时自动缩放并提示。
- DPI 低于 120 时提示警告。
- 图片原始宽度超过页面可用宽度时提示自动缩放。

验收标准：

- 上传低 DPI 图片时能看到警告。
- 导出时不会生成超出页面可用宽度的图片。

完成记录（2026-06-17）：

- 已按照模板 profile 的图片最大宽度计算建议显示尺寸，超宽图片会自动按比例收缩到可用宽度。
- 已按照 DPI 阈值生成低清警告。
- 已对原始宽度超过页面可用宽度的图片生成自动缩放提示。
- 已新增图片元数据、DPI 和自动缩放提示测试。

## 8. 阶段六：DOCX 生成器

### 8.1 生成器结构

建议文件：

```text
backend/app/services/docx_generator/
├─ generator.py
├─ profile.py
├─ tables.py
├─ fields.py
├─ images.py
├─ content_controls.py
├─ styles.py
└─ validator.py
```

职责划分：

- `profile.py`：读取模板 profile。
- `tables.py`：生成两类测评表。
- `fields.py`：生成 `SEQ`、`REF`、书签和字段显示文本。
- `images.py`：插入图片和题注。
- `content_controls.py`：生成下拉内容控件。
- `styles.py`：应用字体、边框、底纹、段落格式。
- `generator.py`：编排完整导出流程。

完成记录（2026-06-17）：

- 已新增 `backend/app/services/docx_generator/` 生成器包。
- 已拆分实现 `generator.py`、`tables.py`、`fields.py`、`images.py`、`content_controls.py` 和 `styles.py`。
- 已新增 `POST /api/projects/{project_id}/exports/docx?mode=editable|final` 导出接口。
- 前端项目页已新增“导出可编辑版”和“导出最终版”按钮。
- 生成文件保存到 `storage/exports/`，原始样本文档不参与写入。

### 8.2 可编辑版导出

要求：

- 生成真实 Word 下拉内容控件。
- 每个控件有稳定 tag。
- 保留字段和书签。
- 保留可编辑表格。

验收标准：

- Word 打开后可修改下拉。
- 重新解析 DOCX 能读取控件 tag。

完成记录（2026-06-17）：

- 可编辑版 DOCX 已为技术测评 D/A/K 生成真实 Word 下拉内容控件。
- 可编辑版 DOCX 已为管理测评符合情况生成真实 Word 下拉内容控件。
- 每个控件包含稳定 tag，例如 `A1.row1.D`、`A5.row1.compliance`。
- 重新解析生成 DOCX 可统计到下拉内容控件。

### 8.3 最终版导出

要求：

- 下拉控件扁平化为普通文本。
- 字段显示文本固定。
- 文档更适合提交和归档。

验收标准：

- Word 打开后不需要用户更新字段即可看到图号和引用。
- 文档结构校验无错误。

完成记录（2026-06-17）：

- 最终版 DOCX 已将下拉控件扁平化为普通文本。
- 表号、图号和正文引用已写入字段缓存文本，打开后可直接看到当前编号。
- 导出后自动使用 DOCX 分析器检查 8 分节、8 表和 REF 目标完整性。
- 当前机器未安装 LibreOffice/soffice，视觉渲染 PNG QA 未完成；已完成结构级导出回归。

### 8.4 表格生成验收

必须检查：

- 8 张表均存在。
- 每张表列数与 schema 一致。
- `tblGrid` 与单元格宽度一致。
- 无固定行高。
- 合并单元格正确。
- 表头底纹和边框存在。

完成记录（2026-06-17）：

- 生成器已按 profile 列宽写入 `tblGrid` 和单元格 `tcW`。
- 生成器未设置固定行高，允许长文本自动换行和行高增长。
- 同一测评单元连续行会合并首列单元格。
- 表头已应用底纹，表格已应用边框。
- 已新增 `tests/test_docx_generator.py`，覆盖 editable/final 两类导出和表格 `tblGrid` 检查。

## 9. 阶段七：校验服务

接口：

```text
POST /api/projects/{project_id}/validate
```

校验内容：

- 必填字段缺失。
- 非法下拉值。
- 评分为空或格式错误。
- 引用 token 断链。
- 未使用图片。
- 图片低 DPI。
- 图片缺 alt。
- 导出 DOCX 字段目标缺失。

返回格式建议：

```json
{
  "issues": [
    {
      "severity": "warning",
      "code": "LOW_IMAGE_DPI",
      "message": "图片 DPI 低于 120",
      "targetType": "image",
      "targetId": "..."
    }
  ]
}
```

验收标准：

- 前端能展示错误、警告、提示。
- 错误可定位到章节、测评行或图片。

完成记录（2026-06-17）：

- 已新增 `backend/app/services/validator.py`。
- 已新增 `POST /api/projects/{project_id}/validate` 接口。
- 校验结果会替换式保存到 `validation_issues` 表。
- 已支持错误、警告、提示三类严重级别汇总。
- 已支持必填字段、非法下拉值、评分缺失/格式错误校验。
- 已支持 `[[FIG:imageId]]` 引用 token 断链和已保存交叉引用断链校验。
- 已支持未使用图片、低 DPI、图片过宽、缺少题注、本地图片文件缺失校验。
- 已在校验流程中生成并重新解析 final DOCX，检查导出 REF 目标完整性。
- 前端项目页已新增“校验项目”按钮、校验汇总和问题清单。
- 已新增 `tests/test_validation_service.py`，覆盖问题项目和无错误项目两类路径。

## 10. 阶段八：异步预览

### 10.1 任务接口

```text
POST /api/projects/{project_id}/render-jobs
GET /api/render-jobs/{job_id}
```

任务状态：

- `queued`
- `running`
- `succeeded`
- `failed`
- `timeout`

完成记录（2026-06-17）：

- 已新增 `POST /api/projects/{project_id}/render-jobs?mode=final|editable`。
- 已新增 `GET /api/render-jobs/{job_id}`。
- 已新增 `backend/app/services/preview.py`。
- 已扩展 `render_jobs` 表，记录输出 DOCX、输出 PDF、页数、日志路径和失败原因。
- 预览任务创建后返回 `queued`，后台继续生成 DOCX 和 PDF。
- 前端项目页已新增“生成预览”按钮，并轮询展示任务状态。

### 10.2 Word 导出策略

Windows 环境：

- 使用 Microsoft Word 自动化打开临时 DOCX。
- 导出 PDF。
- 读取页数。
- 保存日志。
- 设置超时时间，避免大文档卡死。

LibreOffice 环境：

- 使用 headless 模式导出 PDF。
- 如导出失败，返回错误日志。

验收标准：

- 小型测试文档可成功生成 PDF 预览。
- 大文档超时时不影响 DOCX 导出。
- 前端清楚显示预览失败原因。

完成记录（2026-06-17）：

- 已支持 LibreOffice/soffice headless 转 PDF。
- 已支持在安装 pywin32 的 Windows 环境下尝试 Microsoft Word 自动化转 PDF。
- 已设置渲染超时，超时会记录为 `timeout` 状态。
- 未找到 Word 或 LibreOffice 时，任务会记录为 `failed` 并保留日志。
- 当前开发机未安装 LibreOffice/soffice，真实 PDF 渲染由自动化测试中的模拟渲染器覆盖；缺少渲染器的失败路径已通过测试。
- 已新增 `tests/test_preview_jobs.py`，覆盖预览成功、页数统计、链接返回和无渲染器失败路径。

## 11. 阶段九：测试与回归

### 11.1 单元测试

测试项：

- profile 读取。
- 章节初始化。
- 图号计算。
- 引用 token 解析。
- 表格 schema 生成。
- 下拉控件 XML 生成。
- 图片宽度和 DPI 校验。
- REF 目标校验。

完成记录（2026-06-17）：

- 已形成覆盖模板、样本文档、结构化数据、图片、DOCX 生成、校验服务和预览任务的单元测试集。
- 已新增端到端回归测试 `tests/test_end_to_end_workflow.py`。
- 当前后端测试共 22 项，覆盖结构化编写到导出和预览任务的主流程。

### 11.2 集成测试

测试流程：

1. 创建项目。
2. 填写 A-1 技术测评行。
3. 填写 A-5 管理测评行。
4. 上传两张图片。
5. 插入图片引用。
6. 导出 editable DOCX。
7. 导出 final DOCX。
8. 重新解析 DOCX。
9. 确认表格、字段、书签、图片和控件正确。

完成记录（2026-06-17）：

- 已实现端到端回归：创建项目、填写 A-1 技术测评行、填写 A-5 管理测评行、创建两张证据图片、插入真实图片引用、执行校验、导出 editable/final DOCX、重新解析 DOCX、模拟预览任务成功路径。
- 已确认 generated DOCX 包含 8 分节、8 表、2 张图片、2 个 REF 字段，且 final 版无下拉内容控件、REF 目标完整。

### 11.3 样本文档回归

对原始样本文档只读分析：

- 分节数量。
- 核心表数量。
- 下拉控件数量。
- REF 和 SEQ 数量。
- 图片对象数量。
- 表格固定布局特征。

验收标准：

- 回归测试稳定。
- 生成文档结构与 profile 预期一致。

完成记录（2026-06-17）：

- 原始样本文档回归测试继续保持只读分析。
- 样本文档基准仍覆盖 8 分节、8 表、307 下拉、292 REF、179 SEQ、196 图片对象。
- 已新增 `scripts/run_checks.ps1`，用于统一运行后端测试、Python 编译、前端构建和高风险依赖审计。
- 已新增 `docs/本地运行与打包说明.md`，记录首次安装、本地启动、完整检查、预览渲染器和交付清单。

## 12. 里程碑

### M1：可运行骨架

交付：

- 前后端可启动。
- 项目可创建和保存。
- A-1 至 A-8 可导航。
- 完成阶段提交并推送到 `ZN4819/FuLuA`。

完成记录（2026-06-17）：

- 已创建后端 FastAPI 应用骨架。
- 已实现 SQLite 数据库初始化。
- 已实现项目创建、读取、更新接口。
- 新项目会自动初始化 A-1 至 A-8 章节。
- 已创建前端 React/Vite 应用骨架。
- 已实现项目创建页和 A-1 至 A-8 章节导航。
- 已新增 README.md，记录本地运行方式和阶段提交要求。

### M2：结构化编辑

交付：

- 两类测评表编辑器。
- 下拉和评分录入。
- 自动保存。
- 完成阶段提交并推送到 `ZN4819/FuLuA`。

### M3：图片与引用

交付：

- 图片上传、排序、题注。
- 引用 token 插入。
- 图号预览。
- 引用检查。
- 完成阶段提交并推送到 `ZN4819/FuLuA`。

### M4：DOCX 导出

交付：

- 可编辑版 DOCX。
- 最终版 DOCX。
- 表格、字段、图片、书签和控件完整。
- 完成阶段提交并推送到 `ZN4819/FuLuA`。

### M5：预览与校验

交付：

- 校验服务。
- 异步预览任务。
- 预览失败日志。
- 完成阶段提交并推送到 `ZN4819/FuLuA`。

### M6：测试与打包

交付：

- 单元测试和集成测试。
- 样本文档回归测试。
- 本地运行说明。
- 完成阶段提交并推送到 `ZN4819/FuLuA`。

完成记录（2026-06-17）：

- 已完成阶段 9 测试与回归增强。
- 已完成本地运行与打包说明。
- 已验证 `scripts/run_checks.ps1` 可正常执行完整检查。

## 13. UI 布局优化专项计划

当前核心功能已基本成型，下一步进入 UI 布局优化专项。专项目标不是新增业务能力，而是把已有创建、打开、删除、录入、图片、校验、预览和导出流程整理成更适合长期使用的工作台界面。

详细执行文件：

```text
docs/UI布局优化实施计划.md
```

专项拆分：

1. UI-1：基础视觉系统与布局骨架。
2. UI-2：首页项目入口优化。
3. UI-3：项目工作台顶部与章节摘要优化。
4. UI-4：左侧章节导航优化。
5. UI-5：测评表录入体验优化。
6. UI-6：证据图片管理面板优化。
7. UI-7：校验、预览与导出反馈优化。
8. UI-8：响应式与视觉验收。

验收重点：

- 不改变现有后端接口、数据库结构和 DOCX 生成规则。
- 创建项目、打开已有项目、删除项目、编辑保存、套用结果记录模板、按光标位置插入图片引用、上传图片、排序图片、校验、预览和导出流程保持可用。
- 桌面和窄屏下不出现文字重叠、按钮溢出或关键操作不可访问。
- 前端构建通过，专项完成后执行统一检查脚本。
- 阶段完成后同步更新 README、开发方案、实施计划，并提交推送到远程仓库。

完成记录（2026-06-17）：

- 已确认 UI 优化方向。
- 已新增 `docs/UI布局优化实施计划.md`。
- 已同步更新 README、开发方案和实施计划。
- 已完成 UI-1：基础视觉系统与布局骨架，统一前端视觉 token、按钮、输入框、面板、状态徽标和响应式外壳。
- 已为技术表和管理表建立表格类型类名及稳定列宽，并保留测评表内部横向滚动。
- 已通过 `npm run build`、`git diff --check`、桌面视口和窄屏视口浏览器检查。
- 已完成 UI-2：首页项目入口优化，将新建项目和已有项目分区展示，补充项目数量、创建时间、更新时间、空状态和加载状态。
- 已验证已有项目可从首页打开，删除操作继续保留确认并使用弱危险样式。
- 已完成 UI-3：项目工作台顶部按返回、校验/预览、导出分组展示，项目级保存状态和当前章节持续可见。
- 已优化章节摘要，集中展示本章保存状态、测评对象数量和证据图片数量，并已通过桌面与窄屏视口检查。
- 已完成 UI-4：左侧章节导航改为章节目录式索引，当前章节高亮和未保存状态更加清晰。
- 已在窄屏下将章节导航改为横向滚动条，确保 A-1 至 A-8 可稳定访问且不造成页面级横向溢出。
- 已完成 UI-5：测评表工具条展示测评对象、固定单元、模板和证据数量，结果记录输入、模板套用和图片引用下拉形成同组控件。
- 已补充固定测评单元录入：测评单元第一列改为只读分组，每个固定单元内可新增测评对象，模板下拉仅显示本单元模板。
- 已收紧 D/A/K、符合情况和评分输入列宽，技术表与管理表均保持内部横向滚动，不造成页面级横向溢出。
- 已完成 UI-6：证据图片管理面板按图片工作流重排，上传区明确图片文件和题注字段，顶部展示图片数量和质量提示数量。
- 已将证据图片卡片改为稳定网格，集中展示图号、原文件名、排序、缩略图、尺寸、DPI、建议显示尺寸、质量提示、题注编辑和上移/下移/保存/删除操作。
- 已通过 `npm run build`、`git diff --check`、桌面视口和 390px 窄屏视口浏览器检查；证据面板无页面级横向溢出，宽表和章节导航继续保持内部滚动。
- 已完成 UI-7：校验结果改为反馈面板，顶部展示发现问题总数，错误、警告、提示使用三组摘要块，问题明细按严重程度排序并保留编码、目标类型和目标 ID。
- 已将预览任务改为状态面板，展示任务状态、PDF 页数、生成模式、PDF/DOCX/日志入口，并将常见失败原因转换为用户可理解的提示。
- 已通过 `npm run build`、`git diff --check`、桌面视口和 390px 窄屏视口浏览器检查；本机未找到 Word 或 LibreOffice 渲染器时，预览失败态和日志入口可正常展示。
- 已完成 UI-8：在 1280px、1024px、820px、390px 和 360px 视口下完成首页和项目工作台响应式验收，确认工作台操作、章节导航、测评表、模板套用、图片引用、上传入口、校验反馈、预览入口和导出入口均可访问。
- 已确认页面级无横向溢出；章节导航和测评表继续使用各自内部横向滚动，未干扰页面纵向滚动。
- 已新增 `docs/UI-8响应式视觉验收记录.md`，记录视口、检查项、浏览器结果和完整检查脚本结果。
- 已通过 `scripts/run_checks.ps1`，覆盖 28 项后端/集成测试、Python 编译、前端构建和 `npm audit --audit-level=high`。

## 14. 开发验收清单

功能验收：

- 用户能从零创建一个附录A项目。
- 用户能填写 A-1 至 A-8。
- 用户能上传证据图片并插入引用。
- 系统能自动生成图号和表号。
- 系统能导出 editable/final 两类 DOCX。
- 系统能报告引用、图片和字段问题。

文档验收：

- DOCX 为横向 A4。
- 8 张核心表存在。
- 技术表和管理表列结构正确。
- 图片不超出页面可用宽度。
- 图题和正文引用一致。
- REF 字段目标完整。
- 下拉选项符合规则。

质量验收：

- 后端测试通过。
- 前端主要流程可手动验证。
- 原始样本文档未被修改。

## 15. 后续扩展方向

可在第一版稳定后扩展：

- 导入现有项目 DOCX 并映射为结构化数据。
- 多模板 profile 管理。
- 批量图片 OCR 或图片自动命名。
- 评分公式自动计算。
- 与报告正文联动生成交叉引用。
- 多用户协作和审批流程。
