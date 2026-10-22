import { useMemo, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Download, Keyboard, RotateCw, Search } from "lucide-react"
import { cn } from "cn"
import { FilmCell } from "@/components/FilmCell"
import { Reviewer } from "@/components/Reviewer"
import { StitchRail } from "@/components/StitchRail"
import { api } from "@/lib/api"
import { exportRange } from "@/lib/download"
import { useSwrQuery } from "@/lib/useSwrQuery"
import { STATUS_LABEL, chunkLabel, chunkRank, seconds } from "@/lib/format"
import { useApp, type CardFilter } from "@/store/useApp"
import type { Card, CardStatus, CardsResp } from "@/lib/types"

interface Block {
  chunk: string
  takes: Card[]
}
interface Row {
  seg: string
  blocks: Block[]
}

const STATUS_RANK: Record<CardStatus, number> = { final: 0, draft: 1, reject: 2 }

function sortTakes(list: Card[]): Card[] {
  return [...list].sort(
    (a, b) =>
      STATUS_RANK[a.status] - STATUS_RANK[b.status] ||
      (b.updated_at || "").localeCompare(a.updated_at || "")
  )
}

/** 卡池 → 段 / 块 / 候选条 两级结构（一块 = 一次 5s 抽卡，一条 = 一次抽出来的结果） */
function groupCards(cards: Card[]): Row[] {
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
      blocks: [...m.entries()]
        .map(([chunk, takes]) => ({ chunk, takes: sortTakes(takes) }))
        .sort((a, b) => chunkRank(a.chunk) - chunkRank(b.chunk) || a.chunk.localeCompare(b.chunk)),
    }))
    .sort((a, b) => (a.seg || "zz").localeCompare(b.seg || "zz"))
}

function matches(c: Card, status: string, q: string): boolean {
  if (status !== "all" && c.status !== status) return false
  if (q) {
    const hay = [
      c.seg ?? "",
      c.chunk ?? "",
      c.rel_path,
      c.note,
      c.reject_note,
      ...c.tags,
      ...c.reject_tags,
    ]
      .join(" ")
      .toLowerCase()
    if (!hay.includes(q.toLowerCase())) return false
  }
  return true
}

