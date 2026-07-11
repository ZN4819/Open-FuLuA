import assert from "node:assert/strict";
import test from "node:test";

import { GuardedStartupCoordinator, RecoverySessionGate } from "./startupGate.js";

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
