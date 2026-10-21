import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  AlertTriangle,
  BookSearch,
  ChevronRight,
  FolderOpen,
  Loader2,
  Search,
  ShieldCheck,
} from "lucide-react"
import { cn } from "cn"
import { api } from "@/lib/api"
import type {
  ChapterTextResp,
  CollectResp,
  SearchCandidate,
  SearchHit,
  SearchResp,
  SearchSource,
  SearchStats,
  SearchStatus,
  TocResp,
} from "@/lib/types"

/** 全网模式一次可能回来上百条（模糊匹配），列前这么多就够挑了。 */
const MAX_SHOWN = 50

/**
 * 小说搜索（整合方案 L3 · 联网采集）—— **真接后端**。
 *
 * 四条闭环：搜书名 → 选书源 → 看目录 → 收正文进原著库。
 *
 * 🔴 三层边界写在代码里，别在 UI 上含糊：
 *   1. 默认关闭：后端 `search.enabled` + `disclaimer_acked` 两个都为真才放行，
 *      未启用时所有采集端点一律 403。启用必须用户在页面上确认免责声明。
 *   2. 只到「文本」：收进原著库的是原文，改编成 §①–§⑥ 提示词是外面那一步的活。
 *   3. 不支持的源（要 JS 引擎 / 字体解密）**明确标出来并给原因**，不假装能用。
 */
