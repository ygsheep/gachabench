import type { CardStatus } from "@/lib/types"

export const STATUS_LABEL: Record<CardStatus, string> = {
  draft: "草稿",
  final: "正片",
  reject: "废稿",
}

/** 时间码 00:05.400 —— 只在等宽字体下才有意义，所以配合 font-mono 使用 */
export function timecode(sec: number | null | undefined): string {
  const s = Math.max(0, sec ?? 0)
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, "0")}:${(s - m * 60).toFixed(3).padStart(6, "0")}`
}

export function seconds(sec: number | null | undefined, digits = 1): string {
  if (sec == null) return "—"
  return `${sec.toFixed(digits)}s`
}

/** ISO 时间戳 → 10-16 00:02（列表里只需要认得出是哪一批） */
export function shortStamp(iso: string | null | undefined): string {
  if (!iso) return "—"
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/)
  if (!m) return String(iso).slice(0, 16)
  return `${m[2]}-${m[3]} ${m[4]}:${m[5]}`
}

export function humanSize(bytes: number | null | undefined): string {
  if (bytes == null) return "—"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 块码排序：1of3 < 2of3 < 3of3 < 整段 < 认不出块号 < 其它 */
export function chunkRank(chunk: string): number {
  const m = chunk.match(/^(\d+)of(\d+)$/)
  if (m) return Number(m[1])
  if (chunk === "未分块") return 89
  if (chunk === "full" || chunk === "final" || chunk === "整段") return 90
  return 99
}

export function chunkLabel(chunk: string | null): string {
  if (!chunk) return "未分块"
  if (chunk === "full" || chunk === "final") return "整段"
  return chunk
}

export function fileName(relPath: string): string {
  return relPath.split(/[\\/]/).pop() ?? relPath
}

/** rel 里的所在目录（相对素材根）；根目录返回空串 */
export function relDir(relPath: string): string {
  const i = relPath.lastIndexOf("/")
  return i < 0 ? "" : relPath.slice(0, i)
}

/** 与后端 _split 同口径：文件名主体名 = 第一段（按 `_` 与空白切） */
export function subjectOf(stem: string): string {
  return stem.split(/[_\s]+/).filter(Boolean)[0] ?? stem
}

/** 从文件名兜底认段号：04_xxx / 第4段 / 04-xxx */
export function segFromName(name: string): string | null {
  const a = name.match(/^(\d{2})[_\-.]/)
  if (a) return a[1]
  const b = name.match(/第(\d+)段/)
  if (b) return b[1].padStart(2, "0")
  return null
}
