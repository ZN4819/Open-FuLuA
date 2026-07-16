import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  createCryptoProduct,
  createAssessmentObject,
  createCorrectionRelation,
  createObjectRelation,
  createReportBlock,
  createReportStandard,
  createSpecialIndicator,
  confirmAppendixBindings,
  deleteAssessmentObject,
  deleteCorrectionRelation,
  deleteCryptoProduct,
  deleteObjectRelation,
  deleteReportBlock,
  deleteReportMember,
  deleteReportOrganization,
  deleteReportStandard,
  deleteSpecialIndicator,
  getReportDistribution,
  getReportNumberAvailability,
  getReportOverview,
  getReportPhaseDates,
  getReportSections,
  getReportSystemProfile,
  getReportTemplateRuleHints,
  listCryptoProducts,
  listReportMembers,
  listReportOrganizations,
  listReportStandards,
  listSpecialIndicators,
  listDuplicateObjectCandidates,
  mergeAssessmentObjects,
  previewAppendixBindings,
  updateReportBlock,
  updateCorrectionRelation,
  updateObjectRelation,
  updateReportSection,
  updateCryptoProduct,
  updateReportDistribution,
  updateReportMember,
  updateReportMetadata,
  updateReportOrganization,
  updateReportStandard,
  updateSpecialIndicator,
  upsertObjectSubsystem
} from "../src/api/reportClient.ts";

const workbenchPath = new URL("../src/components/ReportWorkbench.tsx", import.meta.url);
const basicDataPath = new URL("../src/components/ReportBasicDataWorkspace.tsx", import.meta.url);
const reportClientPath = new URL("../src/api/reportClient.ts", import.meta.url);
const stylesPath = new URL("../src/styles.css", import.meta.url);

test("报告工作台覆盖章节树、对象库、结构化块、未保存与冲突交互", async () => {
  const [workbench, basicData, reportClient, styles] = await Promise.all([
    readFile(workbenchPath, "utf8"),
    readFile(basicDataPath, "utf8"),
    readFile(reportClientPath, "utf8"),
    readFile(stylesPath, "utf8")
  ]);

  assert.match(workbench, /getReportSections\(project\.project_uuid\)/);
  assert.match(workbench, /flattenSectionTree\(sections\)/);
  assert.match(workbench, /完整报告章节树/);
  assert.match(workbench, /测评对象与引用/);
  assert.match(workbench, /ReportBasicDataWorkspace/);
  assert.match(workbench, /提交复核/);
  assert.match(workbench, /ReportBlockEditor/);
  assert.match(workbench, /beforeunload/);
  assert.match(workbench, /REVISION_CONFLICT/);
  assert.match(workbench, /ArrowDown/);
  assert.match(styles, /@media \(max-width: 520px\)/);
  assert.match(styles, /\.report-workbench-grid/);
  assert.match(basicData, /被测单位与委托单位/);
  assert.match(basicData, /项目成员与编审角色/);
  assert.match(basicData, /四阶段日期、差旅与进离场/);
  assert.match(basicData, /分发份数/);
  assert.match(basicData, /系统画像与受控分支/);
  assert.match(basicData, /密码产品/);
  assert.match(basicData, /标准与特殊指标/);
  assert.match(workbench, /母版批注提示/);
  assert.match(workbench, /不会阻断提交复核/);
  assert.match(basicData, /effective_client_organization_name/);
  assert.match(basicData, /REVISION_CONFLICT/);
  assert.match(workbench, /附录 A 绑定预览/);
  assert.match(workbench, /确认选中绑定/);
  assert.match(workbench, /A-4 子系统、测评方式与备注/);
  assert.match(workbench, /重复对象与显式合并/);
  assert.match(workbench, /对象关系/);
  assert.match(workbench, /A-2\/A-4 修正关系/);
  assert.match(workbench, /章节完成状态/);
  for (const label of ["段落", "项目列表", "编号列表", "键值表", "数据表", "图片", "引用"]) {
    assert.match(workbench, new RegExp(label));
  }

  assert.doesNotMatch(workbench, /\bra\b|\brk\b/i);
  assert.doesNotMatch(basicData, /\bra\b|\brk\b/i);
  assert.doesNotMatch(reportClient, /\bra\b|\brk\b/i);
});

