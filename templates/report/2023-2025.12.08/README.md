# 完整报告运行时资产包

该目录对应三级报告模板 `2023` 版、机构修订版 `2025-12-08`。

- `field_dictionary.json`：稳定字段 ID、数据来源、敏感级别和导出槽位。
- `rule_hints.json`：121 条批注的脱敏追踪初稿，全部保持 `pending`；不含作者、时间、原文和绝对路径。
- `narrative_templates.json`：不含客户默认值的可选正文草稿模板，套用后必须人工确认。
- `runtime_template.docx`：经 OPC 白名单重建的脱敏母版。
- `manifest.json`：17 个分节、55 张表、控件和禁用结构的稳定契约。
- `asset_hashes.json`：五个运行时资产的 SHA-256 清单，其自身摘要固定在代码侧 registry。

DOCX 兼容性以 Microsoft Word 无修复提示打开为唯一权威验收。LibreOffice 不用于本资产包的结构分析、字段刷新或交付判定。

原始模板和客户报告仅作为本地只读输入，不属于运行时资产，也不得提交或打包。
