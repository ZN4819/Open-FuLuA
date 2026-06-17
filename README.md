# 附录A编写工具

这是一个面向“附录A测评结果记录”的本地 Web 应用项目。工具目标是把 A-1 至 A-8 的测评结果、评分、符合情况、证据图片、题注和交叉引用结构化维护，并导出符合样本文档格式的 DOCX。

## 当前阶段

当前已完成阶段 9：测试、回归与本地打包说明，并补充了结果记录模板库功能。

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
- 结果记录引用 token 插入和保存。
- 章节切换时保留未保存草稿，并在导航中显示未保存状态。
- PNG/JPEG 证据图片上传、预览、删除。
- 图片像素、DPI、建议显示宽高自动读取。
- 图片题注和 alt 文本维护。
- 同章节图片拖拽排序和上移/下移排序。
- 按章节顺序生成 `图A-x-y` 图号预览。
- 结果记录可插入真实图片 ID 的 `[[FIG:imageId]]` 引用 token。
- 低 DPI、缺少 alt 文本等图片质量提示。
- 可导出可编辑版 DOCX。
- 可导出最终版 DOCX。
- 导出 DOCX 包含 8 个横向 A4 分节和 8 张核心测评表。
- 可编辑版 DOCX 为 D/A/K 和符合情况生成 Word 下拉内容控件。
- 最终版 DOCX 将下拉控件扁平化为普通文本。
- 导出时生成表号、图号、`SEQ` 字段、`REF` 字段和书签。
- 导出后会重新解析 DOCX，检查分节、表格和 REF 目标完整性。
- 可一键校验项目数据。
- 校验结果分为错误、警告和提示。
- 可检查必填字段、非法下拉值、评分缺失或格式错误。
- 可检查图片引用断链、未引用图片、低 DPI、缺少 alt 文本、缺少题注。
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
- 套用模板时，原样本文档中的固定图号会以 `[插入图片引用]` 占位，真实引用仍通过图片引用下拉生成。
- 插入图片引用时会优先使用结果记录输入框的光标位置或选区；若光标落在 `[插入图片引用]` 占位处，会直接替换该占位。

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
- 两类下拉控件选项。
- 图片宽度、DPI 和内联放置规则。
- 样本文档结构基准指标。

结果记录模板库位于：

```text
templates/appendix_a/record_templates.json
```

当前模板库根据 `附录A编写.docx` 只读抽取生成，包含 A-1 至 A-8 的测评单元、测评对象和结果记录正文。重新生成模板库可运行：

```powershell
.\backend\.venv\Scripts\python.exe scripts\extract_record_templates.py
```

## 目录结构

```text
backend/      后端服务
frontend/     前端应用
templates/    模板 profile 与模板说明
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
- 图片尺寸、DPI、alt 文本提示和图号生成。
- 图片排序保存。
- DOCX 生成器结构测试：8 分节、8 表、下拉内容控件、SEQ/REF 字段、书签、图片和表格 `tblGrid`。
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
