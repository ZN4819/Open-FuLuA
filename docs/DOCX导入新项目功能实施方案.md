# DOCX 导入新项目功能实施方案

## 1. 背景

当前工具已经支持结构化编写 A-1 至 A-8、上传证据图片、维护图题和交叉引用，并导出 editable/final 两类 DOCX。下一步需要支持“导入已有附录A DOCX，自动分析内容，并作为一个新项目打开继续编辑”。

该能力的核心不是做通用 Word 编辑器，而是把符合附录A样式或由本工具导出的 DOCX 反向解析为当前系统的数据模型：项目、章节、测评行、评分结果、证据图片、图题和图片引用 token。

## 2. 产品目标

用户在首页或项目列表页上传一个 `.docx` 文件后，系统自动完成：

- 识别文档是否是附录A测评结果记录。
- 识别 A-1 至 A-8 章节、章节标题和表题。
- 解析技术测评表和管理测评表。
- 提取测评单元、测评对象、结果记录、D/A/K、符合情况、对象评分和单元得分。
- 提取 Word 内嵌证据图片、题注和图片排序。
- 将正文中的图引用映射为系统内部 `[[FIG:imageId]]` token。
- 创建一个新的本地项目，导入结果可继续编辑、保存、校验、预览和导出。

## 3. 第一版边界

### 3.1 第一版支持

第一版优先支持以下 DOCX：

- 本工具导出的 editable/final DOCX。
- 与当前 `templates/appendix_a/template_profile.json` 表结构兼容的附录A DOCX。
- 以真实 Word 表格承载 A-1 至 A-8 的测评结果记录。
- 图片以 Word 内嵌图片形式存在，且能在 `word/media/` 中找到 PNG/JPEG 等常见位图资源。
- 图题使用类似 `图A-1-1` 的编号格式。
- 正文引用使用可见图号、Word REF 字段或导出后保留下来的图号文本。

### 3.2 第一版不承诺

第一版不承诺无损导入任意 Word 文档：

- 不还原任意浮动文本框、形状、SmartArt、复杂浮动图形。
- 不保留 Word 的全部富文本样式、修订、批注和页眉页脚内容。
- 不保证导入非附录A结构文档。
- 不直接覆盖用户当前已有项目，只创建新项目。
- 不修改原始样本文档 `附录A编写.docx`，上传文件只作为导入源读取。

## 4. 总体方案

采用“解析预览 + 确认创建项目”的两步式导入：

1. 用户上传 DOCX。
2. 后端创建导入任务，保存源 DOCX 到 `storage/imports/{job_id}/source.docx`。
3. 后端解析 DOCX，生成结构化预览 JSON、图片清单和问题清单。
4. 前端展示导入预览：项目名称、章节覆盖、测评行数量、图片数量、引用映射和问题。
5. 用户确认后，后端在一个事务中创建新项目、写入章节数据、复制图片到项目上传目录、写入证据图片和交叉引用。
6. 前端自动打开新项目，用户继续编辑和校验。

这样可以避免错误导入直接污染项目列表，同时给用户一次确认和修改项目名称的机会。

## 5. 后端架构

### 5.1 新增服务包

建议新增目录：

```text
backend/app/services/docx_importer/
```

建议模块：

```text
backend/app/services/docx_importer/__init__.py
backend/app/services/docx_importer/models.py
backend/app/services/docx_importer/package.py
backend/app/services/docx_importer/document.py
backend/app/services/docx_importer/tables.py
backend/app/services/docx_importer/images.py
backend/app/services/docx_importer/references.py
backend/app/services/docx_importer/writer.py
backend/app/services/docx_importer/validator.py
```

模块职责：

