/// <reference types="node" />
/**
 * 悬停放大面板的几何与时序单测。跑法：npm test
 *
 * 这里只测纯函数与仲裁器（不碰 DOM、不碰 React）：
 * - 几何：贴着格子、靠边翻面、永不越出视口 —— 错了用户会看到面板一半在屏幕外；
 * - 时序：120 才开 / 90 宽限 / 120 淡出后才卸载 —— 错了表现为「横扫一路闪」或「关不掉」。
 *
 * 🔴 时间不用 node 的 `mock.timers`：它在同一个文件里跨用例不重置时钟，
 * 第二个用例起会静默失效（已实测）。这里用 `setHoverPreviewClock` 换成手推的假时钟。
 */
import { test } from "node:test"
import assert from "node:assert/strict"
import {
  PREVIEW_GAP,
  PREVIEW_MAX_EDGE,
  PREVIEW_MARGIN,
  PREVIEW_TIMING,
  PREVIEW_VH_RATIO,
  computePanelRect,
  getHoverPreviewSnapshot,
  hideHoverPreview,
  resetHoverPreview,
  setHoverPreviewClock,
  showHoverPreview,
  suppressHoverPreview,
  type HoverPreviewTarget,
  type PreviewClock,
} from "./hoverPreview.ts"

const VIEW = { width: 1600, height: 1000 }
const CELL = { left: 100, top: 100, right: 300, bottom: 200 }

// ---------------------------------------------------------------------------
// 几何
// ---------------------------------------------------------------------------

test("动效参数锁定为设计稿里的那组数", () => {
  assert.equal(PREVIEW_TIMING.enter, 120)
  assert.equal(PREVIEW_TIMING.grace, 90)
  assert.equal(PREVIEW_TIMING.exit, 120)
  assert.equal(PREVIEW_TIMING.enterAnim, 160)
  assert.equal(PREVIEW_MAX_EDGE, 520)
  assert.equal(PREVIEW_VH_RATIO, 0.6)
  assert.equal(PREVIEW_GAP, 12)
  assert.equal(PREVIEW_MARGIN, 12)
})

test("16:9 素材：长边吃满 520，贴在格子右侧、顶端对齐", () => {
  const r = computePanelRect(CELL, { width: 1920, height: 1080 }, VIEW)
  assert.deepEqual(r, { left: 312, top: 100, width: 520, height: 293 })
})

test("右边放不下就翻到左侧，不越出视口", () => {
  const nearRight = { left: 1400, top: 100, right: 1500, bottom: 200 }
  const r = computePanelRect(nearRight, { width: 1920, height: 1080 }, VIEW)
  assert.equal(r.left, 1400 - PREVIEW_GAP - 520)
  assert.ok(r.left + r.width <= VIEW.width - PREVIEW_MARGIN)
})

test("竖构图按高度封顶：长边仍是 520，宽度等比收敛", () => {
  const r = computePanelRect(CELL, { width: 1080, height: 1920 }, VIEW)
  assert.equal(r.height, 520)
  assert.equal(r.width, 293)
})

test("贴着视口底部时改成底端对齐（向上长），仍留 12px", () => {
  const low = { left: 100, top: 900, right: 300, bottom: 996 }
  const r = computePanelRect(low, { width: 1920, height: 1080 }, VIEW)
  assert.equal(r.top, VIEW.height - PREVIEW_MARGIN - r.height)
})

test("视口很矮时上限跟着 60vh 走，不是死守 520", () => {
  const r = computePanelRect(
    { left: 0, top: 0, right: 100, bottom: 100 },
    { width: 1920, height: 1080 },
    { width: 400, height: 300 }
  )
  assert.equal(r.height, Math.round(180 / (16 / 9)))
  assert.equal(r.width, 180)
  assert.equal(r.top, PREVIEW_MARGIN)
})

test("未知原始尺寸按 16:9 估（和已知尺寸同一结果）", () => {
  const known = computePanelRect(CELL, { width: 1920, height: 1080 }, VIEW)
  const unknown = computePanelRect(CELL, {}, VIEW)
  const nulls = computePanelRect(CELL, { width: null, height: null }, VIEW)
  assert.deepEqual(unknown, known)
  assert.deepEqual(nulls, known)
})