export function GachaBoard({
  workspace,
  filter: filterProp,
  onFilterChange,
}: {
  workspace: string | null
  /** 有值时以路由 search 为准；没有时退回 store（旧入口） */
  filter?: CardFilter
  onFilterChange?: (patch: Partial<CardFilter>) => void
}) {
  const storeFilter = useApp((s) => s.filter)
  const storeSetFilter = useApp((s) => s.setFilter)
  const setSeg = useApp((s) => s.setSeg)

  const filter = filterProp ?? storeFilter
  const setFilter = (patch: Partial<CardFilter>) => {
    storeSetFilter(patch)
    onFilterChange?.(patch)
  }

  const [review, setReview] = useState<{ seg: string; chunk: string; initialId: number } | null>(null)
  const [focusRow, setFocusRow] = useState<string | null>(null)
  const [keys, setKeys] = useState(false)
  const [exporting, setExporting] = useState(false)
  const queryClient = useQueryClient()

  // 冷启动先画上一次的卡池（SWR），后台扫描完再替换；徽标见下方筛选行
  const { data, isPending, isError, error, isFetching } = useSwrQuery<CardsResp>(
    `swr:cards:${workspace ?? "_"}`,
    ["cards", workspace],
    (signal) => api.get<CardsResp>("/api/cards?status=all", { signal }),
    { enabled: !!workspace }
  )

  const scan = useMutation({
    mutationFn: () =>
      api.post<{ added?: number; rescued?: { files?: number } }>(
        `/api/workspaces/${encodeURIComponent(workspace!)}/scan`
      ),
    onSuccess: (r) => {
      queryClient.invalidateQueries({ queryKey: ["cards"] })
      const got = r?.rescued?.files ?? 0
      toast.success("扫描完成", {
        description: got
          ? `新登记 ${r?.added ?? 0} 条；还从 ComfyUI 补收了 ${got} 个产物`
          : "新片段已登记，已存在的会跳过",
      })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const cards = useMemo(() => data?.cards ?? [], [data])
  const rejectTags = data?.reject_tags ?? []
  const rows = useMemo(() => groupCards(cards), [cards])

  const counts = useMemo(() => {
    const c = { all: cards.length, draft: 0, final: 0, reject: 0 }
    for (const k of cards) c[k.status]++
    return c
  }, [cards])

  const visible = useMemo(
    () =>
      rows
        .filter((r) => filter.seg === "all" || r.seg === filter.seg)
        .map((r) => ({
          ...r,
          filtered: r.blocks.map((b) => b.takes.filter((t) => matches(t, filter.status, filter.q))),
        }))
        .filter((r) => r.filtered.some((l) => l.length)),
    [rows, filter]
  )

  const total = cards.length
  const shown = useMemo(
    () => cards.filter((c) => matches(c, filter.status, filter.q)).length,
    [cards, filter]
  )

  /* 按当前筛选打包导出（C4）。后端只认 status + seg —— 关键词是前端的活，
     这里说清楚，别让用户以为筛了关键词就能只导那几条。 */
  async function doExportCards() {
    const st = filter.status === "all" ? "all" : filter.status
    setExporting(true)
    try {
      const r = await exportRange(
        { what: "cards", status: st, seg: filter.seg },
        {
          zipName: `卡片_${filter.status === "all" ? "全部" : STATUS_LABEL[filter.status]}_${
            filter.seg === "all" ? "全部段" : `段${filter.seg}`
          }`,
        }
      )
      if (!r.ok) {
        if (!r.message.includes("取消")) toast.error(r.message)
        return
      }
      toast.success("已导出", {
        description: filter.q.trim() ? `${r.message}（关键词不参与打包，只按状态+段）` : r.message,
      })
    } finally {
      setExporting(false)
    }
  }

  function openBlock(seg: string, chunk: string, cardId: number) {
    setSeg(seg)
    setReview({ seg, chunk, initialId: cardId })
  }

  /** ↑↓ 在块之间走 */
  function switchBlock(dir: -1 | 1) {
    if (!review) return
    const row = rows.find((r) => r.seg === review.seg)
    if (!row) return
    const withTakes = row.blocks.filter((b) => b.takes.length)
    if (!withTakes.length) return
    const i = withTakes.findIndex((b) => b.chunk === review.chunk)
    const next = withTakes[(i + dir + withTakes.length) % withTakes.length]
    setReview({ seg: review.seg, chunk: next.chunk, initialId: next.takes[0].id })
  }

  const reviewData = useMemo(() => {
    if (!review) return null
    const row = rows.find((r) => r.seg === review.seg)
    const block = row?.blocks.find((b) => b.chunk === review.chunk)
    if (!block || !block.takes.length) return null
    return { takes: block.takes, shots: block.takes[0].tags }
  }, [rows, review])

  if (!workspace) return null

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-end gap-x-3.5 gap-y-2 px-6 pb-3.5 pt-5">
        <div className="max-w-[82ch]">
          <h1 className="text-[19px] font-semibold leading-tight">抽卡台</h1>
          <p className="pt-0.5 text-[12.5px] text-ash">
            按段分行、段内按块顺序排。每格显示最靠前的一条，点开能对比同一块的所有候选。
          </p>
        </div>
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => setKeys(true)}
          className="inline-flex h-8 items-center gap-1.5 rounded-ctl px-2 text-[12.5px] text-ash transition-colors hover:bg-cell hover:text-paper"
        >
          <Keyboard className="size-3.5" />
          快捷键
        </button>
        <button
          type="button"
          onClick={() => scan.mutate()}
          disabled={scan.isPending}
          className={cn(
            "inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12.5px] font-medium transition-all hover:bg-hi active:translate-y-px disabled:opacity-45",
            scan.isPending && "gb-sweep"
          )}
        >
          <RotateCw className={cn("size-3.5", scan.isPending && "animate-spin")} />
          {scan.isPending ? "扫描中…" : "重新扫描"}
        </button>
      </header>

      <div className="flex flex-wrap items-center gap-2.5 px-6 pb-3.5">
        <Segmented
          value={filter.status}
          onChange={(v) => setFilter({ status: v as CardStatus | "all" })}
          options={[
            { v: "all", label: "全部", n: counts.all },
            { v: "draft", label: "草稿", n: counts.draft },
            { v: "final", label: "正片", n: counts.final },
            { v: "reject", label: "废稿", n: counts.reject },
          ]}
        />
        <select
          value={filter.seg}
          onChange={(e) => setFilter({ seg: e.target.value })}
          aria-label="按段筛选"
          className="h-8 rounded-ctl border border-rule bg-panel px-2.5 text-[12.5px] outline-none focus:border-wash-line"
        >
          <option value="all">全部段</option>
          {rows.map((r) => (
            <option key={r.seg || "_"} value={r.seg}>
              {r.seg ? `段 ${r.seg}` : "未分段"}
            </option>
          ))}
        </select>
        <div className="relative w-[212px]">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-ash-dim" />
          <input
            value={filter.q}
            onChange={(e) => setFilter({ q: e.target.value })}
            placeholder="镜号 / 文件名 / 备注"
            className="h-8 w-full rounded-ctl border border-rule bg-panel pl-7 pr-2.5 text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
          />
        </div>
        <span className="flex-1" />
        {isFetching && data && (
          <span
            className="inline-flex h-6 items-center rounded-ctl border border-dashed border-rule px-2 text-[11px] text-ash-dim"
            title="先用上次的结果占位，扫盘结束会替换"
          >
            旧结果 · 更新中
          </span>
        )}
        <span className="text-[11.5px] text-ash-dim">
          {shown === total ? `卡池 ${total} 条` : `匹配 ${shown} / ${total} 条`}
        </span>
        <button
          type="button"
          onClick={doExportCards}
          disabled={exporting || !total}
          title="按当前「状态 + 段」打包导出（关键词不参与）"
          className="inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12.5px] font-medium transition-all hover:bg-hi active:translate-y-px disabled:opacity-45"
        >
          <Download className="size-3.5" />
          {exporting ? "打包中…" : "打包导出"}
        </button>
      </div>

      <div className="flex flex-col gap-3 px-6 pb-11">
        {isPending && <p className="text-[12.5px] text-ash">读取卡池…</p>}

        {isError && (
          <div className="rounded-panel border border-rule bg-panel p-4">
            <p className="text-[13px] font-medium">卡池读不出来</p>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-ash">
              {(error as Error)?.message ?? "后端没有响应。"} 确认{" "}
              <span className="font-mono">uv run uvicorn main:app --port 8000</span> 还活着，然后重试。
            </p>
          </div>
        )}

        {!isPending && !isError && !visible.length && (
          <div className="rounded-panel border border-dashed border-rule bg-panel px-6 py-12 text-center">
            {total ? (
              <>
                <h3 className="text-[15px] font-medium">没有匹配的片段</h3>
                <p className="mx-auto mt-2 max-w-[52ch] text-[12.5px] leading-relaxed text-ash">
                  换个状态，或者把关键词清掉。段下拉里的选项是从卡池现读的，不会出现空段。
                </p>
              </>
            ) : (
              <>
                <h3 className="text-[15px] font-medium">这个工作区还没有片子</h3>
                <p className="mx-auto mt-2 max-w-[52ch] text-[12.5px] leading-relaxed text-ash">
                  先把跑出来的 mp4 放进 <span className="font-mono">草稿/</span>，再点右上角「重新扫描」。
                  扫描只登记，不动你的文件。
                </p>
              </>
            )}
          </div>
        )}

        {visible.map((row) => {
          const all = row.blocks
          const missing = all.filter((b) => !b.takes.length).map((b) => b.chunk)
          const sum = all.reduce((a, b) => a + (b.takes.length ? b.takes[0].duration ?? 0 : 0), 0)
          const takeN = all.reduce((a, b) => a + b.takes.length, 0)

          return (
            <section
              key={row.seg || "_"}
              onMouseEnter={() => setFocusRow(row.seg)}
              onMouseLeave={() => setFocusRow(null)}
              className="w-fit max-w-full min-w-0 self-start rounded-panel border border-rule-soft bg-panel transition-colors hover:border-rule"
            >
              <div className="flex flex-wrap items-center gap-2.5 border-b border-rule-soft px-3.5 pb-2.5 pt-3">
                <span className="font-mono text-[17px] font-medium leading-none">
                  {row.seg || "—"}
                </span>
                <span
                  className={cn(
                    "inline-flex items-center gap-1.5 whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px]",
                    missing.length
                      ? "border-dashed border-rule text-ash-dim"
                      : "border-wash-line bg-wash text-paper"
                  )}
                >
                  {missing.length
                    ? `缺 ${missing.map(chunkLabel).join("、")}`
                    : `${all.length}/${all.length} 齐`}
                </span>
                <span className="inline-flex items-center gap-1.5 rounded border border-rule px-1.5 py-0.5 text-[11px] text-ash">
                  <span className="font-mono">{takeN}</span> 条
                </span>
                <span className="inline-flex items-center rounded border border-rule px-1.5 py-0.5 text-[11px] text-ash">
                  {all.length} 块
                </span>
                <span className="flex-1" />
                <span className="font-mono text-[11.5px] text-ash">
                  {missing.length
                    ? `已跑 ${seconds(sum)} / 共 ${all.length} 块`
                    : `合计 ${seconds(sum)}`}
                </span>
              </div>

              <div className="gb-strip-cells">
                {all.map((b, bi) => {
                  const li = row.filtered[bi]
                  if (!li.length) {
                    const why = b.takes.length
                      ? `无${filter.status === "all" ? "候选" : STATUS_LABEL[filter.status as CardStatus]}`
                      : "还没跑"
                    return (
                      <div key={b.chunk} className="gb-cell">
                        <div className="grid aspect-video place-items-center rounded-cell border border-dashed border-rule">
                          <span className="text-[11.5px] text-ash-dim">{why}</span>
                        </div>
                        <div className="flex items-baseline gap-2 px-0.5 pt-2">
                          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ash">
                            {chunkLabel(b.chunk)}
                          </span>
                          <span className="text-[10.5px] text-ash-dim">—</span>
                        </div>
                      </div>
                    )
                  }
                  const face = li[0]
                  return (
                    <FilmCell
                      key={b.chunk}
                      card={face}
                      takeCount={b.takes.length}
                      shots={face.tags}
                      onClick={() => openBlock(row.seg, b.chunk, face.id)}
                    />
                  )
                })}
              </div>

              <StitchRail
                segs={all.map((b) => ({ has: b.takes.length > 0 }))}
                sum={missing.length ? "缺块，拼不回整段" : `拼回 ${seconds(sum)}`}
                full={!missing.length}
                focus={focusRow === row.seg}
              />
            </section>
          )
        })}
      </div>

      {review && reviewData && (
        <Reviewer
          key={`${review.seg}/${review.chunk}`}
          seg={review.seg}
          chunk={review.chunk}
          takes={reviewData.takes}
          initialId={review.initialId}
          shotTags={reviewData.shots}
          rejectTags={rejectTags}
          onClose={() => setReview(null)}
          onSwitchBlock={switchBlock}
        />
      )}

      {keys && <KeysSheet onClose={() => setKeys(false)} />}
    </div>
  )
}

function Segmented({
  value,
  onChange,
  options,
}: {
  value: string
  onChange: (v: string) => void
  options: { v: string; label: string; n: number }[]
}) {
  const i = Math.max(
    0,
    options.findIndex((o) => o.v === value)
  )
  const W = 100 / options.length
  return (
    <div className="relative inline-flex rounded-ctl border border-rule bg-panel p-0.5">
      <span
        aria-hidden
        className="pointer-events-none absolute bottom-0.5 top-0.5 rounded-[4px] border border-[#565656] bg-hi transition-transform duration-300 ease-[cubic-bezier(.2,.8,.25,1)]"
        style={{
          width: `calc(${W}% - 4px)`,
          transform: `translateX(calc(${i * 100}% + ${i * 4}px))`,
        }}
      />
      {options.map((o) => (
        <button
          key={o.v}
          type="button"
          onClick={() => onChange(o.v)}
          aria-pressed={o.v === value}
          className={cn(
            "relative z-1 inline-flex h-[26px] items-center gap-1.5 rounded-[4px] px-2.5 text-[12.5px] transition-colors",
            o.v === value ? "font-medium text-paper" : "text-ash hover:text-paper"
          )}
        >
          {o.label}
          <span
            className={cn("font-mono text-[11px]", o.v === value ? "text-white" : "text-ash-dim")}
          >
            {o.n}
          </span>
        </button>
      ))}
    </div>
  )
}

function KeysSheet({ onClose }: { onClose: () => void }) {
  const rows: [string, string][] = [
    ["1 / 2 / 3", "正片 / 废稿 / 退回草稿"],
    ["← →", "换同一块的候选条"],
    ["↑ ↓", "换块"],
    ["空格", "播放 / 暂停"],
    [", .", "退 / 进一帧"],
    ["L / M", "循环 / 静音"],
    ["Esc", "返回抽卡台"],
  ]
  return (
    <div
      className="fixed inset-0 z-80 grid place-items-center bg-black/65"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="w-[min(520px,92vw)] rounded-panel border border-rule bg-panel p-5">
        <h2 className="mb-3.5 text-[14px] font-medium">审片器快捷键</h2>
        <div className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2.5 text-[12.5px]">
          {rows.map(([k, d]) => (
            <div key={k} className="contents">
              <kbd className="inline-flex h-5 items-center justify-center rounded border border-rule border-b-2 bg-cell px-1.5 font-mono text-[10.5px] text-[#c6c6c6]">
                {k}
              </kbd>
              <span className="text-ash">{d}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
