# Project Image Local Numbering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-project image numbering for user-facing displays while keeping the existing global image ID as the stable internal reference.

**Architecture:** Keep `evidence_images.id` as the global SQLite primary key and calculate `project_image_no` dynamically from all images in a project ordered by section order, image `sort_order`, and image ID. Frontend display functions will prefer current API labels over stored `display_text`, so deleting or reordering images refreshes visible references without changing internal `[[FIG:<global_id>]]` tokens.

**Tech Stack:** Python, FastAPI, SQLite, Pydantic, React, TypeScript, Vite, python-docx, unittest.

**Implementation status:** Completed on branch `codex/project-image-local-numbering`. The implementation keeps global image IDs internally, exposes dynamic `project_image_no` through the API, refreshes cached frontend numbering after deletes, refreshes result-record figure labels from live evidence images, and adds DOCX import/export regression coverage.

---

## File Structure

- `backend/app/schemas.py`
  - Extend `EvidenceImageRead` with optional `project_image_no`.
- `backend/app/database.py`
  - Add a small query helper for all images in a project, ordered by section order and image order.
- `backend/app/api/evidence.py`
  - Compute project-local image numbers and include them in evidence responses.
- `backend/app/api/sections.py`
  - Return `project_image_no` in section detail evidence lists.
- `frontend/src/api/client.ts`
  - Add `project_image_no?: number | null` to `EvidenceImage`.
- `frontend/src/components/EvidencePanel.tsx`
  - Display project-local image number and keep chapter figure label as the primary visual label.
- `frontend/src/components/AssessmentTable.tsx`
  - Refresh displayed figure labels from current `evidenceImages` before falling back to stored reference text.
- `tests/test_evidence_images.py`
  - Cover per-project numbering, independent numbering across projects, deletion reindexing, and unchanged global IDs.
- `tests/test_frontend_template_slots.py`
  - Cover frontend source contracts for `project_image_no` and refreshed display label priority.
- `tests/test_docx_generator.py`
  - Add a focused regression proving high global IDs still export chapter figure labels from `图A-x-1`.
- `README.md`
  - Document user-facing numbering vs internal global ID.
- `docs/superpowers/specs/2026-07-06-project-image-local-numbering-design.md`
  - Keep design doc aligned if implementation makes a narrower choice.

---

### Task 1: Backend API Returns Project-Local Image Numbers

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/database.py`
- Modify: `backend/app/api/evidence.py`
- Modify: `backend/app/api/sections.py`
- Test: `tests/test_evidence_images.py`

- [ ] **Step 1: Write failing backend numbering tests**

Add these tests to `tests/test_evidence_images.py` inside `EvidenceImagesTest`:

```python
    def test_project_image_numbers_restart_per_project_and_follow_section_order(self) -> None:
        first_project = database.create_project("项目一")
        second_project = database.create_project("项目二")
        first_a1 = self.create_stored_evidence_image(first_project["id"], "first-a1.png")
        first_a2 = database.create_evidence_image(
            first_project["id"],
            "A-2",
            {
                "file_path": "uploads/project-one/A-2/first-a2.png",
                "original_name": "first-a2.png",
                "caption": "A-2 图片",
                "alt_text": "A-2 图片",
                "pixel_width": 100,
                "pixel_height": 100,
                "dpi_x": 150,
                "dpi_y": 150,
                "display_width_in": 1,
                "display_height_in": 1,
            },
        )
        second_a1 = self.create_stored_evidence_image(second_project["id"], "second-a1.png")

        first_a1_schema = evidence_to_schema(first_a1, section_index=1, project_image_no=1)
        first_a2_schema = evidence_to_schema(first_a2, section_index=1, project_image_no=2)
        second_a1_schema = evidence_to_schema(second_a1, section_index=1, project_image_no=1)

        self.assertEqual(first_a1_schema.project_image_no, 1)
        self.assertEqual(first_a1_schema.figure_label, "图A-1-1")
        self.assertEqual(first_a2_schema.project_image_no, 2)
        self.assertEqual(first_a2_schema.figure_label, "图A-2-1")
        self.assertEqual(second_a1_schema.project_image_no, 1)
        self.assertEqual(second_a1_schema.figure_label, "图A-1-1")

    def test_section_evidence_reindexes_project_image_numbers_after_delete(self) -> None:
        project = database.create_project("删除后编号")
        first = self.create_stored_evidence_image(project["id"], "first.png")
        second = self.create_stored_evidence_image(project["id"], "second.png")
        third = self.create_stored_evidence_image(project["id"], "third.png")

        database.delete_evidence_image(second["id"])

        rows = database.list_evidence_images(project["id"], "A-1")
        project_numbers = {row["id"]: index for index, row in enumerate(rows, start=1)}
        schemas = [
            evidence_to_schema(row, section_index=index, project_image_no=project_numbers[row["id"]])
            for index, row in enumerate(rows, start=1)
        ]

        self.assertEqual([image.id for image in schemas], [first["id"], third["id"]])
        self.assertEqual([image.project_image_no for image in schemas], [1, 2])
        self.assertEqual([image.figure_label for image in schemas], ["图A-1-1", "图A-1-2"])
        self.assertNotEqual(first["id"], third["id"])
