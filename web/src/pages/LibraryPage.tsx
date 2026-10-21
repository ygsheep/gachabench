import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { BookOpen, Copy, Download, Scissors, Trash2, Upload } from "lucide-react"
import { cn } from "cn"
import { api } from "@/lib/api"
import { exportItems, exportRange } from "@/lib/download"
import type {
  Book,
  BookDetail,
  BooksResp,
  ChapterResp,
  ExcerptResp,
} from "@/lib/types"

/** 两步确认的收回时间（与 TaskBar 一致） */
const ARM_MS = 4000

/**
 * 原著库（C2）：不联网，手动把 txt / md 收进工作区，按章节落盘 + 建索引。
 *
 * 🔴 边界说清楚：这里**只做摘录，不做改编**。生成 §①–§⑥ 结构化提示词是
 *    外部 Agent / LLM 的活 —— 摘录按钮旁必须写明，否则用户会以为点了就能出 §④。
 */
export function LibraryPage() {
  const qc = useQueryClient()
  const [openId, setOpenId] = useState<number | null>(null)
  const [chapterId, setChapterId] = useState<number | null>(null)
  const [from, setFrom] = useState(1)
  const [to, setTo] = useState(0)
  const [excerpt, setExcerpt] = useState<ExcerptResp | null>(null)
  const [title, setTitle] = useState("")
  const [paste, setPaste] = useState("")
  const [picked, setPicked] = useState<string | null>(null)
  const [armed, setArmed] = useState<number | null>(null)
  // 按章范围导出（C4）：to 留 0 就当「到最后一章」
  const [expFrom, setExpFrom] = useState(1)
  const [expTo, setExpTo] = useState(0)
  const [exporting, setExporting] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const booksQ = useQuery({
    queryKey: ["books"],
    queryFn: () => api.get<BooksResp>("/api/library/books"),
    retry: 1,
  })
  const books: Book[] = booksQ.data?.books ?? []

  const bookQ = useQuery({
    queryKey: ["book", openId],
    queryFn: () => api.get<BookDetail>(`/api/library/books/${openId}`),
    enabled: openId !== null,
    retry: 1,
  })

  const chQ = useQuery({
    queryKey: ["chapter", chapterId],
    queryFn: () => api.get<ChapterResp>(`/api/library/chapters/${chapterId}`),
    enabled: chapterId !== null,
    retry: 1,
  })

  /* 两步确认超时收回 */
  useEffect(() => {
    if (armed === null) return
    const t = window.setTimeout(() => setArmed(null), ARM_MS)
    return () => window.clearTimeout(t)
  }, [armed])

  const imp = useMutation({
    mutationFn: (fd: FormData) => api.upload<{ title: string; chapters: unknown[] }>("/api/library/import", fd),
    onSuccess: (r) => {
      setTitle("")
      setPaste("")
      setPicked(null)
      if (fileRef.current) fileRef.current.value = ""
      qc.invalidateQueries({ queryKey: ["books"] })
      toast.success(`《${r.title}》已入库`, { description: `切出 ${r.chapters.length} 章` })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const ex = useMutation({
    mutationFn: () =>
      api.post<ExcerptResp>(`/api/library/chapters/${chapterId}/excerpt`, {
        start: from,
        end: to,
      }),
    onSuccess: (r) => setExcerpt(r),
    onError: (e: Error) => toast.error(e.message),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/api/library/books/${id}`),
    onSuccess: () => {
      setArmed(null)
      setOpenId(null)
      setChapterId(null)
      setExcerpt(null)
      qc.invalidateQueries({ queryKey: ["books"] })
      toast.success("索引已删", { description: "原著 md 还留在盘上，没动" })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  function doImport() {
    const f = fileRef.current?.files?.[0] ?? null
    if (!f && !paste.trim()) {
      toast.error("选一个 txt/md 文件，或者把正文粘进下面的框")
      return
    }
    const fd = new FormData()
    if (f) fd.append("file", f)
    else fd.append("text", paste)
    const name = title.trim() || (f ? f.name.replace(/\.[^.]+$/, "") : "")
    if (name) fd.append("title", name)
    imp.mutate(fd)
  }

  const chapters = bookQ.data?.chapters ?? []

  /** 按章范围导出。整本书导出时 to 直接吃 chapters.length，省得用户去数。 */
  async function doExportChapters() {
    if (openId === null || !chapters.length) return
    const last = expTo > 0 ? expTo : chapters.length
    setExporting(true)
    try {
      const r = await exportRange(
        { what: "chapters", book_id: openId, from: expFrom, to: last },
        { zipName: `${bookQ.data?.title ?? "原著"}_第${expFrom}-${last}章` }
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
          <h1 className="text-[19px] font-semibold leading-tight">原著库</h1>
          <p className="pt-0.5 text-[12.5px] text-ash">
            把原著 / 剧本收进工作区：切好章节、能检索、能摘录。
            <span className="text-ash-dim"> 只做摘录，不改写 —— 改编成提示词是外面那一步的活。</span>
          </p>
        </div>
      </header>

      <div className="grid items-start gap-3.5 px-6 pb-11 min-[1180px]:grid-cols-[380px_minmax(0,1fr)]">
        {/* ---------------------------------------------------------------- 导入 + 书目 */}
        <section className="min-w-0 rounded-panel border border-rule-soft bg-panel p-4">
          <h2 className="text-[13.5px] font-medium">导入</h2>
          <p className="mb-3 mt-1 text-[12px] leading-relaxed text-ash">
            txt / md，按 <span className="font-mono">第N章</span> /{" "}
            <span className="font-mono">Chapter N</span> / md 标题切章，落到{" "}
            <span className="font-mono">原著/&lt;书名&gt;/0001 …</span>
          </p>

          <input
            ref={fileRef}
            type="file"
            accept=".txt,.md,.markdown,.text,text/plain,text/markdown"
            onChange={(e) => setPicked(e.target.files?.[0]?.name ?? null)}
            className="block w-full text-[12px] text-ash file:mr-2.5 file:h-8 file:rounded-ctl file:border file:border-rule file:bg-cell file:px-2.5 file:text-[12px] file:text-paper hover:file:bg-hi"
          />
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="书名（不写就用文件名）"
            className="mt-2 h-8 w-full rounded-ctl border border-rule bg-cell px-2.5 text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
          />
          <textarea
            value={paste}
            onChange={(e) => setPaste(e.target.value)}
            rows={3}
            placeholder="或者直接把正文粘到这里（有文件时不看这里）"
            className="mt-2 w-full resize-y rounded-ctl border border-rule bg-cell px-2.5 py-2 text-[12.5px] leading-relaxed outline-none placeholder:text-ash-dim focus:border-wash-line"
          />
          <button
            type="button"
            onClick={doImport}
            disabled={imp.isPending}
            className={cn(
              "mt-2.5 inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-ctl bg-paper text-[12.5px] font-medium text-[#131313] transition-all hover:bg-white active:translate-y-px disabled:opacity-45",
              imp.isPending && "gb-sweep"
            )}
          >
            <Upload className="size-3.5" />
            {imp.isPending ? "导入中…" : "导入到工作区"}
          </button>
          {picked && <p className="mt-1.5 truncate text-[11px] text-ash-dim">已选：{picked}</p>}

          <div className="mt-4 flex items-center justify-between border-t border-rule-soft pt-3">
            <h2 className="text-[13.5px] font-medium">书目</h2>
            <span className="font-mono text-[11px] text-ash-dim">{books.length} 本</span>
          </div>
          <div className="mt-2 flex flex-col gap-1.5">
            {booksQ.isPending && <p className="text-[12px] text-ash">读目录…</p>}
            {!books.length && !booksQ.isPending && (
              <p className="text-[12px] leading-relaxed text-ash-dim">
                还没有书。上面导入一本，章节会自己切好。
              </p>
            )}
            {books.map((b) => (
              <div
                key={b.id}
                className={cn(
                  "rounded-ctl border px-2.5 py-2 transition-colors",
                  openId === b.id ? "border-wash-line bg-hi" : "border-rule-soft bg-cell hover:bg-hi"
                )}
              >
                <button
                  type="button"
                  onClick={() => {
                    setOpenId(openId === b.id ? null : b.id)
                    setChapterId(null)
                    setExcerpt(null)
                  }}
                  className="flex w-full items-baseline gap-2 text-left"
                >
                  <span className="min-w-0 flex-1 truncate text-[12.5px] text-paper">《{b.title}》</span>
                  <span className="shrink-0 font-mono text-[10.5px] text-ash-dim">
                    {b.chapters} 章 · {b.words} 字
                  </span>
                </button>
                <div className="mt-1 flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-ash-dim">
                    {b.dir_rel}
                  </span>
                  <button
                    type="button"
                    onClick={() => (armed === b.id ? remove.mutate(b.id) : setArmed(b.id))}
                    title="只删索引，盘上的 md 不动"
                    className={cn(
                      "inline-flex h-6 items-center gap-1 rounded-ctl border px-1.5 text-[11px] transition-all active:translate-y-px",
                      armed === b.id
                        ? "border-dashed border-[#8a8a8a] bg-wash text-paper"
                        : "border-rule bg-panel text-ash hover:bg-hi hover:text-paper"
                    )}
                  >
                    <Trash2 className="size-3" />
                    {armed === b.id ? "确认删索引" : "删索引"}
                  </button>
                </div>
              </div>
            ))}
          </div>

          {bookQ.data && (
            <div className="mt-3 border-t border-rule-soft pt-3">
              <div className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1.5">
                <span className="text-[12px] font-medium">章节</span>
                <span className="font-mono text-[10.5px] text-ash-dim">{chapters.length}</span>

                {/* 范围导出（C4）：每章一个 md，打包带上书名做子目录 */}
                <span className="ml-auto flex items-center gap-1.5">
                  <span className="text-[11px] text-ash-dim">导出 第</span>
                  <input
                    type="number"
                    min={1}
                    value={expFrom}
                    onChange={(e) => setExpFrom(Number(e.target.value))}
                    aria-label="导出起始章"
                    className="h-6 w-12 rounded-ctl border border-rule bg-cell px-1 text-center font-mono text-[11px] outline-none focus:border-wash-line"
                  />
                  <span className="text-[11px] text-ash-dim">–</span>
                  <input
                    type="number"
                    min={0}
                    placeholder={String(chapters.length)}
                    value={expTo || ""}
                    onChange={(e) => setExpTo(Number(e.target.value) || 0)}
                    aria-label="导出结束章"
                    title="留空 = 到最后一章"
                    className="h-6 w-12 rounded-ctl border border-rule bg-cell px-1 text-center font-mono text-[11px] outline-none focus:border-wash-line"
                  />
                  <span className="text-[11px] text-ash-dim">章</span>
                  <button
                    type="button"
                    onClick={doExportChapters}
                    disabled={exporting || !chapters.length}
                    className="inline-flex h-6 items-center gap-1 rounded-ctl border border-rule bg-panel px-1.5 text-[11px] text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px disabled:opacity-40"
                  >
                    <Download className="size-3" />
                    {exporting ? "导出中" : "打包"}
                  </button>
                </span>
              </div>
              <div className="flex max-h-[240px] flex-col gap-0.5 overflow-auto">
                {chapters.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => {
                      setChapterId(c.id)
                      setExcerpt(null)
                      setFrom(1)
                      setTo(0)
                    }}
                    className={cn(
                      "flex items-baseline gap-2 rounded-ctl px-1.5 py-1 text-left transition-colors hover:bg-hi",
                      chapterId === c.id && "bg-hi"
                    )}
                  >
                    <span className="w-9 shrink-0 font-mono text-[10.5px] text-ash-dim">
                      {String(c.idx).padStart(4, "0")}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[12px] text-ash">
                      {c.title || "（无标题）"}
                    </span>
                    <span className="shrink-0 font-mono text-[10px] text-ash-dim">{c.word_count}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* ---------------------------------------------------------------- 正文 + 摘录 */}
        <section className="min-w-0 rounded-panel border border-rule-soft bg-panel p-4">
          {!chapterId && (
            <div className="grid place-items-center gap-2 py-16 text-center">
              <BookOpen className="size-5 text-ash-dim" />
              <p className="text-[12.5px] text-ash">左边选一章，正文会在这里。</p>
            </div>
          )}

          {chapterId && (
            <>
              <div className="mb-2 flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                <h2 className="text-[13.5px] font-medium">{chQ.data?.title || "这一章"}</h2>
                <span className="font-mono text-[11px] text-ash-dim">
                  {chQ.data?.book_title} · 第 {chQ.data?.idx} 章 · {chQ.data?.words} 字
                </span>
              </div>

              <div className="max-h-[46vh] overflow-auto rounded-ctl border border-rule-soft bg-void p-3.5">
                {chQ.isPending && <p className="text-[12px] text-ash">读正文…</p>}
                <p className="whitespace-pre-wrap text-[12.5px] leading-[1.85] text-[#c6c6c6]">
                  {chQ.data?.text || ""}
                </p>
              </div>

              <div className="mt-3.5 rounded-ctl border border-rule-soft bg-void p-3">
                <div className="flex flex-wrap items-center gap-2.5">
                  <span className="text-[12px] font-medium">摘录</span>
                  <span className="flex items-center gap-1.5">
                    <span className="text-[11px] text-ash-dim">第</span>
                    <input
                      type="number"
                      min={1}
                      value={from}
                      onChange={(e) => setFrom(Number(e.target.value))}
                      aria-label="起始段"
                      className="h-7 w-14 rounded-ctl border border-rule bg-cell px-1.5 text-center font-mono text-[12px] outline-none focus:border-wash-line"
                    />
                    <span className="text-[11px] text-ash-dim">–</span>
                    <input
                      type="number"
                      min={0}
                      value={to}
                      onChange={(e) => setTo(Number(e.target.value))}
                      aria-label="结束段"
                      className="h-7 w-14 rounded-ctl border border-rule bg-cell px-1.5 text-center font-mono text-[12px] outline-none focus:border-wash-line"
                    />
                    <span className="text-[11px] text-ash-dim">段（0 = 到末段）</span>
                  </span>
                  <button
                    type="button"
                    onClick={() => ex.mutate()}
                    disabled={ex.isPending}
                    className="inline-flex h-7 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] transition-colors hover:bg-hi disabled:opacity-45"
                  >
                    <Scissors className="size-3.5" />
                    {ex.isPending ? "摘录中…" : "摘录这几段"}
                  </button>
                  <button
                    type="button"
                    disabled={!excerpt?.text}
                    onClick={() => {
                      navigator.clipboard.writeText(excerpt?.text ?? "").then(
                        () => toast.success("摘录已复制", { description: "粘给外部 Agent 当素材" }),
                        () => toast.error("复制被浏览器拦了", { description: "手动选中下面的文本也可以" })
                      )
                    }}
                    className="inline-flex h-7 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] transition-colors hover:bg-hi disabled:opacity-45"
                  >
                    <Copy className="size-3.5" />
                    复制摘录
                  </button>
                  <button
                    type="button"
                    disabled={!excerpt?.text}
                    onClick={async () => {
                      const name = `${chQ.data?.book_title ?? "原著"}_第${chQ.data?.idx ?? 0}章_${excerpt?.from}-${excerpt?.to}段.txt`
                      const r = await exportItems([{ text: excerpt?.text ?? "", filename: name }])
                      if (!r.ok) {
                        if (!r.message.includes("取消")) toast.error(r.message)
                        return
                      }
                      toast.success("摘录已导出", { description: r.path ?? r.message })
                    }}
                    className="inline-flex h-7 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] transition-colors hover:bg-hi disabled:opacity-45"
                  >
                    导出摘录
                  </button>
                </div>
                <p className="mt-1.5 text-[11px] text-ash-dim">
                  只摘录，不改写 —— 出 §④ 提示词是外面那一步的活。
                </p>
                {excerpt && (
                  <pre className="mt-2 max-h-[220px] overflow-auto whitespace-pre-wrap rounded-ctl border border-rule-soft bg-void p-2.5 font-mono text-[11px] leading-[1.75] text-[#c6c6c6]">
                    {excerpt.text || "（这段是空的）"}
                  </pre>
                )}
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
