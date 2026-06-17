# 测试目录

本目录存放附录A编写工具的单元测试、集成测试和样本文档结构回归测试。

当前覆盖：

- 模板 profile 解析和章节/table schema 校验。
- 原始样本文档只读结构回归。
- SQLite 结构化数据模型和章节详情契约。
- 证据图片元数据、排序、图号和质量提示。
- DOCX 生成器 editable/final 两类导出。
- 生成 DOCX 的 8 分节、8 表、内容控件、SEQ/REF 字段、书签、图片和表格 `tblGrid`。

运行方式：

```powershell
$env:PYTHONPATH="F:\Codex\FLA\backend"
.\backend\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