test("基础数据客户端覆盖全部 R2 数据域真实路由", async (context) => {
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url, init = {}) => {
    requests.push({ url: String(url), method: init.method ?? "GET", headers: init.headers, body: init.body });
    if (String(url).endsWith("/crypto-products")) return Response.json({ items: [], summary: { total: 0, exclusive: 0, shared: 0, certified: 0, uncertified_domestic: 0, foreign: 0 } });
    if (String(url).endsWith("/distribution")) return Response.json({ regulator_copies: 0, client_copies: 0, assessment_copies: 0, total_copies: 0, revision: 2 });
    if (String(url).endsWith("/phase-dates")) return Response.json({ travel_records: [], onsite_records: [], revision: 3 });
    if (String(url).endsWith("/system-profile")) return Response.json({ selected_algorithms: [], other_algorithms: [], application_catalog: [], revision: 4 });
    return Response.json([]);
  });

  await Promise.all([
    listReportOrganizations("p/1"), listReportMembers("p/1"), getReportPhaseDates("p/1"),
    getReportDistribution("p/1"), getReportSystemProfile("p/1"), listCryptoProducts("p/1"),
    listReportStandards("p/1"), listSpecialIndicators("p/1")
  ]);

  assert.deepEqual(requests.map((item) => item.url), [
    "/api/projects/p%2F1/report/organizations",
    "/api/projects/p%2F1/report/members",
    "/api/projects/p%2F1/report/phase-dates",
    "/api/projects/p%2F1/report/distribution",
    "/api/projects/p%2F1/report/system-profile",
    "/api/projects/p%2F1/report/crypto-products",
    "/api/projects/p%2F1/report/standards",
    "/api/projects/p%2F1/report/special-indicators"
  ]);
});

test("基础数据写入携带 revision 且新增实体不伪造标识", async (context) => {
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url, init = {}) => {
    requests.push({ url: String(url), method: init.method, headers: init.headers, body: JSON.parse(String(init.body)) });
    return Response.json({ revision: 2 });
  });

  await updateReportDistribution("p-1", 7, { regulator_copies: 1, client_copies: 2, assessment_copies: 3 });
  await createCryptoProduct("p-1", { name: "密码机", model: "M1", manufacturer: "厂商", certificate_no: "C1", quantity_text: "若干", use_mode: "exclusive", classification: "certified", sort_order: 0 });
  await createReportStandard("p-1", { code: "GM/T X", name: "人工标准", source_ref: "项目资料", sort_order: 10 });
  await createSpecialIndicator("p-1", { manual_standard_uuid: "00000000-0000-0000-0000-000000000001", indicator_code: "S-1", indicator_name: "特殊指标", description: "说明", sort_order: 0 });

  assert.deepEqual(requests[0].headers, { "Content-Type": "application/json", "If-Match": "7" });
  assert.deepEqual(requests[0].body, { regulator_copies: 1, client_copies: 2, assessment_copies: 3, expected_revision: 7 });
  assert.equal(requests[1].url, "/api/projects/p-1/report/crypto-products");
  assert.equal(Object.hasOwn(requests[1].body, "product_uuid"), false);
  assert.equal(requests[2].url, "/api/projects/p-1/report/standards");
  assert.equal(requests[3].body.manual_standard_uuid, "00000000-0000-0000-0000-000000000001");
});

test("报告编号可用性查询编码输入并保留重复数量", async (context) => {
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url) => {
    requests.push(String(url));
    return Response.json({ report_number: "宁密评/2026 01", available: false, duplicate_project_count: 2, empty: false });
  });

  const result = await getReportNumberAvailability("project/1", "宁密评/2026 01");

  assert.equal(
    requests[0],
    "/api/projects/project%2F1/report/metadata/report-number-availability?report_number=%E5%AE%81%E5%AF%86%E8%AF%84%2F2026%2001"
  );
  assert.equal(result.available, false);
  assert.equal(result.duplicate_project_count, 2);
});

test("母版批注提示按模板包读取且独立于阻断校验", async (context) => {
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url) => {
    requests.push(String(url));
    return Response.json({
      package_id: "report-2023-2025.12.08",
      rules: [{ rule_id: "hint_001", category: "layout", sanitized_summary: "版式提示", approval_status: "pending", runtime_behavior: "none" }]
    });
  });

  const result = await getReportTemplateRuleHints("report/2023");

  assert.equal(requests[0], "/api/report-templates/report%2F2023/rule-hints");
  assert.equal(result.rules[0].approval_status, "pending");
});

