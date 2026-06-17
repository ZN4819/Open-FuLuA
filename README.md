# 附录A编写工具

这是一个面向“附录A测评结果记录”的本地 Web 应用项目。工具目标是把 A-1 至 A-8 的测评结果、评分、符合情况、证据图片、题注和交叉引用结构化维护，并导出符合样本文档格式的 DOCX。

## 当前阶段

当前已完成阶段 2：模板 Profile 与样本文档结构分析器。

已包含：

- 后端 FastAPI 应用骨架。
- SQLite 本地数据库初始化。
- 项目创建、读取、更新接口。
- 新项目自动初始化 A-1 至 A-8 章节。
- 前端 React/Vite 应用骨架。
- 项目创建页面和章节导航。
- 附录A模板 profile。
- DOCX 结构分析器。
- 样本文档结构回归测试。

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

构建前端：

```powershell
cd frontend
npm run build
```
