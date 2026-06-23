# DI-8 DOCX 导入专项验收记录

验收日期：2026-06-23

验收分支：`codex/di-8-import-e2e`

## 验收范围

本阶段验收 DOCX 导入新项目专项的闭环能力，重点确认本工具导出的 DOCX 可以重新导入为新项目，并继续接入现有结构化编写流程。

覆盖范围：

- editable DOCX 导出后重新导入。
- final DOCX 导出后重新导入。
- A-1 技术测评表核心字段恢复。
- A-5 管理测评表核心字段恢复。
- 证据图片复制到新项目运行目录。
- 图号引用从导入临时 token 替换为真实图片 token。
- 交叉引用目标图片保持一致。
- 导入项目可继续校验和导出。

## 自动化验收

新增测试文件：

```text
tests/test_docx_import_roundtrip.py
```

新增测试项：

- `test_editable_docx_roundtrip_import_preserves_rows_images_and_references`
- `test_final_docx_roundtrip_import_preserves_core_fields_without_content_controls`

验证内容：

- 创建源项目并写入 A-1、A-5 两类测评行。
- 上传两张证据图片并插入结果记录引用。
- 导出 editable DOCX 后上传解析并确认导入。
- 导出 final DOCX 后上传解析并确认导入。
- 比对导入后的测评单元、测评对象、结果记录、D/A/K、符合情况、评分、图片数量、引用数量和文件落盘情况。
- 对导入项目执行数据校验。
- 对 editable 导入后的项目再次导出 final DOCX，并检查表格、图片、REF 字段和 REF 目标完整性。

阶段新增测试已单独通过：

```powershell
.\backend\.venv\Scripts\python.exe -m unittest tests.test_docx_import_roundtrip -v
```

统一检查脚本已通过：

```powershell
.\scripts\run_checks.ps1
```

当前自动化测试共 94 项，全部通过。

## 验收结论

DI-8 自动化回归通过。DOCX 导入专项已经完成第一版闭环：本工具导出的 editable/final DOCX 可以作为新项目导入，导入后项目可以继续使用章节编辑、图片引用、校验和 DOCX 导出能力。

当前导入范围仍以本工具导出的 DOCX 或结构兼容的附录A文档为主，不承诺任意第三方 Word 文档的无损导入。若后续需要支持非同源 DOCX，应另行增加字段映射、导入修复界面和更宽松的结构识别策略。

## 后续建议

- 增加前端浏览器级导入手动验收截图或 Playwright 自动化。
- 支持导入任务历史列表和失败任务清理。
- 为非同源 DOCX 增加导入前字段映射和章节匹配确认。
