# 附录A编写工具

这是一个面向“附录A测评结果记录”的本地 Web 应用项目。工具目标是把 A-1 至 A-8 的测评结果、评分、符合情况、证据图片、题注和交叉引用结构化维护，并导出符合样本文档格式的 DOCX。

## 当前阶段

当前已完成阶段 9：测试、回归与本地打包说明，并补充了结果记录模板库功能。当前已确认 UI 布局优化方向，并完成 UI-1 基础视觉系统与布局骨架、UI-2 首页项目入口优化、UI-3 项目工作台顶部与章节摘要优化、UI-4 左侧章节导航优化、UI-5 测评表录入体验优化、UI-6 证据图片管理面板优化、UI-7 校验预览反馈优化、UI-8 响应式与视觉验收，并补充项目级全部保存入口。

已包含：

- 后端 FastAPI 应用骨架。
- SQLite 本地数据库初始化。
- 项目创建、读取、更新接口。
- 新项目自动初始化 A-1 至 A-8 章节。
- 首页可列出、打开和删除已有项目，浏览器刷新后可继续进入之前创建的项目。
- 前端 React/Vite 应用骨架。
- 项目创建页面和章节导航。
- 附录A模板 profile。
- DOCX 结构分析器。
- 样本文档结构回归测试。
- 测评行、评分结果、证据图片、交叉引用、渲染任务和校验问题数据表。
- 章节详情读取与保存接口。
- 前端按章节读取详情并缓存本地草稿。
- A-1 至 A-4 技术测评表编辑器。
- A-5 至 A-8 管理测评表编辑器。
- 测评表按模板库固定展示 A-1 至 A-8 的测评单元，用户在对应测评单元下新增测评对象。
- 结果记录引用 token 插入和保存。
- 章节切换时保留未保存草稿，并在导航中显示未保存状态。
- 项目工作台支持一次性保存所有未保存章节。
- PNG/JPEG 证据图片上传、预览、删除。
- 图片像素、DPI、建议显示宽高自动读取。
- 图片题注维护。
- 同章节图片拖拽排序和上移/下移排序。
- 按章节顺序生成 `图A-x-y` 图号预览。
- 结果记录可插入真实图片 ID 的 `[[FIG:imageId]]` 引用 token。
- 低 DPI、图片过宽等图片质量提示。
- 可导出可编辑版 DOCX。
- 可导出最终版 DOCX。
- 导出 DOCX 包含 8 个横向 A4 分节和 8 张核心测评表。
- 导出 DOCX 已补齐“附录A测评结果记录”总标题，章节标题、表题、技术表两行合并表头和表格外粗内细边框按样本文档规则生成。
- 导出 DOCX 已在“测评单元得分”表头下生成样本文档中的 Word 公式：技术表为测评对象评分均值公式，管理表为 `S_i,j`。
- 导出 DOCX 的测评单元列按样本文档使用灰底、加粗、水平居中和垂直居中；同一测评单元跨多行时合并显示且单元名称只出现一次。
- 可编辑版 DOCX 为 D/A/K 和符合情况生成 Word 下拉内容控件。
- 最终版 DOCX 将下拉控件扁平化为普通文本。
- 导出时生成表号、图号、`SEQ` 字段、`REF` 字段和书签。
- 导出后会重新解析 DOCX，检查分节、表格和 REF 目标完整性。
- 可一键校验项目数据。
- 校验结果分为错误、警告和提示。
- 可检查必填字段、非法下拉值、评分缺失或格式错误。
- 可检查图片引用断链、未引用图片、低 DPI、图片过宽、缺少题注。
- 可检查导出 DOCX 的 REF 目标完整性。
- 前端可展示校验汇总和问题清单。
- 可创建异步预览任务。
- 预览任务支持 `queued`、`running`、`succeeded`、`failed`、`timeout` 状态。
- 可使用 LibreOffice headless 或 Microsoft Word 自动化生成 PDF 预览。
- 预览任务会记录 DOCX、PDF、页数、日志和失败原因。
- 前端可展示预览任务状态、PDF 链接、DOCX 链接和日志链接。
- 已补充端到端回归测试，覆盖创建项目、填写 A-1/A-5、插入图片引用、校验、导出和预览任务。
- 已提供统一检查脚本 `scripts/run_checks.ps1`。
- 已提供本地运行与打包说明 `docs/本地运行与打包说明.md`。
- 已从样本文档抽取 117 条结果记录模板，录入结果记录时可选择套用模板或手动填写。
- 已从结果记录模板库提取各章节固定测评单元，测评单元不再需要手动填写。
- 套用模板时，原样本文档中的固定图号会以 `[插入图片引用]` 占位，真实引用仍通过图片引用下拉生成。
- 插入图片引用时会优先使用结果记录输入框的光标位置或选区；若光标落在 `[插入图片引用]` 占位处，会直接替换该占位。
- 已新增 UI 布局优化专项计划 `docs/UI布局优化实施计划.md`，后续将按计划优化首页、项目工作台、章节导航、测评表、证据图片区和校验预览反馈。
- 已完成 UI-1：统一前端颜色、间距、圆角、按钮、输入框、面板、状态徽标和基础响应式外壳，并为技术表和管理表建立稳定列宽基础。
- 已完成 UI-2：首页按“新建项目”和“已有项目”分区，已有项目列表展示项目数量、创建时间、更新时间和打开/删除操作。
- 已完成 UI-3：项目工作台顶部按返回/全部保存、校验预览、导出三组操作分区，展示项目级保存状态和当前编辑章节；章节摘要展示本章保存状态、测评对象数和证据数量，并通过桌面与窄屏浏览器检查。
- 已完成 UI-4：左侧章节导航改为章节目录式索引，章节编号、标题、当前状态和未保存状态更加清晰；窄屏下改为横向滚动章节条，保持 A-1 至 A-8 可稳定访问。
- 已完成 UI-5：测评表工具条展示测评对象、固定单元、模板和证据数量状态；结果记录、模板套用和图片引用形成同组控件；D/A/K、符合情况和评分列更紧凑，表格继续在自身容器内横向滚动。
- 已完成 UI-6：证据图片管理面板新增图片数量和质量提示统计；上传表单显示明确字段标签；图片卡片集中展示图号、原文件名、缩略图、尺寸、DPI、显示尺寸、质量提示、题注和排序操作。
- 已完成 UI-7：校验结果改为反馈面板，错误、警告、提示计数和问题明细更加清楚；预览任务展示状态、页数、模式、PDF/DOCX/日志入口和更易理解的失败原因。
- 已完成 UI-8：在 1280px、1024px、820px、390px 和 360px 视口下完成响应式与视觉验收，确认工作台、章节导航、测评表、证据图片、校验反馈和首页入口均无页面级横向溢出；完整检查脚本 `scripts/run_checks.ps1` 已通过。
- 已补充固定测评单元录入：测评表第一列改为只读分组，固定单元来自结果记录模板库，用户只在对应单元下新增、删除和编辑测评对象。
- 已补充全部保存：顶部“全部保存”按钮会一次性提交所有未保存章节，保存期间暂停校验、预览、导出和返回操作。

