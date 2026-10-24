/**
 * 悬停预演（hover preview）的仲裁器 + 面板几何。
 *
 * 设计定案见画布「悬停放大 · 运动规范」：
 * - 悬停＝预演：视频靠播放、图片靠放大，共用一个仲裁器 —— 屏幕上同时只有一条 preview
 * - 停留 120ms 才打开（横扫过去不闪）
 * - 淡入 160ms（opacity 0→1 + scale .96→1）；淡出 120ms 纯 opacity，不位移
 * - 换格 90ms 宽限，**容器不重建**，只换内容（看起来是「内容淡换」而不是「面板闪烁」）
 *
 * 🔴 为什么把状态放在模块级而不是 React state：
 * 三个网格（分组 / 平铺 / 重复）各自渲染 `<AssetCell>`，如果每个格子自己持有一份
 * 「我要不要显示预览」的状态，就会出现两条预览同时存在。仲裁必须是**全局唯一**的，
 * 和 `AudioCell.tsx` 里 `activeEl` / `pinnedEl` 那套播放仲裁同一个思路。
 *
 * 🔴 为什么 `anchor` 存的是取矩形的函数而不是矩形快照：
 * 面板是 `position: fixed`，页面滚动/窗口缩放后快照就过期了。存元素、渲染时现取，
 * 滚动时重取一次即可，不需要在打开那一刻算死。
 *
 * 🔴 为什么计时器走 `clock` 而不是直接调 setTimeout：
 * 这套东西的正确性全在时序上（120 / 90 / 120）。要测它就得能控制时间，
 * 而 node 的 `mock.timers` 在同一个文件里跨用例不重置时钟，会串味。
 * 换成可替换的时钟，单测自己推进时间，确定性强、也不依赖测试框架的实现细节。
 */

/** 时间参数（ms）。改这里就等于改全站动效，别在组件里写魔法数。 */
export const PREVIEW_TIMING = {
  /** 停留多久才开始打开 */
  enter: 120,
  /** 离开后给多少宽限，期间进入别的格子算「换格」不算「关闭」 */
  grace: 90,
  /** 关闭的淡出时长（只做 opacity，不做位移） */
  exit: 120,
  /** 打开的淡入时长（opacity + scale） */
  enterAnim: 160,
  /** 换格时内容自身的淡换时长 */
  swapAnim: 90,
} as const

/** 放大面板的长边上限（px） */
export const PREVIEW_MAX_EDGE = 520
/** 放大面板的高度上限：视口高度的占比 */
export const PREVIEW_VH_RATIO = 0.6
/** 面板与源格子之间的间距 */
export const PREVIEW_GAP = 12
/** 面板与视口边缘的最小留白 */
export const PREVIEW_MARGIN = 12

export interface RectLike {
  left: number
  top: number
  right: number
  bottom: number
  width?: number
  height?: number
}

export interface MediaSize {
  width?: number | null
  height?: number | null
}

export interface PanelRect {
  left: number
  top: number
  width: number
  height: number
}

/**
 * 算放大面板的落点与尺寸。
 *
 * 规则（按优先级）：
 * ① 长边 ≤ 520px 且 ≤ 60vh —— 再大就不是「看一眼」，是遮挡；
 * ② 优先贴在格子**右侧**，右边放不下就翻到左侧；
 * ③ 竖直方向顶端对齐格子，底部溢出就改成底端对齐（向上长）；
 * ④ 任何情况下都夹在视口留白内 —— 宁可压住格子，也不越出屏幕。
 *
 * 未知原始尺寸时按 16:9 估。面板 `pointer-events: none`，所以「压住格子」
 * 也不会真的挡住指针，这一条是兜底不是常态。
 */
