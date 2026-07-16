import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  commitReportRoundtripJob,
  createReportRoundtripJob,
  getReportRoundtripDiff,
  getReportRoundtripIssues,
  getReportRoundtripJob,
  updateReportRoundtripResolution
} from "../src/api/reportRoundtripClient.ts";
import { createReportExportJob } from "../src/api/reportClient.ts";

const jobFixture = {
  id: "job/7",
  project_uuid: "project/1",
  mode: "roundtrip",
  status: "conflicts_pending",
  original_name: "可回收草稿.docx",
  base_project_revision: 12,
  observed_project_revision: 13,
  source_snapshot_id: "snapshot/4",
  diff_hash: "d".repeat(64),
  resolution_hash: null,
  created_at: "2026-07-16T08:00:00Z"
};

test("R7 独立客户端严格覆盖六个 roundtrip 端点和并发绑定字段", async (context) => {
  const calls = [];
  context.mock.method(globalThis, "fetch", async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method ?? "GET", body: init.body });
    const path = String(url);
    if (path.endsWith("/diff")) {
      return Response.json({
        job_id: "job/7",
        status: "conflicts_pending",
        diff_hash: "d".repeat(64),
        base_project_revision: 12,
        observed_project_revision: 13,
        items: []
      });
    }
    if (path.endsWith("/issues")) {
      return Response.json({ job_id: "job/7", status: "conflicts_pending", errors: [], warnings: [], info: [] });
    }
    if (path.endsWith("/resolution")) {
      return Response.json({
        job_id: "job/7",
        status: "ready_to_commit",
        diff_hash: "d".repeat(64),
        resolution_hash: "r".repeat(64),
        expected_project_revision: 13,
        resolved_conflicts: 1
      });
    }
    if (path.endsWith("/commit")) {
      return Response.json({
        job_id: "job/7",
        status: "succeeded",
        project_uuid: "project/1",
        before_revision: 13,
        after_revision: 14,
        resolution_hash: "r".repeat(64),
        applied_fields: 1,
        kept_fields: 0,
        ignored_changes: 2
      });
    }
    return Response.json(jobFixture, { status: init.method === "POST" ? 202 : 200 });
  });

  const file = new File(["docx"], "可回收草稿.docx", {
    type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  });
  await createReportRoundtripJob("project/1", file);
  await getReportRoundtripJob("job/7");
  await getReportRoundtripDiff("job/7");
  await getReportRoundtripIssues("job/7");
  await updateReportRoundtripResolution("job/7", {
    diff_hash: "d".repeat(64),
    expected_project_revision: 13,
    resolutions: [{ conflict_id: 9, action: "apply_word" }]
  });
  await commitReportRoundtripJob("job/7", {
    resolution_hash: "r".repeat(64),
    expected_project_revision: 13
  });

  assert.deepEqual(calls.map(({ url, method }) => ({ url, method })), [
    { url: "/api/projects/project%2F1/report-import-jobs", method: "POST" },
    { url: "/api/report-import-jobs/job%2F7", method: "GET" },
    { url: "/api/report-import-jobs/job%2F7/diff", method: "GET" },
    { url: "/api/report-import-jobs/job%2F7/issues", method: "GET" },
    { url: "/api/report-import-jobs/job%2F7/resolution", method: "PUT" },
    { url: "/api/report-import-jobs/job%2F7/commit", method: "POST" }
  ]);
  assert.equal(calls[0].body.get("mode"), "roundtrip");
  assert.equal(calls[0].body.get("file").name, "可回收草稿.docx");
  assert.deepEqual(JSON.parse(String(calls[4].body)), {
    diff_hash: "d".repeat(64),
    expected_project_revision: 13,
    resolutions: [{ conflict_id: 9, action: "apply_word" }]
  });
  assert.deepEqual(JSON.parse(String(calls[5].body)), {
    resolution_hash: "r".repeat(64),
    expected_project_revision: 13
  });
});

test("R7 工作台独立于 R6 并覆盖上传限制、三方差异、冲突和 stale 闸门", async () => {
  const [workspace, derived, client, styles] = await Promise.all([
    readFile(new URL("../src/components/ReportRoundtripWorkspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/ReportDerivedWorkspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api/reportRoundtripClient.ts", import.meta.url), "utf8"),
    readFile(new URL("../src/styles.css", import.meta.url), "utf8")
  ]);

  assert.match(derived, /<ReportRoundtripWorkspace/);
  assert.match(derived, /hasUnsavedChanges=\{hasUnsavedProjectChanges\}/);
  assert.match(derived, /key !== "roundtrip:resolution"/);
  assert.match(derived, /onDirtyChange=\{handleRoundtripDirty\}/);
  assert.match(workspace, /只修改已有白名单字段和已有业务行/);
  assert.match(workspace, /接受或拒绝全部修订/);
  assert.match(workspace, /图片、题注、引用、页码、域缓存和格式变化不会回收/);
  assert.match(workspace, /导出基线 B/);
  assert.match(workspace, /工具当前值 D/);
  assert.match(workspace, /Word 值 W/);
  assert.match(workspace, /keep_database/);
  assert.match(workspace, /apply_word/);
  assert.match(workspace, /allConflictsResolved/);
  assert.match(workspace, /onDirtyChange\(decisionsDirty\)/);
  assert.match(workspace, /旧差异和冲突决议已经失效/);
  assert.match(workspace, /getReportRoundtripJob\(job\.id\)/);
  assert.match(workspace, /refreshAfterRoundtripError\(resolutionError, operation\)/);
  assert.match(workspace, /refreshAfterRoundtripError\(commitError, operation\)/);
  assert.match(workspace, /ROUNDTRIP_DATABASE_VALUE_STALE/);
  assert.match(workspace, /重新计算评分和正文派生结果/);
  assert.match(styles, /R7 受控 Word 回收/);
  assert.doesNotMatch(client, /reportImportClient|\/api\/report-imports\/docx|migration/);
  assert.doesNotMatch(workspace, /adopt_candidate|keep_original|manual/);
  assert.doesNotMatch(workspace, /localStorage/);
});

test("导出工作台仅为显式可回收草稿发送 roundtrip_capable", async (context) => {
  const payloads = [];
  context.mock.method(globalThis, "fetch", async (_url, init = {}) => {
    payloads.push(JSON.parse(String(init.body)));
    return Response.json({});
  });
  await createReportExportJob("project/1", {
    mode: "draft",
    version: "V1.0",
    expected_project_revision: 7,
    roundtrip_capable: true
  });
  await createReportExportJob("project/1", {
    mode: "draft",
    version: "V1.0",
    expected_project_revision: 7
  });
  await createReportExportJob("project/1", {
    mode: "final",
    version: "V1.0",
    expected_project_revision: 7
  });

  assert.equal(payloads[0].roundtrip_capable, true);
  assert.equal("roundtrip_capable" in payloads[1], false);
  assert.equal("roundtrip_capable" in payloads[2], false);

  const [workspace, client] = await Promise.all([
    readFile(new URL("../src/components/ReportExportWorkspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api/reportClient.ts", import.meta.url), "utf8")
  ]);

  assert.match(workspace, /生成可回收草稿/);
  assert.match(workspace, /handleExport\("draft", true\)/);
  assert.match(workspace, /roundtrip_capable: true/);
  assert.match(workspace, /普通草稿正在装配；该文件不能用于 Word 回收/);
  assert.match(client, /roundtrip_capable\?: boolean/);
  assert.match(workspace, /handleExport\("final"\)/);
  assert.doesNotMatch(workspace, /handleExport\("final", true\)/);
});
