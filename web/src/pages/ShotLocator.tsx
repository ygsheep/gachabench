import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  FileText,
  ListTree,
  Package,
  Type,
} from "lucide-react"
import { cn } from "cn"
import { api } from "@/lib/api"
import { exportItems, exportRange } from "@/lib/download"
import {
  DEFAULT_READER,
  READER_RANGES,
  loadPrefs,
  pushRecent,
  savePrefs,
  type ReaderPrefs,
} from "@/lib/prefs"
import { useApp } from "@/store/useApp"
import type { LocateResult, SegmentPrompt, SegmentsResp, TicketResp } from "@/lib/types"

/** `[Shot N]` 开头的行；NBE 那边是目录，这里换成 §④ 代码块里的镜块标记 */
const SHOT_HEAD = /^\[Shot\s+(\d+)\]/

export function ShotLocator() {
  const seg = useApp((s) => s.seg)
  const setSeg = useApp((s) => s.setSeg)
  const [tags, setTags] = useState("")
  const [comment, setComment] = useState("")
  const [ticket, setTicket] = useState("")

  /* 阅读面板：排版变量 + 镜表抽屉 + 当前镜（都在本页内部，不进全局 store） */
  const [reader, setReader] = useState<ReaderPrefs>(() => loadPrefs().reader)
  const [typoOpen, setTypoOpen] = useState(false)
  const [tocOpen, setTocOpen] = useState(false)
  // 按段范围导出提示词 md（C4）；to 留 0 当「到最后一段」
  const [expOpen, setExpOpen] = useState(false)
  const [expFrom, setExpFrom] = useState(0)
  const [expTo, setExpTo] = useState(0)
  const [exporting, setExporting] = useState(false)
  const [curShot, setCurShot] = useState<number | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)

  const segsQ = useQuery({
    queryKey: ["segments"],
    queryFn: () => api.get<SegmentsResp>("/api/segments"),
    retry: 1,
  })
  const segments = segsQ.data?.segments ?? []
  const cur = seg || segments[0] || ""

  const promptQ = useQuery({
    queryKey: ["prompt", cur],
    queryFn: () => api.get<SegmentPrompt>(`/api/segments/${cur}/prompt`),
    enabled: !!cur,
    retry: 1,
  })

  const locQ = useQuery({
    queryKey: ["locate", cur, tags],
    queryFn: () =>
      api.get<LocateResult>(`/api/segments/${cur}/locate?tags=${encodeURIComponent(tags)}`),
    enabled: !!cur && !!tags.trim(),
    retry: false,
  })

  const hits = useMemo(() => locQ.data?.hits ?? [], [locQ.data])

  /* 最近打开的段（P3）：切一次记一次，新的排最前。
     🔴 这是历史不是会话状态 —— 不受 keepOnReload 那个开关管。 */
  const [recent, setRecent] = useState<string[]>(() => loadPrefs().recent.segs)
  useEffect(() => {
    if (!cur) return
    setRecent((prev) => {
      const next = pushRecent(prev, cur)
      savePrefs({ recent: { segs: next } })
      return next
    })
  }, [cur])

  const code = useMemo(() => {
    const lines = (promptQ.data?.codeblock ?? "").split("\n")
    // 用第一个命中块把「代码块内行号」对齐到「文件绝对行号」
    let offset = 0
    const first = hits[0]
    if (first) {
      const rel = lines.findIndex((l) => l.startsWith(`[Shot ${first.index}]`))
      if (rel >= 0) offset = first.start_line - rel
    }
    const marks = new Set<number>()
    if (offset) {
      for (const h of hits) {
        for (let n = h.start_line; n <= h.end_line; n++) marks.add(n - offset)
      }
    }
    return lines.map((text, i) => ({ n: i + offset, text, hit: marks.has(i) }))
  }, [promptQ.data?.codeblock, hits])

  /** 镜表：从正文里认 `[Shot N]` 行，记下它所在的**绝对行号**供跳转。 */
  const shots = useMemo(() => {
    const out: { index: number; line: number; label: string }[] = []
    for (const l of code) {
      const m = SHOT_HEAD.exec(l.text)
      if (m) {
        out.push({
          index: Number(m[1]),
          line: l.n,
          label: l.text.slice(m[0].length).trim().slice(0, 30) || "（空镜）",
        })
      }
    }
    return out
  }, [code])

  /** 当前镜覆盖的行区间（到下一镜头行为止），给正文打底 */
  const curRange = useMemo(() => {
    if (curShot === null || !shots.length) return null
    const k = Math.min(curShot, shots.length - 1)
    const end = k + 1 < shots.length ? shots[k + 1].line - 1 : Infinity
    return { from: shots[k].line, to: end }
  }, [curShot, shots])

  const scrollToLine = useCallback((n: number) => {
    const box = boxRef.current
    const el = box?.querySelector<HTMLElement>(`[data-line="${n}"]`)
    if (!box || !el) return
    // box 是 relative，行的 offsetTop 就是它内部的滚动偏移
    box.scrollTop = Math.max(0, el.offsetTop - 8)
  }, [])

  const gotoShot = useCallback(
    (i: number) => {
      if (!shots.length) return
      const k = Math.min(shots.length - 1, Math.max(0, i))
      setCurShot(k)
      scrollToLine(shots[k].line)
    },
    [shots, scrollToLine]
  )

  /* ←/→ 跳镜。输入框里与审片器开着时不抢方向键。 */
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return
      const t = e.target as HTMLElement | null
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.tagName === "SELECT" ||
          t.isContentEditable)
      ) {
        return
      }
      if (document.querySelector('[role="dialog"]')) return
      if (!shots.length) return
      e.preventDefault()
      gotoShot(curShot === null ? 0 : curShot + (e.key === "ArrowRight" ? 1 : -1))
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [shots, curShot, gotoShot])

  // 换段 → 镜表整个换掉，当前镜归位
  useEffect(() => {
    setCurShot(null)
  }, [cur])

  function patchReader(patch: Partial<ReaderPrefs>) {
    const next = { ...reader, ...patch }
    setReader(next)
    savePrefs({ reader: next })
  }

  const makeTicket = useMutation({
    mutationFn: () =>
      api.post<TicketResp>("/api/ticket", { seg: cur, tags, comment }),
    onSuccess: (r) => {
      setTicket(r.ticket)
      toast.success("工单已生成", {
        description: hits.map((h) => `SH${cur}${String(h.index).padStart(2, "0")}`).join(" · "),
      })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const err = locQ.isError ? ((locQ.error as Error)?.message ?? "") : ""

  /** 工单文件名：镜号串里可能有 `,` `#` `-`，只留能进文件名的字符 */
  const ticketName = useMemo(() => {
    const slug = (tags.trim() || "工单").replace(/[^\w一-龥-]+/g, "_").slice(0, 40)
    return `工单_段${cur || "?"}_${slug}.txt`
  }, [tags, cur])

  async function doExport() {
    if (!ticket) return
    const r = await exportItems([{ text: ticket, filename: ticketName }])
    if (!r.ok) {
      if (!r.message.includes("取消")) toast.error(r.message)
      return
    }
    toast.success("工单已导出", { description: r.path ?? r.message })
  }

  /** 按段范围导出提示词 md：每段一个文件，打包时放进「提示词/」子目录。 */
  async function doExportPrompts() {
    const nums = segments.map((s) => Number(s)).filter((n) => Number.isFinite(n))
    if (!nums.length) return
    const first = Math.min(...nums)
    const last = Math.max(...nums)
    const a = Math.max(first, expFrom || first)
    const b = Math.min(last, expTo || last)
    setExporting(true)
    try {
      const r = await exportRange(
        { what: "prompts", from: a, to: b },
        { zipName: `提示词_第${a}-${b}段` }
      )
      if (!r.ok) {
        if (!r.message.includes("取消")) toast.error(r.message)
        return
      }
      toast.success("已导出", { description: r.message })
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-end gap-x-3.5 gap-y-2 px-6 pb-3.5 pt-5">
        <div className="max-w-[82ch]">
          <h1 className="text-[19px] font-semibold leading-tight">定位工单</h1>
          <p className="pt-0.5 text-[12.5px] text-ash">
            写镜号，定位到提示词原文那一行，生成一段能直接交给外部 Agent 的改动工单。
          </p>
        </div>
      </header>

      <div className="grid items-start gap-3.5 px-6 pb-11 min-[1180px]:grid-cols-[minmax(0,1fr)_424px]">
        <section className="min-w-0 rounded-panel border border-rule-soft bg-panel p-4">
          <div className="mb-3.5 flex flex-wrap items-center gap-2">
            <select
              value={cur}
              onChange={(e) => {
                setSeg(e.target.value)
                setTags("")
                setTicket("")
              }}
              aria-label="段号"
              className="h-8 rounded-ctl border border-rule bg-cell px-2.5 font-mono text-[12.5px] outline-none focus:border-wash-line"
            >
              {!segments.length && <option value="">无段</option>}
              {segments.map((s) => (
                <option key={s} value={s}>
                  段 {s}
                </option>
              ))}
            </select>
            <div className="relative min-w-[240px] flex-1">
              <FileText className="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-ash-dim" />
              <input
                value={tags}
                onChange={(e) => {
                  setTags(e.target.value)
                  setTicket("")
                }}
                placeholder="SH402 / SH402,SH403 / #13-#14"
                className="h-8 w-full rounded-ctl border border-rule bg-cell pl-7 pr-2.5 text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
              />
            </div>

            {/* 阅读面板的开关组：排版 / 镜表 / 上一镜 · 下一镜 */}
            <button
              type="button"
              onClick={() => setTypoOpen((v) => !v)}
              aria-pressed={typoOpen}
              title="调整正文字号 / 行距 / 字距 / 段距 / 列宽（会记住）"
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] transition-colors hover:bg-hi",
                typoOpen && "border-wash-line bg-hi text-paper"
              )}
            >
              <Type className="size-3.5" />
              排版
            </button>
            <button
              type="button"
              onClick={() => setTocOpen((v) => !v)}
              aria-pressed={tocOpen}
              title="镜表目录：点一条跳到那一镜"
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] transition-colors hover:bg-hi",
                tocOpen && "border-wash-line bg-hi text-paper"
              )}
            >
              <ListTree className="size-3.5" />
              镜表
            </button>
            <span className="flex items-center">
              <button
                type="button"
                onClick={() => gotoShot((curShot ?? 0) - 1)}
                disabled={!shots.length}
                title="上一镜（←）"
                aria-label="上一镜"
                className="inline-flex h-8 w-8 items-center justify-center rounded-l-ctl border border-rule bg-cell transition-colors hover:bg-hi disabled:opacity-45"
              >
                <ChevronLeft className="size-3.5" />
              </button>
              <button
                type="button"
                onClick={() => gotoShot((curShot ?? -1) + 1)}
                disabled={!shots.length}
                title="下一镜（→）"
                aria-label="下一镜"
                className="-ml-px inline-flex h-8 w-8 items-center justify-center rounded-r-ctl border border-rule bg-cell transition-colors hover:bg-hi disabled:opacity-45"
              >
                <ChevronRight className="size-3.5" />
              </button>
            </span>
            <button
              type="button"
              onClick={() => setExpOpen((v) => !v)}
              aria-pressed={expOpen}
              title="按段范围导出提示词 md"
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] transition-colors hover:bg-hi",
                expOpen && "border-wash-line bg-hi text-paper"
              )}
            >
              <Package className="size-3.5" />
              打包
            </button>
            <span className="text-[11px] text-ash-dim">
              {shots.length ? `←/→ 跳镜（共 ${shots.length} 镜）` : "这一段的正文里没有 [Shot N] 标记"}
            </span>
          </div>

          {recent.filter((s) => s !== cur).length > 0 && (
            <div className="mb-3 flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] text-ash-dim">最近</span>
              {recent
                .filter((s) => s !== cur)
                .slice(0, 6)
                .map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => {
                      setSeg(s)
                      setTags("")
                      setTicket("")
                    }}
                    className="gb-chip h-6 px-2 font-mono text-[11px] text-ash transition-colors hover:bg-hi hover:text-paper"
                  >
                    段 {s}
                  </button>
                ))}
            </div>
          )}

          {expOpen && (
            <div className="mb-3 flex flex-wrap items-center gap-2 rounded-ctl border border-rule-soft bg-void px-3 py-2.5">
              <span className="text-[12px] font-medium">导出提示词</span>
              <span className="flex items-center gap-1.5">
                <span className="text-[11px] text-ash-dim">第</span>
                <input
                  type="number"
                  min={1}
                  value={expFrom || ""}
                  placeholder={segments[0] ?? "1"}
                  onChange={(e) => setExpFrom(Number(e.target.value) || 0)}
                  aria-label="导出起始段"
                  className="h-7 w-14 rounded-ctl border border-rule bg-cell px-1.5 text-center font-mono text-[12px] outline-none focus:border-wash-line"
                />
                <span className="text-[11px] text-ash-dim">–</span>
                <input
                  type="number"
                  min={0}
                  value={expTo || ""}
                  placeholder={segments[segments.length - 1] ?? "1"}
                  onChange={(e) => setExpTo(Number(e.target.value) || 0)}
                  aria-label="导出结束段"
                  title="留空 = 到最后一段"
                  className="h-7 w-14 rounded-ctl border border-rule bg-cell px-1.5 text-center font-mono text-[12px] outline-none focus:border-wash-line"
                />
                <span className="text-[11px] text-ash-dim">段</span>
              </span>
              <button
                type="button"
                onClick={doExportPrompts}
                disabled={exporting || !segments.length}
                className="inline-flex h-7 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] transition-colors hover:bg-hi active:translate-y-px disabled:opacity-45"
              >
                <Download className="size-3" />
                {exporting ? "打包中…" : "打包下载"}
              </button>
              <span className="text-[11px] text-ash-dim">
                每段一个 md，zip 里放在 <span className="font-mono">提示词/</span> 下；一次最多 200 个。
              </span>
            </div>
          )}

          {typoOpen && (
            <div className="mb-3 grid gap-1.5 rounded-ctl border border-rule-soft bg-void px-3 py-2.5 sm:grid-cols-2">
              {(Object.keys(READER_RANGES) as (keyof ReaderPrefs)[]).map((k) => {
                const r = READER_RANGES[k]
                const label =
                  k === "fontSize"
                    ? "字号"
                    : k === "lineHeight"
                      ? "行距"
                      : k === "letterSpacing"
                        ? "字距"
                        : k === "paragraphGap"
                          ? "段距"
                          : "列宽"
                return (
                  <span key={k} className="flex items-center gap-2.5">
                    <span className="w-8 shrink-0 text-[11px] text-ash">{label}</span>
                    <input
                      type="range"
                      min={r.min}
                      max={r.max}
                      step={r.step}
                      value={reader[k]}
                      onChange={(e) => patchReader({ [k]: Number(e.target.value) } as Partial<ReaderPrefs>)}
                      aria-label={label}
                      className="h-1 flex-1 accent-white"
                    />
                    <span className="w-14 shrink-0 text-right font-mono text-[11px] text-paper">
                      {k === "contentWidth" && reader[k] === 0 ? "不限" : reader[k]}
                    </span>
                  </span>
                )
              })}
              <span className="flex items-center justify-between sm:col-span-2">
                <span className="text-[11px] text-ash-dim">
                  只改列宽与行距；行号沟槽跟着走，第几行还是第几行。
                </span>
                <button
                  type="button"
                  onClick={() => patchReader({ ...DEFAULT_READER })}
                  className="text-[11px] text-ash underline decoration-dotted underline-offset-2 hover:text-paper"
                >
                  复位
                </button>
              </span>
            </div>
          )}

          <p className="mb-3 text-[12px] leading-relaxed text-ash">
            {err ? (
              <span>{err}</span>
            ) : hits.length ? (
              <>
                命中{" "}
                <span className="font-mono text-paper">
                  {hits.map((h) => `SH${cur}${String(h.index).padStart(2, "0")}`).join("、")}
                </span>
                ，位置在 <span className="font-mono">{promptQ.data?.file ?? "—"}</span> 第{" "}
                <span className="font-mono text-paper">
                  {hits[0].start_line}–{hits[hits.length - 1].end_line}
                </span>{" "}
                行。
              </>
            ) : (
              <>
                没认出镜号。写成 <span className="font-mono">SH402</span> 或直接写{" "}
                <span className="font-mono">402</span> 都行。
                <span className="text-ash-dim">
                  {" "}
                  下面的编号是 §④ 代码块内的相对行号；命中之后会换成文件绝对行号。
                </span>
              </>
            )}
          </p>

          <div className="relative">
            <div
              ref={boxRef}
              className="relative max-h-[62vh] overflow-auto rounded-panel border border-rule-soft bg-void p-3.5"
            >
              {promptQ.isPending && <p className="text-[12px] text-ash">读取提示词…</p>}
              {promptQ.isError && (
                <p className="text-[12px] text-ash">
                  {(promptQ.error as Error)?.message ?? "这个段没找到提示词 md。"}
                </p>
              )}
              {!!code.length && (
                <pre
                  className="m-0 font-mono whitespace-pre text-[#c6c6c6]"
                  style={{
                    fontSize: reader.fontSize,
                    lineHeight: reader.lineHeight,
                    letterSpacing: reader.letterSpacing || undefined,
                    maxWidth: reader.contentWidth || undefined,
                  }}
                >
                  {code.map((l) => {
                    const blank = !l.text.trim()
                    const inCur = !!curRange && l.n >= curRange.from && l.n <= curRange.to
                    return (
                      <span
                        key={l.n}
                        data-line={l.n}
                        className={cn(
                          "block",
                          l.hit && "bg-wash shadow-[inset_2px_0_0_#fff]",
                          inCur && !l.hit && "bg-hi"
                        )}
                        style={blank ? { marginBottom: reader.paragraphGap || undefined } : undefined}
                      >
                        <span className="select-none pr-3.5 text-[#555]">
                          {String(l.n).padStart(4, "0")}
                        </span>
                        {l.text}
                      </span>
                    )
                  })}
                </pre>
              )}
            </div>

            {tocOpen && (
              <aside className="absolute right-0 top-0 z-30 flex max-h-[62vh] w-[276px] flex-col overflow-hidden rounded-panel border border-rule bg-panel">
                <div className="flex items-center justify-between border-b border-rule-soft px-3 py-2">
                  <span className="text-[12px] font-medium">镜表目录</span>
                  <span className="font-mono text-[10.5px] text-ash-dim">{shots.length} 镜</span>
                </div>
                <div className="min-h-0 flex-1 overflow-auto p-1.5">
                  {!shots.length && (
                    <p className="px-1.5 py-2 text-[11.5px] text-ash-dim">
                      这一段没有镜块标记，先看正文。
                    </p>
                  )}
                  {shots.map((s, i) => (
                    <button
                      key={s.line}
                      type="button"
                      onClick={() => gotoShot(i)}
                      className={cn(
                        "block w-full rounded-ctl px-2 py-1.5 text-left transition-colors hover:bg-hi",
                        curShot === i && "bg-hi"
                      )}
                    >
                      <span className="flex items-baseline gap-2">
                        <span
                          className={cn(
                            "font-mono text-[11px]",
                            curShot === i ? "text-paper" : "text-ash"
                          )}
                        >
                          SH{cur}
                          {String(s.index).padStart(2, "0")}
                        </span>
                        <span className="ml-auto font-mono text-[10px] text-ash-dim">
                          {String(s.line).padStart(4, "0")}
                        </span>
                      </span>
                      <span
                        className={cn(
                          "mt-0.5 block truncate text-[11.5px]",
                          curShot === i ? "text-paper" : "text-ash"
                        )}
                      >
                        {s.label}
                      </span>
                    </button>
                  ))}
                </div>
              </aside>
            )}
          </div>
        </section>

        <section className="min-w-0 rounded-panel border border-rule-soft bg-panel p-4">
          <h2 className="text-[13.5px] font-medium">工单</h2>
          <p className="mb-3.5 mt-1 text-[12px] leading-relaxed text-ash">
            命中镜块的原文会自动带进来，你只写意见。
          </p>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={4}
            placeholder="比如：这里的动作太快，改成先顿一下再转身"
            className="w-full resize-y rounded-ctl border border-rule bg-cell px-2.5 py-2 text-[12.5px] leading-relaxed outline-none placeholder:text-ash-dim focus:border-wash-line"
          />
          <div className="mt-2.5 flex flex-wrap items-center gap-2.5">
            <button
              type="button"
              onClick={() => makeTicket.mutate()}
              disabled={!hits.length || makeTicket.isPending}
              className={cn(
                "inline-flex h-8 items-center rounded-ctl bg-paper px-3 text-[12.5px] font-medium text-[#131313] transition-all hover:bg-white active:translate-y-px disabled:opacity-45",
                makeTicket.isPending && "gb-sweep"
              )}
            >
              {makeTicket.isPending ? "生成中…" : "生成工单"}
            </button>
            <button
              type="button"
              disabled={!ticket}
              onClick={() => {
                navigator.clipboard.writeText(ticket).then(
                  () => toast.success("工单已复制", { description: "直接粘给外部 Agent 就行" }),
                  () => toast.error("复制被浏览器拦了", { description: "手动选中下面的文本也可以" })
                )
              }}
              className="inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12.5px] transition-colors hover:bg-hi disabled:opacity-45"
            >
              <Copy className="size-3.5" />
              复制工单
            </button>
            <button
              type="button"
              disabled={!ticket}
              onClick={doExport}
              title="存成 txt：浏览器里直接下载，桌面版先问导出到哪个目录"
              className="inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12.5px] transition-colors hover:bg-hi disabled:opacity-45"
            >
              <Download className="size-3.5" />
              导出工单
            </button>
          </div>
          <pre className="mt-2.5 max-h-[360px] overflow-auto whitespace-pre-wrap rounded-ctl border border-rule-soft bg-void p-3 font-mono text-[11px] leading-[1.75] text-[#c6c6c6]">
            {ticket ||
              (hits.length
                ? "还没生成工单。点上面的「生成工单」。"
                : "还没有命中镜号。写一个镜号，原文那段会自己带进来。")}
          </pre>
        </section>
      </div>
    </div>
  )
}
