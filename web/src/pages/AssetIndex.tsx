import { useEffect, useMemo, useRef, useState } from "react"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Check,
  File,
  FileArchive,
  FileMusic,
  FileText,
  Film,
  ImageOff,
  Images,
  RefreshCw,
  Search,
  X,
} from "lucide-react"
import { cn } from "cn"
import { ApiError } from "@/lib/api"
import {
  ASSETS_KEY_ROOT,
  assetsKey,
  getAsset,
  getAssetCollections,
  getAssetScanStatus,
  getAssets,
  startAssetScan,
} from "@/lib/assetsApi"
import { humanSize } from "@/lib/format"
import { useAssetEvents } from "@/lib/useAssetEvents"
import { useActiveWorkspace } from "@/lib/workspace"
import type { IndexAssetDetail, IndexAssetItem } from "@/lib/types"

export interface AssetIndexQuery {
  role?: string
  category?: string
  collection?: string
  q?: string
  asset?: string
  panel?: string
}

const PAGE_SIZE = 200
const CELL_H = 168
const CELL_GAP = 12
const CELL_STRIDE = CELL_H + CELL_GAP
const MIN_CELL_W = 168
const GRID_PAD = 32

const ROLE_LABEL: Record<string, string> = {
  asset: "资产",
  storyboard: "分镜",
  prompt: "提示词",
  final: "成片",
  draft: "草稿",
  source: "原著",
  other: "其他",
}

const COLLECTION_ORDER = ["recent", "missing", "unreferenced", "large"]

interface ScanProgress {
  processed: number
  total: number
}

