import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Check,
  Download,
  FileX,
  Keyboard,
  Maximize2,
  Play,
  RotateCw,
  Search,
  Undo2,
  X,
} from "lucide-react"
import { cn } from "cn"
import { FilmCell } from "@/components/FilmCell"
import { Reviewer } from "@/components/Reviewer"
import { Segmented } from "@/components/Segmented"
import { api } from "@/lib/api"
import { groupCards, matches } from "@/lib/board"
import { exportItems, exportRange } from "@/lib/download"
import { useSwrQuery } from "@/lib/useSwrQuery"
import { useWorkspaceTitle } from "@/lib/workspace"
import { STATUS_LABEL, chunkLabel, fileName, seconds, shortStamp } from "@/lib/format"
import { useApp, type CardFilter } from "@/store/useApp"
import type { Card, CardStatus, CardsResp } from "@/lib/types"

/**
 * 审片台：左「段」／中「块 × 候选条」／右「这一条」。
 *
 * 旧版是一行一段的卡片墙，可一段 15s 只切 3 块，行宽 ≈ 840px，
 * 宽屏下右侧必然空掉一半。这里把「段」收进左栏纵向列表，中间只放
 * 当前那一段的 3 个块 —— 一屏的高度和宽度都被用上，滚动的只有列表本身。
 */