```

- [ ] **Step 2: Run backend numbering tests to verify they fail**

Run:

```powershell
backend\.venv\Scripts\python.exe -m unittest tests.test_evidence_images.EvidenceImagesTest.test_project_image_numbers_restart_per_project_and_follow_section_order tests.test_evidence_images.EvidenceImagesTest.test_section_evidence_reindexes_project_image_numbers_after_delete -v
```

Expected: FAIL because `EvidenceImageRead` and `evidence_to_schema` do not yet accept `project_image_no`.

- [ ] **Step 3: Add schema field**

In `backend/app/schemas.py`, update `EvidenceImageRead`:

```python
class EvidenceImageRead(BaseModel):
    id: int
    project_id: int
    section_code: str
    file_path: str
    original_name: str
    caption: str
    alt_text: str
    sort_order: int
    project_image_no: int | None = None
    pixel_width: int | None = None
    pixel_height: int | None = None
    dpi_x: float | None = None
    dpi_y: float | None = None
    display_width_in: float | None = None
    display_height_in: float | None = None
    created_at: str
    updated_at: str
    file_url: str | None = None
    figure_label: str | None = None
    warnings: list[str] = []
```

- [ ] **Step 4: Add ordered project image query**

In `backend/app/database.py`, add this function after `list_evidence_images`:

```python
def list_project_evidence_images(project_id: int, db: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT
            e.id,
            e.project_id,
            e.section_code,
            e.file_path,
            e.original_name,
            e.caption,
            e.alt_text,
            e.sort_order,
            e.pixel_width,
            e.pixel_height,
            e.dpi_x,
            e.dpi_y,
            e.display_width_in,
            e.display_height_in,
            e.created_at,
            e.updated_at
        FROM evidence_images e
        JOIN sections s
            ON s.project_id = e.project_id
           AND s.code = e.section_code
        WHERE e.project_id = ?
        ORDER BY s.sort_order, e.sort_order, e.id
    """
    if db is not None:
        return db.execute(query, (project_id,)).fetchall()
    with connect() as connection:
        return connection.execute(query, (project_id,)).fetchall()
```

- [ ] **Step 5: Update evidence response helpers**

In `backend/app/api/evidence.py`, update `evidence_to_schema` and add helper functions:

```python
def evidence_to_schema(
    row,
    section_index: int | None = None,
    project_image_no: int | None = None,
) -> EvidenceImageRead:
    raw = dict(row)
    figure_label = None
    if section_index is not None:
        figure_label = f"图{raw['section_code']}-{section_index}"
    return EvidenceImageRead(
        id=raw["id"],
        project_id=raw["project_id"],
        section_code=raw["section_code"],
        file_path=raw["file_path"],
        original_name=raw["original_name"],
        caption=raw["caption"],
        alt_text=raw["alt_text"],
        sort_order=raw["sort_order"],
        project_image_no=project_image_no,
        pixel_width=raw["pixel_width"],
        pixel_height=raw["pixel_height"],
        dpi_x=raw["dpi_x"],
        dpi_y=raw["dpi_y"],
        display_width_in=raw["display_width_in"],
        display_height_in=raw["display_height_in"],
        created_at=raw["created_at"],
        updated_at=raw["updated_at"],
        file_url=f"/api/files/{raw['file_path']}",
        figure_label=figure_label,
        warnings=image_warnings(raw),
    )


def project_image_numbers(project_id: int) -> dict[int, int]:
    return {row["id"]: index for index, row in enumerate(database.list_project_evidence_images(project_id), start=1)}


def section_evidence_to_schema(project_id: int, rows) -> list[EvidenceImageRead]:
    numbers = project_image_numbers(project_id)
    return [
        evidence_to_schema(row, index, numbers.get(row["id"]))
        for index, row in enumerate(rows, start=1)
    ]
```

Then update response sites:

```python
def list_section_evidence(project_id: int, section_code: str) -> list[EvidenceImageRead]:
    rows = database.list_evidence_images(project_id, section_code)
    return section_evidence_to_schema(project_id, rows)
```

For upload, batch upload, replace, and reorder handlers, replace list comprehensions that call `evidence_to_schema(row, index)` with:

```python
return section_evidence_to_schema(project_id, rows)
```

For delete response, keep the deleted row response safe:

```python
return evidence_to_schema(row)
```

- [ ] **Step 6: Update section detail response**

In `backend/app/api/sections.py`, import `section_evidence_to_schema` and use it:

```python
from .evidence import section_evidence_to_schema
```

Replace the local evidence list construction with:

```python
evidence_rows = database.list_evidence_images(project_id, section["code"])
evidence_images = section_evidence_to_schema(project_id, evidence_rows)
```

- [ ] **Step 7: Run backend numbering tests to verify they pass**

Run:

```powershell
backend\.venv\Scripts\python.exe -m unittest tests.test_evidence_images.EvidenceImagesTest.test_project_image_numbers_restart_per_project_and_follow_section_order tests.test_evidence_images.EvidenceImagesTest.test_section_evidence_reindexes_project_image_numbers_after_delete -v
```

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

Run:

```powershell
git add backend/app/schemas.py backend/app/database.py backend/app/api/evidence.py backend/app/api/sections.py tests/test_evidence_images.py
git commit -m "M: 增加项目内图片编号 API"
```

---

### Task 2: Frontend Shows Project-Local Image Number Without Exposing Global ID

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/components/EvidencePanel.tsx`
- Test: `tests/test_frontend_template_slots.py`

- [ ] **Step 1: Write failing frontend source test**

Add this test to `tests/test_frontend_template_slots.py`:

```python
    def test_evidence_panel_displays_project_local_image_number(self) -> None:
        client_source = (FRONTEND_SRC / "api" / "client.ts").read_text(encoding="utf-8")
        panel_source = (FRONTEND_SRC / "components" / "EvidencePanel.tsx").read_text(encoding="utf-8")

        self.assertIn("project_image_no?: number | null;", client_source)
        self.assertIn("项目图片", panel_source)
        self.assertIn("image.project_image_no", panel_source)
        self.assertNotIn("图片ID", panel_source)
```

- [ ] **Step 2: Run frontend source test to verify it fails**

Run:

```powershell
backend\.venv\Scripts\python.exe -m unittest tests.test_frontend_template_slots.FrontendTemplateSlotSourceTest.test_evidence_panel_displays_project_local_image_number -v
```

Expected: FAIL because `project_image_no` is not yet in the TypeScript type and the panel does not display it.

- [ ] **Step 3: Extend frontend image type**

In `frontend/src/api/client.ts`, update `EvidenceImage`:

```ts
export type EvidenceImage = {
  id: number;
  project_id: number;
  section_code: string;
  file_path: string;
  original_name: string;
  caption: string;
  alt_text: string;
  sort_order: number;
  project_image_no?: number | null;
  pixel_width?: number | null;
  pixel_height?: number | null;
  dpi_x?: number | null;
  dpi_y?: number | null;
  display_width_in?: number | null;
  display_height_in?: number | null;
  created_at: string;
  updated_at: string;
  file_url?: string | null;
  figure_label?: string | null;
  warnings: string[];
};
```

- [ ] **Step 4: Display project-local number on evidence cards**

In `frontend/src/components/EvidencePanel.tsx`, replace the existing header chip:

```tsx
<span className="status-chip">排序 {globalIndex + 1}</span>
```

with:

```tsx
<span className="status-chip">项目图片 {image.project_image_no ?? globalIndex + 1}</span>
```

Keep the primary label:

```tsx
<strong>{image.figure_label ?? `${sectionCode}-${index + 1}`}</strong>
```

- [ ] **Step 5: Run frontend source test to verify it passes**

Run:

```powershell
backend\.venv\Scripts\python.exe -m unittest tests.test_frontend_template_slots.FrontendTemplateSlotSourceTest.test_evidence_panel_displays_project_local_image_number -v
```

Expected: PASS.

- [ ] **Step 6: Run frontend build**

Run:

```powershell
npm run build
```

Workdir: `frontend`

Expected: TypeScript build and Vite build succeed.

- [ ] **Step 7: Commit Task 2**

Run:

```powershell
git add frontend/src/api/client.ts frontend/src/components/EvidencePanel.tsx tests/test_frontend_template_slots.py
git commit -m "UI: 显示项目内图片编号"
```

---

### Task 3: Result Record Display Refreshes Figure Labels After Delete Or Reorder

**Files:**
- Modify: `frontend/src/components/AssessmentTable.tsx`
- Test: `tests/test_frontend_template_slots.py`

- [ ] **Step 1: Write failing source test for current-label priority**

Add this test to `tests/test_frontend_template_slots.py`:

```python
    def test_result_record_prefers_current_figure_label_over_stored_display_text(self) -> None:
        table_source = (FRONTEND_SRC / "components" / "AssessmentTable.tsx").read_text(encoding="utf-8")

        self.assertIn("const storedDisplayText = reference.display_text?.trim();", table_source)
        self.assertIn("const displayText = existing?.displayText ?? fallbackDisplayText;", table_source)
        self.assertIn("displayText: displayText || token", table_source)
```

- [ ] **Step 2: Run source test to verify it fails**

Run:

```powershell
backend\.venv\Scripts\python.exe -m unittest tests.test_frontend_template_slots.FrontendTemplateSlotSourceTest.test_result_record_prefers_current_figure_label_over_stored_display_text -v
```

Expected: FAIL because current code prioritizes `reference.display_text` over the latest image label.

- [ ] **Step 3: Update `figureReferenceOptions` priority**

In `frontend/src/components/AssessmentTable.tsx`, replace the cross-reference merge block inside `figureReferenceOptions` with:

```ts
(row.cross_references ?? []).forEach((reference) => {
  const token = reference.token.trim();
  if (!token) {
    return;
  }
  const existing = optionsByToken.get(token);
  const storedDisplayText = reference.display_text?.trim();
  const fallbackDisplayText = reference.target_image_id ? storedDisplayText : "";
  const displayText = existing?.displayText ?? fallbackDisplayText;
  optionsByToken.set(token, {
    token,
    displayText: displayText || token,
    target_image_id: reference.target_image_id ?? existing?.target_image_id ?? null
  });
});
```

This makes existing live image labels win over stale stored `display_text`. If the target image has been deleted and no live image exists, the visible text falls back to the raw token instead of silently showing a stale valid-looking figure label.

- [ ] **Step 4: Run source test to verify it passes**

Run:

```powershell
backend\.venv\Scripts\python.exe -m unittest tests.test_frontend_template_slots.FrontendTemplateSlotSourceTest.test_result_record_prefers_current_figure_label_over_stored_display_text -v
```

Expected: PASS.

- [ ] **Step 5: Run focused frontend source suite**

Run:

```powershell
backend\.venv\Scripts\python.exe -m unittest tests.test_frontend_template_slots -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

Run:

```powershell
git add frontend/src/components/AssessmentTable.tsx tests/test_frontend_template_slots.py
git commit -m "UI: 刷新结果记录图片显示编号"
```

---

### Task 4: Export And Import Keep Global Tokens While Showing Current Figure Labels

**Files:**
- Modify: `tests/test_docx_generator.py`
- Modify: `tests/test_docx_import_confirm_api.py`

- [ ] **Step 1: Write export regression for high global IDs**

Add this test to `tests/test_docx_generator.py` inside `DocxGeneratorTest`:

```python
    def test_docx_figure_labels_do_not_expose_global_image_ids(self) -> None:
        project = database.create_project("高 ID 图片导出")
        first = self._create_evidence_image(project["id"], "A-1", filename="first.png", caption="第一张")
        second = self._create_evidence_image(project["id"], "A-1", filename="second.png", caption="第二张")
        database.delete_evidence_image(first["id"])

        section = database.get_section(project["id"], "A-1")
        database.replace_section_rows(
            section["id"],
            [
                {
                    "unit": "身份鉴别",
                    "object_name": "机房",
                    "record_text": f"查看证据，见 [[FIG:{second['id']}]]。",
                    "sort_order": 1,
                    "metric_result": {
                        "d": "√",
                        "a": "√",
                        "k": "√",
                        "object_score": "1.0000",
                        "unit_score": "1.0000",
                        "compliance": "符合",
                    },
                    "cross_references": [
                        {
                            "target_image_id": second["id"],
                            "token": f"[[FIG:{second['id']}]]",
                            "display_text": "图A-1-2",
                        }
                    ],
                }
            ],
        )

        output = export_project_docx(project["id"], "final")

        with ZipFile(output.path) as package:
            document_xml = package.read("word/document.xml").decode("utf-8")

        self.assertIn("图A-1-", document_xml)
        self.assertIn("第二张", document_xml)
        self.assertNotIn(f"图A-1-{second['id']}", document_xml)
```

- [ ] **Step 2: Run export regression**

Run:

```powershell
backend\.venv\Scripts\python.exe -m unittest tests.test_docx_generator.DocxGeneratorTest.test_docx_figure_labels_do_not_expose_global_image_ids -v
```

Expected: PASS if existing generator already relies on section order; FAIL if stale display text leaks global ID or deleted order.

- [ ] **Step 3: Write import confirmation regression for returned project-local number**

Add this assertion to `tests/test_docx_import_confirm_api.py` in `test_confirm_import_creates_project_images_rows_references_and_validates` after section detail is loaded:

```python
        self.assertEqual(detail.evidence_images[0].project_image_no, 1)
        self.assertEqual(detail.evidence_images[0].figure_label, "图A-1-1")
```

- [ ] **Step 4: Run import confirmation regression**

Run:

```powershell
backend\.venv\Scripts\python.exe -m unittest tests.test_docx_import_confirm_api.DocxImportConfirmApiTest.test_confirm_import_creates_project_images_rows_references_and_validates -v
```

Expected: PASS after Task 1 section detail response includes `project_image_no`.

- [ ] **Step 5: Commit Task 4**

Run:

```powershell
git add tests/test_docx_generator.py tests/test_docx_import_confirm_api.py
git commit -m "Test: 覆盖图片编号导入导出回归"
```

---

### Task 5: Documentation And Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-06-project-image-local-numbering-design.md`
- Create: `docs/项目内图片编号实施记录.md`

- [ ] **Step 1: Update README numbering section**

In `README.md`, update the image-numbering bullets to say:

```markdown
- 图片内部仍使用全局唯一 `evidence_images.id` 作为稳定引用，不在界面和导出文档中展示。
- API 会返回每个项目内从 1 开始的 `project_image_no`，证据图片卡片显示为“项目图片 N”。
- 图号仍按章节和章节内排序显示为 `图A-x-y`；删除或调整排序后，界面中的结果记录引用显示会按最新图片列表刷新。
- 保存和导出仍使用内部 `[[FIG:全局图片ID]]` token 生成稳定 Word 交叉引用。
```

- [ ] **Step 2: Add implementation record**

Create `docs/项目内图片编号实施记录.md`:

```markdown
# 项目内图片编号实施记录

## 变更范围

- 保留 `evidence_images.id` 作为内部全局图片 ID。
- 新增 API 响应字段 `project_image_no`，按项目内图片顺序从 1 开始动态计算。
- 证据图片卡片显示“项目图片 N”，不展示全局 ID。
- 结果记录显示优先使用当前图片列表中的最新 `图A-x-y`，删除或排序后自动刷新可见编号。

## 保留行为

- 保存章节时仍写入 `[[FIG:全局图片ID]]`。
- 已删除图片不会自动改指向其他图片。
- 已删除图片仍被正文引用时，校验继续报告断链。
- DOCX 导入确认后继续把临时 token 转为真实全局 ID token。

## 验证

- `backend\.venv\Scripts\python.exe -m unittest tests.test_evidence_images -v`
- `backend\.venv\Scripts\python.exe -m unittest tests.test_frontend_template_slots -v`
- `backend\.venv\Scripts\python.exe -m unittest tests.test_docx_generator tests.test_docx_import_confirm_api -v`
- `npm run build`
- `backend\.venv\Scripts\python.exe -m unittest discover -s tests -v`
- `.\scripts\run_checks.ps1`
```

- [ ] **Step 3: Run documentation grep**

Run:

```powershell
rg -n "全局图片 ID|project_image_no|项目图片|图A-x-y|\\[\\[FIG:" README.md docs -S
```

Expected: README and implementation record describe the split between user-facing numbering and internal global ID.

- [ ] **Step 4: Run focused verification**

Run:

```powershell
backend\.venv\Scripts\python.exe -m unittest tests.test_evidence_images -v
backend\.venv\Scripts\python.exe -m unittest tests.test_frontend_template_slots -v
backend\.venv\Scripts\python.exe -m unittest tests.test_docx_generator tests.test_docx_import_confirm_api -v
```

Expected: all focused tests pass.

- [ ] **Step 5: Run frontend build**

Run:

```powershell
npm run build
```

Workdir: `frontend`

Expected: TypeScript build and Vite build succeed.

- [ ] **Step 6: Run full regression**

Run:

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\scripts\run_checks.ps1
git diff --check
```

Expected: all tests pass and `git diff --check` reports no whitespace errors.

- [ ] **Step 7: Commit Task 5**

Run:

```powershell
git add README.md docs/superpowers/specs/2026-07-06-project-image-local-numbering-design.md docs/项目内图片编号实施记录.md
git commit -m "Docs: 更新项目内图片编号说明"
```

- [ ] **Step 8: Push branch**

Run:

```powershell
git status --short --branch
git push
```

Expected: branch `codex/project-image-local-numbering` is pushed to `origin`; only unrelated untracked local files, such as `附录A导入测试.docx`, remain outside the commit.

---

## Self-Review Checklist

- Spec requirement “界面和文档显示项目内编号，内部保留全局 ID” is covered by Tasks 1, 2, 3, and 5.
- Spec requirement “删除后编号自动重排” is covered by Task 1 tests and Task 3 display priority change.
- Spec requirement “结果记录中的引用编号自动刷新” is covered by Task 3.
- Spec requirement “导出 DOCX 使用最新图号” is covered by Task 4.
- Spec requirement “导入 DOCX 继续临时 token 到真实全局 ID” is covered by Task 4.
- No database primary key or foreign-key migration is included, matching the non-goals.
- Every task includes exact files, commands, expected results, and commit boundaries.