test("实体更新只回传可写字段并剔除服务端只读字段", async (context) => {
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url, init = {}) => {
    requests.push({ url: String(url), headers: init.headers, body: JSON.parse(String(init.body)) });
    return Response.json({ revision: 10 });
  });

  await updateReportOrganization("p-1", {
    organization_uuid: "o-1", organization_type: "assessed", name: "被测单位", address: "地址", postal_code: "750000",
    contact_name: "联系人", contact_title: "职务", contact_department: "部门", office_phone: "0951", mobile_phone: "13800000000",
    email: "a@example.com", active: true, sort_order: 0, revision: 3, project_uuid: "只读项目", created_at: "只读时间"
  });
  await updateReportMember("p-1", {
    member_uuid: "m-1", organization_uuid: "o-1", name: "编制人", team_role: "member", is_leader: false,
    qualification_passed_at: "2025-01-01", title: "工程师", department: "测评部", certificate_no: "C-1", office_phone: "0951",
    mobile_phone: "13800000000", email: "m@example.com", active: true, sort_order: 0, revision: 4,
    project_id: 99, created_at: "只读时间"
  });
  await updateCryptoProduct("p-1", {
    product_uuid: "c-1", name: "密码机", model: "M1", manufacturer: "厂商", certificate_no: "CERT",
    quantity_text: "若干", normalized_quantity: 1, use_mode: "exclusive", classification: "certified", sort_order: 0,
    revision: 5, project_uuid: "只读项目", created_at: "只读时间"
  });
  await updateReportStandard("p-1", {
    standard_uuid: "s-1", kind: "manual", code: "GM/T X", name: "人工标准", source_ref: "项目资料", sort_order: 10,
    revision: 6, project_uuid: "只读项目", created_at: "只读时间"
  });
  await updateSpecialIndicator("p-1", {
    indicator_uuid: "i-1", manual_standard_uuid: "s-1", indicator_code: "S-1", indicator_name: "特殊指标",
    description: "说明", sort_order: 0, revision: 7, project_uuid: "只读项目", created_at: "只读时间"
  });

  for (const request of requests) {
    assert.equal(Object.hasOwn(request.body, "project_uuid"), false);
    assert.equal(Object.hasOwn(request.body, "project_id"), false);
    assert.equal(Object.hasOwn(request.body, "created_at"), false);
    assert.match(String(request.headers["If-Match"]), /^\d+$/);
  }
  assert.deepEqual(requests[0].body, {
    organization_type: "assessed", name: "被测单位", address: "地址", postal_code: "750000", contact_name: "联系人",
    contact_title: "职务", contact_department: "部门", office_phone: "0951", mobile_phone: "13800000000", email: "a@example.com",
    active: true, sort_order: 0, expected_revision: 3
  });
  assert.equal(Object.hasOwn(requests[1].body, "member_uuid"), false);
  assert.equal(Object.hasOwn(requests[2].body, "product_uuid"), false);
  assert.equal(Object.hasOwn(requests[2].body, "normalized_quantity"), false);
  assert.equal(Object.hasOwn(requests[3].body, "standard_uuid"), false);
  assert.equal(Object.hasOwn(requests[3].body, "kind"), false);
  assert.equal(Object.hasOwn(requests[4].body, "indicator_uuid"), false);
});

test("报告客户端使用 UUID 路由并携带 revision 并发条件", async (context) => {
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url, init = {}) => {
    requests.push({ url: String(url), method: init.method ?? "GET", headers: init.headers, body: init.body });
    if (String(url).endsWith("/overview")) {
      return Response.json({ project_uuid: "p-1" });
    }
    if (String(url).endsWith("/sections")) {
      return Response.json([]);
    }
    if (String(url).endsWith("/metadata")) {
      return Response.json({ project_uuid: "p-1", report_number: "R-1", default_export_version: "V1.0", revision: 4 });
    }
    if (String(url).includes("/blocks/")) {
      return Response.json({ block_uuid: "b-1", revision: 8, payload: { text: "新内容" } });
    }
    return Response.json({ object_uuid: "o-1", object_type: "device", name_snapshot: "设备一", active: true, reference_count: 0, revision: 1 });
  });

  await getReportOverview("project/1");
  await getReportSections("project/1");
  await updateReportMetadata("project/1", 3, { report_number: "R-1", default_export_version: "V1.0" });
  await updateReportBlock("project/1", { block_uuid: "block/1", revision: 7, payload: { text: "新内容" } });
  await createAssessmentObject("project/1", { object_type: "device", name_snapshot: "设备一" });

  assert.equal(requests[0].url, "/api/projects/project%2F1/report/overview");
  assert.equal(requests[1].url, "/api/projects/project%2F1/report/sections");
  assert.deepEqual(requests[2].headers, { "Content-Type": "application/json", "If-Match": "3" });
  assert.deepEqual(JSON.parse(String(requests[2].body)), {
    report_number: "R-1",
    default_export_version: "V1.0",
    expected_revision: 3
  });
  assert.equal(requests[3].url, "/api/projects/project%2F1/report/blocks/block%2F1");
  assert.deepEqual(JSON.parse(String(requests[3].body)), { payload: { text: "新内容" }, expected_revision: 7 });
  assert.deepEqual(JSON.parse(String(requests[4].body)), { object_type: "device", name_snapshot: "设备一" });
});