- `package.py`：读取 DOCX zip 包、XML 部件、关系文件和媒体文件。
- `document.py`：按 Word body 顺序抽取段落、表格、分节和候选标题。
- `tables.py`：识别 A-1 至 A-8 核心表，解析技术表和管理表行。
- `images.py`：提取内嵌图片、题注、图号、图片尺寸和 DPI。
- `references.py`：解析 REF 字段、可见图号文本和正文中的图片引用位置。
- `validator.py`：生成导入问题清单，区分错误、警告和提示。
- `writer.py`：将解析结果写入当前项目数据模型，创建新项目。

### 5.2 新增数据库表

建议新增导入任务表：

```sql
CREATE TABLE IF NOT EXISTS docx_import_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    source_docx_path TEXT NOT NULL DEFAULT '',
    parsed_json_path TEXT,
    created_project_id INTEGER,
    summary_json TEXT NOT NULL DEFAULT '{}',
    issues_json TEXT NOT NULL DEFAULT '[]',
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(created_project_id) REFERENCES projects(id) ON DELETE SET NULL
);
```

状态建议：

- `uploaded`：已上传，未解析。
- `parsing`：解析中。
- `preview_ready`：解析完成，可预览。
- `importing`：正在创建新项目。
- `succeeded`：新项目创建完成。
- `failed`：解析或创建失败。

第一版可同步解析并返回 `preview_ready`，但表结构保留异步扩展空间。

### 5.3 新增后端 API

建议新增路由文件：

```text
backend/app/api/imports.py
```

接口：

```text
POST   /api/imports/docx
GET    /api/imports/{job_id}
POST   /api/imports/{job_id}/project
DELETE /api/imports/{job_id}
```

接口含义：

- `POST /api/imports/docx`：上传 DOCX，保存源文件并解析，返回导入任务和预览摘要。
- `GET /api/imports/{job_id}`：查看解析状态、摘要、章节预览和问题清单。
- `POST /api/imports/{job_id}/project`：确认导入并创建新项目，可传入项目名称覆盖值。
- `DELETE /api/imports/{job_id}`：删除未确认导入的临时导入任务和临时文件。

建议响应模型：

```python
class DocxImportIssue(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    section_code: str | None = None
    target: str | None = None

class DocxImportSectionPreview(BaseModel):
    code: str
    title: str
    table_title: str
    table_type: Literal["technical", "management"]
    row_count: int
    image_count: int
    reference_count: int

class DocxImportJobRead(BaseModel):
    id: int
    status: str
    original_name: str
    suggested_project_name: str
    created_project_id: int | None = None
    sections: list[DocxImportSectionPreview]
    summary: dict[str, int]
    issues: list[DocxImportIssue]
```

## 6. DOCX 解析策略

### 6.1 DOCX 包读取

解析时直接读取 OpenXML 包：

- `word/document.xml`：正文段落、表格、字段和图片引用。
- `word/_rels/document.xml.rels`：图片关系 ID 到媒体文件的映射。
- `word/media/*`：图片二进制。
- 可选读取 `word/styles.xml`、`word/numbering.xml`，但第一版不依赖样式做主判断。

所有解析都通过 XML 结构完成，避免依赖 Word 自动化。

### 6.2 文档结构识别

识别顺序：

1. 抽取正文 body 中的段落和表格顺序。
2. 识别总标题：优先匹配 `附录A测评结果记录`。
3. 识别章节标题：匹配 `A-1` 至 `A-8` 和章节名称。
4. 识别表题：匹配 `表A-1...测评结果记录` 至 `表A-8...测评结果记录`。
5. 识别核心表：根据表题、列数、表头文本和章节上下文判断。

如果文档缺失某个章节或表格，导入预览中标记为错误或警告。第一版允许“部分章节导入”，但确认创建项目前必须让用户看到缺失清单；是否阻断由错误等级控制。

### 6.3 表格解析

以 `template_profile.json` 为表结构基准：

- A-1 至 A-4 为技术表。
- A-5 至 A-8 为管理表。
- 技术表目标字段：测评单元、测评对象、结果记录、D、A、K、测评对象得分、测评单元得分。
- 管理表目标字段：测评单元、测评对象、结果记录、符合情况、测评单元得分。