export function computePanelRect(
  anchor: RectLike,
  media: MediaSize,
  viewport: { width: number; height: number },
  /**
   * 覆盖上限，只给「按住 Alt 看 1:1」用。
   * 1:1 时把 maxEdge 设成原始像素宽、maxHeightRatio 设成 1 —— 这时候用户的目的是
   * 判断清晰度，60vh 的封顶反而碍事。视口硬边界仍然生效。
   */
  opts?: { maxEdge?: number; maxHeightRatio?: number }
): PanelRect {
  const vw = Math.max(0, Math.round(viewport.width))
  const vh = Math.max(0, Math.round(viewport.height))

  const mw = Number(media.width ?? 0)
  const mh = Number(media.height ?? 0)
  const aspect = mw > 0 && mh > 0 ? mw / mh : 16 / 9

  // 三条上限取最紧的一条；视口极窄时别算出 0 或负数
  const hardMax = Math.max(1, vw - PREVIEW_MARGIN * 2)
  const maxEdge = Math.max(1, opts?.maxEdge ?? PREVIEW_MAX_EDGE)
  const ratio = opts?.maxHeightRatio ?? PREVIEW_VH_RATIO
  const limit = Math.max(1, Math.min(maxEdge, vh * ratio, hardMax))

  const width = Math.max(1, Math.round(aspect >= 1 ? limit : limit * aspect))
  const height = Math.max(1, Math.round(aspect >= 1 ? limit / aspect : limit))

  const maxLeft = Math.max(PREVIEW_MARGIN, vw - PREVIEW_MARGIN - width)
  const maxTop = Math.max(PREVIEW_MARGIN, vh - PREVIEW_MARGIN - height)

  let left = Math.round(anchor.right) + PREVIEW_GAP
  if (left > maxLeft) left = Math.round(anchor.left) - PREVIEW_GAP - width
  left = Math.min(Math.max(left, PREVIEW_MARGIN), maxLeft)

  let top = Math.round(anchor.top)
  if (top > maxTop) top = Math.round(anchor.bottom) - height
  top = Math.min(Math.max(top, PREVIEW_MARGIN), maxTop)

  return { left, top, width, height }
}

// ---------------------------------------------------------------------------
// 可替换时钟（生产 = setTimeout，单测 = 手动推进的假时钟）
// ---------------------------------------------------------------------------

export interface PreviewClock {
  set(fn: () => void, ms: number): number
  clear(id: number): void
}

const REAL_CLOCK: PreviewClock = {
  set: (fn, ms) => setTimeout(fn, ms) as unknown as number,
  clear: (id) => clearTimeout(id as unknown as ReturnType<typeof setTimeout>),
}

let clock: PreviewClock = REAL_CLOCK

/** 只给单测用：换掉时钟；传 null 恢复真实计时器。 */
export function setHoverPreviewClock(next: PreviewClock | null): void {
  clock = next ?? REAL_CLOCK
}

// ---------------------------------------------------------------------------
// 仲裁器
// ---------------------------------------------------------------------------

export interface HoverPreviewTarget {
  kind: "image" | "video"
  /** 原图 / 原视频地址（不是缩略图——放大就是要看真东西） */
  src: string
  name: string
  /** 第二行：规格 / 大小 */
  meta?: string
  width?: number | null
  height?: number | null
  /** 源格子的矩形。元素被卸载时返回 null，调用方据此关闭面板 */
  anchor: () => RectLike | null
}

export interface HoverPreviewSnapshot {
  target: HoverPreviewTarget | null
  /** true = 应该显示（opacity 1）；false 但 target 还在 = 正在淡出 */
  open: boolean
  /** 每次真正「打开」或换格自增，用来让媒体元素重放淡换动画 */
  nonce: number
  /** 是否播放淡入动画（160ms）。淡出期间必须是 false，否则 animation 的
   *  fill-mode 会把 opacity 定在 1，关不掉 */
  animIn: boolean
}

let snap: HoverPreviewSnapshot = { target: null, open: false, nonce: 0, animIn: false }
const listeners = new Set<() => void>()

let openTimer: number | undefined
let graceTimer: number | undefined
let exitTimer: number | undefined
let animTimer: number | undefined

