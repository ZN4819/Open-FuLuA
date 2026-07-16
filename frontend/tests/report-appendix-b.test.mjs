import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  createAppendixBRecord,
  deleteAppendixBItem,
  getAppendixB,
  updateAppendixBCategory,
  updateAppendixBImage,
  updateAppendixBRecord,
  validateAppendixB
} from "../src/api/reportClient.ts";

const workspaceUrl = new URL("../src/components/ReportAppendixBWorkspace.tsx", import.meta.url);
const workbenchUrl = new URL("../src/components/ReportWorkbench.tsx", import.meta.url);

test("R5 客户端使用项目 revision、记录 revision 和 UUID 路由", async (context) => {
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url, init = {}) => {
    requests.push({
      url: String(url), method: init.method ?? "GET",
      body: init.body ? JSON.parse(String(init.body)) : undefined
    });
    return Response.json({ item_uuid: "item/1", revision: 4, categories: [] });
  });
  const category = {
    code: "B-1", category_code: "engagement_proof", title: "委托证明文件", order: 1,
    category_uuid: "category/1", is_not_applicable: false, not_applicable_reason: "",
    warning_acknowledged_at: null, revision: 3, items: [], warnings: [], errors: [], completion: "empty"
  };
  const item = {
    item_uuid: "item/1", project_id: 1, category_code: "engagement_proof", item_kind: "record",
    subtype: "engagement", title: "委托合同", location: "", sort_order: 0, metadata: {},
    caption: "", alt_text: "", revision: 4, usages: []
  };
  const payload = {
    subtype: "engagement", title: "委托合同", starts_on: "2026-01-01", ends_on: null,
    organization_uuid: null, location: "", sort_order: 0,
    metadata: { file_type: "合同", amount: "100", unit_price: "100" },
    member_uuids: [], related_item_uuids: []
  };

  await getAppendixB("project/1");
  await updateAppendixBCategory("project/1", category, 7, {
    is_not_applicable: false, not_applicable_reason: "", acknowledge_warning: true
  });
  await createAppendixBRecord("project/1", "engagement_proof", 8, payload);
  await updateAppendixBRecord(item, 9, payload);
  await updateAppendixBImage({ ...item, item_kind: "image", subtype: "engagement_document" }, 10, {
    subtype: "engagement_document", caption: "合同", alt_text: "合同首页", sort_order: 0
  });
  await deleteAppendixBItem(item, 11);
  await validateAppendixB("project/1", 12);

  assert.deepEqual(requests.map((request) => request.url), [
    "/api/projects/project%2F1/report/appendix-b",
    "/api/projects/project%2F1/report/appendix-b/engagement_proof",
    "/api/projects/project%2F1/report/appendix-b/engagement_proof/items",
    "/api/report-evidence-items/item%2F1",
    "/api/report-evidence-items/item%2F1",
    "/api/report-evidence-items/item%2F1?expected_project_revision=11&expected_revision=4",
    "/api/projects/project%2F1/report/appendix-b/validations"
  ]);
  assert.deepEqual(
    [requests[1].body.expected_project_revision, requests[1].body.expected_revision],
    [7, 3]
  );
  assert.deepEqual(
    [requests[3].body.expected_project_revision, requests[3].body.expected_revision],
    [9, 4]
  );
  assert.equal(requests[6].body.expected_project_revision, 12);
});

test("R5 九表工作台覆盖固定类别、关联、图片与未保存保护", async () => {
  const [workspace, workbench] = await Promise.all([
    readFile(workspaceUrl, "utf8"),
    readFile(workbenchUrl, "utf8")
  ]);
  assert.match(workspace, /workspace\.categories\.map/);
  assert.match(workspace, /表 B-1～表 B-9/);
  assert.match(workspace, /显式覆盖的 B-3 进场记录/);
  assert.match(workspace, /保存影响预览/);
  assert.match(workspace, /有效委托单位（中央数据只读）/);
  assert.match(workspace, /组员、密评报告编制人/);
  assert.match(workspace, /备案系统与被测系统是否相同/);
  assert.match(workspace, /image\/png,image\/jpeg/);
  assert.match(workspace, /replaceAppendixBImage/);
  assert.match(workspace, /deleteAppendixBItem/);
  assert.match(workspace, /排序号越小越靠前/);
  assert.match(workspace, /onDirtyChange\(dirtyTokens\.size > 0\)/);
  assert.match(workbench, /activeSection\.section_type === "appendix_b"/);
  assert.match(workbench, /ReportAppendixBWorkspace/);
});
