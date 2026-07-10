import assert from "node:assert/strict";
import test from "node:test";

import { MigrationCoordinator } from "./migrationWindow.js";

test("首次启动选择新建空数据时不选择源目录也不重启侧车", async () => {
  let chooseCalls = 0;
  let restartCalls = 0;
  const coordinator = new MigrationCoordinator({
    chooseSourceDirectory: async () => { chooseCalls += 1; return "C:/old"; },
    preflight: async () => ({ can_migrate: true, blocking_reasons: [] }),
    migrate: async () => ({ migrated: true, restart_required: true, message: "迁移完成" }),
    restartSidecar: async () => { restartCalls += 1; },
  });

  const result = await coordinator.begin("new");

  assert.deepEqual(result, { status: "new-data" });
  assert.equal(chooseCalls, 0);
  assert.equal(restartCalls, 0);
});

test("预检阻断时不执行迁移或重启并返回可理解诊断", async () => {
  let migrated = 0;
  let restarted = 0;
  const coordinator = new MigrationCoordinator({
    chooseSourceDirectory: async () => "C:/old",
    preflight: async () => ({ can_migrate: false, blocking_reasons: ["发现缺失图片"] }),
    migrate: async () => { migrated += 1; return { migrated: true, restart_required: true, message: "unexpected" }; },
    restartSidecar: async () => { restarted += 1; },
  });

  const result = await coordinator.begin("migrate");

  assert.deepEqual(result, { status: "blocked", message: "发现缺失图片" });
  assert.equal(migrated, 0);
  assert.equal(restarted, 0);
});

test("迁移成功后按受控流程仅重启一次侧车", async () => {
  let restarted = 0;
  const coordinator = new MigrationCoordinator({
    chooseSourceDirectory: async () => "C:/old",
    preflight: async () => ({ can_migrate: true, blocking_reasons: [] }),
    migrate: async () => ({ migrated: true, restart_required: true, message: "迁移完成" }),
    restartSidecar: async () => { restarted += 1; },
  });

  const result = await coordinator.begin("migrate");

  assert.deepEqual(result, { status: "migrated", message: "迁移完成" });
  assert.equal(restarted, 1);
});

test("取消目录选择保留源数据并不执行后续操作", async () => {
  let preflightCalls = 0;
  const coordinator = new MigrationCoordinator({
    chooseSourceDirectory: async () => undefined,
    preflight: async () => { preflightCalls += 1; return { can_migrate: true, blocking_reasons: [] }; },
    migrate: async () => ({ migrated: true, restart_required: true, message: "unexpected" }),
    restartSidecar: async () => undefined,
  });

  const result = await coordinator.begin("migrate");

  assert.deepEqual(result, { status: "cancelled" });
  assert.equal(preflightCalls, 0);
});
