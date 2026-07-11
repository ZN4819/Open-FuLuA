# CD-8 客户端封装验收记录

## 验收结论口径

本记录用于 `0.1.0-rc.1` 未签名预发布候选。安装包校验值、可执行文件签名状态和自动化检查项由 `scripts/test_desktop_acceptance.ps1` 在运行时生成脱敏 JSON 证据；临时路径、会话凭据和测试项目名不会写入证据。动态值不固化在本文，复核时应以脚本运行时输出为准，并核对 CI 上传的 `acceptance-evidence.json`。

只有实际执行并产生证据的项目才能标记为通过。结果分为“自动化通过”“人工验收”“环境性跳过”和“尚未完成”，避免把源码检查或计划事项写成已完成事实。

## 自动化通过

2026-07-11 已在当前 Windows 开发机对重新构建的 `0.1.0-rc.1` NSIS 候选执行一次完整验收，脚本返回 `status: passed`，下列自动化字段均为 `true`。迁移前后源数据库 SHA-256 一致；Setup、桌面 EXE 和侧车 EXE 的 Authenticode 状态均为 `NotSigned`，因此本次结论仅适用于未签名预发布候选。

执行命令：

```powershell
.\scripts\build_desktop.ps1 -Target nsis
$setup = Get-ChildItem .\artifacts\desktop\electron\*Setup*.exe -File | Select-Object -First 1
.\scripts\test_desktop_acceptance.ps1 -InstallerPath $setup.FullName -EvidenceOutputPath .\artifacts\desktop\electron\acceptance-evidence.json
```

验收脚本必须返回 `status: passed`，且以下字段均为 `true`：

- 安装包、程序资源和 `app.asar` 内容检查。
- 打包侧车能够加载 A-1 固定测评单元模板槽位。
- 新建项目、保存 A-1 结果记录和上传证据图片。
- 项目校验、editable/final DOCX 导出。
- editable DOCX 上传解析并创建新项目。
- 关闭重开后两个项目的测评行、评分、图片元数据、图片文件哈希和交叉引用保持一致。
- 通过真实打包侧车执行迁移预检和迁移，且迁移前后源数据库 SHA-256 不变。
- 迁移后及卸载重装后再次验证相同的深层业务状态。

证据 JSON 记录源码提交、候选版本、安装包文件名与 SHA-512、三个组件的实际签名状态、检查项布尔值和未完成人工项。发布工作流在最终 RC 构建后重新执行验收并上传该文件，避免把旧构建或仅有终端文本的结果当作发布证据。

全量质量闸门还包括 `scripts/run_checks.ps1`、桌面 TypeScript 类型检查、前端与桌面 high 级依赖审计、`latest.yml` 校验和 `git diff --check`。具体测试数量和安装包 SHA-512 不在本文固化，以本次运行输出和 CI 工件为准。

## 人工验收

发布负责人需在可交互 Windows 桌面确认：

- 从文件选择器上传图片、在结果记录输入框使用 `Ctrl+V` 粘贴截图，并检查题注和图号。
- 客户端菜单、诊断页、日志目录和备份恢复入口均可见且文案正确。
- 安装、开始菜单、桌面快捷方式和卸载入口符合交付要求。

这些项目不能由 HTTP 接口自动化替代；未留下人工记录前不得标为通过。

## 环境性跳过

- 干净 Windows 虚拟机或未安装 Node.js/Python 的独立测试账户验收，需要单独环境执行。
- Windows 代码签名信任链需要正式证书；当前三个 EXE 的状态由脚本实测，但没有证书时只能作为未签名预发布候选。
- Word/LibreOffice 视觉渲染依赖测试机软件，DOCX 结构校验和业务导出不以该渲染器为前置条件。

## 尚未完成

- 从上一已发布版本在线升级到本候选、校验失败拒绝安装和升级失败回退，需要两个真实发布版本及 GitHub Prerelease 后执行。
- 正式稳定版代码签名、SmartScreen 信誉和签名时间戳验证尚未完成。
- 远程 tag、Prerelease 和 Release 附件由最终审查通过后的发布流程创建，不在 CD-8 实现提交中提前创建。

## 发布判定

自动化结果、人工记录、签名状态和跨版本升级证据必须一起复核。当前允许准备预发布候选，不应把未签名且缺少跨版本升级证据的构建称为稳定版。
