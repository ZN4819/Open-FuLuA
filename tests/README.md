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
- 校验服务规则：必填字段、下拉值、评分格式、引用断链、图片质量、未引用图片和无错误路径。
- 异步预览任务：任务状态更新、PDF 页数统计、输出链接和无渲染器失败记录。
- 端到端工作流：创建项目、填写 A-1/A-5、图片引用、校验、DOCX 导出、重新解析和预览任务。

运行方式：

```powershell
$env:PYTHONPATH="F:\Codex\FLA\backend"
.\backend\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

完整检查：

```powershell
.\scripts\run_checks.ps1
```
