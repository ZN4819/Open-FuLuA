import assert from "node:assert/strict";
import test from "node:test";

import { BackupCoordinator } from "./backupActions.js";

test("恢复前列出可用备份且恢复成功后受控重启侧车", async () => {
  let restartCalls = 0;
  const coordinator = new BackupCoordinator({
    listBackups: async () => [{ id: "backup-a", type: "daily", created_at: "2026-07-10T00:00:00Z" }],
    restore: async (id) => ({ restored: id === "backup-a", restart_required: true, message: "恢复完成" }),
    restartSidecar: async () => { restartCalls += 1; },
  });

  assert.deepEqual(await coordinator.list(), [{ id: "backup-a", type: "daily", created_at: "2026-07-10T00:00:00Z" }]);
  assert.deepEqual(await coordinator.restore("backup-a"), { status: "restored", message: "恢复完成" });
  assert.equal(restartCalls, 1);
});

test("恢复失败不重启侧车并返回用户可理解的诊断", async () => {
  let restartCalls = 0;
  const coordinator = new BackupCoordinator({
    listBackups: async () => [],
    restore: async () => ({ restored: false, restart_required: false, message: "备份校验失败" }),
    restartSidecar: async () => { restartCalls += 1; },
  });

  assert.deepEqual(await coordinator.restore("bad"), { status: "failed", message: "备份校验失败" });
  assert.equal(restartCalls, 0);
});

test("第二实例不会触发第二个迁移或恢复协调器", () => {
  assert.equal(BackupCoordinator.canStartMaintenance(false), false);
  assert.equal(BackupCoordinator.canStartMaintenance(true), true);
});
