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
    prepareUpgrade: async () => ({ ready: true, backup_id: "pre_upgrade-safe", schema_version: "1", lease_id: "lease-safe" }),
    createUpgradeLeaseId: () => "lease-safe",
    cancelUpgrade: async () => undefined,
    writePendingUpgrade: async () => undefined,
    clearPendingUpgrade: async () => undefined,
    stopSidecar: async () => undefined,
    restartSidecar: async () => undefined,
    reloadBusinessPage: async () => undefined,
    clearRunMarker: async () => undefined,
    writeRunMarker: async () => undefined,
    approveControlledQuit: async () => undefined,
    revokeControlledQuit: async () => undefined,
    confirmInstall: async () => true,
    notifyError: async () => undefined,
    version: "0.1.0",
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
  await coordinator.handleUpdateAvailable({ version: "0.2.0" });
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
    await coordinator.handleUpdateDownloaded({ version: "0.2.0" });
    assert.equal(updater.calls.includes("install"), false);
  }
});

test("用户确认安装后再次确认运行时空闲才允许 prepare", async () => {
  const updater = fakeUpdater(); let statusChecks = 0; let prepares = 0; let retries = 0;
  const coordinator = new UpdateCoordinator(updater, dependencies({
    runtimeStatus: async () => {
      statusChecks += 1;
      return statusChecks === 1
        ? { maintenance_active: false, business_writes_active: 0 }
        : { maintenance_active: true, business_writes_active: 1 };
    },
    prepareUpgrade: async (leaseId) => {
      prepares += 1;
      return { ready: true, backup_id: "pre_upgrade-safe", schema_version: "1", lease_id: leaseId };
    },
    schedule: () => { retries += 1; return 1; },
  }));
  await coordinator.handleUpdateDownloaded({ version: "0.2.0" });
  assert.equal(statusChecks, 2); assert.equal(prepares, 0); assert.equal(retries, 1);
  assert.equal(updater.calls.includes("install"), false);
});

test("仅确认、复查空闲、备份、写标记、停侧车与清标记全部成功后安装", async () => {
  const updater = fakeUpdater(); const order: string[] = [];
  const coordinator = new UpdateCoordinator(updater, dependencies({
    prepareUpgrade: async () => { order.push("backup"); return { ready: true, backup_id: "pre_upgrade-safe", schema_version: "1", lease_id: "lease-safe" }; },
    stopSidecar: async () => { order.push("stop"); },
    writePendingUpgrade: async (marker) => { order.push(`marker:${marker.fromVersion}->${marker.targetVersion}`); },
    clearRunMarker: async () => { order.push("clear"); },
    approveControlledQuit: async () => { order.push("approve"); },
  }));
  await coordinator.handleUpdateDownloaded({ version: "0.2.0" });
  assert.deepEqual(order, ["backup", "stop", "marker:0.1.0->0.2.0", "clear", "approve"]);
  assert.equal(updater.calls.at(-1), "install");
});

test("重叠自动和手动检查、重复更新事件保持 single-flight", async () => {
  let resolveCheck!: () => void; let resolveDownload!: () => void;
  const updater = fakeUpdater();
  updater.checkForUpdates = async () => { updater.calls.push("check"); await new Promise<void>((resolve) => { resolveCheck = resolve; }); };
  updater.downloadUpdate = async () => { updater.calls.push("download"); await new Promise<void>((resolve) => { resolveDownload = resolve; }); };
  const coordinator = new UpdateCoordinator(updater, dependencies());
  const checks = [coordinator.checkNow(), coordinator.checkNow()];
  await Promise.resolve(); assert.equal(updater.calls.filter((x) => x === "check").length, 1); resolveCheck(); await Promise.all(checks);
  const downloads = [coordinator.handleUpdateAvailable({ version: "0.2.0" }), coordinator.handleUpdateAvailable({ version: "0.2.0" })];
  await new Promise((resolve) => setImmediate(resolve)); assert.equal(updater.calls.filter((x) => x === "download").length, 1); resolveDownload(); await Promise.all(downloads);
});

