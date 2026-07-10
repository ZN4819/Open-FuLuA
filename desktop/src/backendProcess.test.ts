import assert from "node:assert/strict";
import { ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import test from "node:test";

import { BackendProcessController } from "./backendProcess.js";

class FakeChildProcess extends EventEmitter {
  readonly stdout = new EventEmitter();
  readonly stderr = new EventEmitter();
  killCalls: NodeJS.Signals[] = [];
  pid?: number;

  kill(signal: NodeJS.Signals): boolean {
    this.killCalls.push(signal);
    return true;
  }
}

test("仅在就绪事件和健康检查均成功后返回侧车 URL", async () => {
  const child = new FakeChildProcess();
  const controller = new BackendProcessController({
    spawn: () => child as unknown as ChildProcess,
    fetch: async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    executable: "backend.exe",
    dataRoot: "C:\\data",
    webDist: "C:\\web",
    sessionToken: "secret-token",
  });

  const starting = controller.start();
  child.stdout.emit("data", Buffer.from('{"event":"FULUA_READY","port":48123,"health_url":"http://127.0.0.1:48123/api/health"}\n'));

  assert.equal(await starting, "http://127.0.0.1:48123");
});

test("拒绝并发重复启动", async () => {
  const child = new FakeChildProcess();
  const controller = new BackendProcessController({
    spawn: () => child as unknown as ChildProcess,
    fetch: async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    executable: "backend.exe",
    dataRoot: "C:\\data",
    webDist: "C:\\web",
    sessionToken: "secret-token",
  });

  const starting = controller.start();
  await assert.rejects(controller.start(), /已在启动或运行/);
  child.stdout.emit("data", Buffer.from('{"event":"FULUA_READY","port":48124,"health_url":"http://127.0.0.1:48124/api/health"}\n'));
  await starting;
});

test("超时且未收到就绪事件时拒绝启动", async () => {
  const child = new FakeChildProcess();
  const controller = new BackendProcessController({
    spawn: () => child as unknown as ChildProcess,
    fetch: async () => new Response(null, { status: 503 }),
    executable: "backend.exe",
    dataRoot: "C:\\data",
    webDist: "C:\\web",
    sessionToken: "secret-token",
    startTimeoutMs: 1,
    stopTimeoutMs: 1,
  });

  await assert.rejects(controller.start(), /15 秒内/);
});

test("启动失败后必须等待旧侧车退出才允许下一次启动", async () => {
  const first = new FakeChildProcess();
  const second = new FakeChildProcess();
  let spawnCount = 0;
  const controller = new BackendProcessController({
    spawn: () => {
      spawnCount += 1;
      return (spawnCount === 1 ? first : second) as unknown as ChildProcess;
    },
    fetch: async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    executable: "backend.exe",
    dataRoot: "C:\\data",
    webDist: "C:\\web",
    sessionToken: "secret-token",
  });

  const firstStart = controller.start();
  first.stdout.emit("data", Buffer.from('{"event":"FULUA_FAILED","message":"启动失败"}\n'));
  let firstSettled = false;
  void firstStart.catch(() => { firstSettled = true; });
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(firstSettled, false, "旧侧车仍存活时，启动失败不能提前结束");
  assert.equal(spawnCount, 1);

  first.emit("exit", 1);
  await assert.rejects(firstStart, /启动失败/);
  const secondStart = controller.start();
  second.stdout.emit("data", Buffer.from('{"event":"FULUA_READY","port":48128,"health_url":"http://127.0.0.1:48128/api/health"}\n'));
  await secondStart;
  assert.equal(spawnCount, 2);
});

test("Windows 强制清理失败时拒绝再次启动且不生成第二侧车", async () => {
  const first = new FakeChildProcess();
  first.pid = -1;
  const second = new FakeChildProcess();
  let spawnCount = 0;
  const controller = new BackendProcessController({
    spawn: () => {
      spawnCount += 1;
      return (spawnCount === 1 ? first : second) as unknown as ChildProcess;
    },
    fetch: async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    executable: "backend.exe",
    dataRoot: "C:\\data",
    webDist: "C:\\web",
    sessionToken: "secret-token",
    startTimeoutMs: 1,
    stopTimeoutMs: 1,
  });

  const firstStart = controller.start();
  first.stdout.emit("data", Buffer.from('{"event":"FULUA_FAILED","message":"启动失败"}\n'));
  await assert.rejects(firstStart, /启动失败/);

  await assert.rejects(controller.start(), /无法确认侧车已退出/);
  assert.equal(spawnCount, 1);
});

test("Windows 强制清理超时时拒绝再次启动且不生成第二侧车", async () => {
  const first = new FakeChildProcess();
  first.pid = 42;
  const second = new FakeChildProcess();
  let spawnCount = 0;
  const controller = new BackendProcessController({
    spawn: () => {
      spawnCount += 1;
      return (spawnCount === 1 ? first : second) as unknown as ChildProcess;
    },
    fetch: async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    forceKill: async () => await new Promise<void>(() => undefined),
    executable: "backend.exe",
    dataRoot: "C:\\data",
    webDist: "C:\\web",
    sessionToken: "secret-token",
    startTimeoutMs: 2,
    stopTimeoutMs: 2,
    forceKillTimeoutMs: 1,
  });

  const firstStart = controller.start();
  first.stdout.emit("data", Buffer.from('{"event":"FULUA_FAILED","message":"启动失败"}\n'));
  await assert.rejects(firstStart, /启动失败/);
  await assert.rejects(controller.start(), /无法确认侧车已退出/);
  assert.equal(spawnCount, 1);
});

test("拒绝端口无效或与 health URL 不一致的 READY 事件", async () => {
  for (const event of [
    '{"event":"FULUA_READY","port":0,"health_url":"http://127.0.0.1:0/api/health"}',
    '{"event":"FULUA_READY","port":48129,"health_url":"http://127.0.0.1:48130/api/health"}',
  ]) {
    const child = new FakeChildProcess();
    const controller = new BackendProcessController({
      spawn: () => child as unknown as ChildProcess,
      fetch: async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
      executable: "backend.exe",
      dataRoot: "C:\\data",
      webDist: "C:\\web",
      sessionToken: "secret-token",
      startTimeoutMs: 1,
      stopTimeoutMs: 1,
    });
    const starting = controller.start();
    child.stdout.emit("data", Buffer.from(`${event}\n`));
    await assert.rejects(starting, /15 秒内/);
  }
});

test("正常停止最多等待一次子进程退出", async () => {
  const child = new FakeChildProcess();
  const controller = new BackendProcessController({
    spawn: () => child as unknown as ChildProcess,
    fetch: async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    executable: "backend.exe",
    dataRoot: "C:\\data",
    webDist: "C:\\web",
    sessionToken: "secret-token",
  });
  const starting = controller.start();
  child.stdout.emit("data", Buffer.from('{"event":"FULUA_READY","port":48125,"health_url":"http://127.0.0.1:48125/api/health"}\n'));
  await starting;

  const stopping = controller.stop();
  child.emit("exit", 0);
  await stopping;
  await controller.stop();
  assert.deepEqual(child.killCalls, ["SIGTERM"]);
});

test("异常退出最多允许显式恢复一次", async () => {
  const first = new FakeChildProcess();
  const second = new FakeChildProcess();
  const children = [first, second];
  const controller = new BackendProcessController({
    spawn: () => children.shift()! as unknown as ChildProcess,
    fetch: async () => new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    executable: "backend.exe",
    dataRoot: "C:\\data",
    webDist: "C:\\web",
    sessionToken: "secret-token",
  });
  const starting = controller.start();
  first.stdout.emit("data", Buffer.from('{"event":"FULUA_READY","port":48126,"health_url":"http://127.0.0.1:48126/api/health"}\n'));
  await starting;
  first.emit("exit", 1);

  const restarting = controller.restartOnce();
  await Promise.resolve();
  second.stdout.emit("data", Buffer.from('{"event":"FULUA_READY","port":48127,"health_url":"http://127.0.0.1:48127/api/health"}\n'));
  await restarting;
  await assert.rejects(controller.restartOnce(), /仅允许自动恢复一次/);
});
