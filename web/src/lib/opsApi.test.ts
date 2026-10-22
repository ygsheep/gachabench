/// <reference types="node" />
/** P2 计划请求体归一化 / 错误解析 / 行 diff 的纯函数单测。跑法：npm test */
import { test } from "node:test"
import assert from "node:assert/strict"
import {
  diffLines,
  normalizeDestDir,
  normalizePlanBody,
  opsErrorMessage,
  parseOpsError,
} from "./opsApi.ts"
import { ApiError } from "./api.ts"

test("改名请求只带单个 asset_id 与 trim 后的文件名", () => {
  assert.deepEqual(normalizePlanBody({ kind: "rename", assetId: " a1 ", newName: " 新名字.png " }), {
    kind: "rename",
    asset_id: "a1",
    new_name: "新名字.png",
  })
})

test("移动 / 删除的 asset_ids 去重去空，反斜杠目录转正", () => {
  assert.deepEqual(
    normalizePlanBody({ kind: "move", assetIds: [" a ", "", "a", "b"], destDir: " 资产\\人物\\ " }),
    { kind: "move", asset_ids: ["a", "b"], dest_dir: "资产/人物" }
  )
  assert.deepEqual(normalizePlanBody({ kind: "delete", assetIds: ["a", "a"] }), {
    kind: "delete",
    asset_ids: ["a"],
  })
})

test("空目录表示资产根；越界路径不擅自改写，留给后端判定", () => {
  assert.equal(normalizeDestDir(""), "")
  assert.equal(normalizeDestDir(" . "), "")
  assert.equal(normalizeDestDir("../../etc"), "../../etc")
  assert.equal(normalizeDestDir("/abs"), "/abs")
})

test("非法输入抛中文错误", () => {
  assert.throws(() => normalizePlanBody({ kind: "rename", assetId: "", newName: "a.png" }), /请选择/)
  assert.throws(() => normalizePlanBody({ kind: "rename", assetId: "a", newName: "  " }), /不能为空/)
  assert.throws(() => normalizePlanBody({ kind: "delete", assetIds: [] }), /至少选择/)
})

test("后端 detail 对象字符串能解析成 code/message", () => {
  const err = new ApiError('{"code":"PLAN_STALE","message":"计划已过期"}', {
    status: 409,
    kind: "http",
    detail: '{"code":"PLAN_STALE","message":"计划已过期"}',
  })
  assert.deepEqual(parseOpsError(err), { code: "PLAN_STALE", message: "计划已过期" })
  assert.equal(opsErrorMessage(err), "计划已过期，请重新计算影响面")
})

test("未知错误码回落到后端消息", () => {
  const err = new ApiError('{"code":"SOMETHING_NEW","message":"后端新提示"}', {
    status: 400,
    kind: "http",
    detail: '{"code":"SOMETHING_NEW","message":"后端新提示"}',
  })
  assert.equal(opsErrorMessage(err), "后端新提示")
})

test("逐行 diff 能标出改名那一行的删与增", () => {
  const rows = diffLines("第一行\n白陌.png\n第三行", "第一行\n白陌_v2.png\n第三行")
  assert.deepEqual(
    rows.map((r) => [r.kind, r.beforeNo, r.afterNo, r.text]),
    [
      ["same", 1, 1, "第一行"],
      ["del", 2, null, "白陌.png"],
      ["add", null, 2, "白陌_v2.png"],
      ["same", 3, 3, "第三行"],
    ]
  )
})

test("逐行 diff 处理多行 / 少行", () => {
  const added = diffLines("a\nb", "a\nx\nb")
  assert.deepEqual(
    added.map((r) => r.kind),
    ["same", "add", "same"]
  )
  const removed = diffLines("a\nx\nb", "a\nb")
  assert.deepEqual(
    removed.map((r) => r.kind),
    ["same", "del", "same"]
  )
})
