import assert from "node:assert/strict";
import test from "node:test";

import { exportProjectXlsx } from "../src/api/client.ts";
import { scoreWorkbookExportBlockReason } from "../src/exporting.ts";

test("未保存章节会返回导出阻断原因", () => {
  assert.equal(scoreWorkbookExportBlockReason(1), "当前还有未保存的章节，请先保存后再导出。");
  assert.equal(scoreWorkbookExportBlockReason(0), undefined);
});

test("成功导出会请求 XLSX 接口并触发浏览器下载", async (context) => {
  let requestedUrl = "";
  let requestedMethod = "";
  let clicked = false;
  let appended = false;
  let revokedUrl = "";
  const link = {
    href: "",
    download: "",
    click() { clicked = true; },
    remove() {}
  };
  const originalDocument = globalThis.document;
  globalThis.document = {
    createElement: () => link,
    body: { appendChild() { appended = true; } }
  };
  context.after(() => { globalThis.document = originalDocument; });
  context.mock.method(globalThis, "fetch", async (url, init) => {
    requestedUrl = String(url);
    requestedMethod = String(init?.method);
    return new Response(new Blob(["xlsx"]), {
      status: 200,
      headers: { "content-disposition": 'attachment; filename="score.xlsx"' }
    });
  });
  context.mock.method(URL, "createObjectURL", () => "blob:score");
  context.mock.method(URL, "revokeObjectURL", (url) => { revokedUrl = url; });

  const fileName = await exportProjectXlsx(7);

  assert.equal(requestedUrl, "/api/projects/7/exports/xlsx");
  assert.equal(requestedMethod, "POST");
  assert.equal(fileName, "score.xlsx");
  assert.equal(link.download, "score.xlsx");
  assert.equal(link.href, "blob:score");
  assert.equal(appended, true);
  assert.equal(clicked, true);
  assert.equal(revokedUrl, "blob:score");
});

test("结构化导出错误会展示位置、原因和剩余数量", async (context) => {
  context.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({
    detail: {
      message: "项目评分数据未完成",
      issues: Array.from({ length: 6 }, (_, index) => ({
        section_code: "A-5",
        unit: `指标${index + 1}`,
        object_name: "管理制度",
        field: "compliance",
        message: "符合情况未填写"
      }))
    }
  }), { status: 400, headers: { "content-type": "application/json" } }));

  await assert.rejects(
    () => exportProjectXlsx(7),
    (error) => {
      assert.match(error.message, /项目评分数据未完成/);
      assert.match(error.message, /A-5 \/ 指标1 \/ 管理制度：符合情况未填写/);
      assert.match(error.message, /另有 1 项问题/);
      return true;
    }
  );
});
