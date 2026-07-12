# Ra、Rk 参数与自动评分功能实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 A-1 至 A-4 技术测评记录增加 Ra、Rk 参数，并依据评分表自动、只读地计算对象评分和单元得分，同时保持 Word 导入导出结构不变。

**Architecture:** 将评分公式分别封装为后端和前端纯函数；前端负责即时反馈，后端在保存时权威重算并持久化结果。SQLite schema 从 1 升级到 2，为 `metric_results` 幂等增加 `ra`、`rk`，旧技术记录读取时按 `1`、`1` 兼容，DOCX 导入时使用相同默认值。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLite、React 19、TypeScript 6、Node 22 内置测试运行器、python-docx。

## Global Constraints

- Ra 选项只能为 `1`、`0.5`、`0.2`，默认值为 `1`。
- Rk 选项只能为 `1`、`1.2`，默认值为 `1`。
- D、A、K 全为 `/` 时对象评分为 `/`；任一参数未选择时对象评分为空。
- 对象评分禁止人工覆盖，后端必须忽略客户端提交的对象评分并重新计算。
- 单元得分只平均数值评分，忽略 `/`，保留四位小数。
- Ra、Rk 仅适用于 A-1 至 A-4，不得出现在 editable/final DOCX 中。
- 不提交 `.codex/` 和根目录未跟踪的评分表文件。

---

### Task 1: 建立后端权威评分规则

**Files:**
- Create: `backend/app/services/scoring.py`
- Create: `tests/test_scoring.py`

**Interfaces:**
- Produces: `calculate_object_score(d, a, k, ra="1", rk="1") -> str | None`
- Produces: `calculate_unit_score(scores: Iterable[str | None]) -> str`
- Produces: `calculate_technical_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]`
- Consumes: D/A/K 字符串与 Ra/Rk 枚举值。

- [ ] **Step 1: 写对象评分失败测试**

```python
class ScoringTests(unittest.TestCase):
    def test_calculates_all_score_branches(self) -> None:
        cases = [
            (("/", "/", "/", "1", "1"), "/"),
            (("×", "/", "/", "1", "1"), "0.0000"),
            (("√", "√", "√", "0.2", "1.2"), "1.0000"),
            (("√", "×", "√", "0.5", "1"), "0.2500"),
            (("√", "√", "×", "1", "1.2"), "0.6000"),
            (("√", "×", "×", "0.2", "1.2"), "0.0600"),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(calculate_object_score(*arguments), expected)

    def test_incomplete_parameters_do_not_calculate(self) -> None:
        self.assertIsNone(calculate_object_score("√", "", "√", "1", "1"))
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `backend/.venv/Scripts/python.exe -m unittest tests.test_scoring -v`

Expected: `ModuleNotFoundError: No module named 'app.services.scoring'`。

- [ ] **Step 3: 实现最小对象评分纯函数**

```python
RA_VALUES = {"1", "0.5", "0.2"}
RK_VALUES = {"1", "1.2"}