export function AssetIndex({
  search,
  onSearchChange,
}: {
  search: AssetIndexQuery
  onSearchChange: (patch: Partial<AssetIndexQuery>) => void
}) {
  const workspace = useActiveWorkspace()
  const qc = useQueryClient()

  /* ---------------- 集合 / 角色树 ---------------- */
  const collectionsQ = useQuery({
    queryKey: ["asset-collections", workspace],
    queryFn: ({ signal }) => getAssetCollections({ signal }),
    enabled: !!workspace,
    retry: 1,
  })

  const roleRows = collectionsQ.data?.roles ?? []
  const collectionRows = useMemo(() => {
    const rows = collectionsQ.data?.collections ?? []
    return [...rows].sort((a, b) => {
      const ia = COLLECTION_ORDER.indexOf(a.id)
      const ib = COLLECTION_ORDER.indexOf(b.id)
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.name.localeCompare(b.name)
    })
  }, [collectionsQ.data])

  /** 旧 hash 会带 `collection=novel`；后端没有这个集合 —— 当作没选，绝不拿它去请求。 */
  const collectionKnown = !search.collection
    ? true
    : collectionRows.some((c) => c.id === search.collection)
  const activeCollection = collectionKnown ? search.collection : undefined
  const waitingCollections = !!search.collection && collectionsQ.isPending

  /* ---------------- 搜索框（本地 state + 250ms 防抖写 URL） ---------------- */
  const [qInput, setQInput] = useState(search.q ?? "")
  const onChangeRef = useRef(onSearchChange)
  useEffect(() => {
    onChangeRef.current = onSearchChange
  })

  useEffect(() => {
    setQInput(search.q ?? "")
  }, [search.q])

  useEffect(() => {
    const next = qInput.trim()
    if (next === (search.q ?? "")) return
    const timer = window.setTimeout(() => {
      onChangeRef.current({ q: next || undefined })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [qInput, search.q])

  /* ---------------- 无限列表 ---------------- */
  const listQ = useInfiniteQuery({
    queryKey: assetsKey({
      role: search.role,
      category: search.category,
      collection: activeCollection,
      q: search.q,
    }),
    queryFn: ({ pageParam, signal }) =>
      getAssets(
        {
          role: search.role,
          category: search.category,
          collection: activeCollection,
          q: search.q,
          cursor: pageParam,
          limit: PAGE_SIZE,
        },
        { signal }
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !!workspace && !waitingCollections,
    retry: 1,
  })

  const items = useMemo(
    () => listQ.data?.pages.flatMap((page) => page.items) ?? [],
    [listQ.data]
  )
  const total = listQ.data?.pages[0]?.total ?? 0

  /* ---------------- 多选 ---------------- */
  const [multi, setMulti] = useState<Set<string>>(() => new Set())
  function toggleMulti(id: string) {
    setMulti((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  /* ---------------- 虚拟网格度量 ---------------- */
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const [viewport, setViewport] = useState({ w: 0, h: 0 })
  const [scrollTop, setScrollTop] = useState(0)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const measure = () => setViewport({ w: el.clientWidth, h: el.clientHeight })
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const filterSig = JSON.stringify([
    search.role ?? "",
    search.category ?? "",
    activeCollection ?? "",
    search.q ?? "",
  ])
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = 0
    setScrollTop(0)
  }, [filterSig])

  const gridW = Math.max(0, (viewport.w || 900) - GRID_PAD)
  const cols = Math.max(1, Math.floor((gridW + CELL_GAP) / (MIN_CELL_W + CELL_GAP)))
  const viewH = viewport.h || 620
  const rowCount = Math.ceil(items.length / cols)
  const startRow = Math.max(0, Math.floor(scrollTop / CELL_STRIDE) - 2)
  const endRow = Math.min(rowCount, Math.ceil((scrollTop + viewH) / CELL_STRIDE) + 2)
  const visibleItems = items.slice(startRow * cols, endRow * cols)
  const virtualH = Math.max(0, rowCount * CELL_STRIDE - CELL_GAP)

  /* ---------------- 重新扫描 + 进度 ---------------- */
  const [scanTaskId, setScanTaskId] = useState<string | null>(null)
  const [scanRunning, setScanRunning] = useState(false)
  const [progress, setProgress] = useState<ScanProgress | null>(null)
  const finishedScan = useRef<string | null>(null)

  const scanStart = useMutation({
    mutationFn: () => startAssetScan(true),
    onSuccess: (r) => {
      setScanTaskId(r.task_id)
      setScanRunning(true)
      setProgress({ processed: 0, total: 0 })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const scanStatusQ = useQuery({
    queryKey: ["index-scan", scanTaskId],
    queryFn: ({ signal }) => getAssetScanStatus(scanTaskId ?? "", { signal }),
    enabled: !!scanTaskId,
    retry: 1,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === "done" || status === "error" ? false : 800
    },
  })

  useEffect(() => {
    const st = scanStatusQ.data
    if (!st || (st.status !== "done" && st.status !== "error")) return
    if (finishedScan.current === st.id) return
    finishedScan.current = st.id
    setScanTaskId(null)
    setScanRunning(false)
    setProgress(null)
    void qc.invalidateQueries({ queryKey: ASSETS_KEY_ROOT })
    void qc.invalidateQueries({ queryKey: ["asset-collections"] })
    if (st.status === "error") {
      toast.error(`扫描失败：${String(st.stats.error ?? "后端未给出原因")}`)
    }
  }, [scanStatusQ.data, qc])

  /* ---------------- SSE ---------------- */
  useAssetEvents({
    enabled: !!workspace,
    onProgress: (e) => {
      if (e.done) {
        setScanRunning(false)
        setProgress(null)
        void qc.invalidateQueries({ queryKey: ASSETS_KEY_ROOT })
        void qc.invalidateQueries({ queryKey: ["asset-collections"] })
        return
      }
      setScanRunning(true)
      setProgress({ processed: e.processed, total: e.total })
    },
    onChanged: () => {
      void qc.invalidateQueries({ queryKey: ASSETS_KEY_ROOT })
      void qc.invalidateQueries({ queryKey: ["asset-collections"] })
    },
  })

  /* ---------------- 详情 ---------------- */
  const assetId = search.asset
  const detailQ = useQuery({
    queryKey: ["index-asset", workspace, assetId],
    queryFn: ({ signal }) => getAsset(assetId ?? "", { signal }),
    enabled: !!workspace && !!assetId,
    retry: 1,
  })

  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.processed / progress.total) * 100))
      : 0

  const listError = listQ.error as Error | null
  const noWorkspace = !!listError && listError instanceof ApiError && listError.status === 400

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1 max-[980px]:overflow-x-auto">
        {/* ---------------- 左栏：角色 / 集合 ---------------- */}
        <aside className="flex w-[196px] flex-none flex-col overflow-y-auto border-r border-rule-soft bg-panel max-[980px]:w-[148px]">
          <h1 className="px-4 pb-2 pt-4 text-[13.5px] font-medium">资产</h1>

          <div className="px-2">
            {collectionsQ.isPending && (
              <p className="px-2 py-1 text-[11.5px] text-ash-dim">读取角色…</p>
            )}
            {roleRows.map((r) => {
              const on = search.role === r.key
              return (
                <button
                  key={r.key}
                  type="button"
                  aria-pressed={on}
                  onClick={() => {
                    if (on) onSearchChange({ role: undefined })
                    else onSearchChange({ role: r.key, collection: undefined })
                  }}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-ctl px-2 py-[7px] text-left text-[12.5px] transition-colors",
                    on ? "bg-hi font-medium text-paper" : "text-ash hover:bg-hi hover:text-paper"
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{r.label}</span>
                  <span
                    className={cn(
                      "flex-none font-mono text-[10.5px]",
                      on ? "text-white" : "text-ash-dim"
                    )}
                  >
                    {r.count}
                  </span>
                </button>
              )
            })}
          </div>

          <div className="mt-4 border-t border-rule-soft px-4 pb-2 pt-3 text-[11px] font-medium text-ash">
            集合
          </div>
          <div className="px-2 pb-3">
            {collectionRows.map((c) => {
              const on = activeCollection === c.id
              return (
                <button
                  key={c.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => {
                    if (on) onSearchChange({ collection: undefined })
                    else onSearchChange({ collection: c.id, role: undefined })
                  }}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-ctl px-2 py-[7px] text-left text-[12.5px] transition-colors",
                    on ? "bg-hi font-medium text-paper" : "text-ash hover:bg-hi hover:text-paper"
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{c.name}</span>
                  <span
                    className={cn(
                      "flex-none font-mono text-[10.5px]",
                      on ? "text-white" : "text-ash-dim"
                    )}
                  >
                    {c.count}
                  </span>
                </button>
              )
            })}
            {!collectionsQ.isPending && !collectionRows.length && (
              <p className="px-2 py-1 text-[11px] text-ash-dim">暂无集合</p>
            )}
          </div>
        </aside>

        {/* ---------------- 中栏：搜索 + 网格 + 进度 ---------------- */}
        <section className="flex min-w-0 flex-1 flex-col">
          <div className="flex flex-none flex-wrap items-center gap-2 border-b border-rule-soft bg-panel px-4 py-2.5">
            <div className="relative w-[236px] max-[980px]:w-[160px]">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-ash-dim" />
              <input
                value={qInput}
                onChange={(e) => setQInput(e.target.value)}
                autoFocus={search.panel === "search"}
                placeholder="搜索文件名 / 主体 / 标签"
                aria-label="搜索资产"
                className="h-8 w-full rounded-ctl border border-rule bg-cell pl-7 pr-2.5 text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
              />
            </div>

            <span className="font-mono text-[11px] text-ash-dim">共 {total} 项</span>

            {multi.size > 0 && (
              <span className="inline-flex h-7 items-center gap-2 rounded-ctl border border-wash-line bg-wash px-2.5 text-[11.5px] text-paper">
                已选 <b className="font-mono font-medium">{multi.size}</b> 项
                <button
                  type="button"
                  onClick={() => setMulti(new Set())}
                  className="text-ash-dim transition-colors hover:text-paper"
                >
                  清除
                </button>
              </span>
            )}

            <span className="flex-1" />
            {listQ.isFetching && !listQ.isPending && (
              <span className="text-[11px] text-ash-dim">更新中</span>
            )}
            <button
              type="button"
              disabled={scanStart.isPending || scanRunning}
              onClick={() => scanStart.mutate()}
              className="inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12.5px] font-medium text-paper transition-all hover:bg-hi active:translate-y-px disabled:opacity-45"
            >
              <RefreshCw
                className={cn("size-3.5", (scanStart.isPending || scanRunning) && "animate-spin")}
              />
              重新扫描
            </button>
          </div>

          <div
            ref={scrollRef}
            onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
            className="min-h-0 flex-1 overflow-auto"
          >
            {listQ.isPending || waitingCollections ? (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(168px,1fr))] gap-3 p-4">
                {Array.from({ length: 12 }, (_, i) => (
                  <div
                    key={i}
                    className="h-[168px] animate-pulse rounded-cell border border-rule-soft bg-cell"
                  />
                ))}
              </div>
            ) : listQ.isError ? (
              <div className="m-4 rounded-panel border border-rule bg-panel p-4">
                <p className="text-[13px] font-medium">
                  {noWorkspace ? "还没有激活工作区" : "资产列表读不出来"}
                </p>
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-ash">
                  {noWorkspace
                    ? "先在项目里激活一个工作区，索引会自己开始扫描。"
                    : (listError?.message ?? "后端没有响应。")}
                </p>
                <button
                  type="button"
                  onClick={() => void listQ.refetch()}
                  className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12.5px] transition-colors hover:bg-hi"
                >
                  <RefreshCw className="size-3.5" />
                  重试
                </button>
              </div>
            ) : !items.length ? (
              <div className="rounded-panel border border-dashed border-rule bg-panel px-6 py-12 text-center m-4">
                <h3 className="text-[15px] font-medium">没有匹配的资产</h3>
                <p className="mx-auto mt-2 max-w-[46ch] text-[12.5px] leading-relaxed text-ash">
                  换个角色或集合，或者把搜索清掉。
                </p>
              </div>
            ) : (
              <div className="px-4 pb-4 pt-1">
                <div className="relative" style={{ height: virtualH }}>
                  <div
                    className="absolute inset-x-0 grid gap-3"
                    style={{
                      top: startRow * CELL_STRIDE,
                      gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
                    }}
                  >
                    {visibleItems.map((item) => (
                      <AssetCell
                        key={item.id}
                        item={item}
                        selected={assetId === item.id}
                        checked={multi.has(item.id)}
                        onOpen={() => onSearchChange({ asset: item.id })}
                        onToggleCheck={() => toggleMulti(item.id)}
                      />
                    ))}
                  </div>
                </div>

                {listQ.hasNextPage && (
                  <div className="flex justify-center py-4">
                    <button
                      type="button"
                      disabled={listQ.isFetchingNextPage}
                      onClick={() => void listQ.fetchNextPage()}
                      className="inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3.5 text-[12.5px] transition-colors hover:bg-hi disabled:opacity-45"
                    >
                      {listQ.isFetchingNextPage ? "加载中…" : "加载更多"}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          {(scanRunning || progress) && (
            <div className="flex-none border-t border-rule-soft bg-panel px-4 py-2">
              <div className="flex items-baseline justify-between font-mono text-[10.5px] text-ash">
                <span>
                  {progress && progress.total > 0
                    ? `扫描 ${progress.processed} / ${progress.total}`
                    : "扫描准备中…"}
                </span>
                <span>{pct}%</span>
              </div>
              <div
                className={cn(
                  "mt-1 h-[3px] overflow-hidden rounded bg-rule-soft",
                  (!progress || progress.total === 0) && "gb-sweep"
                )}
              >
                <div
                  className="h-full bg-white transition-[width] duration-200"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          )}
        </section>

        {/* ---------------- 右栏：详情（只读） ---------------- */}
        <aside className="flex w-[328px] flex-none flex-col overflow-y-auto border-l border-rule-soft bg-panel max-[980px]:hidden">
          {!assetId ? (
            <div className="grid flex-1 place-items-center px-6 text-center">
              <div>
                <Images className="mx-auto size-5 text-ash-dim" strokeWidth={1.6} />
                <p className="mt-2 text-[12.5px] text-ash">点一个资产看详情</p>
                <p className="mt-1 text-[11px] leading-relaxed text-ash-dim">
                  标签 / 别名 / 备注本期只读。
                </p>
              </div>
            </div>
          ) : detailQ.isPending ? (
            <div className="flex flex-col gap-2 p-4">
              <div className="h-5 w-2/3 animate-pulse rounded bg-cell" />
              <div className="h-3 w-full animate-pulse rounded bg-cell" />
              <div className="h-3 w-5/6 animate-pulse rounded bg-cell" />
              <div className="mt-3 h-24 animate-pulse rounded-cell bg-cell" />
            </div>
          ) : detailQ.isError ? (
            <div className="p-4">
              <div className="flex items-start gap-2">
                <p className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-ash">
                  {(detailQ.error as Error)?.message ?? "这个资产读不出来。"}
                </p>
                <button
                  type="button"
                  onClick={() => onSearchChange({ asset: undefined })}
                  aria-label="关闭详情"
                  className="grid size-7 flex-none place-items-center rounded-ctl border border-rule bg-cell text-ash hover:bg-hi hover:text-paper"
                >
                  <X className="size-3.5" />
                </button>
              </div>
            </div>
          ) : detailQ.data ? (
            <AssetDetailPanel
              detail={detailQ.data}
              onClose={() => onSearchChange({ asset: undefined })}
            />
          ) : null}
        </aside>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */

function AssetCell({
  item,
  selected,
  checked,
  onOpen,
  onToggleCheck,
}: {
  item: IndexAssetItem
  selected: boolean
  checked: boolean
  onOpen: () => void
  onToggleCheck: () => void
}) {
  const missing = item.status === "missing"
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={(e) => {
        if (e.ctrlKey || e.metaKey) onToggleCheck()
        else onOpen()
      }}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          onOpen()
        }
      }}
      className={cn(
        "group relative h-[168px] cursor-pointer overflow-hidden rounded-cell border bg-cell transition-colors",
        selected
          ? "border-wash-line bg-hi"
          : "border-rule-soft hover:border-rule hover:bg-hi",
        checked && !selected && "border-wash-line"
      )}
    >
      <div className="relative h-[112px] overflow-hidden bg-black">
        {item.thumb_url ? (
          <img
            src={item.thumb_url}
            alt=""
            loading="lazy"
            className={cn("h-full w-full object-cover", missing && "opacity-40")}
          />
        ) : (
          <span className="grid h-full w-full place-items-center text-ash-dim">
            <KindIcon kind={item.media_kind} />
          </span>
        )}

        {missing && (
          <span className="gb-pill bottom-1.5 left-1.5" data-on="0">
            已丢失
          </span>
        )}

        <button
          type="button"
          role="checkbox"
          aria-checked={checked}
          aria-label={checked ? "取消选中" : "选中这一项"}
          onClick={(e) => {
            e.stopPropagation()
            onToggleCheck()
          }}
          className={cn(
            "absolute left-1.5 top-1.5 grid size-5 place-items-center rounded-[4px] border transition-colors",
            checked
              ? "border-white bg-paper text-[#131313]"
              : "border-white/40 bg-black/55 text-transparent hover:border-white hover:text-paper"
          )}
        >
          <Check className="size-3" strokeWidth={3} />
        </button>
      </div>

      <div className="px-2 py-1.5">
        <p className="truncate text-[12px] leading-[1.35] text-paper" title={item.name}>
          {item.name}
        </p>
        <p
          className="mt-0.5 truncate text-[10.5px] leading-tight text-ash-dim"
          title={item.subject || item.dir_path}
        >
          {item.subject || item.dir_path || "—"}
        </p>
      </div>
    </div>
  )
}

function KindIcon({ kind }: { kind: string }) {
  if (kind === "video") return <Film className="size-5" strokeWidth={1.5} />
  if (kind === "audio") return <FileMusic className="size-5" strokeWidth={1.5} />
  if (kind === "text") return <FileText className="size-5" strokeWidth={1.5} />
  if (kind === "archive") return <FileArchive className="size-5" strokeWidth={1.5} />
  if (kind === "image") return <ImageOff className="size-5" strokeWidth={1.5} />
  return <File className="size-5" strokeWidth={1.5} />
}

function AssetDetailPanel({
  detail,
  onClose,
}: {
  detail: IndexAssetDetail
  onClose: () => void
}) {
  return (
    <>
      <div className="flex flex-none items-start gap-2 border-b border-rule-soft px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[13.5px] font-medium" title={detail.name}>
            {detail.name}
          </h2>
          <p className="mt-0.5 truncate font-mono text-[10.5px] text-ash-dim">
            {detail.path}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭详情"
          className="grid size-7 flex-none place-items-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper"
        >
          <X className="size-3.5" />
        </button>
      </div>

      <div className="px-4 pb-5 pt-1.5">
        <InfoRow label="路径">
          <span className="break-all font-mono text-[11px]">{detail.path}</span>
        </InfoRow>
        <InfoRow label="大小">{humanSize(detail.size)}</InfoRow>
        <InfoRow label="哈希">
          {detail.hash_status === "quick" ? (
            <span className="text-[11.5px] text-ash">快速模式（&gt;2GB 未全量哈希）</span>
          ) : detail.sha256 ? (
            <span className="break-all font-mono text-[11px]" title={detail.sha256}>
              {detail.sha256.slice(0, 16)}…
            </span>
          ) : (
            "—"
          )}
        </InfoRow>
        <InfoRow label="主体">{detail.subject || "—"}</InfoRow>
        <InfoRow label="类别">{detail.category || "—"}</InfoRow>
        <InfoRow label="角色">{ROLE_LABEL[detail.role] ?? detail.role}</InfoRow>
        <InfoRow label="扩展名">
          {detail.ext ? <span className="font-mono">{detail.ext}</span> : "—"}
        </InfoRow>
        {detail.width != null && detail.height != null && (
          <InfoRow label="宽高">
            <span className="font-mono">
              {detail.width} × {detail.height}
            </span>
          </InfoRow>
        )}
        <InfoRow label="状态">
          {detail.status === "missing" ? (
            <span className="text-ash">已丢失</span>
          ) : detail.status === "active" ? (
            "正常"
          ) : (
            detail.status
          )}
        </InfoRow>

        <ChipList label="标签" values={detail.tags} />
        <ChipList label="别名" values={detail.aliases} />

        <div className="border-b border-rule-soft py-2.5">
          <p className="mb-1 text-[11.5px] text-ash-dim">备注</p>
          <p className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-ash">
            {detail.note || "—"}
          </p>
        </div>

        <div className="pt-3">
          <div className="mb-1.5 flex items-baseline gap-2">
            <h3 className="text-[11.5px] text-ash-dim">引用</h3>
            <span className="font-mono text-[10.5px] text-ash-dim">{detail.refs.length}</span>
          </div>
          {detail.refs.length ? (
            <ul className="flex flex-col gap-1">
              {detail.refs.map((r) => (
                <li
                  key={r.id}
                  className="flex items-baseline gap-1 rounded-ctl bg-cell px-2 py-1"
                >
                  <span className="min-w-0 flex-1 break-all font-mono text-[10.5px] text-ash">
                    {r.file_path}
                  </span>
                  {r.line != null && (
                    <span className="flex-none font-mono text-[10.5px] text-ash-dim">
                      :{r.line}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[11.5px] text-ash-dim">还没有文件引用它。</p>
          )}
        </div>
      </div>
    </>
  )
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 border-b border-rule-soft py-1.5">
      <span className="w-[58px] flex-none text-[11.5px] text-ash-dim">{label}</span>
      <span className="min-w-0 flex-1 break-words text-[12px] text-paper">{children}</span>
    </div>
  )
}

function ChipList({ label, values }: { label: string; values: string[] }) {
  return (
    <div className="border-b border-rule-soft py-2.5">
      <p className="mb-1 text-[11.5px] text-ash-dim">{label}</p>
      {values.length ? (
        <div className="flex flex-wrap gap-1.5">
          {values.map((v) => (
            <span key={v} className="gb-chip" data-on="1">
              {v}
            </span>
          ))}
        </div>
      ) : (
        <p className="text-[11.5px] text-ash-dim">—</p>
      )}
    </div>
  )
}