test("Alt 1:1：放开 520 与 60vh 的封顶，只受视口限制", () => {
  const r = computePanelRect(CELL, { width: 1920, height: 1080 }, VIEW, {
    maxEdge: 1920,
    maxHeightRatio: 1,
  })
  // 横构图的长边是宽：1000 由视口高度定（上限不再走 520 / 60vh）
  assert.equal(r.width, VIEW.height)
  assert.equal(r.height, Math.round(VIEW.height / (16 / 9)))
  assert.ok(r.width > PREVIEW_MAX_EDGE, "必须比常态上限大，否则 1:1 没意义")
})

test("退化视口不产生 0 / 负数 / NaN（宁可变 1px 也不让样式炸掉）", () => {
  const r = computePanelRect(
    { left: 0, top: 0, right: 10, bottom: 10 },
    { width: 1920, height: 1080 },
    { width: 10, height: 10 }
  )
  for (const v of [r.left, r.top, r.width, r.height]) {
    assert.ok(Number.isFinite(v), `应为有限数，实际 ${v}`)
  }
  assert.ok(r.width >= 1 && r.height >= 1)
})

// ---------------------------------------------------------------------------
// 时序（假时钟）
// ---------------------------------------------------------------------------

/** 手动推进的时钟：和真实 setTimeout 同样的语义（执行期间新排的也会被排上）。 */
function fakeClock() {
  let now = 0
  let seq = 0
  const queue = new Map<number, { at: number; fn: () => void }>()
  const clk: PreviewClock & { tick: (ms: number) => void } = {
    set(fn, ms) {
      seq += 1
      queue.set(seq, { at: now + ms, fn })
      return seq
    },
    clear(id) {
      queue.delete(id)
    },
    tick(ms) {
      const target = now + ms
      for (;;) {
        let next: [number, { at: number; fn: () => void }] | undefined
        for (const entry of queue) {
          if (entry[1].at > target) continue
          if (!next || entry[1].at < next[1].at) next = entry
        }
        if (!next) break
        queue.delete(next[0])
        now = next[1].at
        next[1].fn()
      }
      now = target
    },
  }
  return clk
}

function targetOf(src: string): HoverPreviewTarget {
  return {
    kind: "image",
    src,
    name: src,
    width: 1920,
    height: 1080,
    anchor: () => ({ left: 0, top: 0, right: 10, bottom: 10 }),
  }
}

/** 每个用例一份新时钟 + 干净状态；结束后恢复真实计时器 */
function withClock(t: { after: (fn: () => void) => void }) {
  const clk = fakeClock()
  setHoverPreviewClock(clk)
  t.after(() => setHoverPreviewClock(null))
  resetHoverPreview()
  return clk
}

test("停留满 120ms 才打开，早 1ms 都不行", (t) => {
  const clk = withClock(t)
  showHoverPreview(targetOf("a.png"))

  clk.tick(PREVIEW_TIMING.enter - 1)
  assert.equal(getHoverPreviewSnapshot().open, false, "还没到 120ms 不该打开")

  clk.tick(1)
  const s = getHoverPreviewSnapshot()
  assert.equal(s.open, true)
  assert.equal(s.animIn, true, "打开时要带上淡入动画标记")
  assert.equal(s.target?.src, "a.png")
})

test("淡入 160ms 后摘掉动画标记（否则 fill-mode 会把 opacity 钉死，关不掉）", (t) => {
  const clk = withClock(t)
  showHoverPreview(targetOf("a.png"))
  clk.tick(PREVIEW_TIMING.enter)
  assert.equal(getHoverPreviewSnapshot().animIn, true)

  clk.tick(PREVIEW_TIMING.enterAnim)
  assert.equal(getHoverPreviewSnapshot().animIn, false)
  assert.equal(getHoverPreviewSnapshot().open, true, "摘动画不能顺手把面板关了")
})

