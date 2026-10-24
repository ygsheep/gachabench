import { useEffect, useState, useSyncExternalStore } from "react"
import { createPortal } from "react-dom"
import {
  PREVIEW_MAX_EDGE,
  PREVIEW_TIMING,
  computePanelRect,
  getHoverPreviewSnapshot,
  subscribeHoverPreview,
} from "@/lib/hoverPreview"
import { loadPrefs } from "@/lib/prefs"

/**
 * 悬停预演面板（图片放大 / 视频静音试播）。
 *
 * 三条硬规矩，改之前先读 `lib/hoverPreview.ts` 的头注释：
 * ① 全局只有这一个面板 —— 状态在模块级仲裁器里，不在任何格子的 state 里；
 * ② 容器不重建：换格只换内容，CSS 变量与 DOM 都留着，否则每次换格都要重新淡入，看起来就是闪；
 * ③ `pointer-events: none` —— 面板再大也不会挡住指针，所以它能贴着格子放而不用精确避让。
 *
 * 动效（`gb-pv` 那几个 class 在 index.css）：
 *   打开 = animation 160ms（opacity 0→1 + scale .96→1）
 *   关闭 = transition 120ms（只有 opacity，不位移）
 * 之所以「开用 animation、关用 transition」：animation 带 `both` 会把 opacity 钉在 1，
 * 关闭前必须摘掉；用属性切换比用 JS 逐帧改 style 稳。
 */
export function PreviewPortal() {
  const snap = useSyncExternalStore(subscribeHoverPreview, getHoverPreviewSnapshot)
  const [, bumpTick] = useState(0)
  const [altHeld, setAltHeld] = useState(false)

  const open = snap.open
  /** 只有面板真的开着时才算 1:1，避免关着按 Alt 留下脏状态 */
  const alt = open && altHeld

  // 页面滚动 / 窗口缩放后面板要跟着格子走：锚点是元素，渲染时现取矩形
  useEffect(() => {
    if (!open) return
    let raf = 0
    const bump = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        bumpTick((t) => t + 1)
      })
    }
    window.addEventListener("scroll", bump, true)
    window.addEventListener("resize", bump)
    return () => {
      window.removeEventListener("scroll", bump, true)
      window.removeEventListener("resize", bump)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [open])

  // Alt = 按 1:1 像素看（判断清晰度，而不是构图）
  // 监听常驻、只在事件回调里改 state —— 不在 effect 体里同步 setState，否则每次
  // 开关面板都要多渲染一轮。面板关着时按 Alt 也无所谓：渲染时用 open && alt 兜住。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => setAltHeld(e.altKey)
    const off = () => setAltHeld(false)
    window.addEventListener("keydown", onKey)
    window.addEventListener("keyup", onKey)
    window.addEventListener("blur", off)
    return () => {
      window.removeEventListener("keydown", onKey)
      window.removeEventListener("keyup", onKey)
      window.removeEventListener("blur", off)
    }
  }, [])

  const target = snap.target
  if (!target) return null

  // 格子被卸载（筛选 / 切视图）后锚点会返回 null，这时候什么都不画
  const anchor = target.anchor()
  if (!anchor) return null

  const viewport = { width: window.innerWidth, height: window.innerHeight }
  const media = { width: target.width, height: target.height }
  const panel = computePanelRect(
    anchor,
    media,
    viewport,
    alt ? { maxEdge: Math.max(1, Number(target.width) || PREVIEW_MAX_EDGE), maxHeightRatio: 1 } : undefined
  )

  // 放大倍率：面板宽 / 格子宽。小于 1.05 就不显示，省得说废话
  const cellW = Math.max(1, Math.round(anchor.width ?? anchor.right - anchor.left))
  const scaleX = panel.width / cellW
  const muted = loadPrefs().preview.muted

  return createPortal(
    <div
      className="gb-pv z-40"
      data-open={snap.open ? "1" : "0"}
      data-anim={snap.animIn ? "in" : undefined}
      style={{
        left: panel.left,
        top: panel.top,
        width: panel.width,
        height: panel.height,
        // 换格的淡换时长也走同一个变量，CSS 不用写死
        ["--gb-pv-swap" as string]: `${PREVIEW_TIMING.swapAnim}ms`,
      }}
      aria-hidden="true"
    >
      <div className="relative h-full w-full overflow-hidden rounded-[6px] border border-white/25 bg-black shadow-[0_18px_48px_-10px_rgba(0,0,0,0.7)]">
        {target.kind === "video" ? (
          <video
            key={snap.nonce}
            src={target.src}
            className="gb-pv-swap h-full w-full object-cover"
            autoPlay
            loop
            muted={muted}
            playsInline
          />
        ) : (
          <img
            key={snap.nonce}
            src={target.src}
            alt=""
            draggable={false}
            className="gb-pv-swap h-full w-full object-cover"
          />
        )}

        <div className="absolute inset-x-0 bottom-0 flex items-end gap-2 bg-gradient-to-t from-black/80 to-transparent px-2 pb-1.5 pt-5">
          <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] leading-tight text-paper">
            {target.name}
          </span>
          {scaleX >= 1.05 && (
            <span className="flex-none font-mono text-[10px] leading-tight text-ash">
              {scaleX.toFixed(1)}×
            </span>
          )}
        </div>

        <span className="absolute left-1.5 top-1.5 rounded-[3px] bg-black/70 px-1.5 py-[1px] font-mono text-[9.5px] leading-[1.5] text-ash">
          {alt ? "1:1" : target.kind === "video" ? (muted ? "静音试播" : "试播") : "悬停放大"}
        </span>

        {target.meta && (
          <span className="absolute inset-x-2 bottom-7 truncate text-[10px] leading-tight text-ash-dim">
            {target.meta}
          </span>
        )}
      </div>
    </div>,
    document.body
  )
}
