/**
 * 首屏 SWR 缓存：先把上一次的结果画出来，后台刷新完再替换。
 *
 * 为什么需要：卡池（`/api/cards`）与素材库（`/api/assets`）都是「先扫盘入库再读库」，
 * 首屏体感完全取决于磁盘快慢。冷启动（QueryCache 是空的）时干等一次扫描很蠢。
 *
 * 行为对齐 stale-while-revalidate：
 *   - 冷启动：读 localStorage 旧数据 → 立刻渲染 → 后台请求 → 成功后替换 + 写缓存；
 *   - 已有数据时的刷新（扫描 / 上传后 invalidate）：走 TanStack 自带的
 *     「保留旧数据 + 后台 refetch」，与本模块无关；
 *   - 旧数据必须**能被识别为旧**：调用方在「有数据且正在拉取」时挂虚线徽标
 *     （`border-dashed` = 未确认，正好是 GB 的轮廓语言）。
 *
 * 可靠性：localStorage 任何异常（隐私模式 / 配额满 / 脏数据）一律降级成「没有缓存」，
 * 绝不让 UI 因为存储失败而白屏。
 */

export const SWR_KEY = "gb.swr.v1"

/** 每个键族最多留几条（换工作区会攒出很多 `swr:cards:*`，不能无限长） */
const MAX_ENTRIES = 12
/** 超过 14 天的缓存视为过期，不再渲染 */
const MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000

interface Entry {
  ts: number
  data: unknown
}

type CacheFile = Record<string, Entry>

function readAll(): CacheFile {
  try {
    const raw = window.localStorage.getItem(SWR_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {}
    return parsed as CacheFile
  } catch (err) {
    console.warn("[swr] 读取本地缓存失败，本次当作没有缓存", err)
    return {}
  }
}

function writeAll(file: CacheFile): boolean {
  try {
    window.localStorage.setItem(SWR_KEY, JSON.stringify(file))
    return true
  } catch (err) {
    // 配额满 / 隐私模式：静默失败即可，缓存只是优化，不是功能
    console.warn("[swr] 写入本地缓存失败（本次不缓存）", err)
    return false
  }
}

/** 读旧数据；过期、缺键、形状不对都返回 null */
export function swrRead<T>(key: string): T | null {
  if (typeof window === "undefined") return null
  const file = readAll()
  const hit = file[key]
  if (!hit || typeof hit.ts !== "number") return null
  if (Date.now() - hit.ts > MAX_AGE_MS) {
    delete file[key]
    writeAll(file)
    return null
  }
  return hit.data as T
}

/** 写缓存。同键覆盖；超量时按时间戳从旧到新丢。 */
export function swrWrite<T>(key: string, data: T): void {
  if (typeof window === "undefined") return
  const file = readAll()
  file[key] = { ts: Date.now(), data }

  const keys = Object.keys(file)
  if (keys.length > MAX_ENTRIES) {
    keys
      .sort((a, b) => (file[a]?.ts ?? 0) - (file[b]?.ts ?? 0))
      .slice(0, keys.length - MAX_ENTRIES)
      .forEach((k) => delete file[k])
  }
  writeAll(file)
}

/** 清缓存。带前缀只清一族（如 `swr:cards:`），不带则全清。 */
export function swrClear(prefix = ""): void {
  if (typeof window === "undefined") return
  const file = readAll()
  const kept: CacheFile = {}
  for (const [k, v] of Object.entries(file)) {
    if (!k.startsWith(prefix)) kept[k] = v
  }
  writeAll(kept)
}
