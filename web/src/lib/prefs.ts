/**
 * UI 偏好持久化：把「刷新就丢」的那点状态收口到一个版本化键里。
 *
 * 来源：novel-booksource-engine 的 `lib/storage.js` + `state/settings.jsx`
 * （命名空间 + 版本号 + 深合并 + clamp，任何存储异常都降级成默认值）。
 * 移植时按 GB 的实际需要裁剪：这里只放**界面偏好**，不放大盘参数
 * （那些在 config.json 里，走 `/api/config`）。
 *
 * 当前收录：
 * - `view`：上次停留的视图（刷新后回到原处，不用从头点）
 * - `filter`：卡池的筛选条件（状态 / 段 / 关键词）
 * - `keepOnReload`：是否启用上面两条（关掉就是「每次刷新都回抽卡台」）
 * - `reader`：定位工单阅读面板的排版变量（字号 / 行距 / 字距 / 段距 / 列宽）
 *
 * 可靠性：localStorage 读写失败（隐私模式 / 配额满 / 脏数据）一律降级默认值，
 * 绝不让 UI 因为存储失败而白屏 —— 与 `swrCache.ts` 同一套态度。
 */

export const PREFS_KEY = "gb.ui.v1"

/** 阅读面板排版变量的范围（滑块区间 + 越界钳制都用它） */
export const READER_RANGES = {
  fontSize: { min: 10, max: 20, step: 0.5 },
  lineHeight: { min: 1.4, max: 2.6, step: 0.05 },
  letterSpacing: { min: 0, max: 2, step: 0.1 },
  paragraphGap: { min: 0, max: 28, step: 2 },
  /** 0 = 不限（跟着面板走） */
  contentWidth: { min: 0, max: 1400, step: 20 },
} as const

export interface ReaderPrefs {
  fontSize: number
  lineHeight: number
  letterSpacing: number
  paragraphGap: number
  contentWidth: number
}

/**
 * 跑片参数：拆分台顶部参数条的记忆。
 * 按**工作流名**分组存，切换工作流不会把 A 的 LoRA 带到 B 上。
 * `null` 值 = 故意留空（例如时长「跟随块」），序列化保留。
 */
export interface RunParams {
  /** null = 用配置里的默认工作流 */
  workflow: string | null
  byWorkflow: Record<string, Record<string, string | number | null>>
}

const DEFAULT_RUN_PARAMS: RunParams = { workflow: null, byWorkflow: {} }

/** 最近打开（P3）：只记段号，最近用过的排在最前。 */
export const RECENT_LIMIT = 8

export interface RecentPrefs {
  segs: string[]
}

export const DEFAULT_RECENT: RecentPrefs = { segs: [] }

/**
 * 把一条记到最前：去重 + 限长。
 *
 * 🔴 这是**历史记录**不是会话状态 —— 不受 `keepOnReload` 管：那个开关说的是
 * 「刷新后要不要回到上次的位置」，跟「最近看过哪些段」是两件事。
 */
export function pushRecent(list: string[], value: string, limit = RECENT_LIMIT): string[] {
  const v = String(value ?? "").trim()
  if (!v) return list
  return [v, ...list.filter((x) => x !== v)].slice(0, limit)
}

/** 脏数据清洗：只留字符串、去重、限长（localStorage 可能被手改过）。 */
export function sanitizeRecent(list: unknown, limit = RECENT_LIMIT): string[] {
  if (!Array.isArray(list)) return []
  const out: string[] = []
  for (const raw of list) {
    const v = typeof raw === "string" ? raw.trim() : raw == null ? "" : String(raw).trim()
    if (v && !out.includes(v)) out.push(v)
    if (out.length >= limit) break
  }
  return out
}

export const DEFAULT_READER: ReaderPrefs = {
  fontSize: 11.5,
  lineHeight: 1.75,
  letterSpacing: 0,
  paragraphGap: 0,
  contentWidth: 0,
}

export const DEFAULT_PREFS: Prefs = {
  version: 1,
  view: "board",
  filter: { status: "all", seg: "all", q: "" },
  keepOnReload: true,
  reader: { ...DEFAULT_READER },
  runParams: { ...DEFAULT_RUN_PARAMS, byWorkflow: {} },
  recent: { ...DEFAULT_RECENT, segs: [] },
}