test("R2 对象绑定、关系、修正、合并与章节状态使用真实契约", async (context) => {
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url, init = {}) => {
    const request = { url: String(url), method: init.method ?? "GET", headers: init.headers, body: init.body ? JSON.parse(String(init.body)) : undefined };
    requests.push(request);
    if (request.url.endsWith("/appendix-a-bindings/preview")) return Response.json({ exact: [], candidate: [], ambiguous: [], unmatched: [] });
    if (request.url.endsWith("/appendix-a-bindings/confirm")) return Response.json({ bound_count: 1, bindings: request.body.choices });
    if (request.url.endsWith("/objects/duplicate-candidates")) return Response.json([]);
    if (request.url.includes("/objects/source-1/merge")) return Response.json({ object_uuid: "target-1", object_type: "network", name_snapshot: "通道", properties: {}, methods: [], remark: "", active: true, reference_count: 0, revision: 4 });
    if (request.url.endsWith("/assessment-object-subsystems")) return Response.json({ binding_uuid: "sub-1", object_uuid: "a4-1", subsystem_name: "系统一", methods: ["访谈"], remark: "", revision: 2 });
    if (request.url.includes("/object-relations")) return Response.json({ relation_uuid: "rel-1", source_object_uuid: "a2-1", target_object_uuid: "a4-1", relation_type: "connects", properties: {}, active: true, revision: 2 });
    if (request.url.includes("/result-correction-relations")) return Response.json({ correction_uuid: "cor-1", a2_object_uuid: "a2-1", a4_object_uuid: "a4-1", correction_kind: "integrity", a2_metric_code: "通信数据完整性", a4_metric_code: "重要数据传输完整性", original_references: {}, revision: 2 });
    if (request.url.includes("/sections/section-1") && request.method === "PUT") return Response.json({ section_uuid: "section-1", completion_status: "complete", revision: 4 });
    return Response.json({ block_uuid: "block-1", revision: 1, payload: request.body?.payload ?? {} });
  });

  await previewAppendixBindings("p-1");
  await confirmAppendixBindings("p-1", [{ source_row_id: 9, object_uuid: "a2-1" }]);
  await upsertObjectSubsystem("p-1", { object_uuid: "a4-1", subsystem_name: "系统一", methods: ["访谈"], remark: "", expected_revision: 1 });
  await listDuplicateObjectCandidates("p-1");
  await mergeAssessmentObjects("p-1", { object_uuid: "source-1", revision: 2 }, { object_uuid: "target-1", revision: 3 });
  await createObjectRelation("p-1", { source_object_uuid: "a2-1", target_object_uuid: "a4-1", relation_type: "connects", properties: {}, active: true });
  await updateObjectRelation("p-1", { relation_uuid: "rel-1", source_object_uuid: "a2-1", target_object_uuid: "a4-1", relation_type: "protects", properties: {}, active: true, revision: 5 });
  await createCorrectionRelation("p-1", { a2_object_uuid: "a2-1", a4_object_uuid: "a4-1", correction_kind: "integrity", a2_metric_code: "通信数据完整性", a4_metric_code: "重要数据传输完整性", original_references: {} });
  await updateCorrectionRelation("p-1", { correction_uuid: "cor-1", a2_object_uuid: "a2-1", a4_object_uuid: "a4-1", correction_kind: "integrity", a2_metric_code: "通信数据完整性", a4_metric_code: "重要数据传输完整性", original_references: {}, revision: 6 });
  await updateReportSection("p-1", { section_uuid: "section-1", completion_status: "in_progress", revision: 3 }, "complete");

  const blockPayloads = [
    ["paragraph", { text: "" }], ["bullet_list", { items: [] }], ["numbered_list", { items: [] }],
    ["key_value_table", { rows: [] }], ["data_table", { schema_version: "1", columns: [{ key: "column_1", label: "列 1" }], rows: [] }],
    ["figure", { figure_uuid: "image-1", caption: null }], ["reference", { target_uuid: "object-1", label: null }]
  ];
  for (const [type, payload] of blockPayloads) await createReportBlock("p-1", "section-1", type, payload);

  assert.deepEqual(requests[1].body, { choices: [{ source_row_id: 9, object_uuid: "a2-1" }] });
  assert.equal(requests[2].headers["If-Match"], "1");
  assert.deepEqual(requests[4].body, { target_object_uuid: "target-1", source_expected_revision: 2, target_expected_revision: 3 });
  assert.equal(requests[6].headers["If-Match"], "5");
  assert.equal(requests[8].headers["If-Match"], "6");
  assert.deepEqual(requests[9].body, { completion_status: "complete", expected_revision: 3 });
  for (const request of requests.slice(10)) {
    assert.equal(Object.hasOwn(request.body, "block_key"), false);
    assert.ok(request.body.payload);
  }
});

