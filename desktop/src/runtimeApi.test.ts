import assert from "node:assert/strict";
import test from "node:test";

import { RuntimeApiClient } from "./runtimeApi.js";

test("运行时 API 仅请求同源受控端点并在失败时给出脱敏诊断", async () => {
  const requests: Array<{ url: string; method: string; body?: string }> = [];
  const api = new RuntimeApiClient("http://127.0.0.1:43123", "test-token", async (input, init) => {
    requests.push({ url: String(input), method: init?.method ?? "GET", body: String(init?.body ?? "") });
    return new Response(JSON.stringify({ can_migrate: true, blocking_reasons: [] }), { status: 200 });
  });

  assert.deepEqual(await api.preflight("C:/old-data"), { can_migrate: true, blocking_reasons: [] });
  assert.deepEqual(requests, [{
    url: "http://127.0.0.1:43123/api/runtime/migration/preflight",
    method: "POST",
    body: JSON.stringify({ source_root: "C:/old-data" }),
  }]);
});

test("运行时 API 拒绝非本机侧车地址", () => {
  assert.throws(() => new RuntimeApiClient("https://example.com", "test-token"), /本机侧车/);
});

test("运行时 API 提供状态、完整性和升级准备边界", async () => {
  const requested: string[] = [];
  const api = new RuntimeApiClient("http://127.0.0.1:43123", "test-token", async (input) => {
    requested.push(String(input));
    return new Response(JSON.stringify({ ready: true, backup_id: "pre_upgrade-safe", schema_version: "1" }), { status: 200 });
  });

  await api.status(); await api.integrity(); await api.prepareUpgrade(); await api.cancelUpgrade("lease-safe");
  assert.deepEqual(requested.map((url) => new URL(url).pathname), [
    "/api/runtime/status", "/api/runtime/integrity", "/api/runtime/upgrade/prepare", "/api/runtime/upgrade/cancel",
  ]);
});
