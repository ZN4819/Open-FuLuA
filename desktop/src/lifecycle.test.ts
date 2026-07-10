import assert from "node:assert/strict";
import test from "node:test";

import { diagnosticsPage } from "./diagnostics.js";
import { QuitGuard, focusExistingWindow, runSingleInstance } from "./lifecycle.js";

test("第二实例不进入窗口或侧车启动回调", () => {
  let quitCalls = 0;
  let primaryStarts = 0;

  const primary = runSingleInstance(false, () => { quitCalls += 1; }, () => { primaryStarts += 1; });

  assert.equal(primary, false);
  assert.equal(quitCalls, 1);
  assert.equal(primaryStarts, 0);
});

test("第二实例恢复并聚焦已有最小化窗口", () => {
  let restored = 0;
  let focused = 0;
  focusExistingWindow({ isMinimized: () => true, restore: () => { restored += 1; }, focus: () => { focused += 1; } });
  assert.equal(restored, 1);
  assert.equal(focused, 1);
});

test("关闭侧车失败时保留控制器且不触发新实例创建", async () => {
  const backend = { stop: async (): Promise<void> => { throw new Error("无法确认侧车已退出"); } };
  const guard = new QuitGuard(backend, async () => undefined);
  let createBackendCalls = 1;

  const canQuit = await guard.stopForQuit();
  if (canQuit) createBackendCalls += 1;

  assert.equal(canQuit, false);
  assert.equal(guard.currentBackend(), backend);
  assert.equal(createBackendCalls, 1);
});

test("诊断页面脱敏命令行和 JSON 形式的 session token", () => {
  const page = diagnosticsPage("启动失败", '--session-token=alpha {"session_token":"beta"} {"session-token":"gamma"} session token delta');
  for (const secret of ["alpha", "beta", "gamma", "delta"]) {
    assert.doesNotMatch(page, new RegExp(secret));
  }
  assert.match(page, /已隐藏/);
});