def calculate_object_score(d, a, k, ra="1", rk="1"):
    values = [str(value or "").strip() for value in (d, a, k)]
    if not all(values):
        return None
    if values == ["/", "/", "/"]:
        return "/"
    if values[0] != "√":
        return "0.0000"
    score = Decimal("1")
    if values[1] != "√":
        score *= Decimal("0.5") * Decimal(ra)
    if values[2] != "√":
        score *= Decimal("0.5") * Decimal(rk)
    return str(score.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
```

函数应对非法 Ra/Rk 抛出 `ValueError`，错误文本说明允许选项；Pydantic 仍负责 API 第一层校验。

- [ ] **Step 4: 写并验证单元平均分失败测试**

```python
def test_unit_score_ignores_excluded_values(self) -> None:
    self.assertEqual(calculate_unit_score(["1.0000", "/", "0.5000"]), "0.7500")
    self.assertEqual(calculate_unit_score(["/", "/"]), "/")
    self.assertEqual(calculate_unit_score([None, ""]), "")
```

Run: `backend/.venv/Scripts/python.exe -m unittest tests.test_scoring -v`

Expected: FAIL，提示 `calculate_unit_score` 尚未实现。

- [ ] **Step 5: 实现单元得分与整行重算**

`calculate_technical_rows` 必须复制输入字典，不原地修改；先为每行补 `ra/rk` 默认值并调用 `calculate_object_score`，再按 `unit.strip()` 分组调用 `calculate_unit_score`，将同组 `unit_score` 写回每行。

- [ ] **Step 6: 运行评分测试并提交**

Run: `backend/.venv/Scripts/python.exe -m unittest tests.test_scoring -v`

Expected: 所有评分分支通过。

```powershell
git add backend/app/services/scoring.py tests/test_scoring.py
git commit -m "评分: 增加 Ra Rk 权威计算规则"
```

---

### Task 2: 升级 SQLite、API 模型和保存读取链路

**Files:**
- Modify: `backend/app/runtime.py`
- Modify: `backend/app/database.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/api/sections.py`
- Modify: `tests/test_database_schema.py`
- Modify: `tests/test_structured_data.py`

**Interfaces:**
- Consumes: Task 1 的 `calculate_technical_rows`。
- Produces: API `metric_result.ra`、`metric_result.rk`。
- Produces: SQLite `metric_results.ra`、`metric_results.rk`。

- [ ] **Step 1: 写 schema 升级失败测试**

在 `tests/test_database_schema.py` 构造 schema 1 数据库，其中 `metric_results` 只有旧列；执行 `database.init_db()` 后断言：

```python
columns = {row[1] for row in connection.execute("PRAGMA table_info(metric_results)")}
self.assertIn("ra", columns)
self.assertIn("rk", columns)
self.assertEqual(database.read_schema_version(path, readonly=True), "2")
```

- [ ] **Step 2: 运行并确认旧库缺列测试失败**

Run: `backend/.venv/Scripts/python.exe -m unittest tests.test_database_schema -v`

Expected: FAIL，`ra`、`rk` 不在列集合或版本仍为 `1`。

- [ ] **Step 3: 实现幂等 schema 2 升级**

在 `backend/app/runtime.py` 将 `SCHEMA_VERSION` 改为 `"2"`。在建表 SQL 中加入 `ra TEXT`、`rk TEXT`，并在 `init_db()` 中调用：

```python
_ensure_column(db, "metric_results", "ra", "TEXT")
_ensure_column(db, "metric_results", "rk", "TEXT")
```

保持失败时不提升 `PRAGMA user_version` 的现有事务语义。

- [ ] **Step 4: 写 API 枚举和权威重算失败测试**

在 `tests/test_structured_data.py` 保存 A-1 记录，故意提交错误的 `object_score="9.9999"`，并断言数据库返回：

```python
self.assertEqual(row["ra"], "0.5")
self.assertEqual(row["rk"], "1.2")
self.assertEqual(row["object_score"], "0.3000")
self.assertEqual(row["unit_score"], "0.3000")
```

另用 `MetricResultWrite(ra="0.3")` 和 `MetricResultWrite(rk="2")` 断言 Pydantic 校验失败。

- [ ] **Step 5: 扩展模型、查询和保存 SQL**

`MetricResultRead/Write` 增加：

```python
ra: Literal["1", "0.5", "0.2"] | None = None
rk: Literal["1", "1.2"] | None = None
```

`list_assessment_rows` 查询、两处 `INSERT INTO metric_results` 和章节复制 metric 字典均加入 `ra/rk`。`replace_section_rows` 仅在 `code in {"A-1", ..., "A-4"}` 时调用 `calculate_technical_rows`；管理章节继续保留原有评分行为，并把 Ra/Rk 存为 `NULL`。

`assessment_row_to_schema` 接收 `technical: bool`；旧技术记录数据库值为空时返回 `ra="1"`、`rk="1"`，管理记录返回 `None`。

- [ ] **Step 6: 验证保存、读取、旧数据默认值和管理表隔离**

Run: `backend/.venv/Scripts/python.exe -m unittest tests.test_database_schema tests.test_structured_data -v`

Expected: PASS；既有单元得分测试按新权威公式更新测试输入，不再依赖客户端对象评分。

- [ ] **Step 7: 提交数据库与 API 改动**

```powershell
git add backend/app/runtime.py backend/app/database.py backend/app/schemas.py backend/app/api/sections.py tests/test_database_schema.py tests/test_structured_data.py
git commit -m "评分: 持久化 Ra Rk 并在保存时重算"
```

---

### Task 3: 保留复制迁移并隔离 Word 导入导出

**Files:**
- Modify: `backend/app/services/docx_importer/confirm.py`
- Modify: `tests/test_docx_import_confirm_api.py`
- Modify: `tests/test_docx_import_roundtrip.py`
- Modify: `tests/test_docx_generator.py`
- Modify: `tests/test_data_migration.py`

**Interfaces:**
- Consumes: Task 2 的数据库列和 API 字段。
- Produces: DOCX 导入技术记录默认 `ra="1"`、`rk="1"`。
- Preserves: DOCX 技术表仍为 8 列，不含 Ra/Rk。

- [ ] **Step 1: 写 DOCX 边界失败测试**

在导入确认测试中断言新建 A-1 行：

```python
self.assertEqual(row.metric_result.ra, "1")
self.assertEqual(row.metric_result.rk, "1")
```

在生成器测试中解析表头和内容控件，断言技术表仍为 `4x8`，所有单元格文本和 tag 均不包含 `Ra`、`Rk`。

- [ ] **Step 2: 运行并确认导入默认值测试失败**

Run: `backend/.venv/Scripts/python.exe -m unittest tests.test_docx_import_confirm_api tests.test_docx_generator -v`

Expected: FAIL，导入记录尚未返回 Ra/Rk。

- [ ] **Step 3: 在 DOCX 确认阶段补技术默认值**

`confirm.py` 插入 `metric_results` 时加入 `ra/rk` 列；根据章节 `table_type == "technical"` 写入 `"1"`、`"1"`，管理章节写入 `None`。不修改 `template_profile.json`、DOCX 表格列定义和导入表头识别规则。

- [ ] **Step 4: 覆盖章节复制和桌面数据迁移**

扩展结构化数据测试，保存非默认 `ra/rk` 后导入到另一项目，断言目标记录值保持一致。扩展迁移测试的 `metric_results` 样本与迁移后查询，确认 schema 2 数据完整复制。

- [ ] **Step 5: 运行 DOCX、复制和迁移回归**

Run:

```powershell
backend/.venv/Scripts/python.exe -m unittest tests.test_docx_import_confirm_api tests.test_docx_import_roundtrip tests.test_docx_generator tests.test_structured_data tests.test_data_migration -v
```

Expected: PASS，Word 表结构无变化。

- [ ] **Step 6: 提交导入导出兼容改动**

```powershell
git add backend/app/services/docx_importer/confirm.py tests/test_docx_import_confirm_api.py tests/test_docx_import_roundtrip.py tests/test_docx_generator.py tests/test_data_migration.py tests/test_structured_data.py
git commit -m "评分: 保持 Ra Rk 与 Word 结构隔离"
```

---

### Task 4: 实现前端即时评分与只读界面

**Files:**
- Create: `frontend/src/scoring.ts`
- Create: `frontend/src/scoring.test.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/components/AssessmentTable.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `tests/test_frontend_template_slots.py`

**Interfaces:**
- Produces: `calculateObjectScore(metric: MetricResult) -> string`
- Produces: `calculateTechnicalRows(rows: AssessmentRowInput[]) -> AssessmentRowInput[]`
- Consumes: API 的 `ra/rk` 字段和 Task 1 相同测试向量。

- [ ] **Step 1: 写前端评分失败测试**

使用 Node 22 内置测试器创建 `frontend/src/scoring.test.ts`：

```typescript
import test from "node:test";
import assert from "node:assert/strict";
import { calculateObjectScore } from "./scoring.ts";

test("applies Ra and Rk correction factors", () => {
  assert.equal(calculateObjectScore({ d: "√", a: "×", k: "√", ra: "0.5", rk: "1" }), "0.2500");
  assert.equal(calculateObjectScore({ d: "√", a: "√", k: "×", ra: "1", rk: "1.2" }), "0.6000");
  assert.equal(calculateObjectScore({ d: "√", a: "×", k: "×", ra: "0.2", rk: "1.2" }), "0.0600");
});

test("treats only three slash metrics as excluded", () => {
  assert.equal(calculateObjectScore({ d: "/", a: "/", k: "/", ra: "1", rk: "1" }), "/");
  assert.equal(calculateObjectScore({ d: "×", a: "/", k: "/", ra: "1", rk: "1" }), "0.0000");
});
```

`frontend/package.json` 增加 `"test:scoring": "node --test src/scoring.test.ts"`。

- [ ] **Step 2: 运行并确认前端测试因模块不存在而失败**

Run: `npm --prefix frontend run test:scoring`

Expected: FAIL，无法导入 `scoring.ts`。

- [ ] **Step 3: 实现前端纯函数**

在 `scoring.ts` 使用数值运算并统一 `toFixed(4)`；`calculateTechnicalRows` 为旧行补 `ra/rk="1"`，重算对象评分，再按测评单元计算平均分。不得从 `AssessmentTable` 反向导入组件代码。

- [ ] **Step 4: 写 UI 契约失败测试**

在 `tests/test_frontend_template_slots.py` 断言：

```python
self.assertIn('<th>Ra</th>', table_source)
self.assertIn('<th>Rk</th>', table_source)
self.assertIn('(["d", "a", "k"] as const)', table_source)
self.assertIn('(["ra", "rk"] as const)', table_source)
self.assertIn('className="score-output object-score-output"', table_source)
self.assertNotIn('onChange={(event) => updateMetric(index, "object_score"', table_source)
```

- [ ] **Step 5: 扩展类型、默认值和技术表 UI**

`MetricResult` 增加：

```typescript
ra?: "1" | "0.5" | "0.2" | null;
rk?: "1" | "1.2" | null;
```

`EMPTY_METRIC` 使用 `ra: "1"`、`rk: "1"`。`normalizeRows` 在技术章节调用 `calculateTechnicalRows`。表头在 K 后增加 Ra、Rk，技术表列数从 9 改为 11；Ra/Rk 分别使用固定选项数组。对象评分改为：

```tsx
<output className="score-output object-score-output">
  {row.metric_result?.object_score ?? ""}
</output>
```

删除 `formatObjectScore` 及对象评分的 `onChange/onBlur`。

- [ ] **Step 6: 调整紧凑列宽和只读视觉**

在 `styles.css` 增加 `.col-factor`、`.factor-cell`、`.factor-select` 和 `.object-score-output`。Ra/Rk 宽度应不大于现有 D/A/K 列，对象评分使用中性只读背景；保持表格自身横向滚动，不扩大页面宽度。

- [ ] **Step 7: 运行前端评分、契约和构建测试**

Run:

```powershell
npm --prefix frontend run test:scoring
backend/.venv/Scripts/python.exe -m unittest tests.test_frontend_template_slots -v
npm --prefix frontend run build
```

Expected: Node 评分测试、Python UI 契约和 TypeScript/Vite 构建全部通过。

- [ ] **Step 8: 提交前端改动**

```powershell
git add frontend/package.json frontend/src/scoring.ts frontend/src/scoring.test.ts frontend/src/api/client.ts frontend/src/components/AssessmentTable.tsx frontend/src/styles.css tests/test_frontend_template_slots.py
git commit -m "前端: 增加 Ra Rk 即时评分录入"
```

---

### Task 5: 同步说明并执行完整交付验证

**Files:**
- Modify: `README.md`
- Modify: `docs/客户端封装实施计划.md`
- Modify: `docs/客户端封装实施方案.md`
- Modify: `docs/superpowers/plans/2026-07-13-ra-rk-score-calculation.md`

**Interfaces:**
- Consumes: Tasks 1-4 的最终行为与测试结果。
- Produces: 面向用户的 Ra/Rk 使用说明和真实验收记录。

- [ ] **Step 1: 更新用户和开发文档**

README 说明 Ra/Rk 取值、默认值、自动评分和全 `/` 规则；明确 Ra/Rk 不进入 Word。实施计划记录数据库 schema 2、前后端双层计算及验证命令。正文只写用户可感知行为和维护边界，不加入制作过程元叙事。

- [ ] **Step 2: 运行完整检查**

Run:

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
./scripts/run_checks.ps1
npm --prefix frontend run test:scoring
npm --prefix desktop run test:cd7
npm --prefix desktop audit --audit-level=high
```

Expected: Python 全量测试、前端构建、前端/桌面审计和桌面 CD-7 测试全部通过；样本文档或符号链接相关测试只允许现有的环境性跳过。

- [ ] **Step 3: 运行开发模式冒烟**

Run: `./scripts/test_dev_smoke.ps1`

Expected: 后端健康状态 `ok`、`runtime_mode=development`、前端状态 `200`。

- [ ] **Step 4: 核对 Word 排除边界和工作区**

Run:

```powershell
rg -n "Ra|Rk|\bra\b|\brk\b" backend/app/services/docx_generator templates/appendix_a/template_profile.json
git diff --check
git status --short
```

Expected: DOCX 生成器和模板 profile 没有新增 Ra/Rk 列；状态中不包含 `.codex/` 或评分表的暂存项。

- [ ] **Step 5: 更新计划完成状态并提交文档**

将本计划中已执行步骤标记为完成，并在实施计划中记录实际测试数量与环境性跳过原因。

```powershell
git add README.md docs/客户端封装实施计划.md docs/客户端封装实施方案.md docs/superpowers/plans/2026-07-13-ra-rk-score-calculation.md
git commit -m "文档: 完成 Ra Rk 自动评分验收说明"
```

- [ ] **Step 6: 最终审查与发布准备**

确认分支只包含本功能相关提交；执行代码审查，修复重要问题后重新运行受影响测试。随后推送当前分支并创建以 `main` 为基线的 Pull Request，不直接覆盖已发布的 `v0.1.0-rc.1`。