test("横扫：120ms 内连续换格只打开最后一个，中途一个都不闪", (t) => {
  const clk = withClock(t)
  showHoverPreview(targetOf("a.png"))
  clk.tick(60)
  showHoverPreview(targetOf("b.png"))
  clk.tick(60)
  assert.equal(getHoverPreviewSnapshot().open, false, "被后面的格子重置了计时")

  clk.tick(60)
  assert.equal(getHoverPreviewSnapshot().open, true)
  assert.equal(getHoverPreviewSnapshot().target?.src, "b.png")
})

test("换格：90ms 宽限内进入别的格子算换内容，不算关闭", (t) => {
  const clk = withClock(t)
  showHoverPreview(targetOf("a.png"))
  clk.tick(PREVIEW_TIMING.enter)
  // 先把打开的淡入放完，这样「换格不重放淡入」才测得出来
  clk.tick(PREVIEW_TIMING.enterAnim)
  const before = getHoverPreviewSnapshot()
  assert.equal(before.animIn, false)

  hideHoverPreview()
  clk.tick(PREVIEW_TIMING.grace - 10)
  showHoverPreview(targetOf("b.png"))

  const after = getHoverPreviewSnapshot()
  assert.equal(after.open, true, "宽限期内换格不该关掉面板")
  assert.equal(after.animIn, false, "换格不重放完整淡入，只做内容淡换")
  assert.equal(after.target?.src, "b.png")
  assert.equal(after.nonce, before.nonce + 1, "nonce 要变，媒体元素才会重新淡换")

  // 宽限的定时器也得被取消，否则 90ms 后又把面板关了
  clk.tick(PREVIEW_TIMING.grace + PREVIEW_TIMING.exit)
  assert.equal(getHoverPreviewSnapshot().open, true)
})

test("离开：90ms 后开始淡出，再 120ms 才卸载（先淡后卸）", (t) => {
  const clk = withClock(t)
  showHoverPreview(targetOf("a.png"))
  clk.tick(PREVIEW_TIMING.enter)

  hideHoverPreview()
  clk.tick(PREVIEW_TIMING.grace - 1)
  assert.equal(getHoverPreviewSnapshot().open, true, "宽限还没过")

  clk.tick(1)
  assert.equal(getHoverPreviewSnapshot().open, false, "开始淡出")
  assert.notEqual(getHoverPreviewSnapshot().target, null, "淡出期间内容还得在，不然没得淡")

  clk.tick(PREVIEW_TIMING.exit)
  assert.equal(getHoverPreviewSnapshot().target, null, "淡出结束才卸载")
})

test("点过就不再弹；鼠标离开后立刻解禁（滑到隔壁格要马上能用）", (t) => {
  const clk = withClock(t)
  showHoverPreview(targetOf("a.png"))
  clk.tick(PREVIEW_TIMING.enter)
  assert.equal(getHoverPreviewSnapshot().open, true)

  suppressHoverPreview()
  assert.equal(getHoverPreviewSnapshot().open, false, "点完要立刻收掉，别挡检查器")
  clk.tick(PREVIEW_TIMING.exit)
  assert.equal(getHoverPreviewSnapshot().target, null)

  // 封锁期间再来 request 也无效
  showHoverPreview(targetOf("b.png"))
  clk.tick(PREVIEW_TIMING.enter)
  assert.equal(getHoverPreviewSnapshot().open, false)

  // 离开这一格 → 解禁
  hideHoverPreview()
  clk.tick(PREVIEW_TIMING.grace)
  showHoverPreview(targetOf("c.png"))
  clk.tick(PREVIEW_TIMING.enter)
  assert.equal(getHoverPreviewSnapshot().open, true)
  assert.equal(getHoverPreviewSnapshot().target?.src, "c.png")
})

test("锚点元素被卸载（anchor 返回 null）由组件层收口，仲裁器不崩", (t) => {
  const clk = withClock(t)
  showHoverPreview({ ...targetOf("a.png"), anchor: () => null })
  clk.tick(PREVIEW_TIMING.enter)
  assert.equal(getHoverPreviewSnapshot().open, true)
  assert.equal(getHoverPreviewSnapshot().target?.anchor(), null)
})
