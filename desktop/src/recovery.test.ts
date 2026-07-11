import assert from "node:assert/strict";
import { mkdir, mkdtemp, readdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { JsonRecoveryMarkerStore, RecoveryCoordinator, type PendingUpgradeMarker, type RecoveryMarkerStore } from "./recovery.js";

const STARTUP = { currentVersion: "0.2.0", schemaVersion: "1" };

class MemoryMarkers implements RecoveryMarkerStore {
  runMarker = false;
  pending: PendingUpgradeMarker | undefined;
  get pendingBackupId() { return this.pending?.backupId; }
  set pendingBackupId(value: string | undefined) {
    this.pending = value ? { fromVersion: "0.1.0", targetVersion: "0.2.0", schemaVersion: "1", createdAt: new Date().toISOString(), backupId: value } : undefined;
  }
  async hasRunMarker() { return this.runMarker; }
  async writeRunMarker() { this.runMarker = true; }
  async clearRunMarker() { this.runMarker = false; }
  async readPendingUpgrade() { return this.pending; }
  async writePendingUpgrade(marker: PendingUpgradeMarker) { this.pending = marker; }
  async clearPendingUpgrade() { this.pending = undefined; }
}

test("旧运行标记存在时先校验完整性，确认后才加载业务页", async () => {
  const markers = new MemoryMarkers(); markers.runMarker = true;
  const order: string[] = [];
  const recovery = new RecoveryCoordinator(markers, {
    checkIntegrity: async () => { order.push("integrity"); return { integrity: "ok", schema_version: "1" }; },
    chooseCrashAction: async () => "continue",
    loadBusinessPage: async () => { order.push("load"); },
    restoreOffline: async () => true,
    restartSidecar: async () => undefined,
    showLogs: async () => undefined,
  });

  await recovery.openAfterStartup(STARTUP);
  assert.deepEqual(order, ["integrity", "load"]);
});

test("完整性异常时即使选择继续也不加载业务页", async () => {
  const markers = new MemoryMarkers(); markers.runMarker = true;
  let loaded = false;
  const recovery = new RecoveryCoordinator(markers, {
    checkIntegrity: async () => ({ integrity: "corrupt", schema_version: "1" }),
    chooseCrashAction: async (canContinue) => { assert.equal(canContinue, false); return "continue"; },
    loadBusinessPage: async () => { loaded = true; },
    restoreOffline: async () => false,
    restartSidecar: async () => undefined,
    showLogs: async () => undefined,
  });

  await recovery.openAfterStartup(STARTUP);
  assert.equal(loaded, false);
});

test("schema 不匹配与数据库损坏同样禁止继续", async () => {
  const markers = new MemoryMarkers(); markers.runMarker = true; let loaded = false;
  const recovery = new RecoveryCoordinator(markers, {
    checkIntegrity: async () => ({ integrity: "ok", schema_version: "999" }),
    chooseCrashAction: async (canContinue) => { assert.equal(canContinue, false); return "continue"; },
    loadBusinessPage: async () => { loaded = true; }, restoreOffline: async () => false,
    restartSidecar: async () => undefined, showLogs: async () => undefined,
  });
  await recovery.openAfterStartup(STARTUP);
  assert.equal(loaded, false);
});

test("待升级恢复失败保留标记，成功才清除并重启", async () => {
  const markers = new MemoryMarkers(); markers.pendingBackupId = "pre_upgrade-safe";
  let restarted = 0;
  const dependencies = {
    checkIntegrity: async () => ({ integrity: "corrupt", schema_version: "1" }),
    chooseCrashAction: async () => "restore" as const,
    loadBusinessPage: async () => undefined,
    restoreOffline: async () => false,
    restartSidecar: async () => { restarted += 1; },
    showLogs: async () => undefined,
  };
  const recovery = new RecoveryCoordinator(markers, dependencies);

  assert.equal(await recovery.restorePendingUpgrade(STARTUP), false);
  assert.equal(markers.pendingBackupId, "pre_upgrade-safe");
  dependencies.restoreOffline = async () => true;
  dependencies.checkIntegrity = async () => ({ integrity: "ok", schema_version: "1" });
  assert.equal(await recovery.restorePendingUpgrade(STARTUP), true);
  assert.equal(markers.pendingBackupId, undefined);
  assert.equal(restarted, 1);
});

test("没有待升级标记时恢复动作进入最近备份入口", async () => {
  const markers = new MemoryMarkers(); markers.runMarker = true;
  let restored = 0;
  const recovery = new RecoveryCoordinator(markers, {
    checkIntegrity: async () => ({ integrity: "ok", schema_version: "1" }),
    chooseCrashAction: async () => "restore",
    loadBusinessPage: async () => undefined,
    restoreOffline: async () => { restored += 1; return true; },
    listOfflineBackups: async () => [{ id: "daily-safe", type: "daily", created_at: "2026-07-11" }],
    chooseOfflineBackup: async () => "daily-safe",
    restartSidecar: async () => undefined,
    showLogs: async () => undefined,
  });
  await recovery.openAfterStartup(STARTUP);
  assert.equal(restored, 1);
});

test("侧车无法启动时只接受离线列表中的安全备份并在恢复后重启", async () => {
  const markers = new MemoryMarkers(); let restored = ""; let restarted = 0;
  const recovery = new RecoveryCoordinator(markers, {
    checkIntegrity: async () => ({ integrity: "ok", schema_version: "1" }),
    chooseCrashAction: async () => "restore",
    loadBusinessPage: async () => undefined,
    restoreOffline: async (id) => { restored = id; return true; },
    listOfflineBackups: async () => [{ id: "pre_upgrade-safe", type: "pre_upgrade", created_at: "2026-07-11" }],
    chooseOfflineBackup: async () => "pre_upgrade-safe",
    restartSidecar: async () => { restarted += 1; },
    showLogs: async () => undefined,
  });
  assert.equal(await recovery.recoverWhenSidecarUnavailable(STARTUP), true);
  assert.equal(restored, "pre_upgrade-safe");
  assert.equal(restarted, 1);
});

test("恢复失败时保留待升级标记并进入诊断", async () => {
  const markers = new MemoryMarkers(); markers.runMarker = true; markers.pendingBackupId = "pre_upgrade-safe";
  let diagnostics = 0;
  const recovery = new RecoveryCoordinator(markers, {
    checkIntegrity: async () => ({ integrity: "corrupt", schema_version: "1" }),
    chooseCrashAction: async () => "restore",
    loadBusinessPage: async () => undefined,
    restoreOffline: async () => false,
    restartSidecar: async () => undefined,
    showLogs: async () => undefined,
    showRecoveryFailure: async () => { diagnostics += 1; },
  });
  await recovery.openAfterStartup(STARTUP);
  assert.equal(diagnostics, 1);
  assert.equal(markers.pendingBackupId, "pre_upgrade-safe");
});

test("恢复后重启仍失败时不清除待升级标记", async () => {
  const markers = new MemoryMarkers(); markers.pendingBackupId = "pre_upgrade-safe";
  const recovery = new RecoveryCoordinator(markers, {
    checkIntegrity: async () => ({ integrity: "corrupt", schema_version: "1" }),
    chooseCrashAction: async () => "restore",
    loadBusinessPage: async () => undefined,
    restoreOffline: async () => true,
    restartSidecar: async () => { throw new Error("restart failed"); },
    showLogs: async () => undefined,
  });
  await assert.rejects(recovery.restorePendingUpgrade(STARTUP), /restart failed/);
  assert.equal(markers.pendingBackupId, "pre_upgrade-safe");
});

test("正常升级后的 pending 在加载业务页前校验版本、schema 和完整性后清除", async () => {
  const markers = new MemoryMarkers(); markers.pendingBackupId = "pre_upgrade-safe";
  const order: string[] = [];
  const recovery = new RecoveryCoordinator(markers, {
    checkIntegrity: async () => { order.push("integrity"); return { integrity: "ok", schema_version: "1" }; },
    chooseCrashAction: async () => "continue",
    loadBusinessPage: async () => { order.push("load"); },
    restoreOffline: async () => false,
    restartSidecar: async () => undefined,
    showLogs: async () => undefined,
  });
  const originalClear = markers.clearPendingUpgrade.bind(markers);
  markers.clearPendingUpgrade = async () => { order.push("clear-pending"); await originalClear(); };
  const originalWrite = markers.writeRunMarker.bind(markers);
  markers.writeRunMarker = async () => { order.push("write-run"); await originalWrite(); };

  await recovery.openAfterStartup(STARTUP);
  assert.deepEqual(order, ["integrity", "clear-pending", "write-run", "load"]);
  assert.equal(markers.pending, undefined);
});

test("不匹配或陈旧 pending 禁止加载且不得自动恢复", async () => {
  for (const pending of [
    { fromVersion: "0.1.0", targetVersion: "0.3.0", schemaVersion: "1", createdAt: new Date().toISOString(), backupId: "pre_upgrade-safe" },
    { fromVersion: "0.1.0", targetVersion: "0.2.0", schemaVersion: "1", createdAt: "2000-01-01T00:00:00Z", backupId: "pre_upgrade-safe" },
  ]) {
    const markers = new MemoryMarkers(); markers.pending = pending;
    let loaded = false; let restored = false; let diagnosed = false;
    const recovery = new RecoveryCoordinator(markers, {
      checkIntegrity: async () => ({ integrity: "ok", schema_version: "1" }),
      chooseCrashAction: async () => "restore",
      loadBusinessPage: async () => { loaded = true; },
      restoreOffline: async () => { restored = true; return true; },
      restartSidecar: async () => undefined,
      showLogs: async () => undefined,
      showRecoveryFailure: async () => { diagnosed = true; },
    });
    await recovery.openAfterStartup(STARTUP);
    assert.equal(loaded, false); assert.equal(restored, false); assert.equal(diagnosed, true);
  }
});

test("新启动先原子写运行标记再加载业务页", async () => {
  const markers = new MemoryMarkers(); const order: string[] = [];
  markers.writeRunMarker = async () => { order.push("write-run"); markers.runMarker = true; };
  const recovery = new RecoveryCoordinator(markers, {
    checkIntegrity: async () => ({ integrity: "ok", schema_version: "1" }), chooseCrashAction: async () => "continue",
    loadBusinessPage: async () => { order.push("load"); }, restoreOffline: async () => false,
    restartSidecar: async () => undefined, showLogs: async () => undefined,
  });
  await recovery.openAfterStartup(STARTUP);
  assert.deepEqual(order, ["write-run", "load"]);
});

test("marker 仅 ENOENT 视为不存在，损坏 JSON、非法字段与 IO 错误均 fail-closed", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "fulua-marker-"));
  const store = new JsonRecoveryMarkerStore(root);
  assert.equal(await store.hasRunMarker(), false);
  await writeFile(path.join(root, "runtime.json"), "{broken", "utf8");
  await assert.rejects(store.hasRunMarker());
  await writeFile(path.join(root, "pending-upgrade.json"), JSON.stringify({ backupId: "../escape" }), "utf8");
  await assert.rejects(store.readPendingUpgrade());
  const ioRoot = path.join(root, "io-root");
  await mkdir(ioRoot);
  await mkdir(path.join(ioRoot, "runtime.json"));
  const ioStore = new JsonRecoveryMarkerStore(ioRoot);
  await assert.rejects(ioStore.hasRunMarker());
});

test("原子 marker 使用唯一临时名并清理自身失败残留", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "fulua-marker-"));
  await writeFile(path.join(root, `runtime.json.${process.pid}.tmp`), "stale", "utf8");
  const store = new JsonRecoveryMarkerStore(root);
  await store.writeRunMarker("0.2.0");
  const entries = await readdir(root);
  assert.ok(entries.includes("runtime.json"));
  assert.equal(entries.filter((entry) => entry.includes(".tmp-")).length, 0);
});

test("回滚只清理本次 backupId 对应的 pending marker", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "fulua-marker-"));
  const store = new JsonRecoveryMarkerStore(root);
  const marker = { fromVersion: "0.1.0", targetVersion: "0.2.0", schemaVersion: "1", createdAt: new Date().toISOString(), backupId: "pre_upgrade-safe" };
  await store.writePendingUpgrade(marker);
  await assert.rejects(store.clearPendingUpgrade("different-backup"), /已变化/);
  assert.equal((await store.readPendingUpgrade())?.backupId, "pre_upgrade-safe");
  await store.clearPendingUpgrade("pre_upgrade-safe");
  assert.equal(await store.readPendingUpgrade(), undefined);
});