test("所有 R2 删除请求使用查询 revision 与 If-Match 且没有请求体", async (context) => {
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url, init = {}) => {
    requests.push({ url: String(url), method: init.method, headers: init.headers, body: init.body });
    return Response.json({ revision: 1, properties: {}, methods: [], remark: "" });
  });

  await deleteReportOrganization("p-1", { organization_uuid: "org/1", revision: 1 });
  await deleteReportMember("p-1", { member_uuid: "member/1", revision: 2 });
  await deleteCryptoProduct("p-1", { product_uuid: "product/1", revision: 3 });
  await deleteReportStandard("p-1", { standard_uuid: "standard/1", revision: 4 });
  await deleteSpecialIndicator("p-1", { indicator_uuid: "indicator/1", revision: 5 });
  await deleteAssessmentObject("p-1", { object_uuid: "object/1", revision: 6 });
  await deleteObjectRelation("p-1", { relation_uuid: "relation/1", revision: 7 });
  await deleteCorrectionRelation("p-1", { correction_uuid: "correction/1", revision: 8 });
  await deleteReportBlock("p-1", { block_uuid: "block/1", revision: 9 });

  assert.deepEqual(requests.map((request) => request.url), [
    "/api/projects/p-1/report/organizations/org%2F1?expected_revision=1",
    "/api/projects/p-1/report/members/member%2F1?expected_revision=2",
    "/api/projects/p-1/report/crypto-products/product%2F1?expected_revision=3",
    "/api/projects/p-1/report/standards/standard%2F1?expected_revision=4",
    "/api/projects/p-1/report/special-indicators/indicator%2F1?expected_revision=5",
    "/api/projects/p-1/report/objects/object%2F1?expected_revision=6",
    "/api/projects/p-1/report/object-relations/relation%2F1?expected_revision=7",
    "/api/projects/p-1/report/result-correction-relations/correction%2F1?expected_revision=8",
    "/api/projects/p-1/report/blocks/block%2F1?expected_revision=9"
  ]);
  requests.forEach((request, index) => {
    assert.equal(request.method, "DELETE");
    assert.equal(request.headers["If-Match"], String(index + 1));
    assert.equal(request.body, undefined);
  });
});

test("顶层结构化 revision 冲突保留错误码和服务器版本", async (context) => {
  context.mock.method(globalThis, "fetch", async () => Response.json({
    code: "REVISION_CONFLICT",
    message: "内容已在其他页面更新，请刷新后重试。",
    entity_type: "report_block",
    entity_uuid: "b-1",
    details: { expected_revision: 3, current_revision: 4 }
  }, { status: 409 }));

  await assert.rejects(
    () => updateReportBlock("p-1", { block_uuid: "b-1", revision: 3, payload: { text: "本地草稿" } }),
    (error) => {
      assert.equal(error.code, "REVISION_CONFLICT");
      assert.equal(error.status, 409);
      assert.deepEqual(error.details, { expected_revision: 3, current_revision: 4 });
      return true;
    }
  );
});
