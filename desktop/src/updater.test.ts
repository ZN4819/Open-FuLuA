import assert from "node:assert/strict";
import test from "node:test";

import { UpdateCoordinator, type AutoUpdaterPort } from "./updater.js";

function fakeUpdater(): AutoUpdaterPort & { calls: string[] } {
  return {
    autoDownload: true,
    autoInstallOnAppQuit: true,
    calls: [],
    on() { return this; },
    async checkForUpdates() { this.calls.push("check"); return null; },
    async downloadUpdate() { this.calls.push("download"); return []; },
    quitAndInstall() { this.calls.push("install"); },
  };
}

function dependencies(overrides: Partial<ConstructorParameters<typeof UpdateCoordinator>[1]> = {}) {
  return {
    isPackaged: true,
    now: () => 1_000_000_000,
    readLastCheck: async () => 0,
    writeLastCheck: async () => undefined,
    schedule: (_delay: number, callback: () => void) => { callback(); return 1; },
    runtimeStatus: async () => ({ maintenance_active: false, business_writes_active: 0 }),
    prepareUpgrade: async () => ({ ready: true, backup_id: "pre_upgrade-safe", schema_version: "1" }),
    writePendingUpgrade: async () => undefined,
    stopSidecar: async () => undefined,
    clearRunMarker: async () => undefined,
    approveControlledQuit: async () => undefined,
    confirmInstall: async () => true,
    notifyError: async () => undefined,
    ...overrides,
  };
}

test("开发模式不联网，打包模式配置手动下载与手动安装", async () => {
  const development = fakeUpdater();
  const coordinator = new UpdateCoordinator(development, dependencies({ isPackaged: false }));
  coordinator.start();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(development.calls, []);

  const packaged = fakeUpdater();
  new UpdateCoordinator(packaged, dependencies());
  assert.equal(packaged.autoDownload, false);
  assert.equal(packaged.autoInstallOnAppQuit, false);
});

test("24 小时内不重复自动检查，成功发起后才持久化", async () => {
  const updater = fakeUpdater(); let writes = 0;
  const coordinator = new UpdateCoordinator(updater, dependencies({
    readLastCheck: async () => 1_000_000_000 - 60_000,
    writeLastCheck: async () => { writes += 1; },
  }));
  coordinator.start();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(updater.calls, []);
  assert.equal(writes, 0);
});

test("菜单手动检查在打包模式忽略自动限频但开发模式仍不联网", async () => {
  const packaged = fakeUpdater();
  const coordinator = new UpdateCoordinator(packaged, dependencies({ readLastCheck: async () => 1_000_000_000 }));
  await coordinator.checkNow();
  assert.deepEqual(packaged.calls, ["check"]);
  const development = fakeUpdater();
  await new UpdateCoordinator(development, dependencies({ isPackaged: false })).checkNow();
  assert.deepEqual(development.calls, []);
});

test("业务忙时不下载并延期，空闲后才下载", async () => {
  const updater = fakeUpdater(); let scheduled = 0;
  const coordinator = new UpdateCoordinator(updater, dependencies({
    runtimeStatus: async () => ({ maintenance_active: true, business_writes_active: 1 }),
    schedule: () => { scheduled += 1; return 1; },
  }));
  await coordinator.handleUpdateAvailable();
  assert.deepEqual(updater.calls, []);
  assert.equal(scheduled, 1);
});

test("用户取消、备份失败或停止侧车失败都不安装", async () => {
  for (const overrides of [
    { confirmInstall: async () => false },
    { prepareUpgrade: async () => { throw new Error("backup failed"); } },
    { stopSidecar: async () => { throw new Error("stop failed"); } },
  ]) {
    const updater = fakeUpdater();
    const coordinator = new UpdateCoordinator(updater, dependencies(overrides));
    await coordinator.handleUpdateDownloaded();
    assert.equal(updater.calls.includes("install"), false);
  }
});

test("仅确认、复查空闲、备份、写标记、停侧车与清标记全部成功后安装", async () => {
  const updater = fakeUpdater(); const order: string[] = [];
  const coordinator = new UpdateCoordinator(updater, dependencies({
    prepareUpgrade: async () => { order.push("backup"); return { ready: true, backup_id: "pre_upgrade-safe", schema_version: "1" }; },
    writePendingUpgrade: async () => { order.push("marker"); },
    stopSidecar: async () => { order.push("stop"); },
    clearRunMarker: async () => { order.push("clear"); },
    approveControlledQuit: async () => { order.push("approve"); },
  }));
  await coordinator.handleUpdateDownloaded();
  assert.deepEqual(order, ["backup", "marker", "stop", "clear", "approve"]);
  assert.equal(updater.calls.at(-1), "install");
});

test("更新校验错误只报告脱敏诊断且绝不安装", async () => {
  const updater = fakeUpdater(); let message = "";
  const coordinator = new UpdateCoordinator(updater, dependencies({ notifyError: async (value) => { message = value; } }));
  await coordinator.handleError(new Error("sha512 mismatch at C:\\Users\\secret\\payload.exe"));
  assert.match(message, /更新校验或下载失败/);
  assert.doesNotMatch(message, /secret|payload/);
  assert.equal(updater.calls.includes("install"), false);
});