关键规则：

- 表头按文本归一化识别，不依赖固定行号。
- 支持纵向合并单元格：如果测评单元列为空或 `vMerge continue`，沿用上一行测评单元。
- 支持技术表两行表头：跳过合并表头和公式表头，只解析数据行。
- 支持内容控件：读取 `w:sdtContent` 中的实际文本。
- 支持字段结果文本：如果单元格内存在 REF/SEQ 字段，使用字段显示文本参与正文重建。
- 过滤空行：测评单元、测评对象、结果记录和评分均为空的行不导入。

### 6.4 图片和题注解析

图片解析目标是生成 `evidence_images`：

- 从 `w:drawing` 找到 `a:blip r:embed`。
- 用 `document.xml.rels` 找到对应 `word/media/*`。
- 复制 PNG/JPEG 到导入任务临时目录。
- 用 Pillow 读取像素、DPI、建议显示宽高。
- 识别附近段落中的题注，优先匹配 `图A-x-y`。
- 根据 `A-x` 判断图片所属章节。
- 按图号中的 `y` 或文档出现顺序设置图片排序。

不支持或无法读取的图片格式生成警告。若图片被正文引用但未能提取为证据图片，应生成错误或警告，避免静默丢失证据。

### 6.5 引用 token 映射

导入后系统内部仍使用 `[[FIG:imageId]]`。解析阶段先使用临时 token：

```text
[[FIG:import:A-1-1]]
```

确认创建项目时，根据实际插入数据库后的图片 ID 替换为：

```text
[[FIG:123]]
```

映射来源优先级：

1. Word REF 字段目标书签映射到图题书签。
2. 字段显示文本或普通文本中的 `图A-x-y`。
3. 结果记录中的 `[插入图片引用]` 只作为占位，不自动绑定图片。

如果引用图号找不到对应图片，保留可见文本并生成断链问题。

## 7. 写入新项目策略

确认导入后，后端在单个事务中完成：

1. 创建新项目，项目名默认来自文档标题或上传文件名。
2. 初始化 A-1 至 A-8 章节。
3. 根据解析结果更新章节标题和表题。
4. 复制图片到 `storage/uploads/{project_id}/{section_code}/`。
5. 创建 `evidence_images`，保持章节内排序、题注、尺寸和 DPI。
6. 写入每个章节的测评行和评分结果。
7. 替换临时图片 token 为真实 `[[FIG:imageId]]`。
8. 写入 `cross_references`。
9. 更新导入任务状态为 `succeeded` 并记录 `created_project_id`。

如任一步失败，应回滚数据库，并清理本次创建项目对应的运行时文件。

## 8. 前端交互方案

### 8.1 首页导入入口

在首页“新建项目”和“已有项目”附近新增“导入 DOCX 创建项目”入口：

- 文件选择：仅允许 `.docx`。
- 上传按钮：上传后进入解析状态。
- 状态展示：上传中、解析中、解析成功、解析失败。

### 8.2 导入预览面板

解析成功后展示：

- 建议项目名称，可编辑。
- 文件名、章节数量、测评行数量、图片数量、引用数量。
- A-1 至 A-8 章节解析结果表。
- 问题清单，按错误、警告、提示分组。
- “确认创建项目”按钮。

如果存在错误：

- 第一版建议阻止确认导入。
- 用户可以查看错误，重新选择文件。

如果只有警告或提示：

- 允许确认导入。
- 创建后仍可在项目内继续校验和修正。

### 8.3 创建后行为

确认创建成功后：

- 刷新项目列表。
- 自动打开新项目。
- 在顶部显示“已从 DOCX 导入，建议先校验项目”。
- 不自动导出，避免用户误以为导入已经完成最终审校。

## 9. 校验和质量控制

新增导入专属问题码：

