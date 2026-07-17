import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  getAppendixTransmissionRelations,
  updateAppendixTransmissionRelation
} from "../src/api/reportClient.ts";

const responseFixture = {
  project_revision: 9,
  shared_subsystems: ["核心系统"],
  a2_objects: [{
    object_uuid: "00000000-0000-0000-0000-000000000002",
    object_name: "网络通道一",
    subsystem: "核心系统",
    available_kinds: ["confidentiality", "integrity"],
    relations: []
  }],
  a4_objects: [{
    object_uuid: "00000000-0000-0000-0000-000000000004",
    object_name: "交易数据",
    subsystem: "核心系统",
    available_kinds: ["confidentiality"],
    relations: []
  }]
};

test("附录传输关系客户端固定 GET/PUT 路由并绑定 relation revision", async (context) => {
  const calls = [];
  context.mock.method(globalThis, "fetch", async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method ?? "GET", headers: init.headers, body: init.body });
    return Response.json(responseFixture);
  });

  await getAppendixTransmissionRelations("project/1");
  await updateAppendixTransmissionRelation("project/1", {
    kind: "confidentiality",
    a4_object_uuid: "00000000-0000-0000-0000-000000000004",
    a2_object_uuid: "00000000-0000-0000-0000-000000000002",
    expected_correction_uuid: "00000000-0000-0000-0000-000000000099",
    expected_revision: 3
  });
  await updateAppendixTransmissionRelation("project/1", {
    kind: "integrity",
    a4_object_uuid: "00000000-0000-0000-0000-000000000004",
    a2_object_uuid: null,
    expected_correction_uuid: null,
    expected_revision: null
  });

  assert.deepEqual(calls.map(({ url, method }) => ({ url, method })), [
    { url: "/api/projects/project%2F1/report/appendix-transmission-relations", method: "GET" },
    { url: "/api/projects/project%2F1/report/appendix-transmission-relations", method: "PUT" },
    { url: "/api/projects/project%2F1/report/appendix-transmission-relations", method: "PUT" }
  ]);
  assert.equal(calls[1].headers["If-Match"], "3");
  assert.equal(Object.hasOwn(calls[2].headers, "If-Match"), false);
  assert.deepEqual(JSON.parse(String(calls[1].body)), {
    kind: "confidentiality",
    a4_object_uuid: "00000000-0000-0000-0000-000000000004",
    a2_object_uuid: "00000000-0000-0000-0000-000000000002",
    expected_correction_uuid: "00000000-0000-0000-0000-000000000099",
    expected_revision: 3
  });
});

test("A-2/A-4 面板只展示同子系统同指标候选并在写入后重新读取", async () => {
  const [panel, table, page, client, styles] = await Promise.all([
    readFile(new URL("../src/components/AppendixTransmissionRelationsPanel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/AssessmentTable.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/pages/ProjectPage.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/api/client.ts", import.meta.url), "utf8"),
    readFile(new URL("../src/styles.css", import.meta.url), "utf8")
  ]);

  assert.match(panel, /双向传输指标关联/);
  assert.match(panel, /A-4 每类指标关联一个 A-2 网络通道/);
  assert.match(panel, /一个 A-2 通道可以关联多个 A-4 对象/);
  assert.match(panel, /normalizeSubsystemName\(candidate\.subsystem\) === subsystem/);
  assert.match(panel, /candidates\.length === 0 && !current/);
  assert.match(panel, /hasUnsavedChanges \|\| hasUnsavedObjects/);
  assert.match(panel, /replace\(\/\\s\+\/g, " "\)\.toLowerCase\(\)/);
  assert.match(panel, /candidate\.available_kinds\.includes\(kind\)/);
  assert.match(panel, /type="checkbox"/);
  assert.match(panel, /<select/);
  assert.match(panel, /当前子系统没有具备/);
  assert.match(panel, /存在新对象尚未保存，请先保存附录 A/);
  assert.match(panel, /const refreshed = await updateAppendixTransmissionRelation/);
  assert.match(panel, /const previousHasUnsavedChanges = useRef\(hasUnsavedChanges\)/);
  assert.match(panel, /wasUnsaved && !hasUnsavedChanges && !savingKey/);
  assert.match(panel, /if \(wasUnsaved && !hasUnsavedChanges && !savingKey\) \{\s*void load\(\);/);
  assert.doesNotMatch(panel, />[^<]*(?:UUID|JSON|原始行)[^<]*</);

  assert.match(table, /subsystemInputCandidates/);
  assert.match(table, /sharedSubsystemNames/);
  assert.match(table, /<datalist id=\{`subsystem-candidates-/);
  assert.match(table, /<AppendixTransmissionRelationsPanel/);
  assert.match(page, /enableTransmissionRelations=\{project\.project_type === "full_report"\}/);
  assert.match(styles, /附录 A：A-2 \/ A-4 双向传输指标关联/);

  assert.match(client, /assessment_object_uuid\?: string \| null/);
  assert.match(page, /assessment_object_uuid: row\.assessment_object_uuid/);
  assert.match(table, /assessmentObjectUuid: string = crypto\.randomUUID\(\)/);
  assert.match(table, /assessment_object_uuid: assessmentObjectUuid/);
  assert.match(table, /const assessmentObjectUuid = existingObjectRows/);
});