/** v1 的页面偏好字段；P0 路由化后不再用于决定页面，保留只为旧值兼容。 */
type View = "board" | "split" | "locator" | "assets" | "library" | "search" | "settings"

export interface Prefs {
  version: number
  view: View
  filter: { status: string; seg: string; q: string }
  /** 关闭后刷新一律回到抽卡台 + 清空筛选 */
  keepOnReload: boolean
  reader: ReaderPrefs
  /** 拆分台跑片参数（工作流 + 各项覆盖值） */
  runParams: RunParams
  /** 最近打开过的段（P3）。历史记录，不受 keepOnReload 管 */
  recent: RecentPrefs
}

// ---------------------------------------------------------------------------
// 底层读写（任何异常都吃掉，返回默认值）
// ---------------------------------------------------------------------------

export function readJSON<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return fallback
    const parsed: unknown = JSON.parse(raw)
    if (parsed === null || parsed === undefined) return fallback
    return parsed as T
  } catch (err) {
    console.warn(`[prefs] 读取失败，已回退默认值：${key}`, err)
    return fallback
  }
}

export function writeJSON(key: string, value: unknown): boolean {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
    return true
  } catch (err) {
    console.warn(`[prefs] 写入失败：${key}`, err)
    return false
  }
}

/**
 * 深合并：只覆盖已知字段，旧版本残留的未知键不会污染运行时。
 * 数组整体替换（不做逐项合并），保证语义直观。
 */
export function mergeDefaults<T extends object>(defaults: T, incoming: unknown): T {
  if (!incoming || typeof incoming !== "object") return structuredClone(defaults)
  const out = structuredClone(defaults) as Record<string, unknown>
  const src = incoming as Record<string, unknown>
  for (const key of Object.keys(defaults)) {
    const dv = (defaults as Record<string, unknown>)[key]
    const iv = src[key]
    if (iv === undefined) continue
    if (dv && typeof dv === "object" && !Array.isArray(dv)) {
      out[key] = mergeDefaults(dv as object, iv)
    } else if (Array.isArray(dv)) {
      out[key] = Array.isArray(iv) ? iv.slice() : (dv as unknown[]).slice()
    } else {
      out[key] = iv
    }
  }
  return out as T
}

export function clamp(value: number, min: number, max: number): number {
  const n = Number(value)
  if (!Number.isFinite(n)) return min
  return Math.min(max, Math.max(min, n))
}

// ---------------------------------------------------------------------------
// 读 / 写 / 重置
// ---------------------------------------------------------------------------

export function loadPrefs(): Prefs {
  const merged = mergeDefaults(DEFAULT_PREFS, readJSON<unknown>(PREFS_KEY, null))
  // view 只认已知视图，脏值（老版本 / 手改过）回退到抽卡台
  const known: View[] = ["board", "split", "locator", "assets", "library", "search", "settings"]
  if (!known.includes(merged.view)) merged.view = "board"
  // 排版变量：手改过 / 老版本残留的脏值一律钳回区间内
  for (const k of Object.keys(READER_RANGES) as (keyof ReaderPrefs)[]) {
    const r = READER_RANGES[k]
    merged.reader[k] = clamp(merged.reader[k], r.min, r.max)
  }
  // 跑片参数：只认对象结构，脏值整体丢弃，别让一个 null 把参数条炸掉
  const rp = merged.runParams
  if (!rp || typeof rp !== "object" || typeof rp.byWorkflow !== "object" || !rp.byWorkflow) {
    merged.runParams = { ...DEFAULT_RUN_PARAMS, byWorkflow: {} }
  }
  // 最近打开：只留字符串、去重、限长
  merged.recent = { segs: sanitizeRecent(merged.recent?.segs) }
  return merged
}

let _timer: number | undefined

/** 存偏好。200ms 防抖：连续切换筛选只落一次盘。 */
export function savePrefs(patch: Partial<Prefs>): void {
  if (typeof window === "undefined") return
  const next = mergeDefaults(loadPrefs(), patch)
  if (_timer) window.clearTimeout(_timer)
  _timer = window.setTimeout(() => {
    writeJSON(PREFS_KEY, next)
  }, 200)
}

/** 恢复默认（设置页「重置」用）。同步落盘，不等防抖。 */
export function resetPrefs(): Prefs {
  writeJSON(PREFS_KEY, DEFAULT_PREFS)
  return structuredClone(DEFAULT_PREFS)
}