export function ReviewDesk({
  workspace,
  filter: filterProp,
  onFilterChange,
}: {
  workspace: string | null
  /** 有值时以路由 search 为准；没有时退回 store */
  filter?: CardFilter
  onFilterChange?: (patch: Partial<CardFilter>) => void
}) {
  const storeFilter = useApp((s) => s.filter)
  const storeSetFilter = useApp((s) => s.setFilter)
  const filter = filterProp ?? storeFilter
  const setFilter = (patch: Partial<CardFilter>) => {
    storeSetFilter(patch)
    onFilterChange?.(patch)
  }

  const [pickedId, setPickedId] = useState<number | null>(null)
  const [open, setOpen] = useState<{ chunk: string; id: number } | null>(null)
  const [keys, setKeys] = useState(false)
  const [exporting, setExporting] = useState(false)
  const queryClient = useQueryClient()
  /* 顶栏显示的是「用户改过的项目名」（label）；workspace 参数仍是目录名，只用于接口 */
  const workspaceTitle = useWorkspaceTitle()

  const { data, isPending, isError, error, isFetching } = useSwrQuery<CardsResp>(
    `swr:cards:${workspace ?? "_"}`,
    ["cards", workspace],
    (signal) => api.get<CardsResp>("/api/cards?status=all", { signal }),
    // 实时读盘：后台每 8s 只看一次盘上的文件指纹，没新片就零写入，
    // 所以这里的轮询几乎不花钱；窗口在后台时自动停（useSwrQuery 里定的）。
    { enabled: !!workspace, refetchInterval: 6000 }
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

  const patch = useMutation({
    mutationFn: (v: { id: number; body: Record<string, unknown> }) =>
      api.patch<Card>(`/api/cards/${v.id}`, v.body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cards"] }),
    onError: (e: Error) => toast.error(e.message),
  })

  const toFinal = useMutation({
    mutationFn: (id: number) =>
      api.post<{ card: Card; copied_to: string | null }>(`/api/cards/${id}/to-final`, {
        copy_to_final: false,
      }),
    onSuccess: (r) => {
      queryClient.invalidateQueries({ queryKey: ["cards"] })
      toast.success(r.copied_to ? "已复到 生成/" : "已标为正片", {
        description: r.copied_to ?? "原件留在 草稿/",
      })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const cards = useMemo(() => data?.cards ?? [], [data])
  const rejectTags = data?.reject_tags ?? []

  const counts = useMemo(() => {
    const c = { all: cards.length, draft: 0, final: 0, reject: 0 }
    for (const k of cards) c[k.status]++
    return c
  }, [cards])

  /* 段 → 块 → 当前筛选下的候选。关键词/状态只收窄候选，
     不改变「这一段有几块、缺哪块」的判断 —— 那是片子的事实，不是筛选结果。 */
  const view = useMemo(
    () =>
      groupCards(cards).map((r) => {
        const blocks = r.blocks.map((b) => ({
          chunk: b.chunk,
          all: b.takes,
          takes: b.takes.filter((t) => matches(t, filter.status, filter.q)),
        }))
        return {
          seg: r.seg,
          blocks,
          hits: blocks.reduce((n, b) => n + b.takes.length, 0),
          total: blocks.reduce((n, b) => n + b.all.length, 0),
          missing: blocks.filter((b) => !b.all.length).map((b) => b.chunk),
          dur: blocks.reduce((n, b) => n + (b.all[0]?.duration ?? 0), 0),
        }
      }),
    [cards, filter]
  )

  /* 选中段：URL 里带 seg 就用它；否则落到第一段「有匹配候选」的段。
     不把默认值写回 URL，避免一进页面就改地址。 */
  const activeSeg = useMemo(() => {
    if (filter.seg !== "all" && view.some((r) => r.seg === filter.seg)) return filter.seg
    return (view.find((r) => r.hits > 0) ?? view[0])?.seg ?? null
  }, [view, filter.seg])

  const active = useMemo(() => view.find((r) => r.seg === activeSeg) ?? null, [view, activeSeg])

  const flat = useMemo(
    () => (active ? active.blocks.flatMap((b) => b.takes) : []),
    [active]
  )
  const selected = useMemo(
    () => (flat.length ? (flat.find((t) => t.id === pickedId) ?? flat[0]) : null),
    [flat, pickedId]
  )

  const shown = useMemo(() => cards.filter((c) => matches(c, filter.status, filter.q)).length, [cards, filter])

  async function doExport() {
    setExporting(true)
    try {
      const r = await exportRange(
        { what: "cards", status: filter.status, seg: filter.seg },
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

  /* 键盘只在台面上生效：进审片器后按 Esc 回来，别和里面的 ← → 打架 */
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (open || keys) return
      const t = e.target as HTMLElement | null
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return
      if (!active) return
      const i = view.findIndex((r) => r.seg === active.seg)
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (view.length < 2) return
        e.preventDefault()
        const j = (i + (e.key === "ArrowDown" ? 1 : -1) + view.length) % view.length
        setFilter({ seg: view[j].seg })
        return
      }
      if (e.key === "Enter" && selected) {
        const block = active.blocks.find((b) => b.all.some((c) => c.id === selected.id))
        if (block) {
          e.preventDefault()
          setOpen({ chunk: block.chunk, id: selected.id })
        }
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, active, selected, open, keys])

  if (!workspace) return null

  const segBlocks = active
    ? active.blocks.filter((b) => b.all.length).map((b) => ({ chunk: b.chunk, takes: b.all }))
    : []
  const reviewBlock = open ? active?.blocks.find((b) => b.chunk === open.chunk) : null
  const wide = (active?.blocks.length ?? 0) > 4

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col bg-void">
      {/* ---------- 顶栏 ---------- */}
      <header className="flex flex-none items-center gap-3 border-b border-rule-soft px-4 py-2.5">
        <h1 className="text-[15px] font-semibold leading-none">审片台</h1>
        <span className="h-3.5 w-px bg-rule" />
        <span className="max-w-[240px] truncate text-[11.5px] text-ash-dim">
          {workspaceTitle || workspace}
        </span>
        <span className="flex-1" />
        {isFetching && data && (
          <span
            className="inline-flex h-6 items-center rounded-ctl border border-dashed border-rule px-2 text-[11px] text-ash-dim"
            title="先用上次的结果占位，扫盘结束会替换"
          >
            旧结果 · 更新中
          </span>
        )}
        <span className="font-mono text-[11px] text-ash-dim">
          {shown === cards.length ? `卡池 ${cards.length}` : `匹配 ${shown}/${cards.length}`}
        </span>
        <IconButton onClick={() => setKeys(true)} icon={<Keyboard className="size-3.5" />} title="快捷键" />
        <button
          type="button"
          onClick={() => scan.mutate()}
          disabled={scan.isPending}
          title="平时会自动发现新片（后台每 8 秒看一眼盘）；点这里 = 立刻强制重探，连旧片也重新探测时长/分辨率"
          className={cn(
            "inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12.5px] font-medium transition-all hover:bg-hi active:translate-y-px disabled:opacity-45",
            scan.isPending && "gb-sweep"
          )}
        >
          <RotateCw className={cn("size-3.5", scan.isPending && "animate-spin")} />
          {scan.isPending ? "扫描中…" : "重新扫描"}
        </button>
        <button
          type="button"
          onClick={doExport}
          disabled={exporting || !cards.length}
          title="按当前「状态 + 段」打包导出（关键词不参与）"
          className="inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12.5px] font-medium transition-all hover:bg-hi active:translate-y-px disabled:opacity-45"
        >
          <Download className="size-3.5" />
          {exporting ? "打包中…" : "打包导出"}
        </button>
      </header>

      {/* ---------- 筛选 ---------- */}
      <div className="flex flex-none flex-wrap items-center gap-2.5 border-b border-rule-soft px-4 py-2">
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
        <div className="relative w-[236px]">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-ash-dim" />
          <input
            value={filter.q}
            onChange={(e) => setFilter({ q: e.target.value })}
            placeholder="镜号 / 文件名 / 备注"
            className="h-8 w-full rounded-ctl border border-rule bg-panel pl-7 pr-2.5 text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
          />
        </div>
        {/* 窄屏没有左栏，段切换收进这个下拉 */}
        <select
          value={activeSeg ?? ""}
          onChange={(e) => {
            setFilter({ seg: e.target.value })
            setPickedId(null)
          }}
          aria-label="按段切换"
          className="hidden h-8 rounded-ctl border border-rule bg-panel px-2.5 text-[12.5px] outline-none focus:border-wash-line max-[860px]:block"
        >
          {view.map((r) => (
            <option key={r.seg || "_"} value={r.seg}>
              {r.seg ? `段 ${r.seg}` : "未分段"}
            </option>
          ))}
        </select>
        {filter.seg !== "all" && (
          <button
            type="button"
            onClick={() => setFilter({ seg: "all" })}
            className="inline-flex h-7 items-center gap-1 rounded-ctl border border-rule bg-cell px-2 text-[11.5px] text-ash transition-colors hover:bg-hi hover:text-paper"
          >
            只看段 {filter.seg}
            <X className="size-3" />
          </button>
        )}
        <span className="flex-1" />
        {!!cards.length && !shown && (
          <button
            type="button"
            onClick={() => setFilter({ status: "all", q: "" })}
            className="inline-flex h-7 items-center gap-1.5 rounded-ctl border border-dashed border-rule px-2 text-[11.5px] text-ash transition-colors hover:bg-cell hover:text-paper"
          >
            当前筛选没有命中 · 清空条件
          </button>
        )}
        <span className="text-[11px] text-ash-dim">{view.length} 段 · ↑↓ 换段 · Enter 审片</span>
      </div>

      {/* ---------- 三栏 ---------- */}
      <div className="flex min-h-0 flex-1">
        {/* 左：段 */}
        <aside className="flex w-[238px] flex-none flex-col border-r border-rule-soft max-[860px]:hidden">
          <div className="flex flex-none items-baseline justify-between px-3.5 pb-1.5 pt-3">
            <span className="text-[11.5px] text-ash">段</span>
            <span className="font-mono text-[10.5px] text-ash-dim">块 / 条</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
            {isPending && <p className="px-1.5 py-2 text-[12px] text-ash">读取卡池…</p>}
            {isError && (
              <p className="px-1.5 py-2 text-[11.5px] leading-relaxed text-ash">
                卡池读不出来：{(error as Error)?.message ?? "后端没有响应。"}
              </p>
            )}
            {!isPending &&
              !isError &&
              view.map((r) => {
                const on = r.seg === activeSeg
                return (
                  <button
                    key={r.seg || "_"}
                    type="button"
                    onClick={() => {
                      setFilter({ seg: r.seg })
                      setPickedId(null)
                    }}
                    aria-current={on || undefined}
                    className={cn(
                      "mb-0.5 flex w-full flex-col gap-1.5 rounded-cell border px-2.5 py-2 text-left transition-colors",
                      on
                        ? "border-wash-line bg-wash"
                        : "border-transparent hover:border-rule-soft hover:bg-cell"
                    )}
                  >
                    <span className="flex items-baseline gap-2">
                      <span
                        className={cn(
                          "font-mono text-[13.5px] leading-none",
                          on ? "text-paper" : "text-[#d4d4d4]",
                          r.hits === 0 && "opacity-55"
                        )}
                      >
                        {r.seg || "—"}
                      </span>
                      <span className="flex-1" />
                      <span className="font-mono text-[10.5px] text-ash-dim">{seconds(r.dur)}</span>
                    </span>
                    <span className="flex items-center gap-[3px]">
                      {r.blocks.map((b) => (
                        <i
                          key={b.chunk}
                          className={cn(
                            "block h-[10px] w-[8px] rounded-[1px]",
                            b.all.length
                              ? "bg-[#cfcfcf]"
                              : "border border-dashed border-[#5c5c5c] bg-transparent"
                          )}
                          title={`${chunkLabel(b.chunk)}：${b.all.length} 条`}
                        />
                      ))}
                      <span className="ml-1.5 font-mono text-[10.5px] text-ash-dim">
                        {filter.status === "all" && !filter.q ? r.total : `${r.hits}/${r.total}`}
                      </span>
                      {r.missing.length > 0 && (
                        <span className="ml-auto text-[10px] text-ash-dim">缺 {r.missing.length}</span>
                      )}
                    </span>
                  </button>
                )
              })}
          </div>
        </aside>

        {/* 中：当前段的块 × 候选 */}
        <section className="flex min-w-0 flex-1 flex-col">
          {!view.length ? (
            <div className="grid flex-1 place-items-center p-6">
              <div className="max-w-[52ch] rounded-panel border border-dashed border-rule bg-panel px-6 py-10 text-center">
                <h3 className="text-[15px] font-medium">
                  {cards.length ? "没有匹配的片段" : "这个工作区还没有片子"}
                </h3>
                <p className="mx-auto mt-2 text-[12.5px] leading-relaxed text-ash">
                  {cards.length ? (
                    "换个状态，或者把关键词清掉。左边的段是从卡池现读的，不会出现空段。"
                  ) : (
                    <>
                      把 mp4 放进 <span className="font-mono">草稿/</span> 或{" "}
                      <span className="font-mono">生成/</span>，这里会自己出现 —— 后台每几秒只看一次文件指纹，
                      有新片才登记。等不及就点右上角「重新扫描」立刻重探。扫描只登记，不动你的文件。
                    </>
                  )}
                </p>
              </div>
            </div>
          ) : (
            <>
              <div className="flex flex-none flex-wrap items-center gap-2 border-b border-rule-soft px-4 py-2.5">
                <span className="font-mono text-[16px] font-medium leading-none">
                  {active?.seg || "未分段"}
                </span>
                <Chip
                  tone={active?.missing.length ? "warn" : "ok"}
                  label={
                    active?.missing.length
                      ? `缺 ${active.missing.map(chunkLabel).join("、")}`
                      : `${active?.blocks.length ?? 0}/${active?.blocks.length ?? 0} 齐`
                  }
                />
                <Chip label={`${active?.total ?? 0} 条`} />
                <Chip label={`${active?.blocks.length ?? 0} 块 · ${seconds(active?.dur)}`} />
                <span className="flex-1" />
                <span className="text-[11px] text-ash-dim">双击格子进审片器 · ↑↓ 换段</span>
              </div>

              <div className="min-h-0 flex-1 overflow-auto p-3.5">
                <div
                  className={cn("grid items-start gap-3", wide && "w-max")}
                  style={{
                    gridTemplateColumns: `repeat(${active?.blocks.length ?? 1}, ${
                      wide ? "216px" : "minmax(0,1fr)"
                    })`,
                  }}
                >
                  {active?.blocks.map((b) => (
                    <div
                      key={b.chunk}
                      className="flex min-w-0 flex-col gap-2 rounded-panel border border-rule-soft bg-panel p-2"
                    >
                      <div className="flex items-baseline gap-2 px-0.5">
                        <span className="font-mono text-[11.5px] text-paper">{chunkLabel(b.chunk)}</span>
                        <span className="font-mono text-[10.5px] text-ash-dim">
                          {seconds(b.all[0]?.duration)}
                        </span>
                        <span className="flex-1" />
                        <span
                          className={cn(
                            "font-mono text-[10.5px]",
                            b.takes.length ? "text-ash" : "text-ash-dim"
                          )}
                        >
                          {b.takes.length}/{b.all.length}
                        </span>
                      </div>

                      {b.takes.length ? (
                        b.takes.map((t, i) => (
                          <div key={t.id} onDoubleClick={() => setOpen({ chunk: b.chunk, id: t.id })}>
                            <FilmCell
                              card={t}
                              variant="take"
                              index={i + 1}
                              shots={t.tags}
                              active={t.id === selected?.id}
                              onClick={() => setPickedId(t.id)}
                            />
                          </div>
                        ))
                      ) : (
                        <div className="grid aspect-video place-items-center rounded-cell border border-dashed border-rule">
                          <span className="text-[11px] text-ash-dim">
                            {b.all.length
                              ? `无${filter.status === "all" ? "候选" : STATUS_LABEL[filter.status as CardStatus]}`
                              : "还没跑"}
                          </span>
                        </div>
                      )}                    </div>
                  ))}
                </div>
              </div>

              {/* 拼接轨：栏宽与上面的块一一对齐，读成「这几块拼回整段」 */}
              <div className="flex-none border-t border-rule-soft px-4 pb-2 pt-2.5">
                <div
                  className={cn("grid items-center gap-3", wide && "w-max")}
                  style={{
                    gridTemplateColumns: `repeat(${active?.blocks.length ?? 1}, ${
                      wide ? "216px" : "minmax(0,1fr)"
                    })`,
                  }}
                >
                  {active?.blocks.map((b) => (
                    <span
                      key={b.chunk}
                      className={cn(
                        "h-[3px] rounded-full",
                        b.all.length ? "bg-[#d1d1d1]" : "border-t border-dashed border-[#3f3f3f]"
                      )}
                    />
                  ))}
                </div>
                <div className="mt-1.5 flex items-baseline justify-between">
                  <span className="text-[10.5px] text-ash-dim">
                    {active?.blocks.length ?? 0} 块 · 每块 5s
                  </span>
                  <span className="font-mono text-[10.5px] text-ash">
                    {active?.missing.length ? "缺块，拼不回整段" : `拼回 ${seconds(active?.dur)}`}
                  </span>
                </div>
              </div>
            </>
          )}
        </section>

        {/* 右：这一条 */}
        <aside className="flex w-[324px] flex-none flex-col overflow-y-auto border-l border-rule-soft bg-panel max-[1180px]:hidden">
          {selected ? (
            <Detail
              card={selected}
              rejectTags={rejectTags}
              onOpen={() => {
                const b = active?.blocks.find((x) => x.all.some((c) => c.id === selected.id))
                if (b) setOpen({ chunk: b.chunk, id: selected.id })
              }}
              onFinal={() => toFinal.mutate(selected.id)}
              onReject={() => patch.mutate({ id: selected.id, body: { status: "reject" } })}
              onDraft={() => patch.mutate({ id: selected.id, body: { status: "draft" } })}
              onNote={(note) => patch.mutate({ id: selected.id, body: { note } })}
              busy={patch.isPending || toFinal.isPending}
            />
          ) : (
            <p className="p-4 text-[12px] leading-relaxed text-ash">
              这一段没有匹配的候选。换个状态，或者清掉关键词。
            </p>
          )}
        </aside>
      </div>

      {open && reviewBlock && active && (
        <Reviewer
          key={`${active.seg}/${open.chunk}`}
          seg={active.seg}
          chunk={open.chunk}
          takes={reviewBlock.all}
          blocks={segBlocks}
          initialId={open.id}
          shotTags={reviewBlock.all[0].tags}
          rejectTags={rejectTags}
          onClose={() => setOpen(null)}
          onJump={(chunk) =>
            setOpen({
              chunk,
              id: active.blocks.find((b) => b.chunk === chunk)?.all[0]?.id ?? 0,
            })
          }
        />
      )}

      {keys && <KeysSheet onClose={() => setKeys(false)} />}
    </div>
  )
}

/* ------------------------------------------------------------------ */

function Detail({
  card,
  rejectTags,
  onOpen,
  onFinal,
  onReject,
  onDraft,
  onNote,
  busy,
}: {
  card: Card
  rejectTags: string[]
  onOpen: () => void
  onFinal: () => void
  onReject: () => void
  onDraft: () => void
  onNote: (note: string) => void
  busy: boolean
}) {
  return (
    <>
      <div className="flex flex-none items-center gap-2 border-b border-rule-soft px-3.5 py-2.5">
        <span className="font-mono text-[11.5px] text-ash-dim">#{card.id}</span>
        <span
          className={cn(
            "inline-flex items-center rounded border px-1.5 py-0.5 text-[11px]",
            card.status === "final"
              ? "border-wash-line bg-wash text-paper"
              : card.status === "reject"
                ? "border-dashed border-[#6e6e6e] text-[#c9c9c9]"
                : "border-rule text-ash"
          )}
        >
          {STATUS_LABEL[card.status]}
        </span>
        {card.missing && (
          <span
            title="磁盘上找不到这个文件（被改名 / 移动 / 删掉）"
            className="inline-flex items-center gap-1 rounded border border-dashed border-[#6e6e6e] px-1.5 py-0.5 text-[11px] text-ash-dim"
          >
            <FileX className="size-3" />
            文件缺失
          </span>
        )}
        <span className="flex-1" />
        <button
          type="button"
          onClick={onOpen}
          title="进审片器（全屏对比、逐帧、改理由）"
          className="inline-flex h-7 items-center gap-1 rounded-ctl border border-rule bg-cell px-2 text-[11.5px] text-ash transition-colors hover:bg-hi hover:text-paper"
        >
          <Maximize2 className="size-3" />
          审片器
        </button>
      </div>

      <div className="flex-none border-b border-rule-soft p-3.5">
        <Preview card={card} />
        <div className="mt-2 flex items-baseline gap-2">
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ash-dim">
            {fileName(card.rel_path)}
          </span>
          <span className="flex-none font-mono text-[11px] text-ash">{seconds(card.duration)}</span>
        </div>
      </div>

      <div className="flex-none border-b border-rule-soft p-3.5">
        <h3 className="mb-2 text-[11.5px] text-ash">这一条</h3>
        <dl className="grid grid-cols-[58px_1fr] gap-y-1.5 text-[11.5px]">
          <dt className="text-ash-dim">镜号</dt>
          <dd className="min-w-0">
            {card.tags.length ? (
              <span className="flex flex-wrap gap-1">
                {card.tags.map((t) => (
                  <span key={t} className="rounded border border-rule px-1.5 py-px font-mono text-[10.5px] text-ash">
                    {t}
                  </span>
                ))}
              </span>
            ) : (
              <span className="text-ash-dim">没打标签</span>
            )}
          </dd>
          <dt className="text-ash-dim">尺寸</dt>
          <dd className="font-mono text-ash">
            {card.width && card.height ? `${card.width}×${card.height}` : "—"}
          </dd>
          <dt className="text-ash-dim">音频</dt>
          <dd className="text-ash">{card.has_audio ? "有" : "无"}</dd>
          <dt className="text-ash-dim">更新</dt>
          <dd className="font-mono text-ash">{shortStamp(card.updated_at || card.created_at)}</dd>
          <dt className="text-ash-dim">废稿因</dt>
          <dd className="text-ash">
            {card.reject_tags.length ? card.reject_tags.join("、") : <span className="text-ash-dim">—</span>}
          </dd>
        </dl>
      </div>

      <div className="flex-none border-b border-rule-soft p-3.5">
        <button
          type="button"
          onClick={onFinal}
          disabled={busy || card.status === "final"}
          className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-ctl bg-paper text-[13px] font-medium text-[#131313] transition-transform hover:bg-white active:translate-y-px active:scale-[0.99] disabled:opacity-40"
        >
          <Check className="size-4" strokeWidth={2.4} />
          {card.status === "final" ? "已是正片" : "标为正片"}
        </button>
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            onClick={onReject}
            disabled={busy || card.status === "reject"}
            className="inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-ctl border border-[#5c5c5c] text-[12.5px] text-[#c9c9c9] transition-colors hover:border-[#828282] hover:bg-wash active:translate-y-px disabled:opacity-40"
          >
            <X className="size-3.5" />
            废稿
          </button>
          <button
            type="button"
            onClick={onDraft}
            disabled={busy || card.status === "draft"}
            className="inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-ctl border border-rule bg-cell text-[12.5px] text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px disabled:opacity-40"
          >
            <Undo2 className="size-3.5" />
            退回草稿
          </button>
        </div>
        <p className="mt-2 text-[10.5px] leading-relaxed text-ash-dim">
          废稿原因在审片器里选（{rejectTags.slice(0, 3).join(" / ") || "标签"}…）。
        </p>
      </div>

      <div className="flex-none border-b border-rule-soft p-3.5">
        <h3 className="mb-2 text-[11.5px] text-ash">备注</h3>
        <textarea
          key={card.id}
          defaultValue={card.note}
          rows={2}
          placeholder="这条想记住什么"
          onBlur={(e) => {
            if (e.target.value !== card.note) onNote(e.target.value)
          }}
          className="w-full resize-y rounded-ctl border border-rule bg-cell px-2.5 py-2 text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
        />
      </div>

      <div className="flex-none p-3.5">
        <button
          type="button"
          onClick={async () => {
            const r = await exportItems([{ rel_path: card.rel_path }])
            if (!r.ok) {
              if (!r.message.includes("取消")) toast.error(r.message)
              return
            }
            toast.success("已导出这一条", { description: r.path ?? r.message })
          }}
          className="inline-flex h-8 w-full items-center justify-center gap-2 rounded-ctl border border-rule bg-cell text-[12.5px] text-paper transition-colors hover:bg-hi active:translate-y-px"
        >
          <Download className="size-3.5" />
          导出这一条
        </button>
        <p className="mt-2 break-all font-mono text-[10.5px] leading-relaxed text-ash-dim">
          {card.rel_path}
        </p>
      </div>
    </>
  )
}

function Preview({ card }: { card: Card }) {
  const ref = useRef<HTMLVideoElement>(null)
  const [playing, setPlaying] = useState(false)

  // 文件已经不在盘上：给个占位，别让人对着一个永远黑的播放器点半天
  if (card.missing) {
    return (
      <div className="relative overflow-hidden rounded-cell bg-[#0c0c0c] outline-1 outline-rule">
        <div className="grid aspect-video w-full place-items-center">
          <div className="flex max-w-[26ch] flex-col items-center gap-1.5 px-3 text-center">
            <FileX className="size-5 text-ash-dim" strokeWidth={1.5} />
            <span className="text-[11.5px] text-ash">文件已不在盘上</span>
            <span className="text-[10.5px] leading-relaxed text-ash-dim">
              可能被改名或移动了。重新扫描后，新路径下会另立一张卡。
            </span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="relative overflow-hidden rounded-cell bg-black outline-1 outline-rule">
      <video
        key={card.id}
        ref={ref}
        src={`/api/media/${card.id}`}
        muted
        loop
        playsInline
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onClick={() => {
          const v = ref.current
          if (!v) return
          if (v.paused) v.play().catch(() => {})
          else v.pause()
        }}
        className="block aspect-video w-full cursor-pointer object-contain"
      />
      {!playing && (
        <span className="pointer-events-none absolute inset-0 grid place-items-center">
          <span className="grid size-9 place-items-center rounded-full border border-white/50 bg-black/50">
            <Play className="ml-0.5 size-4 fill-white text-white" />
          </span>
        </span>
      )}
      <span className="pointer-events-none absolute right-2 top-2 rounded bg-black/70 px-1.5 py-px font-mono text-[10px] text-[#c8c8c8]">
        {card.chunk ? chunkLabel(card.chunk) : "—"}
      </span>
    </div>
  )
}

function Chip({ label, tone = "dim" }: { label: string; tone?: "dim" | "ok" | "warn" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px]",
        tone === "ok"
          ? "border-wash-line bg-wash text-paper"
          : tone === "warn"
            ? "border-dashed border-rule text-ash-dim"
            : "border-rule text-ash"
      )}
    >
      {label}
    </span>
  )
}

function IconButton({
  onClick,
  icon,
  title,
}: {
  onClick: () => void
  icon: React.ReactNode
  title: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      className="grid size-8 place-items-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper"
    >
      {icon}
    </button>
  )
}

function KeysSheet({ onClose }: { onClose: () => void }) {
  const rows: [string, string][] = [
    ["↑ ↓", "换段"],
    ["Enter", "进审片器"],
    ["双击格子", "进审片器"],
    ["1 / 2 / 3", "审片器内：正片 / 废稿 / 退回草稿"],
    ["← →", "审片器内：换同一块的候选"],
    ["空格", "审片器内：播放 / 暂停"],
    ["Esc", "退出审片器"],
  ]
  return (
    <div
      className="fixed inset-0 z-80 grid place-items-center bg-black/65"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="w-[min(540px,92vw)] rounded-panel border border-rule bg-panel p-5">
        <h2 className="mb-3.5 text-[14px] font-medium">审片台快捷键</h2>
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
