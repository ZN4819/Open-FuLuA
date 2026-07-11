import assert from "node:assert/strict";
import test from "node:test";

import { GuardedStartupCoordinator, RecoverySessionGate, UnexpectedExitRecovery } from "./startupGate.js";

function dependencies(overrides: Partial<ConstructorParameters<typeof GuardedStartupCoordinator>[1]> = {}) {
  const order: string[] = [];
  return {
    order,
    values: {
      hasRecoveryMarker: async () => { order.push("markers"); return true; },
      offlineIntegrity: async () => { order.push("offline-integrity"); return { integrity: "ok", schema_version: "1" }; },
      startBackend: async () => { order.push("start-backend"); },
      recoverWithSidecar: async () => { order.push("recover-sidecar"); return true; },
      recoverWithoutSidecar: async () => { order.push("recover-offline"); return false; },
      startUpdater: async () => { order.push("updater"); },
      diagnose: async () => { order.push("diagnose"); },
      ...overrides,
    },
  };
}

test("存在 marker 时离线完整性检查严格先于正常侧车启动", async () => {
  const gate = new RecoverySessionGate();
  const setup = dependencies();
  const coordinator = new GuardedStartupCoordinator(gate, setup.values);
  assert.equal(await coordinator.enter(), true);
  assert.deepEqual(setup.order, ["markers", "offline-integrity", "start-backend", "recover-sidecar", "updater"]);
  assert.equal(gate.passed, true);
});

test("离线完整性失败时不启动正常侧车和 updater", async () => {
  const gate = new RecoverySessionGate();
  const setup = dependencies({
    offlineIntegrity: async () => { setup.order.push("offline-integrity"); return { integrity: "corrupt", schema_version: "1" }; },
  });
  const coordinator = new GuardedStartupCoordinator(gate, setup.values);
  assert.equal(await coordinator.enter(), false);
  assert.deepEqual(setup.order, ["markers", "offline-integrity", "recover-offline", "diagnose"]);
  assert.equal(gate.passed, false);
});

test("marker 读取异常进入诊断且重试必须重新走完整闸门", async () => {
  let attempts = 0;
  const gate = new RecoverySessionGate();
  const setup = dependencies({
    hasRecoveryMarker: async () => {
      setup.order.push("markers"); attempts += 1;
      if (attempts === 1) throw new Error("marker io");
      return true;
    },
  });
  const coordinator = new GuardedStartupCoordinator(gate, setup.values);
  assert.equal(await coordinator.enter(), false);
  assert.equal(await coordinator.enter(), true);
  assert.deepEqual(setup.order, ["markers", "diagnose", "markers", "offline-integrity", "start-backend", "recover-sidecar", "updater"]);
});

test("日志或诊断会话退出保留 run marker，只有已过闸且侧车已停才允许清理", () => {
  const gate = new RecoverySessionGate();
  assert.equal(gate.canClearRunMarker(true), false);
  gate.markPassed();
  assert.equal(gate.canClearRunMarker(false), false);
  assert.equal(gate.canClearRunMarker(true), true);
});

test("marker 场景正常侧车启动失败后进入一次受控离线恢复", async () => {
  const gate = new RecoverySessionGate(); const setup = dependencies({
    startBackend: async () => { setup.order.push("start-backend"); throw new Error("start failed"); },
    recoverWithoutSidecar: async () => { setup.order.push("recover-offline"); return true; },
  });
  const coordinator = new GuardedStartupCoordinator(gate, setup.values);
  assert.equal(await coordinator.enter(), true);
  assert.deepEqual(setup.order, ["markers", "offline-integrity", "start-backend", "recover-offline", "updater"]);
  assert.equal(gate.passed, true);
});

test("marker 场景启动和离线恢复均失败时只诊断一次且不递归", async () => {
  const gate = new RecoverySessionGate(); let starts = 0; let recoveries = 0;
  const setup = dependencies({
    startBackend: async () => { setup.order.push("start-backend"); starts += 1; throw new Error("start failed"); },
    recoverWithoutSidecar: async () => { setup.order.push("recover-offline"); recoveries += 1; return false; },
  });
  const coordinator = new GuardedStartupCoordinator(gate, setup.values);
  assert.equal(await coordinator.enter(), false);
  assert.deepEqual(setup.order, ["markers", "offline-integrity", "start-backend", "recover-offline", "diagnose"]);
  assert.equal(starts, 1); assert.equal(recoveries, 1); assert.equal(gate.passed, false);
});

test("异常退出立即撤销 gate 并通过 guarded entry 恢复", async () => {
  const gate = new RecoverySessionGate(); gate.markPassed(); const order: string[] = [];
  const recovery = new UnexpectedExitRecovery(gate, {
    enterGuarded: async () => { assert.equal(gate.passed, false); order.push("guarded-entry"); return true; },
    diagnoseNested: async () => { order.push("diagnose"); },
  });
  assert.equal(await recovery.handle(new Error("exit")), true);
  assert.deepEqual(order, ["guarded-entry"]);
});

test("异常退出恢复失败保持 gate 关闭，嵌套退出不得递归启动", async () => {
  const gate = new RecoverySessionGate(); gate.markPassed(); let release!: () => void; let entries = 0; let diagnoses = 0;
  const recovery = new UnexpectedExitRecovery(gate, {
    enterGuarded: async () => { entries += 1; await new Promise<void>((resolve) => { release = resolve; }); return false; },
    diagnoseNested: async () => { diagnoses += 1; },
  });
  const first = recovery.handle(new Error("first")); await Promise.resolve();
  assert.equal(await recovery.handle(new Error("nested")), false);
  release(); assert.equal(await first, false);
  assert.equal(entries, 1); assert.equal(diagnoses, 1); assert.equal(gate.passed, false);
});
