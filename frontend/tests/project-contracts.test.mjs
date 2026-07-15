import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, createProject, upgradeProjectCopy } from "../src/api/client.ts";
import {
  FULL_REPORT_TEMPLATE_IDENTITY,
  canUpgradeProject,
  defaultProjectWorkspace,
  projectCreatePayload,
  projectTypeLabel,
  projectUpgradeCopyPayload,
  workflowStatusLabel
} from "../src/projectContracts.ts";

const PROJECT_RESPONSE = {
  id: 7,
  project_uuid: "c54a4090-d69f-4feb-aeca-760df514b5e8",
  name: "测试项目",
  project_type: "appendix_a",
  workflow_status: "draft",
  template_package_id: null,
  template_edition: null,
  template_revision: null,
  template_asset_set_hash: null,
  source_project_uuid: null,
  created_by_operation: "create",
  created_at: "2026-07-15T00:00:00Z",
  updated_at: "2026-07-15T00:00:00Z",
  sections: []
};

test("项目契约固定两类创建请求和工作台路由", () => {
  assert.deepEqual(projectCreatePayload("附录项目", "appendix_a"), { name: "附录项目" });
  assert.deepEqual(projectCreatePayload("报告项目", "full_report"), {
    name: "报告项目",
    project_type: "full_report",
    ...FULL_REPORT_TEMPLATE_IDENTITY
  });
  assert.equal(defaultProjectWorkspace("appendix_a"), "appendix_a");
  assert.equal(defaultProjectWorkspace("full_report"), "report_home");
  assert.equal(canUpgradeProject("appendix_a"), true);
  assert.equal(canUpgradeProject("full_report"), false);
  assert.equal(projectTypeLabel("full_report"), "完整报告");
  assert.equal(workflowStatusLabel("ready_for_review"), "待复核");
});

test("复制升级请求固定模板三元组并携带调用方幂等键", () => {
  assert.deepEqual(projectUpgradeCopyPayload("升级项目", "stable-key"), {
    name: "升级项目",
    ...FULL_REPORT_TEMPLATE_IDENTITY,
    idempotency_key: "stable-key"
  });
});

test("附录 A 创建继续只发送 name，完整报告发送冻结模板三元组", async (context) => {
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url, init) => {
    requests.push({ url: String(url), body: JSON.parse(String(init?.body)) });
    return Response.json(PROJECT_RESPONSE, { status: 201 });
  });

  await createProject("旧式请求");
  await createProject("完整报告", "full_report");

  assert.deepEqual(requests, [
    { url: "/api/projects", body: { name: "旧式请求" } },
    {
      url: "/api/projects",
      body: {
        name: "完整报告",
        project_type: "full_report",
        ...FULL_REPORT_TEMPLATE_IDENTITY
      }
    }
  ]);
});

test("复制升级客户端使用项目 UUID 路由并原样复用幂等键", async (context) => {
  let request;
  context.mock.method(globalThis, "fetch", async (url, init) => {
    request = { url: String(url), method: init?.method, body: JSON.parse(String(init?.body)) };
    return Response.json({ ...PROJECT_RESPONSE, project_type: "full_report" }, { status: 201 });
  });

  await upgradeProjectCopy("source/project", "升级副本", "same-key-on-retry");

  assert.deepEqual(request, {
    url: "/api/projects/source%2Fproject/upgrade-copy",
    method: "POST",
    body: {
      name: "升级副本",
      ...FULL_REPORT_TEMPLATE_IDENTITY,
      idempotency_key: "same-key-on-retry"
    }
  });
});

test("通用请求将 FastAPI detail 转换为结构化 ApiError", async (context) => {
  context.mock.method(globalThis, "fetch", async () => Response.json({
    detail: {
      code: "TEMPLATE_PACKAGE_UNTRUSTED",
      message: "模板包完整性校验失败",
      project_uuid: "source-uuid",
      field: "template_package_id",
      details: { expected: "report-2023-2025.12.08" }
    }
  }, { status: 503 }));

  await assert.rejects(
    () => createProject("失败项目", "full_report"),
    (error) => {
      assert.equal(error instanceof ApiError, true);
      assert.equal(error.message, "模板包完整性校验失败");
      assert.equal(error.status, 503);
      assert.equal(error.code, "TEMPLATE_PACKAGE_UNTRUSTED");
      assert.equal(error.projectUuid, "source-uuid");
      assert.equal(error.field, "template_package_id");
      assert.deepEqual(error.details, { expected: "report-2023-2025.12.08" });
      return true;
    }
  );
});
