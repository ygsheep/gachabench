// @vitest-environment jsdom
/**
 * 悬停预演面板的接线测试（RTL + jsdom）。跑法：npm run test:ui
 *
 * 纯逻辑在 `lib/hoverPreview.test.ts` 里测过了，这里只验「组件有没有把仲裁器的状态
 * 正确变成 DOM」：出现时机、用的是原图还是缩略图、静音开关有没有接上、先淡出再卸载。
 *
 * ⚠️ jsdom 不跑 CSS，所以「淡入 160ms」在这里体现为 data-anim 与 data-open 两个属性；
 * 真正的过渡效果靠 `index.css` 里的 `.gb-pv` 规则，改了那里要人眼过一遍。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render } from "@testing-library/react"
import { PreviewPortal } from "@/components/PreviewPortal"
import { PREVIEW_TIMING, resetHoverPreview, showHoverPreview, hideHoverPreview } from "@/lib/hoverPreview"
import { PREFS_KEY, loadPrefs, writeJSON } from "@/lib/prefs"

/** 格子矩形：宽 200，右边到 300 */
const ANCHOR = { left: 100, top: 100, right: 300, bottom: 200, width: 200, height: 100 }

function targetOf(kind: "image" | "video", src = "/api/media/a.png") {
  return { kind, src, name: "白兴学_参考.png", meta: "1920×1080 · 0.4 MB", width: 1920, height: 1080, anchor: () => ANCHOR }
}

function panel(): HTMLElement | null {
  return document.querySelector(".gb-pv")
}

beforeEach(() => {
  vi.useFakeTimers()
  resetHoverPreview()
})

afterEach(() => {
  cleanup()
  resetHoverPreview()
  vi.useRealTimers()
  window.localStorage.removeItem(PREFS_KEY)
})

describe("PreviewPortal", () => {
  it("图片：停留 120ms 才出现，位置在格子右侧，用的是原图而不是缩略图", () => {
    render(<PreviewPortal />)
    expect(panel()).toBeNull()

    act(() => showHoverPreview(targetOf("image", "/api/media/原图.png")))
    act(() => {
      vi.advanceTimersByTime(PREVIEW_TIMING.enter - 1)
    })
    expect(panel()).toBeNull()

    act(() => {
      vi.advanceTimersByTime(1)
    })
    const el = panel()
    expect(el).not.toBeNull()
    expect(el?.dataset.open).toBe("1")
    expect(el?.dataset.anim).toBe("in")

    // 贴格子右侧 12px
    expect(el?.style.left).toBe(`${ANCHOR.right + 12}px`)
    // jsdom 视口 1024×768 → 60vh = 460.8 比 520 先到顶，面板宽 461。
    // 这正好验证了「短屏不让大图糊满」这条上限真的接上了（不是靠常量写死）。
    expect(el?.style.width).toBe("461px")
    expect(el?.style.height).toBe("259px")

    const img = el?.querySelector("img")
    expect(img?.getAttribute("src")).toBe("/api/media/原图.png")
    expect(el?.textContent).toContain("白兴学_参考.png")
  })

  it("视频：默认静音；关掉静音开关后才出声", () => {
    render(<PreviewPortal />)
    act(() => showHoverPreview(targetOf("video", "/api/media/clip.mp4")))
    act(() => {
      vi.advanceTimersByTime(PREVIEW_TIMING.enter)
    })
    expect(panel()?.querySelector("video")?.muted).toBe(true)
    expect(panel()?.textContent).toContain("静音试播")

    // 设置页把「试播静音」关掉 → 下一块面板就该有声
    const prefs = loadPrefs()
    writeJSON(PREFS_KEY, { ...prefs, preview: { ...prefs.preview, muted: false } })
    act(() => hideHoverPreview())
    act(() => {
      vi.advanceTimersByTime(PREVIEW_TIMING.grace + PREVIEW_TIMING.exit)
    })
    act(() => showHoverPreview(targetOf("video", "/api/media/clip.mp4")))
    act(() => {
      vi.advanceTimersByTime(PREVIEW_TIMING.enter)
    })
    expect(panel()?.querySelector("video")?.muted).toBe(false)
    expect(panel()?.textContent).toContain("试播")
  })

  it("离开：先淡出（还在 DOM 里）再卸载，不是一下子消失", () => {
    render(<PreviewPortal />)
    act(() => showHoverPreview(targetOf("image")))
    act(() => {
      vi.advanceTimersByTime(PREVIEW_TIMING.enter)
    })
    expect(panel()).not.toBeNull()

    act(() => hideHoverPreview())
    act(() => {
      vi.advanceTimersByTime(PREVIEW_TIMING.grace)
    })
    // 宽限过后进入淡出：节点还在，只是 data-open 变 0
    expect(panel()).not.toBeNull()
    expect(panel()?.dataset.open).toBe("0")
    expect(panel()?.dataset.anim).toBeUndefined()

    act(() => {
      vi.advanceTimersByTime(PREVIEW_TIMING.exit)
    })
    expect(panel()).toBeNull()
  })
})
