import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const pagePath = new URL("../src/pages/ProjectPage.tsx", import.meta.url);
const stylesPath = new URL("../src/styles.css", import.meta.url);

test("项目首页接入双类型创建、标签和复制升级交互", async () => {
  const source = await readFile(pagePath, "utf8");
  assert.match(source, /createProject\(name, projectType\)/);
  assert.match(source, /仅编写附录 A/);
  assert.match(source, /生成完整报告/);
  assert.match(source, /projectTypeLabel\(savedProject\.project_type\)/);
  assert.match(source, /workflowStatusLabel\(savedProject\.workflow_status\)/);
  assert.match(source, /复制为完整报告/);
  assert.match(source, /upgradeProjectCopy\(/);
  assert.match(source, /setUpgradeIdempotencyKey\(crypto\.randomUUID\(\)\)/);
  assert.match(source, /保留名称与幂等键/);
});

test("完整报告默认进入占位工作台且仅从附录 A 工作区导出现有文件", async () => {
  const source = await readFile(pagePath, "utf8");
  assert.match(source, /defaultProjectWorkspace\(projectToOpen\.project_type\)/);
  assert.match(source, /workspaceView === "report_home"/);
  assert.match(source, /完整报告数据域和章节编辑将在下一阶段提供/);
  assert.match(source, /导出附录 A 可编辑版/);
  assert.match(source, /导出附录 A 最终版/);
  assert.match(source, /导出附录 A 打分表/);

  const placeholderStart = source.indexOf("function FullReportPlaceholder");
  const placeholderEnd = source.indexOf("type ImportPreviewPanelProps", placeholderStart);
  const placeholder = source.slice(placeholderStart, placeholderEnd);
  assert.doesNotMatch(placeholder, /handleExport|exportProjectDocx|完整报告导出/);
});

test("DOCX 导入边界和响应式双项目样式可见", async () => {
  const [source, styles] = await Promise.all([
    readFile(pagePath, "utf8"),
    readFile(stylesPath, "utf8")
  ]);
  assert.match(source, /当前 DOCX 导入仅创建附录 A 项目/);
  assert.match(styles, /\.project-type-options/);
  assert.match(styles, /\.project-card-badges/);
  assert.match(styles, /\.project-upgrade-backdrop/);
  assert.match(styles, /\.report-workspace-nav/);
  const mobileStyles = styles.slice(styles.indexOf("@media (max-width: 820px)"));
  assert.match(mobileStyles, /\.full-report-placeholder \.placeholder-grid,/);
});
