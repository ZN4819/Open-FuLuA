# 测试目录

本目录存放附录A编写工具的单元测试、集成测试和样本文档结构回归测试。

当前覆盖：

- 模板 profile 解析和章节/table schema 校验。
- 结果记录模板库解析、章节覆盖、按章节筛选和旧图号清理。
- 原始样本文档只读结构回归。
- SQLite 结构化数据模型、已有项目列表、项目删除和章节详情契约。
- 证据图片元数据、排序、图号和质量提示。
- DOCX 生成器 editable/final 两类导出。
- 生成 DOCX 的 8 分节、8 表、内容控件、SEQ/REF 字段、书签、图片和表格 `tblGrid`。
- 校验服务规则：必填字段、下拉值、评分格式、引用断链、图片文件与题注、未引用图片和无错误路径。
- 后端历史预览任务服务：任务状态更新、PDF 页数统计、输出链接和无渲染器失败记录；当前前端不再提供创建预览任务入口。
- 端到端工作流：创建项目、填写 A-1/A-5、图片引用、校验、DOCX 导出和重新解析。
- R6 既有完整报告迁移：Schema 9 升级、路由契约、模板结构指纹、危险 OOXML 包拒绝、源文件只读和桌面迁移审阅路由。

运行方式：

```powershell
$env:PYTHONPATH=(Resolve-Path ".\backend").Path
.\backend\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

真实报告仅用于本机 R6 只读验收，测试不会自动搜索仓库文件，也不会把真实文件名写入源码或输出。需要运行该项时显式指定本地路径；未指定时自动跳过：

```powershell
$env:FULUA_R6_CUSTOMER_DOCX="X:\local-only\report.docx"
$env:PYTHONPATH=(Resolve-Path ".\backend").Path
.\backend\.venv\Scripts\python.exe -m unittest tests.test_r6_report_import_contract -v
```

该验收在迁移前后比较源文件 SHA-256、字节数和修改时间；迁移仅写入隔离的数据根目录副本。

完整检查：

```powershell
.\scripts\run_checks.ps1
```
