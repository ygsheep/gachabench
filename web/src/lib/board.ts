/** 卡池的二级结构：段 → 块 → 候选条。抽卡台与审片台共用同一套口径。 */
import { chunkRank } from "@/lib/format"
import type { Card, CardStatus } from "@/lib/types"

export interface Block {
  chunk: string
  takes: Card[]
}
export interface Row {
  seg: string
  blocks: Block[]
}

/** 正片在前、废稿在后；同状态按最近更新 */
const STATUS_RANK: Record<CardStatus, number> = { final: 0, draft: 1, reject: 2 }

export function sortTakes(list: Card[]): Card[] {
  return [...list].sort(
    (a, b) =>
      STATUS_RANK[a.status] - STATUS_RANK[b.status] ||
      (b.updated_at || "").localeCompare(a.updated_at || "")
  )
}

/** 一块 = 一次 5s 抽卡，一条 = 一次抽出来的结果 */
export function groupCards(cards: Card[]): Row[] {
  const bySeg = new Map<string, Map<string, Card[]>>()
  for (const c of cards) {
    const seg = c.seg ?? ""
    const chunk = c.chunk ?? ((c.duration ?? 0) >= 10 ? "整段" : "未分块")
    let m = bySeg.get(seg)
    if (!m) {
      m = new Map<string, Card[]>()
      bySeg.set(seg, m)
    }
    const list = m.get(chunk)
    if (list) list.push(c)
    else m.set(chunk, [c])
  }
  return [...bySeg.entries()]
    .map(([seg, m]) => ({
      seg,
      blocks: fillMissing(
        [...m.entries()]
          .map(([chunk, takes]) => ({ chunk, takes: sortTakes(takes) }))
          .sort((a, b) => chunkRank(a.chunk) - chunkRank(b.chunk) || a.chunk.localeCompare(b.chunk))
      ),
    }))
    .sort((a, b) => (a.seg || "zz").localeCompare(b.seg || "zz"))
}

/**
 * 按块码把没跑过的块补出来。
 *
 * 分组只认「有卡片」的块，于是 2of3 跑过、3of3 一条都没有时，
 * 3of3 这个块根本不存在 —— 「缺哪块」永远算不出来，界面上也看不见。
 * 块码 `1of3 / 2of3 / 3of3` 自带总块数，按它补齐，缺块才说得出来。
 * 认不出块码的（整段 / 未分块）原样接在后面。
 */
function fillMissing(blocks: Block[]): Block[] {
  const total = blocks.reduce((n, b) => {
    const m = b.chunk.match(/^(\d+)of(\d+)$/)
    return m ? Math.max(n, Number(m[2])) : n
  }, 0)
  if (!total) return blocks

  const byChunk = new Map(blocks.map((b) => [b.chunk, b]))
  const filled: Block[] = []
  for (let i = 1; i <= total; i++) {
    const chunk = `${i}of${total}`
    filled.push(byChunk.get(chunk) ?? { chunk, takes: [] })
  }
  return [...filled, ...blocks.filter((b) => !/^\d+of\d+$/.test(b.chunk))]
}

/** 状态 + 关键词的匹配口径（关键词是前端算的，后端只认 status + seg） */
export function matches(c: Card, status: string, q: string): boolean {
  if (status !== "all" && c.status !== status) return false
  if (q) {
    const hay = [c.seg ?? "", c.chunk ?? "", c.rel_path, c.note, c.reject_note, ...c.tags, ...c.reject_tags]
      .join(" ")
      .toLowerCase()
    if (!hay.includes(q.toLowerCase())) return false
  }
  return true
}