## 模板 Profile

模板规则位于：

```text
templates/appendix_a/template_profile.json
```

当前 profile 固化了：

- 横向 A4 页面设置。
- A-1 至 A-8 章节和表题。
- 技术测评 8 列表格 schema。
- 管理测评 5 列表格 schema。
- 总标题、章节标题、表题、表头和正文的字体、字号、段落间距规则。
- 表格外框与内框线宽规则：外框加粗黑色边框，内部网格细边框。
- 测评单元列正文规则：灰底、加粗、居中、垂直居中，同单元多行合并后只保留一份单元名称。
- 测评单元得分表头公式规则：技术表使用均值公式，管理表使用 `S_i,j`。
- 两类下拉控件选项。
- 图片宽度、DPI 和内联放置规则。
- 样本文档结构基准指标。

结果记录模板库位于：

```text
templates/appendix_a/record_templates.json
```

当前模板库根据 `附录A编写.docx` 只读抽取生成，包含 A-1 至 A-8 的测评单元、测评对象和结果记录正文。重新生成模板库可运行：

前端会从该模板库按章节提取去重后的固定测评单元，用于测评表分组展示；测评单元本身不再作为用户手动填写项。

```powershell
.\backend\.venv\Scripts\python.exe scripts\extract_record_templates.py
```

## 目录结构

```text
backend/      后端服务
frontend/     前端应用
templates/    模板 profile 与模板说明
docs/         项目说明、运行说明和专项实施计划
storage/      本地上传、导出和预览产物
tests/        测试
```

原始样本文档 `附录A编写.docx` 仅作为格式分析和回归基准，不应被覆盖或直接修改。

## 后端运行

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

默认服务地址：

```text
http://127.0.0.1:8000
```

健康检查：

```text
GET http://127.0.0.1:8000/api/health
```

模板 profile 接口：

```text
GET http://127.0.0.1:8000/api/template-profile
```

结果记录模板接口：

