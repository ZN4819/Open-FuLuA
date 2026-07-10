import assert from "node:assert/strict";
import test from "node:test";

import { RestoreWindowCoordinator } from "./restoreWindow.js";

test("恢复入口列出备份、选择并确认后执行恢复，且在确认前提示服务将重启", async () => {
  const prompts: string[] = [];
  let restoredId: string | undefined;
  const coordinator = new RestoreWindowCoordinator({
    listBackups: async () => [{ id: "backup-20260710", type: "自动备份", created_at: "2026-07-10T10:00:00Z" }],
    chooseBackup: async (backups) => {
      assert.equal(backups.length, 1);
      return backups[0]?.id;
    },
    confirmRestore: async (_backup, detail) => {
      prompts.push(detail);
      return true;
    },
    restore: async (id) => {
      restoredId = id;
      return { status: "restored", message: "恢复完成，本地服务已重新启动。" };
    },
    notify: async () => undefined,
  });

  const outcome = await coordinator.open();

  assert.deepEqual(outcome, { status: "restored", message: "恢复完成，本地服务已重新启动。" });
  assert.equal(restoredId, "backup-20260710");
  assert.match(prompts[0] ?? "", /本地服务将重启/);
});

test("用户取消确认时不恢复备份", async () => {
  let restoreCalls = 0;
  const coordinator = new RestoreWindowCoordinator({
    listBackups: async () => [{ id: "backup-a", type: "手动备份", created_at: "2026-07-10T10:00:00Z" }],
    chooseBackup: async () => "backup-a",
    confirmRestore: async () => false,
    restore: async () => { restoreCalls += 1; return { status: "restored", message: "unexpected" }; },
    notify: async () => undefined,
  });

  assert.deepEqual(await coordinator.open(), { status: "cancelled" });
  assert.equal(restoreCalls, 0);
});

test("恢复入口拒绝未包含在当前列表中的备份标识", async () => {
  let confirmationCalls = 0;
  const coordinator = new RestoreWindowCoordinator({
    listBackups: async () => [{ id: "backup-a", type: "手动备份", created_at: "2026-07-10T10:00:00Z" }],
    chooseBackup: async () => "backup-forged",
    confirmRestore: async () => { confirmationCalls += 1; return true; },
    restore: async () => ({ status: "restored", message: "unexpected" }),
    notify: async () => undefined,
  });

  assert.deepEqual(await coordinator.open(), { status: "failed", message: "所选备份已不可用，请重新打开恢复入口。" });
  assert.equal(confirmationCalls, 0);
});