export function NovelSearch() {
  const qc = useQueryClient()

  const [keyword, setKeyword] = useState("")
  //: 空串 = 全网多源并行（默认）；填源名 = 只搜那一个
  const [source, setSource] = useState("")
  //: 精确模式：只留书名/作者含关键词的，滤掉搜索页的推荐位和模糊命中
  const [strict, setStrict] = useState(true)
  //: 当前这本书用哪个源取目录/正文 —— 全网模式下每本书可能来自不同的源
  const [activeSource, setActiveSource] = useState("")
  const [hit, setHit] = useState<SearchHit | null>(null)
  const [from, setFrom] = useState(1)
  const [to, setTo] = useState(0)
  const [preview, setPreview] = useState<ChapterTextResp | null>(null)

  const statusQ = useQuery({
    queryKey: ["searchStatus"],
    queryFn: () => api.get<SearchStatus>("/api/search/status"),
    retry: 1,
  })
  const status = statusQ.data
  const enabled = !!status?.enabled

  const sourcesQ = useQuery({
    queryKey: ["searchSources"],
    queryFn: () => api.get<{ sources: SearchSource[]; errors: string[] }>("/api/search/sources"),
    enabled,
    retry: 1,
  })
  const sources = useMemo(
    () => (sourcesQ.data?.sources ?? []).filter((s) => s.can_search),
    [sourcesQ.data]
  )

  const enable = useMutation({
    mutationFn: () =>
      api.put("/api/config", { search: { enabled: true, disclaimer_acked: true } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["searchStatus"] })
      qc.invalidateQueries({ queryKey: ["config"] })
      toast.success("已启用联网采集", { description: "来源会随书一起记进原著库" })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const search = useMutation({
    mutationFn: () =>
      api.post<SearchResp>(
        "/api/search",
        { keyword: keyword.trim(), source, strict },
        // 全网模式并发打几十个源，最坏要等到服务端的整体截止时间
        { timeoutMs: 90_000 }
      ),
    onSuccess: (d) => {
      setHit(null)
      setPreview(null)
      setActiveSource("")
      toc.reset()
      if (!d.books.length) {
        toast.message("没搜到", {
          description: d.mode === "all" ? "账本里能看到每个源各自的情况" : "换个源或换个关键词试试",
        })
      }
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const books = search.data?.books ?? []
  //: 当前展示的这本书，在 activeSource 下的定位（换源后 bookUrl 会变）
  const activeCandidate = useMemo(
    () => hit?.candidates?.find((c) => c.source === activeSource) ?? hit?.candidates?.[0],
    [hit, activeSource]
  )

  const toc = useMutation({
    mutationFn: (arg: { url: string; source: string }) =>
      api.post<TocResp>("/api/search/toc", { source: arg.source, book_url: arg.url }),
    onSuccess: (d) => {
      setPreview(null)
      setFrom(1)
      setTo(0)
      toast.message(`目录 ${d.chapters.length} 章`)
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const chapters = toc.data?.chapters ?? []

  /** 换源：同一本书在另一个源下的目录重新拉一遍（标题对不上就收不到正文） */
  const switchSource = (c: SearchCandidate) => {
    setActiveSource(c.source)
    toc.mutate({ url: c.bookUrl, source: c.source })
  }

  const collect = useMutation({
    mutationFn: () =>
      api.post<CollectResp>(
        "/api/search/import",
        {
          source: activeSource,
          book_url: activeCandidate?.bookUrl ?? hit?.bookUrl ?? "",
          title: hit?.name ?? "",
          author: hit?.author ?? "",
          chapter_from: from,
          chapter_to: to,
        },
        // 逐章抓正文，可能几十秒甚至更久
        { timeoutMs: 300_000 }
      ),
    onSuccess: (d) => {
      const bad = d.failed?.length ?? 0
      toast.success(`已收进原著库：${d.chapters.length} 章`, {
        description: bad ? `有 ${bad} 章没取到（见下方列表）` : d.dir_rel,
      })
      qc.invalidateQueries({ queryKey: ["books"] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const peek = useMutation({
    mutationFn: (url: string) =>
      api.post<ChapterTextResp>("/api/search/chapter", { source: activeSource, url }),
    onSuccess: (d) => setPreview(d),
    onError: (e: Error) => toast.error(e.message),
  })

  /* ---------------- 未启用：把状态和代价说清楚，再给一个明确的开关 ---------------- */
  if (statusQ.isPending) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <Loader2 className="size-5 animate-spin text-ash-dim" />
      </div>
    )
  }

  if (!enabled) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <Header subtitle="联网检索书源，找到的书直接收进原著库。" />
        <div className="px-6 pb-11">
          <section className="rounded-panel border border-dashed border-rule bg-panel px-6 py-10">
            <div className="mx-auto max-w-[72ch]">
              <div className="flex items-start gap-3">
                <ShieldCheck className="mt-0.5 size-5 flex-none text-ash" strokeWidth={1.5} />
                <div>
                  <h2 className="text-[15px] font-medium">这一层默认关闭</h2>
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-ash">
                    联网采集会去访问第三方站点。启用前请确认下面这条，启用之后才会真的发请求。
                  </p>
                </div>
              </div>

              <p className="mt-5 rounded-ctl border border-rule-soft bg-cell px-3.5 py-3 text-[12px] leading-relaxed text-ash">
                {status?.disclaimer ?? "（免责声明加载中）"}
              </p>

              <div className="mt-5 flex flex-col gap-2">
                <Note n="01" t="范围">
                  只到「原文」为止 —— 改编成 §①–§⑥ 提示词仍是外面那一步的活。
                </Note>
                <Note n="02" t="标注">
                  来源会写进书的信息里，跟着工作区一起走（可在设置里关）。
                </Note>
                <Note n="03" t="能力">
                  当前 {status?.sources_usable ?? 0} / {status?.sources_total ?? 0} 个源可用；
                  需要 JS 引擎或字体解密的源不支持，会在列表里标出原因。
                </Note>
              </div>

              {!!status?.errors?.length && (
                <p className="mt-4 flex items-start gap-2 rounded-ctl border border-rule-soft bg-cell px-3 py-2.5 text-[11.5px] text-ash-dim">
                  <AlertTriangle className="mt-px size-3.5 flex-none" strokeWidth={1.5} />
                  {status.errors.join("；")}
                </p>
              )}

              <button
                type="button"
                disabled={enable.isPending}
                onClick={() => enable.mutate()}
                className="mt-7 inline-flex h-8 items-center justify-center gap-1.5 rounded-ctl bg-paper px-3.5 text-[12.5px] font-medium text-[#131313] transition-all hover:bg-white active:translate-y-px disabled:opacity-50"
              >
                {enable.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <ShieldCheck className="size-3.5" />}
                我已阅读并确认，启用采集
              </button>
            </div>
          </section>
        </div>
      </div>
    )
  }

  /* ---------------- 已启用：搜 → 目录 → 收 ---------------- */
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Header
        subtitle="联网检索书源，找到的书直接收进原著库。"
        right={
          <span className="font-mono text-[10.5px] text-ash-dim">
            可用源 {status?.sources_usable ?? 0} / {status?.sources_total ?? 0}
          </span>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-4 px-6 pb-11">
        {/* ① 搜 */}
        <section className="rounded-panel border border-rule bg-panel p-3.5">
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1">
              <span className="text-[10.5px] text-ash-dim">书源</span>
              <select
                aria-label="书源"
                value={source}
                onChange={(e) => {
                  setSource(e.target.value)
                  setHit(null)
                  setActiveSource("")
                  toc.reset()
                }}
                className="h-8 w-[15rem] rounded-ctl border border-rule bg-cell px-2 text-[12.5px] text-paper outline-none focus:border-ash-dim"
              >
                {/* 默认全网：一次并行问所有可用源，同书按「书名+作者」合并 */}
                <option value="">全网 · {sources.length} 个源并行</option>
                {sources.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name}
                    {!s.can_toc || !s.can_content ? "（只能搜，收不了正文）" : ""}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex min-w-[16rem] flex-1 flex-col gap-1">
              <span className="text-[10.5px] text-ash-dim">书名 / 作者</span>
              <input
                aria-label="关键词"
                value={keyword}
                placeholder="例如：凡人修仙传"
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && keyword.trim()) search.mutate()
                }}
                className="h-8 rounded-ctl border border-rule bg-cell px-2.5 text-[12.5px] text-paper outline-none placeholder:text-ash-dim focus:border-ash-dim"
              />
            </label>

            {/* 模糊搜索会把「凡人碎空传」这类也带回来；默认只留书名/作者真的含关键词的 */}
            <label className="flex h-8 items-center gap-1.5 text-[11.5px] text-ash-dim">
              <input
                type="checkbox"
                checked={strict}
                onChange={(e) => setStrict(e.target.checked)}
                className="size-3.5 accent-[var(--gb-paper)]"
              />
              精确
            </label>

            <button
              type="button"
              disabled={!keyword.trim() || search.isPending}
              onClick={() => search.mutate()}
              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-ctl bg-paper px-3.5 text-[12.5px] font-medium text-[#131313] transition-all hover:bg-white active:translate-y-px disabled:opacity-40"
            >
              {search.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}
              搜索
            </button>
          </div>

          {!!sourcesQ.data?.errors?.length && (
            <p className="mt-2 text-[11px] text-ash-dim">{sourcesQ.data.errors.join("；")}</p>
          )}
        </section>

        {/* 结果 + 书源账本 */}
        {!!search.data && (
          <section className="rounded-panel border border-rule bg-panel">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-rule-soft px-3.5 py-2.5">
              <span className="text-[12px] font-medium">结果</span>
              <span className="font-mono text-[10.5px] text-ash-dim">{books.length}</span>
              {!!search.data.stats && (
                <span className="ml-auto">
                  <StatsLine s={search.data.stats} />
                </span>
              )}
            </div>

            {!!books.length && (
              <ul className="divide-y divide-[var(--gb-rule-soft)]">
                {books.slice(0, MAX_SHOWN).map((b, i) => {
                  const n = b.candidates?.length ?? 1
                  const isHit = hit?.name === b.name && hit?.author === b.author
                  return (
                    <li key={`${b.name}-${b.author}-${i}`}>
                      <button
                        type="button"
                        onClick={() => {
                          // 全网模式下每本书的首选源可能不同，必须跟着这本书走
                          const c = b.candidates?.[0]
                          const src = c?.source ?? source
                          setHit(b)
                          setActiveSource(src)
                          toc.mutate({ url: c?.bookUrl ?? b.bookUrl, source: src })
                        }}
                        className={cn(
                          "flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition-colors hover:bg-hi",
                          isHit && "bg-hi"
                        )}
                      >
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] text-paper">{b.name}</span>
                          <span className="mt-0.5 block truncate text-[11px] text-ash-dim">
                            {b.author}
                            {b.lastChapter ? ` · 最新 ${b.lastChapter}` : ""}
                          </span>
                        </span>
                        {n > 1 && (
                          <span
                            className="flex-none rounded-ctl border border-rule px-1.5 py-0.5 font-mono text-[10px] text-ash-dim"
                            title={`${n} 个源都有这本：${b.sources?.join(" / ") ?? ""}`}
                          >
                            ×{n}
                          </span>
                        )}
                        <ChevronRight className="size-4 flex-none text-ash-dim" strokeWidth={1.5} />
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}

            {books.length > MAX_SHOWN && (
              <p className="px-3.5 py-2 text-[11px] text-ash-dim">
                只列前 {MAX_SHOWN} 条（共 {books.length}）—— 关键词写全一点能更快找到
              </p>
            )}

            {!books.length && (
              <p className="px-3.5 py-3 text-[12px] leading-relaxed text-ash">
                没有命中。勾掉「精确」可以放宽到模糊匹配；账本里的「空 / 失败 / 放弃」
                说明是源站不可达，还是能连上但没收录。
              </p>
            )}
          </section>
        )}

        {/* 不支持的源：说清楚为什么 */}
        {!books.length && !search.data && !!sourcesQ.data?.sources?.length && (
          <section className="rounded-panel border border-dashed border-rule bg-panel px-3.5 py-3">
            <p className="text-[11.5px] text-ash-dim">
              共 {sourcesQ.data.sources.length} 个源，其中{" "}
              {sourcesQ.data.sources.filter((s) => s.unsupported).length} 个当前跑不了：
            </p>
            <ul className="mt-2 flex flex-col gap-1">
              {sourcesQ.data.sources
                .filter((s) => s.unsupported)
                .slice(0, 4)
                .map((s) => (
                  <li key={s.name} className="flex gap-2 text-[11px] leading-relaxed text-ash-dim">
                    <span className="flex-none text-ash">{s.name}</span>
                    <span>{s.unsupported}</span>
                  </li>
                ))}
            </ul>
          </section>
        )}

        {/* ② 目录 + ③ 收 */}
        {hit && (
          <section className="rounded-panel border border-rule bg-panel">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-rule-soft px-3.5 py-2.5">
              <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-paper">
                {hit.name}
              </span>
              {activeSource && (
                <span className="flex-none font-mono text-[10.5px] text-ash-dim" title="当前取目录用的源">
                  {activeSource}
                </span>
              )}
              {toc.isPending && <Loader2 className="size-3.5 animate-spin text-ash-dim" />}
              {!!chapters.length && (
                <>
                  <label className="flex items-center gap-1.5 text-[11.5px] text-ash-dim">
                    从第
                    <input
                      aria-label="起始章"
                      type="number"
                      min={1}
                      value={from}
                      onChange={(e) => setFrom(Math.max(1, Number(e.target.value) || 1))}
                      className="h-7 w-16 rounded-ctl border border-rule bg-cell px-2 font-mono text-[11.5px] text-paper outline-none focus:border-ash-dim"
                    />
                    章
                  </label>
                  <label className="flex items-center gap-1.5 text-[11.5px] text-ash-dim">
                    到
                    <input
                      aria-label="结束章"
                      type="number"
                      min={0}
                      value={to}
                      onChange={(e) => setTo(Math.max(0, Number(e.target.value) || 0))}
                      className="h-7 w-16 rounded-ctl border border-rule bg-cell px-2 font-mono text-[11.5px] text-paper outline-none focus:border-ash-dim"
                    />
                    <span className="text-[10.5px]">（0 = 到最后一章）</span>
                  </label>
                  <button
                    type="button"
                    disabled={collect.isPending}
                    onClick={() => collect.mutate()}
                    className="inline-flex h-8 items-center justify-center gap-1.5 rounded-ctl bg-paper px-3.5 text-[12.5px] font-medium text-[#131313] transition-all hover:bg-white active:translate-y-px disabled:opacity-50"
                  >
                    {collect.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <FolderOpen className="size-3.5" />}
                    收进原著库
                  </button>
                </>
              )}
            </div>

            {/* 换源：同一本书换个源取目录 —— 某个源标题对不上就收不到正文 */}
            {!!hit.candidates && hit.candidates.length > 1 && (
              <div className="flex flex-wrap items-center gap-1.5 border-b border-rule-soft px-3.5 py-2">
                <span className="flex-none text-[10.5px] text-ash-dim">换源</span>
                {hit.candidates.map((c) => (
                  <button
                    key={c.source}
                    type="button"
                    onClick={() => switchSource(c)}
                    className={cn(
                      "h-6 rounded-ctl border px-2 font-mono text-[10.5px] transition-colors",
                      c.source === activeSource
                        ? "border-paper bg-paper text-[#131313]"
                        : "border-rule text-ash hover:border-ash-dim hover:text-paper"
                    )}
                  >
                    {c.source}
                  </button>
                ))}
              </div>
            )}

            {!!chapters.length && (
              <ul className="max-h-[26rem] divide-y divide-[var(--gb-rule-soft)] overflow-y-auto">
                {chapters.map((c, i) => (
                  <li key={`${c.url}-${i}`} className="flex items-center gap-2 px-3.5 py-1.5">
                    <span className="w-10 flex-none font-mono text-[10.5px] text-ash-dim">
                      {String(i + 1).padStart(3, "0")}
                    </span>
                    <button
                      type="button"
                      onClick={() => peek.mutate(c.url)}
                      className="min-w-0 flex-1 truncate text-left text-[12px] text-ash transition-colors hover:text-paper"
                      title="点一下看正文"
                    >
                      {c.title}
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {toc.isError && (
              <p className="px-3.5 py-3 text-[12px] text-ash">目录取不到：{(toc.error as Error).message}</p>
            )}
          </section>
        )}

        {/* 正文预览：确认源没抓错再整本收 */}
        {preview && (
          <section className="rounded-panel border border-rule bg-panel">
            <div className="flex items-center gap-2 border-b border-rule-soft px-3.5 py-2.5">
              <span className="text-[12px] font-medium">正文预览</span>
              <span className="text-[11px] text-ash-dim">确认没抓错再整本收</span>
            </div>
            <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap px-3.5 py-3 text-[12px] leading-relaxed text-ash">
              {preview.text.slice(0, 2000)}
            </pre>
          </section>
        )}

        {/* 收失败的章：不藏 */}
        {!!collect.data?.failed?.length && (
          <section className="rounded-panel border border-dashed border-rule bg-panel px-3.5 py-3">
            <p className="text-[11.5px] text-ash">
              {collect.data.failed.length} 章没取到（其余已入库）：
            </p>
            <ul className="mt-2 flex flex-col gap-1">
              {collect.data.failed.map((f) => (
                <li key={f.index} className="flex gap-2 text-[11px] text-ash-dim">
                  <span className="flex-none font-mono">{String(f.index).padStart(3, "0")}</span>
                  <span className="min-w-0 truncate">{f.title || "（无标题）"}</span>
                  <span className="min-w-0 flex-1 truncate text-right">{f.reason}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  )
}

/** 书源账本：这次问了多少源、谁给了结果、谁掉队。数字比「没搜到」有信息量。 */
function StatsLine({ s }: { s: SearchStats }) {
  const parts = [
    `投入 ${s.requested}`,
    `有结果 ${s.ok}`,
    s.empty ? `空 ${s.empty}` : "",
    s.failed ? `失败 ${s.failed}` : "",
    s.abandoned ? `放弃 ${s.abandoned}` : "",
    `${s.elapsed}s`,
  ].filter(Boolean)
  return <span className="font-mono text-[10.5px] text-ash-dim">{parts.join(" · ")}</span>
}

/** 页头。right 放状态元信息，不抢标题。 */
function Header({ subtitle, right }: { subtitle: string; right?: React.ReactNode }) {
  return (
    <header className="flex flex-wrap items-end gap-x-3.5 gap-y-2 px-6 pb-3.5 pt-5">
      <div className="max-w-[82ch]">
        <h1 className="flex items-center gap-2 text-[19px] font-semibold leading-tight">
          <BookSearch className="size-[18px] text-ash" strokeWidth={1.5} />
          小说搜索
        </h1>
        <p className="pt-0.5 text-[12.5px] text-ash">{subtitle}</p>
      </div>
      {right && <div className="ml-auto">{right}</div>}
    </header>
  )
}

/** 一行说明：等宽编号 + 形状标签 */
function Note({ n, t, children }: { n: string; t: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 rounded-ctl border border-rule-soft bg-cell px-3.5 py-2.5">
      <span className="mt-px flex-none font-mono text-[10.5px] text-ash-dim">{n}</span>
      <span className="w-8 flex-none text-[12px] font-medium text-paper">{t}</span>
      <span className="min-w-0 text-[12px] leading-relaxed text-ash">{children}</span>
    </div>
  )
}