```text
GET http://127.0.0.1:8000/api/record-templates
GET http://127.0.0.1:8000/api/record-templates?section_code=A-1
```

章节详情接口：

```text
GET http://127.0.0.1:8000/api/projects
POST http://127.0.0.1:8000/api/projects
GET http://127.0.0.1:8000/api/projects/{project_id}
PUT http://127.0.0.1:8000/api/projects/{project_id}
DELETE http://127.0.0.1:8000/api/projects/{project_id}
GET http://127.0.0.1:8000/api/projects/{project_id}/sections/{code}
PUT http://127.0.0.1:8000/api/projects/{project_id}/sections/{code}
```

`PUT` 请求可一次性保存章节标题、表题、测评行、评分结果和引用 token。

证据图片接口：

```text
POST   http://127.0.0.1:8000/api/projects/{project_id}/evidence
PUT    http://127.0.0.1:8000/api/evidence/{image_id}
DELETE http://127.0.0.1:8000/api/evidence/{image_id}
PUT    http://127.0.0.1:8000/api/projects/{project_id}/sections/{code}/evidence-order
```

上传图片会存入 `storage/uploads/`，接口会返回缩略图访问地址、图号、图片尺寸、DPI、建议显示尺寸和质量提示。

DOCX 导出接口：

```text
POST http://127.0.0.1:8000/api/projects/{project_id}/exports/docx?mode=editable
POST http://127.0.0.1:8000/api/projects/{project_id}/exports/docx?mode=final
```

导出文件会生成到 `storage/exports/`，接口会直接返回可下载的 DOCX。

项目校验接口：

```text
POST http://127.0.0.1:8000/api/projects/{project_id}/validate
```

校验结果会写入 `validation_issues`，接口会返回错误、警告、提示数量和问题清单。

异步预览接口：

```text
POST http://127.0.0.1:8000/api/projects/{project_id}/render-jobs?mode=final
GET  http://127.0.0.1:8000/api/render-jobs/{job_id}
```

预览文件和日志会生成到 `storage/previews/`。如果本机未安装 Microsoft Word 或 LibreOffice，任务会进入 `failed` 状态并返回清晰失败原因。

## 前端运行

```powershell
cd frontend
npm install
npm run dev
```

默认前端地址：

```text
http://127.0.0.1:5173
```

如需调整后端地址，可设置：

```text
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## 阶段提交要求

每个实施阶段完成后，都需要：

1. 更新相关文档。
2. 运行该阶段可运行的测试或手动检查。
3. 提交代码。
4. 推送到远程仓库 [ZN4819/FuLuA](https://github.com/ZN4819/FuLuA)。

重要变更应在独立阶段分支中完成，例如：

```text
stage2-template-profile
```

## 测试

运行后端结构测试：

```powershell
$env:PYTHONPATH="F:\Codex\FLA\backend"
.\backend\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前测试覆盖：

- 模板 profile 结构。
- 结果记录模板库结构、章节覆盖和旧图号清理。
- 样本文档结构回归。
- 新项目 8 个章节初始化。
- 已有项目列表、重新打开和删除。
- 测评行、评分结果和交叉引用保存读取。
- 章节详情 API 契约。
- 图片尺寸、DPI、自动缩放提示和图号生成。
- 图片排序保存。
- DOCX 生成器结构测试：8 分节、8 表、下拉内容控件、SEQ/REF 字段、书签、图片和表格 `tblGrid`。
- DOCX 模板格式测试：总标题、章节标题、表题、技术表两行合并表头、外粗内细表格边框。
- 测评单元列格式测试：灰底加粗、居中、垂直居中、合并后不重复拼接单元名称。
- 测评单元得分表头公式测试：检查技术表和管理表的 OMML 公式文本。
- 可编辑版与最终版导出差异测试。
- 校验服务测试：必填字段、下拉值、评分格式、引用断链、图片质量、未引用图片和无错误路径。
- 异步预览任务测试：成功生成 PDF 的状态更新、页数统计和缺少渲染器时的失败记录。
- 前端结构化编辑器通过 TypeScript 构建检查。

视觉渲染说明：

- DOCX 视觉渲染需要本机安装 Microsoft Word 或 LibreOffice。
- 当前自动化测试会先做结构级回归；安装 LibreOffice 后可继续接入 PDF/PNG 预览验证。

构建前端：

```powershell
cd frontend
npm run build
```

一键运行完整检查：

```powershell
.\scripts\run_checks.ps1
```

本地运行、预览渲染器和交付清单见：

```text
docs/本地运行与打包说明.md
```
