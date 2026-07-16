import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  createReportExportJob,
  downloadReportExportDocx,
  getReportExportIssues,
  getReportExportJob,
  validateReportExport
} from "../src/api/reportClient.ts";

const workspaceUrl = new URL("../src/components/ReportExportWorkspace.tsx", import.meta.url);
const derivedUrl = new URL("../src/components/ReportDerivedWorkspace.tsx", import.meta.url);

test("完整报告导出客户端覆盖校验、快照任务、轮询、问题和 DOCX 下载端点", async (context) => {
  const requests = [];
  let clicked = false;
  const link = { href: "", download: "", click() { clicked = true; }, remove() {} };
  const originalDocument = globalThis.document;
  globalThis.document = { createElement: () => link, body: { appendChild() {} } };
  context.after(() => { globalThis.document = originalDocument; });
  context.mock.method(URL, "createObjectURL", () => "blob:report");
  context.mock.method(URL, "revokeObjectURL", () => {});
  context.mock.method(globalThis, "fetch", async (url, init) => {
    requests.push({ url: String(url), method: init?.method ?? "GET", body: init?.body });
    if (String(url).endsWith("/docx")) {
      return new Response(new Blob(["docx"]), {
        status: 200,
        headers: { "content-disposition": "attachment; filename*=utf-8''R4%E6%8A%A5%E5%91%8A.docx" }
      });
    }
    if (String(url).endsWith("/issues")) {
      return Response.json({ job_uuid: "job/1", status: "succeeded", errors: [], warnings: [], info: [] });
    }
    if (String(url).includes("report-validations")) {
      return Response.json({ project_uuid: "project/1", project_revision: 7, mode: "final", errors: 0, warnings: 0, issues: [], valid: true });
    }
    return Response.json({
      job_uuid: "job/1", project_id: 1, mode: "final", version: "V1.0", status: "succeeded",
      project_revision: 7, template_package_id: "report-template", template_asset_set_hash: "a".repeat(64),
      template_docx_hash: "b".repeat(64), word_refresh_status: "succeeded", issues: [], created_at: "2026-07-16T00:00:00Z",
      download_available: true
    }, { status: init?.method === "POST" ? 202 : 200 });
  });

  await validateReportExport("project/1", "final");
  await createReportExportJob("project/1", { mode: "final", version: "V1.0", expected_project_revision: 7 });
  await getReportExportJob("job/1");
  await getReportExportIssues("job/1");
  const fileName = await downloadReportExportDocx("job/1");

  assert.deepEqual(requests.map((item) => item.url), [
    "/api/projects/project%2F1/report-validations?mode=final",
    "/api/projects/project%2F1/report-export-jobs",
    "/api/report-export-jobs/job%2F1",
    "/api/report-export-jobs/job%2F1/issues",
    "/api/report-export-jobs/job%2F1/docx"
  ]);
  assert.equal(requests[0].method, "POST");
  assert.equal(JSON.parse(requests[1].body).expected_project_revision, 7);
  assert.equal(fileName, "R4报告.docx");
  assert.equal(link.download, "R4报告.docx");
  assert.equal(clicked, true);
});

test("导出工作区阻断未保存内容并区分草稿、正式版及结构化问题", async () => {
  const [workspace, derived] = await Promise.all([
    readFile(workspaceUrl, "utf8"),
    readFile(derivedUrl, "utf8")
  ]);
  assert.match(derived, /ReportExportWorkspace/);
  assert.match(derived, /hasUnsavedChanges=\{dirtyKeys\.size > 0\}/);
  assert.match(workspace, /生成草稿/);
  assert.match(workspace, /生成正式版/);
  assert.match(workspace, /重新下载 DOCX/);
  assert.match(workspace, /正式版校验未通过/);
  assert.match(workspace, /Microsoft Word 原生刷新目录、页码及交叉引用/);
  assert.match(workspace, /错误（/);
  assert.match(workspace, /警告（/);
  assert.doesNotMatch(workspace, /PDF/);
});