test("下载完成时业务忙只保留一个延期任务，空闲后只弹窗和备份一次", async () => {
  const updater = fakeUpdater(); let busy = true; let retry: (() => void) | undefined; let confirmations = 0; let backups = 0;
  const coordinator = new UpdateCoordinator(updater, dependencies({
    runtimeStatus: async () => ({ maintenance_active: busy, business_writes_active: busy ? 1 : 0 }),
    schedule: (_delay, callback) => { retry ??= callback; return 1; },
    confirmInstall: async () => { confirmations += 1; return true; },
    prepareUpgrade: async () => { backups += 1; return { ready: true, backup_id: "pre_upgrade-safe", schema_version: "1", lease_id: "lease-safe" }; },
  }));
  await Promise.all([coordinator.handleUpdateDownloaded({ version: "0.2.0" }), coordinator.handleUpdateDownloaded({ version: "0.2.0" })]);
  assert.equal(confirmations, 0); assert.ok(retry);
  busy = false; retry!(); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(confirmations, 1); assert.equal(backups, 1); assert.equal(updater.calls.filter((x) => x === "install").length, 1);
});

test("停侧车后的 marker/clear/approve/install 失败均清理本次 pending 并重启恢复", async () => {
  for (const failedStep of ["marker", "clear", "approve", "install"] as const) {
    const updater = fakeUpdater(); const order: string[] = [];
    if (failedStep === "install") updater.quitAndInstall = () => { throw new Error("install failed"); };
    const coordinator = new UpdateCoordinator(updater, dependencies({
      stopSidecar: async () => { order.push("stop"); },
      writePendingUpgrade: async () => { order.push("marker"); if (failedStep === "marker") throw new Error("marker failed"); },
      clearRunMarker: async () => { order.push("clear"); if (failedStep === "clear") throw new Error("clear failed"); },
      approveControlledQuit: async () => { order.push("approve"); if (failedStep === "approve") throw new Error("approve failed"); },
      clearPendingUpgrade: async (id) => { order.push(`clear-pending:${id}`); },
      restartSidecar: async () => { order.push("restart"); },
      writeRunMarker: async () => { order.push("write-run"); },
      revokeControlledQuit: async () => { order.push("revoke"); },
    }));
    await coordinator.handleUpdateDownloaded({ version: "0.2.0" });
    assert.ok(order.includes("restart"), failedStep);
    if (failedStep !== "marker") assert.ok(order.includes("clear-pending:pre_upgrade-safe"), failedStep);
  }
});

test("安装中止后侧车重启失败进入诊断且文案不声称可继续", async () => {
  const updater = fakeUpdater(); const messages: string[] = [];
  const coordinator = new UpdateCoordinator(updater, dependencies({
    writePendingUpgrade: async () => { throw new Error("marker failed"); },
    restartSidecar: async () => { throw new Error("restart failed"); },
    notifyFatal: async (message) => { messages.push(message); },
    notifyError: async (message) => { messages.push(message); },
  }));
  await coordinator.handleUpdateDownloaded({ version: "0.2.0" });
  assert.ok(messages.some((message) => /暂勿继续编辑/.test(message)));
  assert.ok(messages.every((message) => !/仍可继续|可以继续/.test(message)));
});

test("stopSidecar 失败表示退出状态不确定，禁止启动或重载第二套侧车", async () => {
  let restarted = 0; let reloaded = 0; let fatal = "";
  const coordinator = new UpdateCoordinator(fakeUpdater(), dependencies({
    stopSidecar: async () => { throw new Error("unknown process state"); },
    restartSidecar: async () => { restarted += 1; }, reloadBusinessPage: async () => { reloaded += 1; },
    notifyFatal: async (message) => { fatal = message; },
  }));
  await coordinator.handleUpdateDownloaded({ version: "0.2.0" });
  assert.equal(restarted, 0); assert.equal(reloaded, 0); assert.match(fatal, /暂勿继续编辑/);
});

test("prepare 响应丢失时使用请求前生成的 lease ID 幂等取消", async () => {
  const cancelled: string[] = [];
  const coordinator = new UpdateCoordinator(fakeUpdater(), dependencies({
    createUpgradeLeaseId: () => "lease-known-before-request",
    prepareUpgrade: async () => { throw new Error("response lost"); },
    cancelUpgrade: async (leaseId) => { cancelled.push(leaseId); },
  }));
  await coordinator.handleUpdateDownloaded({ version: "0.2.0" });
  assert.deepEqual(cancelled, ["lease-known-before-request"]);
});

test("更新校验错误只报告脱敏诊断且绝不安装", async () => {
  const updater = fakeUpdater(); let message = "";
  const coordinator = new UpdateCoordinator(updater, dependencies({ notifyError: async (value) => { message = value; } }));
  await coordinator.handleError(new Error("sha512 mismatch at C:\\Users\\secret\\payload.exe"));
  assert.match(message, /更新校验、下载或安装准备失败/);
  assert.doesNotMatch(message, /secret|payload/);
  assert.equal(updater.calls.includes("install"), false);
});
