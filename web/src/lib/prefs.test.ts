/// <reference types="node" />
/**
 * 偏好存储的单测：跑 `npm test`。
 *
 * 只测纯函数与清洗逻辑 —— `loadPrefs` / `savePrefs` 依赖 localStorage，
 * node 下 `window` 不存在，读路径会走 catch 回退默认值（顺带证明了
 * 「存储失败绝不白屏」这条），写路径直接 no-op。
 */
import { test } from "node:test"
import assert from "node:assert/strict"

import {
  DEFAULT_PREFS,
  RECENT_LIMIT,
  clamp,
  loadPrefs,
  mergeDefaults,
  pushRecent,
  sanitizeRecent,
} from "./prefs.ts"

test("pushRecent：新的排最前，且不重复", () => {
  assert.deepEqual(pushRecent([], "17"), ["17"])
  assert.deepEqual(pushRecent(["17"], "4"), ["4", "17"])
  assert.deepEqual(pushRecent(["4", "17"], "4"), ["4", "17"], "重复项要被挤掉而不是再记一条")
})

test("pushRecent：空值不记", () => {
  assert.deepEqual(pushRecent(["17"], ""), ["17"])
  assert.deepEqual(pushRecent(["17"], "   "), ["17"])
})

test("pushRecent：超过上限只留前 N 条", () => {
  const list = Array.from({ length: RECENT_LIMIT }, (_, i) => String(i + 1))
  const out = pushRecent(list, "99")
  assert.equal(out.length, RECENT_LIMIT)
  assert.equal(out[0], "99")
  assert.ok(!out.includes(String(RECENT_LIMIT)), "最老的一条应该被挤出去")
})

test("sanitizeRecent：脏数据洗得动", () => {
  assert.deepEqual(sanitizeRecent(null), [])
  assert.deepEqual(sanitizeRecent("nope"), [])
  assert.deepEqual(sanitizeRecent(["17", 17, null, "", "  17  ", "4"]), ["17", "4"])
  assert.equal(sanitizeRecent(Array.from({ length: 50 }, (_, i) => String(i))).length, RECENT_LIMIT)
})

test("mergeDefaults：数组整体替换，不做逐项合并", () => {
  const out = mergeDefaults({ a: [1, 2] }, { a: [9] })
  assert.deepEqual(out.a, [9])
  const kept = mergeDefaults({ a: [1, 2] }, {})
  assert.deepEqual(kept.a, [1, 2])
})

test("loadPrefs：没有 window 也能回退到默认值（存储失败不白屏）", () => {
  const p = loadPrefs()
  assert.deepEqual(p.recent, { segs: [] })
  assert.equal(p.view, "board")
  assert.ok(p.reader.fontSize >= 10)
})

test("clamp 与默认偏好的基本不变量", () => {
  assert.equal(clamp(999, 0, 10), 10)
  assert.equal(clamp(-1, 0, 10), 0)
  assert.equal(clamp(Number.NaN, 3, 10), 3)
  assert.deepEqual(DEFAULT_PREFS.recent, { segs: [] })
})
