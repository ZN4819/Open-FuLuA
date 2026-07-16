import assert from "node:assert/strict";
import test from "node:test";

import { withBackendSessionToken } from "./sessionHeaders.js";


test("仅为当前本机侧车 API 注入会话令牌", () => {
  const origin = "http://127.0.0.1:43123";
  assert.deepEqual(
    withBackendSessionToken(`${origin}/api/projects`, origin, "secret", { accept: "application/json" }),
    { accept: "application/json", "x-fulua-session-token": "secret" },
  );
  assert.deepEqual(
    withBackendSessionToken("http://127.0.0.1:43124/api/projects", origin, "secret", { accept: "application/json" }),
    { accept: "application/json" },
  );
  assert.deepEqual(
    withBackendSessionToken(`${origin}.example/api/projects`, origin, "secret", {}),
    {},
  );
});
