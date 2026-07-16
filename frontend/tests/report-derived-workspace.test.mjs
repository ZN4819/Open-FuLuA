import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  parseProjectWorkspacePath,
  projectWorkspacePath
} from "../src/projectContracts.ts";

const workspaceUrl = new URL("../src/components/ReportDerivedWorkspace.tsx", import.meta.url);
const workbenchUrl = new URL("../src/components/ReportWorkbench.tsx", import.meta.url);
const clientUrl = new URL("../src/api/reportClient.ts", import.meta.url);
const stylesUrl = new URL("../src/styles.css", import.meta.url);

test("正文生成工作台拥有稳定深链接并接入章节树", async () => {
  const projectUuid = "3ca1ed39-cd3c-4e4b-8aa9-8c5e416ca6d2";
  assert.equal(
    projectWorkspacePath(projectUuid, { view: "derived" }),
    `/projects/${projectUuid}/derived`
  );
  assert.deepEqual(
    parseProjectWorkspacePath(`/projects/${projectUuid}/derived`),
    { projectUuid, route: { view: "derived" } }
  );

  const workbench = await readFile(workbenchUrl, "utf8");
  assert.match(workbench, /ReportDerivedWorkspace/);
  assert.match(workbench, /正文生成与一致性/);
  assert.match(workbench, /onDirtyChange=\{handleDerivedDirty\}/);
});

test("派生工作台只消费后端权威结果并覆盖生成风险确认一致性链路", async () => {
  const [workspace, client, styles] = await Promise.all([
    readFile(workspaceUrl, "utf8"),
    readFile(clientUrl, "utf8"),
    readFile(stylesUrl, "utf8")
  ]);

  for (const endpoint of [
    "generation/impact-preview",
    "generation/runs",
    "generation/review",
    "/risks",
    "derived-blocks",
    "consistency-checks"
  ]) {
    assert.match(client, new RegExp(endpoint.replace("/", "\\/")));
  }
  assert.match(workspace, /保存并确认风险/);
  assert.match(workspace, /保存人工版本/);
  assert.match(workspace, /执行一致性校验/);
  assert.match(workspace, /上游事实已变化，原一致性结果已失效/);
  assert.match(workspace, /impact\?\.has_changes/);
  assert.match(styles, /R3 正文生成与一致性工作台/);

  assert.doesNotMatch(workspace, /calculate(?:Score|Result|Correction)|ROUND_HALF_UP/);
  assert.doesNotMatch(client, /\bra\b|\brk\b/i);
  assert.doesNotMatch(workspace, /\bra\b|\brk\b/i);
});
