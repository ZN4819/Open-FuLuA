import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  confirmReportImport,
  getReportImport,
  updateReportImportResolutions,
  uploadReportImport
} from "../src/api/reportImportClient.ts";
import { getProjectReportMigrationReview } from "../src/api/reportClient.ts";
import {
  parseProjectWorkspacePath,
  parseReportImportPath,
  projectWorkspacePath,
  reportImportPath
} from "../src/projectContracts.ts";

const jobFixture = {
  id: 17,
  status: "preview_ready",
  mode: "migration",
  job_revision: 3,
  original_name: "完整报告.docx",
  detected_edition: "2023",
  detected_revision: "2025-12-08",
  fingerprint: {
    sha256: "a".repeat(64),
    table_count: 55,
    section_count: 17,
    top_level_table_columns: [],
    heading_matches: ["1", "7", "附录A", "附录B"],
    matched: true
  },
  summary: {
    template_match: true,
    chapter_stats: [],
    automatic_mappings: 4,
    pending_confirmation: 1,
    unmapped_content: 1,
    appendix_sources: []
  },
  issues: [],
  resolutions: [],
  appendix_a_source: null,
  confirmable: true,
  created_project_uuid: null,
  created_project_updated_at: null,
  error_message: null,
  created_at: "2026-07-16T08:00:00Z",
  started_at: null,
  finished_at: null
};

test("R6 迁移客户端固定上传、任务、歧义、确认与项目审阅路由", async (context) => {
  const calls = [];
  context.mock.method(globalThis, "fetch", async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method ?? "GET", body: init.body });
    return Response.json(jobFixture);
  });

  await uploadReportImport(new File(["docx"], "完整报告.docx"));
  await getReportImport(17);
  await updateReportImportResolutions(17, 3, [{ issue_id: 9, revision: 2, action: "keep_original" }]);
  await updateReportImportResolutions(
    17,
    4,
    [{ issue_id: 10, revision: 3, action: "skip" }],
    "2026-07-16T08:30:00+00:00"
  );
  await confirmReportImport(17, {
    job_revision: 4,
    project_name: "迁移报告",
    appendix_a_source: "document",
    accepted_resolutions: [],
    keep_unresolved_original: true
  });
  await getProjectReportMigrationReview("project/uuid");

  assert.deepEqual(calls.map(({ url, method }) => ({ url, method })), [
    { url: "/api/report-imports/docx?mode=migration", method: "POST" },
    { url: "/api/report-imports/17", method: "GET" },
    { url: "/api/report-imports/17/resolutions", method: "PUT" },
    { url: "/api/report-imports/17/resolutions", method: "PUT" },
    { url: "/api/report-imports/17/confirm", method: "POST" },
    { url: "/api/projects/project%2Fuuid/report/migration-review", method: "GET" }
  ]);
  assert.deepEqual(JSON.parse(String(calls[2].body)), {
    job_revision: 3,
    resolutions: [{ issue_id: 9, revision: 2, action: "keep_original" }]
  });
  assert.deepEqual(JSON.parse(String(calls[3].body)), {
    job_revision: 4,
    expected_project_updated_at: "2026-07-16T08:30:00+00:00",
    resolutions: [{ issue_id: 10, revision: 3, action: "skip" }]
  });
  assert.equal(JSON.parse(String(calls[4].body)).keep_unresolved_original, true);
});

test("R6 迁移入口覆盖六区审阅、后端附录候选和创建闸门", async () => {
  const [workspace, page, workbench, styles] = await Promise.all([
    readFile(new URL("../src/components/ReportMigrationWorkspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/pages/ProjectPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/ReportWorkbench.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/styles.css", import.meta.url), "utf8")
  ]);

  for (const label of ["模板匹配", "章节统计", "自动映射", "待确认与歧义处理", "未识别内容", "附录来源与创建确认"]) {
    assert.match(workspace, new RegExp(label));
  }
  assert.match(workspace, /appendixCandidatesFromSummary\(job\?\.summary\.appendix_sources\)/);
  assert.match(workspace, /candidate\.complete === true/);
  assert.match(workspace, /selectedAppendixCandidateIsComplete/);
  assert.match(workspace, /\.map\(\(resolution\) => resolution\.id\)/);
  assert.match(workspace, /job\.issues\.filter\(isResolvableIssue\)/);
  assert.match(workspace, /!readonly && isIssueEditable\(issue\)/);
  assert.match(workspace, /isIssueEditable = isResolvableIssue/);
  assert.match(workspace, /issue\.needs_confirmation && !issue\.blocks_confirmation/);
  assert.match(workspace, /isCompositeCandidate\(issue\.candidate_value\)/);
  assert.match(workspace, /必须选择一个标量候选或人工填写标量值/);
  assert.match(workspace, /数组或对象不能整体写入/);
  assert.match(workspace, /resolvedValue: action === "adopt_candidate" && isResolutionScalar\(issue\.candidate_value\)/);
  assert.doesNotMatch(workspace, /resolvedValue: action === "adopt_candidate" \? issue\.candidate_value/);
  assert.match(workspace, /job\.status !== "succeeded" \|\| !isResolvableIssue\(issue\)/);
  assert.match(workspace, /!resolution\.applied/);
  assert.match(workspace, /resolutionActions=\{\["adopt_candidate", "skip"\]\}/);
  assert.match(workspace, /job\.created_project_updated_at/);
  assert.match(workspace, /保存最终处理/);
  assert.match(workspace, /已采用、已跳过及硬阻断项保持只读/);
  assert.match(workspace, /保留原文待确认/);
  assert.match(workspace, /不修改或回写源 DOCX/);
  assert.match(page, /<ReportMigrationWorkspace/);
  assert.match(page, /parseReportImportPath\(window\.location\.pathname\)/);
  assert.match(page, /reportImportPath\(jobId\)/);
  assert.match(workspace, /getReportImport\(initialJobId\)/);
  assert.match(workbench, /<ReportMigrationReview projectUuid=\{project\.project_uuid\}/);
  assert.match(workbench, /created_by_operation === "migration_import"/);
  assert.match(styles, /\.report-migration-panel/);
  assert.doesNotMatch(workspace, /localStorage/);
});

test("迁移审阅拥有稳定深链接", () => {
  const path = projectWorkspacePath("project/1", { view: "migration_review" });
  assert.equal(path, "/projects/project%2F1/migration-review");
  assert.deepEqual(parseProjectWorkspacePath(path), {
    projectUuid: "project/1",
    route: { view: "migration_review" }
  });
  assert.equal(reportImportPath(17), "/report-imports/17");
  assert.deepEqual(parseReportImportPath("/report-imports/17"), { jobId: 17 });
  assert.equal(parseReportImportPath("/report-imports/0"), null);
  assert.equal(parseReportImportPath("/report-imports/17/extra"), null);
});
