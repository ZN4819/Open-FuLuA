import assert from "node:assert/strict";
import test from "node:test";

import { calculateObjectScore, calculateTechnicalRows, calculateUnitScore } from "../src/scoring.ts";

test("对象评分覆盖全部公式分支", () => {
  const cases = [
    [{ d: "/", a: "/", k: "/", ra: "1", rk: "1" }, "/"],
    [{ d: "×", a: "/", k: "/", ra: "1", rk: "1" }, "0.0000"],
    [{ d: "√", a: "√", k: "√", ra: "0.2", rk: "1.2" }, "1.0000"],
    [{ d: "√", a: "×", k: "√", ra: "0.5", rk: "1" }, "0.2500"],
    [{ d: "√", a: "√", k: "×", ra: "1", rk: "1.2" }, "0.6000"],
    [{ d: "√", a: "×", k: "×", ra: "0.2", rk: "1.2" }, "0.0600"]
  ];
  cases.forEach(([metric, expected]) => assert.equal(calculateObjectScore(metric), expected));
});

test("空系数使用默认值，非法或未完成输入不评分", () => {
  assert.equal(calculateObjectScore({ d: "√", a: "×", k: "×", ra: "", rk: null }), "0.2500");
  assert.equal(calculateObjectScore({ d: "√", a: "", k: "√" }), "");
  assert.equal(calculateObjectScore({ d: "√", a: "×", k: "√", ra: "0.3" }), "");
});

test("单元分忽略斜杠，但存在未完成对象时留空", () => {
  assert.equal(calculateUnitScore(["1.0000", "/", "0.5000"]), "0.7500");
  assert.equal(calculateUnitScore(["/", "/"]), "/");
  assert.equal(calculateUnitScore(["1.0000", ""]), "");
});

test("整行计算补默认系数并按完整单元聚合", () => {
  const rows = calculateTechnicalRows([
    { unit: "身份鉴别", metric_result: { d: "√", a: "×", k: "×", ra: "0.2", rk: "1.2" } },
    { unit: "身份鉴别", metric_result: { d: "/", a: "/", k: "/" } }
  ]);
  assert.equal(rows[0].metric_result.object_score, "0.0600");
  assert.equal(rows[0].metric_result.unit_score, "0.0600");
  assert.equal(rows[1].metric_result.ra, "1");
  assert.equal(rows[1].metric_result.rk, "1");
});