/** 点过之后指针还停在这一格时不要再弹（否则面板一直挡着检查器） */
let suppressed = false

function emit() {
  for (const fn of listeners) fn()
}

function setSnap(patch: Partial<HoverPreviewSnapshot>) {
  snap = { ...snap, ...patch }
  emit()
}

function stopTimer(id: number | undefined): undefined {
  if (id !== undefined) clock.clear(id)
  return undefined
}

function clearTimers() {
  openTimer = stopTimer(openTimer)
  graceTimer = stopTimer(graceTimer)
  exitTimer = stopTimer(exitTimer)
  animTimer = stopTimer(animTimer)
}

export function subscribeHoverPreview(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function getHoverPreviewSnapshot(): HoverPreviewSnapshot {
  return snap
}

/** 请求显示（或换格）。指针每进入一个格子调一次。 */
export function showHoverPreview(target: HoverPreviewTarget): void {
  if (suppressed) return
  graceTimer = stopTimer(graceTimer)
  exitTimer = stopTimer(exitTimer)

  if (snap.open) {
    // 已经开着：只换内容，容器不重建 —— 这就是「内容淡换」
    if (snap.target && snap.target.src === target.src) {
      setSnap({ target })
      return
    }
    setSnap({ target, nonce: snap.nonce + 1 })
    return
  }

  // 还没开：等满 120ms 才开。期间换格子就重新计时（横扫不闪）
  openTimer = stopTimer(openTimer)
  openTimer = clock.set(() => {
    openTimer = undefined
    if (suppressed) return
    setSnap({ target, open: true, nonce: snap.nonce + 1, animIn: true })
    animTimer = stopTimer(animTimer)
    animTimer = clock.set(() => {
      // 动画放完要摘掉，否则 fill-mode: both 会把 opacity 钉在 1
      animTimer = undefined
      if (snap.animIn && snap.open) setSnap({ animIn: false })
    }, PREVIEW_TIMING.enterAnim)
  }, PREVIEW_TIMING.enter)
}

/** 指针离开格子。给 90ms 宽限：这段时间内进入别的格子算换格。 */
export function hideHoverPreview(): void {
  // 指针已经离开那一格 → 「点过就不再弹」的封锁立刻解除。
  // 不能放在宽限回调里：从 A（刚点过）直接滑到 B 时，B 的 show 会撞上还没解除的封锁，
  // 结果是 B 要等指针再进出一次才响应。
  suppressed = false
  openTimer = stopTimer(openTimer)
  graceTimer = stopTimer(graceTimer)
  graceTimer = clock.set(() => {
    graceTimer = undefined
    if (!snap.open) {
      // 本来就没开，直接收干净
      if (snap.target) setSnap({ target: null })
      return
    }
    setSnap({ open: false, animIn: false })
    exitTimer = stopTimer(exitTimer)
    exitTimer = clock.set(() => {
      exitTimer = undefined
      setSnap({ target: null })
    }, PREVIEW_TIMING.exit)
  }, PREVIEW_TIMING.grace)
}

/**
 * 点过这一格之后别再弹（选中 / 双击看大图时会挡住工作区）。
 * 指针离开这一格后自动解除。
 */
export function suppressHoverPreview(): void {
  suppressed = true
  openTimer = stopTimer(openTimer)
  animTimer = stopTimer(animTimer)
  if (snap.open) {
    setSnap({ open: false, animIn: false })
    exitTimer = stopTimer(exitTimer)
    exitTimer = clock.set(() => {
      exitTimer = undefined
      setSnap({ target: null })
    }, PREVIEW_TIMING.exit)
  } else if (snap.target) {
    setSnap({ target: null })
  }
}

/** 单测 / 切换工作区时把状态清干净 */
export function resetHoverPreview(): void {
  clearTimers()
  suppressed = false
  setSnap({ target: null, open: false, animIn: false })
}
