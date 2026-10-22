/// <reference types="node" />
/** P1 资产索引 URL / query key 的纯函数单测。跑法：npm test */
import { test } from "node:test"
import assert from "node:assert/strict"
import { assetsKey, buildAssetsQuery, normalizeAssetsParams } from "./assetsApi.ts"

test("空参数不写 query string", () => {
  assert.equal(buildAssetsQuery({}), "/api/assets")
  assert.equal(
    buildAssetsQuery({ role: "", category: "  ", collection: "", q: " ", cursor: "" }),
    "/api/assets"
  )
})

test("有效参数按固定顺序写入并 trim", () => {
  assert.equal(
    buildAssetsQuery({
      q: "  林深  ",
      role: "asset",
      collection: "recent",
      category: "image",
      cursor: "",
      limit: 200,
    }),
    "/api/assets?role=asset&category=image&collection=recent&q=%E6%9E%97%E6%B7%B1&limit=200"
  )
})

test("特殊字符与游标会被编码", () => {
  const url = buildAssetsQuery({ q: "a&b=c", cursor: "e30|abc" })
  assert.equal(url, "/api/assets?q=a%26b%3Dc&cursor=e30%7Cabc")
})

test("非法 limit 被丢掉", () => {
  assert.deepEqual(normalizeAssetsParams({ limit: 0 }), {})
  assert.deepEqual(normalizeAssetsParams({ limit: -3 }), {})
  assert.deepEqual(normalizeAssetsParams({ limit: Number.NaN }), {})
  assert.deepEqual(normalizeAssetsParams({ limit: 12.9 }), { limit: 12 })
})

test("assetsKey 对同义参数稳定，且不带游标 / limit", () => {
  const a = assetsKey({ role: "asset", q: " 林深 ", limit: 200, cursor: "next" })
  const b = assetsKey({ q: "林深", role: "  asset ", limit: 50 })
  assert.deepEqual(a, b)
  assert.equal(JSON.stringify(a), JSON.stringify(["index-assets", "list", { role: "asset", q: "林深" }]))
})
