import assert from "node:assert/strict";
import test from "node:test";

import { RecoveryCoordinator, type RecoveryMarkerStore } from "./recovery.js";

class MemoryMarkers implements RecoveryMarkerStore {
  runMarker = false;
  pendingBackupId: string | undefined;
  async hasRunMarker() { return this.runMarker; }
  async writeRunMarker() { this.runMarker = true; }
  async clearRunMarker() { this.runMarker = false; }
  async readPendingUpgrade() { return this.pendingBackupId ? { version: "0.2.0", createdAt: "2026-07-11T00:00:00Z", backupId: this.pendingBackupId } : undefined; }
  async writePendingUpgrade(marker: { backupId: string }) { this.pendingBackupId = marker.backupId; }
  async clearPendingUpgrade() { this.pendingBackupId = undefined; }
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

  await recovery.openAfterStartup();
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

  await recovery.openAfterStartup();
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

  assert.equal(await recovery.restorePendingUpgrade(), false);
  assert.equal(markers.pendingBackupId, "pre_upgrade-safe");
  dependencies.restoreOffline = async () => true;
  assert.equal(await recovery.restorePendingUpgrade(), true);
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
  await recovery.openAfterStartup();
  assert.equal(restored, 1);
});

test("侧车无法启动时只接受离线列表中的安全备份并在恢复后重启", async () => {
  const markers = new MemoryMarkers(); let restored = ""; let restarted = 0;
  const recovery = new RecoveryCoordinator(markers, {
    checkIntegrity: async () => ({ integrity: "corrupt", schema_version: "1" }),
    chooseCrashAction: async () => "restore",
    loadBusinessPage: async () => undefined,
    restoreOffline: async (id) => { restored = id; return true; },
    listOfflineBackups: async () => [{ id: "pre_upgrade-safe", type: "pre_upgrade", created_at: "2026-07-11" }],
    chooseOfflineBackup: async () => "pre_upgrade-safe",
    restartSidecar: async () => { restarted += 1; },
    showLogs: async () => undefined,
  });
  assert.equal(await recovery.recoverWhenSidecarUnavailable(), true);
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
  await recovery.openAfterStartup();
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
  await assert.rejects(recovery.restorePendingUpgrade(), /restart failed/);
  assert.equal(markers.pendingBackupId, "pre_upgrade-safe");
});