```text
IMPORT_NOT_APPENDIX_A
IMPORT_MISSING_SECTION
IMPORT_MISSING_TABLE
IMPORT_UNKNOWN_TABLE_SHAPE
IMPORT_EMPTY_REQUIRED_CELL
IMPORT_INVALID_DAK_VALUE
IMPORT_INVALID_COMPLIANCE_VALUE
IMPORT_IMAGE_UNSUPPORTED_FORMAT
IMPORT_IMAGE_CAPTION_MISSING
IMPORT_REFERENCE_TARGET_MISSING
IMPORT_PARTIAL_CONTENT_DROPPED
```

导入后仍复用现有项目校验服务，重点检查：

- 必填字段。
- 图片引用断链。
- 未使用图片。
- 图片质量。
- DOCX 导出 REF 目标完整性。

## 10. 测试策略

必须新增测试层级：

- DOCX 包读取单元测试。
- 表格识别和行解析测试。
- 合并单元格测评单元继承测试。
- 技术表 D/A/K 和评分解析测试。
- 管理表符合情况解析测试。
- 图片和题注提取测试。
- 图号引用到 token 的映射测试。
- 导入预览 API 测试。
- 确认创建新项目 API 测试。
- 端到端回归：已有项目导出 DOCX 后再导入，核心数据一致。
- 前端源码级测试：首页存在导入入口，导入预览接线正确。

建议用当前生成器动态创建 roundtrip fixture，避免维护大量二进制测试文件。

## 11. 风险与控制

| 风险 | 影响 | 控制 |
| --- | --- | --- |
| 非同源 Word 表格结构差异大 | 解析错误或字段错位 | 第一版只承诺模板兼容 DOCX；预览阶段展示问题并阻断严重错误。 |
| Word 字段显示文本和字段目标不一致 | 图片引用错配 | 同时解析 REF 字段、可见图号和题注，无法唯一映射时生成警告。 |
| 图片格式不是 PNG/JPEG | 无法进入现有图片管理流程 | 第一版对不可读格式生成警告；后续可接 LibreOffice 转换。 |
| 合并单元格解析不完整 | 测评单元丢失 | 专门实现 `vMerge` 和 `gridSpan` 归一化测试。 |
| 导入失败留下临时文件 | storage 污染 | 导入任务目录与项目运行目录分离，失败后清理确认阶段创建的项目文件。 |
| 解析耗时较长 | 前端等待过久 | 使用导入任务表承载状态，后续可切换后台任务。 |

## 12. 验收标准

- 可以上传本工具导出的 editable DOCX 并创建新项目。
- 可以上传本工具导出的 final DOCX 并创建新项目。
- 新项目包含 A-1 至 A-8 章节。
- 技术表和管理表测评行核心字段能够恢复。
- 图片文件、题注和排序能够恢复。
- 结果记录中的图片引用位置能够恢复为真实 `[[FIG:imageId]]` token。
- 导入后项目可以保存、校验、预览和导出 editable/final DOCX。
- 导入问题清单能够说明无法解析或疑似丢失的内容。
- 原始上传 DOCX 和原始样本文档不会被覆盖或修改。

## 13. 当前进度

当前进度（2026-06-22）：DI-1 至 DI-2 已完成。后端已新增 `docx_import_jobs` 导入任务表，用于记录上传文件、解析结果、导入摘要、问题清单和最终创建的项目 ID；已新增导入任务 CRUD 函数、导入预览 schema、`backend/app/services/docx_importer/` 服务包骨架和 `storage/imports/{job_id}/` 目录约定。DI-2 已新增 DOCX 包读取与文档结构扫描能力，可安全读取 OpenXML 包、解析关系文件和媒体清单、按 body 顺序识别总标题、章节标题、表题和核心表候选，并对缺失章节/表格生成问题清单；统一检查脚本已通过，当前自动化测试为 69 项。当前尚未开始 DI-3 的核心表格数据行解析。
