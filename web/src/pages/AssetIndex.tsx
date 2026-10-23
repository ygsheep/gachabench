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
  ClipboardCopy,
  File,
  FileArchive,
  FileMusic,
  FileText,
  Film,
  Grid2x2,
  ImageOff,
  Images,
  LayoutGrid,
  Plus,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react"
import { cn } from "cn"
import { AssetUploadDialog, type UploadTarget } from "@/components/AssetUploadDialog"
import { AssetOpsDialog, type AssetOpsTarget } from "@/components/AssetOpsDialog"
import { AudioPreview, PinButton, VoiceBars, fmt, useAudioPlayer } from "@/components/AudioCell"
import { MarkdownView } from "@/components/MarkdownView"
import { copyImagesToClipboard } from "@/lib/clipboard"
import { ApiError } from "@/lib/api"
import {
  assetsKey,
  getAsset,
  getAssetCollections,
  getAssetScanStatus,
  getAssets,
  startAssetScan,
} from "@/lib/assetsApi"
import {
  DUPLICATES_KEY,
  TRASH_KEY,
  getDuplicates,
  getTrash,
  invalidateAfterAssetOps,
  opsErrorMessage,
  purgeTrash,
  restoreTrash,
} from "@/lib/opsApi"
import { humanSize, shortStamp } from "@/lib/format"
import { filesFromDataTransfer, isFileDrag, useAssetUpload } from "@/lib/assetUpload"
import { useAssetEvents } from "@/lib/useAssetEvents"
import { useActiveWorkspace } from "@/lib/workspace"
import type {
  AssetOpsKind,
  DuplicateGroup,
  IndexAssetDetail,
  IndexAssetItem,
  TrashEntry,
} from "@/lib/types"

export interface AssetIndexQuery {
  role?: string
  category?: string
  collection?: string
  q?: string
  /** 一级目录 tab：`素材`（当前集要用的）或 `资产`（全量资产库）。 */
  dir?: string
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

const COLLECTION_ORDER = ["recent", "missing", "unreferenced", "large", "trashed"]

const OUTLINE_BTN =
  "inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12.5px] font-medium transition-all hover:bg-hi active:translate-y-px"
const INSPECTOR_BTN =
  "inline-flex h-7 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] font-medium text-ash transition-all hover:bg-hi hover:text-paper active:translate-y-px"

interface ScanProgress {
  processed: number
  total: number
}

/** 文件名主体名：第一个 `_` / 空白分隔段 */
function subjectOf(name: string): string {
  const stem = name.replace(/\.[^.]+$/, "")
  const first = stem.split(/[_\s]+/)[0]
  return first || "未归组"
}

/**
 * 资产库主页面（重设版）。
 *
 * 布局回到用户认可的旧资产页：页头三钮 + 双行筛选 + 按「套」分组的卡片铺满主区，
 * 右栏检查器常驻。索引（P1）与文件操作（P2）的能力折进三处：
 *   ① 筛选行：角色视角 + 内置集合（最近 / 已丢失 / 未被引用 / 大文件 / 回收站 / 重复文件）
 *   ② 检查器：预览 + 元信息 + 引用位置 + 单件操作（改名 / 移动 / 回收站）
 *   ③ 多选操作条：改名 / 移动 / 删除，全部走「先计划后应用」
 */
export function AssetIndex({
  search,
  onSearchChange,
}: {
  search: AssetIndexQuery
  onSearchChange: (patch: Partial<AssetIndexQuery>) => void
}) {
  const workspace = useActiveWorkspace()
  const qc = useQueryClient()

  /* ---------------- 视图模式 / 多选 / 弹窗 ---------------- */
  const [mode, setMode] = useState<"grouped" | "flat">("grouped")
  const [multi, setMulti] = useState<Set<string>>(() => new Set())
  const [dlg, setDlg] = useState<UploadTarget | null>(null)
  const [opsTarget, setOpsTarget] = useState<AssetOpsTarget | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  /* 双击预览只记被点的那一项，关掉即清空（不复用选中态：预览是「打开看一眼」，
     不该改变右侧详情面板在讲谁） */
  const [preview, setPreview] = useState<IndexAssetItem | null>(null)

  /* ---------------- 集合 / 角色 ---------------- */
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
  /** URL `panel=duplicates` —— 用视图入口，不伪造 collection，避免后端 404 */
  const duplicatesMode = search.panel === "duplicates"
  const trashedMode = activeCollection === "trashed"
  const specialMode = duplicatesMode || trashedMode

  /** 目录 tab：默认「素材」（当前集要用的），可切到「资产」（全量资产库）。
      只有这两个合法值；其余一律回落到「素材」。 */
  const DIR_TABS = ["素材", "资产"] as const
  const activeDir = DIR_TABS.includes(search.dir as (typeof DIR_TABS)[number])
    ? (search.dir as (typeof DIR_TABS)[number])
    : "素材"
  /** 「资产」是全量库（几百张），强制平铺 + 懒加载；「素材」才允许分组视图。 */
  const effectiveMode: "grouped" | "flat" =
    activeDir === "资产" ? "flat" : mode

  /* ---------------- 重复 / 回收站（chip 计数 + 面板 + 检查器共用） ---------------- */
  const dupQ = useQuery({
    queryKey: DUPLICATES_KEY,
    queryFn: ({ signal }) => getDuplicates({ signal }),
    enabled: !!workspace,
    retry: 1,
  })
  const trashQ = useQuery({
    queryKey: TRASH_KEY,
    queryFn: ({ signal }) => getTrash({ signal }),
    enabled: !!workspace && trashedMode,
    retry: 1,
  })
  const dupGroups: DuplicateGroup[] = dupQ.data?.groups ?? []
  const trashEntries: TrashEntry[] = trashQ.data?.items ?? []

  const restoreM = useMutation({
    mutationFn: (id: string) => restoreTrash(id),
    onSuccess: (r) => {
      invalidateAfterAssetOps(qc)
      toast.success(`已恢复：${r.name}`, { description: `回到 ${r.path}` })
    },
    onError: (e: Error) => toast.error(opsErrorMessage(e)),
  })
  const purgeM = useMutation({
    mutationFn: (id: string) => purgeTrash(id),
    onSuccess: () => {
      invalidateAfterAssetOps(qc)
      onSearchChange({ asset: undefined })
      toast.success("已彻底清除", { description: "文件与索引记录都没了，不可恢复" })
    },
    onError: (e: Error) => toast.error(opsErrorMessage(e)),
  })
  const trashBusyId = restoreM.isPending
    ? restoreM.variables
    : purgeM.isPending
      ? purgeM.variables
      : null

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

  /* ---------------- 主列表（无限） ---------------- */
  const listQ = useInfiniteQuery({
    queryKey: assetsKey({
      role: search.role,
      category: search.category,
      collection: activeCollection,
      q: search.q,
      dir: activeDir,
    }),
    queryFn: ({ pageParam, signal }) =>
      getAssets(
        {
          role: search.role,
          category: search.category,
          collection: activeCollection,
          q: search.q,
          dir: activeDir,
          cursor: pageParam,
          limit: PAGE_SIZE,
        },
        { signal }
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: !!workspace && !waitingCollections && !specialMode,
    retry: 1,
  })

  const items = useMemo(
    () => listQ.data?.pages.flatMap((page) => page.items) ?? [],
    [listQ.data]
  )
  const total = listQ.data?.pages[0]?.total ?? 0
  const listRoot = listQ.data?.pages[0]?.root ?? ""

  /* 分组视图要看到完整集合，把后续分页自动拉完（上限 500/页，通常 2~3 页）。
     「素材」tab 文件少（几十个），分组有意义，拉完没问题；
     「资产」tab 是全量库（几百个），必须严格懒加载，绝不自动拉完。 */
  const needAllPages =
    mode === "grouped" &&
    activeDir === "素材" &&
    !specialMode &&
    listQ.hasNextPage &&
    !listQ.isFetchingNextPage
  useEffect(() => {
    if (needAllPages) void listQ.fetchNextPage()
  }, [needAllPages, listQ])

  /* ---------------- 分组：角色 → 主体（「套」） ---------------- */
  const sections = useMemo(() => {
    const order = roleRows.map((r) => r.key)
    const byRole = new Map<string, Map<string, IndexAssetItem[]>>()
    for (const it of items) {
      const role = it.role || "other"
      const subj = it.subject.trim() || "未归组"
      if (!byRole.has(role)) byRole.set(role, new Map())
      const m = byRole.get(role)!
      if (m.has(subj)) m.get(subj)!.push(it)
      else m.set(subj, [it])
    }
    const keys = [...byRole.keys()].sort((a, b) => {
      const ia = order.indexOf(a)
      const ib = order.indexOf(b)
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b)
    })
    return keys.map((role) => {
      const groups = [...byRole.get(role)!.entries()]
        .map(([name, list]) => ({
          name,
          items: list,
          subdir: list[0]?.dir_path ?? "",
          tags: [...new Set(list.flatMap((i) => i.tags))].slice(0, 4),
        }))
        .sort((a, b) => b.items.length - a.items.length || a.name.localeCompare(b.name))
      const n = groups.reduce((a, g) => a + g.items.length, 0)
      return { role, groups, n }
    })
  }, [items, roleRows])

  /* ---------------- 多选 ---------------- */
  function toggleMulti(id: string) {
    setMulti((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const selectedItems = useMemo(() => items.filter((it) => multi.has(it.id)), [items, multi])

  function openOps(kind: AssetOpsKind, ids: string[], meta?: { name?: string; dir?: string }) {
    if (!ids.length) return
    if (kind === "rename" && ids.length !== 1) return
    setOpsTarget({
      mode: kind,
      assetIds: ids,
      defaultName: kind === "rename" ? meta?.name : undefined,
      defaultDir: kind === "move" ? (meta?.dir ?? "") : undefined,
    })
  }
  /** 操作条入口：多选同目录才预填目标目录，混目录的留空让用户填 */
  function openOpsFromSelection(kind: AssetOpsKind) {
    if (!multi.size) return
    const first = selectedItems[0]
    const sameDir = !!first && selectedItems.every((it) => it.dir_path === first.dir_path)
    openOps(kind, [...multi], {
      name: first?.name,
      dir: sameDir ? first.dir_path : "",
    })
  }

  /* ---------------- 复制图片到剪贴板 ---------------- */
  async function copySelectedToClipboard() {
    const imgs = selectedItems.filter((it) => it.media_kind === "image" && !!it.media_url)
    if (!imgs.length) {
      toast.error("选中的不是图片，复制不了")
      return
    }
    const urls = imgs.map((it) => it.media_url)
    const r = await copyImagesToClipboard(urls)
    if (r.ok) {
      if (urls.length > 1) {
        // 当前内核不支持多条目，只复制了最后一张 —— 诚实告知，不假装全复制成功
        toast("已复制最后选中的那张图片", {
          description: "当前环境一次只能复制单张，多选时取最后一张",
        })
      } else {
        toast.success("已复制图片到剪贴板", { description: "去微信 / 文档 / 画图里 Ctrl+V 粘贴" })
      }
    } else {
      toast.error(r.message)
    }
  }

  async function copyOneToClipboard(url: string, name: string) {
    const r = await copyImagesToClipboard([url])
    if (r.ok) {
      toast.success("已复制图片到剪贴板", { description: name })
    } else {
      toast.error(r.message)
    }
  }

  /* ---------------- 虚拟网格度量（平铺） ---------------- */
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
    search.panel ?? "",
    activeDir,
  ])
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = 0
    setScrollTop(0)
    /* 筛选一变，勾选就作废——留着会点错 */
    setMulti(new Set())
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
    invalidateAfterAssetOps(qc)
    if (st.status === "error") {
      toast.error(`扫描失败：${String(st.stats.error ?? "后端未给出原因")}`)
    }
  }, [scanStatusQ.data, qc])

  /* ---------------- SSE：进度 + 变化 ---------------- */
  useAssetEvents({
    enabled: !!workspace,
    onProgress: (e) => {
      if (e.done) {
        setScanRunning(false)
        setProgress(null)
        invalidateAfterAssetOps(qc)
        return
      }
      setScanRunning(true)
      setProgress({ processed: e.processed, total: e.total })
    },
    onChanged: () => {
      invalidateAfterAssetOps(qc)
    },
  })

  /* ---------------- 详情 ---------------- */
  const assetId = search.asset
  const detailQ = useQuery({
    queryKey: ["index-asset", workspace, assetId],
    queryFn: ({ signal }) => getAsset(assetId ?? "", { signal }),
    enabled: !!workspace && !!assetId && !trashedMode,
    retry: 1,
  })

  /* ---------------- 上传（拖到卡片 / 页面级拖放 / 粘贴） ---------------- */
  const upload = useAssetUpload()

  function refreshAll() {
    invalidateAfterAssetOps(qc)
  }

  function quickUpload(subject: string, subdir: string, files: File[]) {
    upload.mutate({ prefix: subject, suffix: "", category: "character", subdir, files })
  }

  /** 三个入口汇到这一处决定去向：有主体名直接进那一套，否则交给对话框问 */
  const incomingRef = useRef<(files: File[], subject?: string | null) => void>(() => {})
  useEffect(() => {
    incomingRef.current = (files, subject) => {
      if (subject) {
        const it = items.find((i) => (i.subject.trim() || "未归组") === subject)
        quickUpload(subject, it?.dir_path ?? "", files)
      } else {
        setDlg({ mode: "attach", files })
      }
    }
  })

  const overlayOpen = !!dlg || !!opsTarget
  const overlayRef = useRef(false)
  useEffect(() => {
    overlayRef.current = overlayOpen
  })

  // 拖放 / 粘贴：dragover 看门狗判拖拽态（350ms 不收就算拖拽中），
  // 落点只在页面级算一次（elementFromPoint → [data-asset-group]）
  useEffect(() => {
    let lastOver = 0
    const clear = () => {
      lastOver = 0
      setDragActive(false)
      setDropTarget(null)
    }
    function onOver(e: DragEvent) {
      if (!isFileDrag(e)) return
      e.preventDefault()
      lastOver = Date.now()
      if (overlayRef.current) return
      const card = document
        .elementFromPoint(e.clientX, e.clientY)
        ?.closest?.("[data-asset-group]")
      setDropTarget(card ? card.getAttribute("data-asset-group") : null)
      setDragActive(true)
    }
    function onDrop(e: DragEvent) {
      if (!isFileDrag(e)) return
      e.preventDefault()
      clear()
      if (overlayRef.current) return
      const el = e.target as HTMLElement | null
      if (el?.closest?.("[data-asset-group]")) return // 卡片自己收下了
      const files = Array.from(e.dataTransfer?.files ?? [])
      if (files.length) incomingRef.current(files)
    }
    function onPaste(e: ClipboardEvent) {
      if (overlayRef.current) return
      const el = document.activeElement as HTMLElement | null
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable))
        return
      const files = filesFromDataTransfer(e.clipboardData)
      if (!files.length) return
      e.preventDefault()
      incomingRef.current(files)
    }
    const watchdog = window.setInterval(() => {
      if (lastOver && Date.now() - lastOver > 350) clear()
    }, 180)
    window.addEventListener("dragover", onOver, true)
    window.addEventListener("drop", onDrop)
    window.addEventListener("paste", onPaste)
    return () => {
      window.clearInterval(watchdog)
      window.removeEventListener("dragover", onOver, true)
      window.removeEventListener("drop", onDrop)
      window.removeEventListener("paste", onPaste)
    }
  }, [])

  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.processed / progress.total) * 100))
      : 0

  const listError = listQ.error as Error | null
  const noWorkspace = !!listError && listError instanceof ApiError && listError.status === 400
  const roleTotal = roleRows.reduce((a, r) => a + r.count, 0)
  const trashEntry = trashedMode
    ? trashEntries.find((t) => t.asset_id === assetId) ?? null
    : null

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* ================= 页头 ================= */}
      <header className="flex flex-wrap items-end gap-x-3.5 gap-y-2 px-6 pb-3.5 pt-5">
        <div className="max-w-[82ch]">
          <h1 className="text-[19px] font-semibold leading-tight">资产管理</h1>
          <p className="pt-0.5 text-[12.5px] text-ash">
            参考图和参考音频是 <span className="font-mono">&lt;Picture N&gt;</span> /{" "}
            <span className="font-mono">&lt;Audio N&gt;</span> 的锚。索引覆盖整个工作区，文件名主体名相同即归为同一套。
          </p>
          <p className="pt-0.5 text-[11.5px] text-ash-dim">
            点缩略图打开检查器 · 勾选后顶部出现操作条 · 拖文件到某一套直接存入
          </p>
        </div>
        <span className="flex-1" />
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={scanStart.isPending || scanRunning}
            onClick={() => scanStart.mutate()}
            title="重新递归扫描整个工作区并增量入库"
            className={cn(OUTLINE_BTN, "disabled:opacity-45")}
          >
            <RefreshCw
              className={cn("size-3.5", (scanStart.isPending || scanRunning) && "animate-spin")}
            />
            重扫
          </button>
          <button
            type="button"
            onClick={() => setDlg({ mode: "attach" })}
            className={OUTLINE_BTN}
          >
            <Upload className="size-3.5" />
            上传资产
          </button>
          <button
            type="button"
            onClick={() => setDlg({ mode: "new" })}
            className="inline-flex h-8 items-center gap-1.5 rounded-ctl bg-paper px-3 text-[12.5px] font-medium text-[#131313] transition-transform hover:bg-white active:translate-y-px active:scale-[0.99]"
          >
            <Plus className="size-3.5" />
            新建资产
          </button>
        </div>
      </header>

      {/* ================= 目录 tab：素材（当前集） / 资产（全量库） ================= */}
      {!specialMode && (
        <div className="flex items-center gap-1 px-6 pb-3">
          {DIR_TABS.map((t) => {
            const active = activeDir === t
            return (
              <button
                key={t}
                type="button"
                onClick={() => onSearchChange({ dir: t, role: undefined, collection: undefined, panel: undefined })}
                aria-pressed={active}
                className={cn(
                  "inline-flex h-8 items-center gap-1.5 rounded-ctl border px-3.5 text-[12.5px] font-medium transition-colors",
                  active
                    ? "border-wash-line bg-paper text-[#131313]"
                    : "border-rule-soft bg-cell text-ash hover:bg-hi hover:text-paper"
                )}
              >
                {t === "素材" ? <Images className="size-3.5" strokeWidth={1.7} /> : <LayoutGrid className="size-3.5" strokeWidth={1.7} />}
                {t}
                <span className="font-mono text-[10.5px] opacity-60">
                  {t === "素材" ? "当前集" : "全量"}
                </span>
              </button>
            )
          })}
        </div>
      )}

      {/* ================= 筛选第一行 ================= */}
      <div className="flex flex-wrap items-center gap-2.5 px-6 pb-2.5">
        <div className="relative w-[236px] max-[980px]:w-[160px]">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-ash-dim" />
          <input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="搜索文件名 / 主体 / 标签"
            aria-label="搜索资产"
            className="h-8 w-full rounded-ctl border border-rule bg-cell pl-7 pr-2.5 text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
          />
        </div>

        {!specialMode && (
          <>
            <select
              value={search.role ?? ""}
              onChange={(e) => onSearchChange({ role: e.target.value || undefined })}
              aria-label="按角色筛选"
              className="h-8 w-[128px] rounded-ctl border border-rule bg-cell px-2 text-[12.5px] outline-none focus:border-wash-line"
            >
              <option value="">角色：全部</option>
              {roleRows.map((r) => (
                <option key={r.key} value={r.key}>
                  {r.label} {r.count}
                </option>
              ))}
            </select>

            {/* 视图切换只在「素材」tab 有意义；「资产」是全量库，强制平铺 + 懒加载 */}
            {activeDir === "素材" && (
              <div
                role="group"
                aria-label="视图模式"
                className="inline-flex overflow-hidden rounded-ctl border border-rule bg-cell"
              >
                {([
                  { key: "grouped", label: "分组", Icon: LayoutGrid },
                  { key: "flat", label: "平铺", Icon: Grid2x2 },
                ] as const).map(({ key, label, Icon }) => (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={mode === key}
                    onClick={() => setMode(key)}
                    className={cn(
                      "inline-flex h-8 items-center gap-1 px-2.5 text-[11.5px] transition-colors",
                      mode === key
                        ? "bg-hi font-medium text-paper"
                        : "text-ash hover:bg-hi hover:text-paper"
                    )}
                  >
                    <Icon className="size-3.5" strokeWidth={1.7} />
                    {label}
                  </button>
                ))}
              </div>
            )}
          </>
        )}

        <span className="flex-1" />
        {listQ.isFetching && !listQ.isPending && (
          <span
            className="inline-flex h-7 items-center rounded-ctl border border-dashed border-rule px-2 text-[11px] text-ash-dim"
            title="先用上次的结果占位，扫描结束会替换"
          >
            旧结果 · 更新中
          </span>
        )}
        {listRoot && (
          <span
            className="max-w-[52ch] truncate font-mono text-[11px] text-ash-dim"
            title={listRoot}
          >
            索引根 <b className="text-ash">{listRoot}</b>
          </span>
        )}
      </div>

      {/* ================= 筛选第二行：集合 chips + 多选操作条 ================= */}
      <div className="flex flex-wrap items-center gap-2 px-6 pb-3">
        <div className="inline-flex rounded-ctl border border-rule bg-panel p-0.5">
          <button
            type="button"
            onClick={() =>
              onSearchChange({ collection: undefined, panel: undefined, role: undefined })
            }
            aria-pressed={!search.collection && !duplicatesMode}
            className={cn(
              "inline-flex h-[26px] items-center gap-1.5 rounded-[4px] px-2.5 text-[12.5px] transition-colors",
              !search.collection && !duplicatesMode
                ? "bg-hi font-medium text-paper"
                : "text-ash hover:text-paper"
            )}
          >
            全部
            <span
              className={cn(
                "font-mono text-[11px]",
                !search.collection && !duplicatesMode ? "text-white" : "text-ash-dim"
              )}
            >
              {roleTotal}
            </span>
          </button>
        </div>

        {collectionRows.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() =>
              onSearchChange({
                collection: search.collection === c.id ? undefined : c.id,
                panel: undefined,
                role: undefined,
              })
            }
            aria-pressed={search.collection === c.id}
            className={cn(
              "inline-flex h-[26px] items-center gap-1.5 rounded-[4px] border px-2.5 text-[12px] transition-colors",
              search.collection === c.id
                ? "border-wash-line bg-wash font-medium text-paper"
                : "border-rule-soft bg-cell text-ash hover:bg-hi hover:text-paper"
            )}
          >
            {c.name}
            <span className="font-mono text-[11px] text-ash-dim">{c.count}</span>
          </button>
        ))}

        <button
          type="button"
          onClick={() =>
            onSearchChange({
              panel: duplicatesMode ? undefined : "duplicates",
              collection: undefined,
              role: undefined,
            })
          }
          aria-pressed={duplicatesMode}
          className={cn(
            "inline-flex h-[26px] items-center gap-1.5 rounded-[4px] border px-2.5 text-[12px] transition-colors",
            duplicatesMode
              ? "border-wash-line bg-wash font-medium text-paper"
              : "border-rule-soft bg-cell text-ash hover:bg-hi hover:text-paper"
          )}
        >
          重复文件
          <span className="font-mono text-[11px] text-ash-dim">{dupGroups.length || "—"}</span>
        </button>

        {/* 多选操作条：勾选后才出现 */}
        {multi.size > 0 && !trashedMode && (
          <>
            <span className="mx-1 h-4 w-px bg-rule-soft" />
            <span className="inline-flex h-[26px] items-center gap-2 rounded-[4px] border border-wash-line bg-wash px-2.5 text-[12px] text-paper">
              已选 <b className="font-mono font-medium">{multi.size}</b> 项
              <button
                type="button"
                onClick={() => setMulti(new Set())}
                className="text-ash-dim transition-colors hover:text-paper"
              >
                清除
              </button>
            </span>
            <button
              type="button"
              onClick={() => void copySelectedToClipboard()}
              className="inline-flex h-[26px] items-center gap-1 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] font-medium text-paper transition-all hover:bg-hi active:translate-y-px"
            >
              <ClipboardCopy className="size-3.5" />
              复制
            </button>
            <button
              type="button"
              disabled={multi.size !== 1}
              title={multi.size === 1 ? "改名并同步改写引用" : "改名一次一个"}
              onClick={() => openOpsFromSelection("rename")}
              className="inline-flex h-[26px] items-center rounded-ctl border border-rule bg-cell px-2.5 text-[12px] font-medium text-paper transition-all hover:bg-hi active:translate-y-px disabled:opacity-45"
            >
              改名
            </button>
            <button
              type="button"
              onClick={() => openOpsFromSelection("move")}
              className="inline-flex h-[26px] items-center rounded-ctl border border-rule bg-cell px-2.5 text-[12px] font-medium text-paper transition-all hover:bg-hi active:translate-y-px"
            >
              移动
            </button>
            <button
              type="button"
              onClick={() => openOpsFromSelection("delete")}
              className="inline-flex h-[26px] items-center rounded-ctl border border-rule bg-cell px-2.5 text-[12px] font-medium text-paper transition-all hover:bg-hi active:translate-y-px"
            >
              删除
            </button>
          </>
        )}
      </div>

      {/* ================= 主区 + 检查器 ================= */}
      <div className="flex min-h-0 flex-1 max-[980px]:flex-col">
        <main
          ref={scrollRef}
          onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
          className="min-w-0 flex-1 overflow-auto"
        >
          {duplicatesMode ? (
            <DuplicatesPanel
              groups={dupGroups}
              pending={dupQ.isPending}
              error={dupQ.error as Error | null}
              selectedId={assetId}
              multi={multi}
              onOpen={(id) => onSearchChange({ asset: id })}
              onToggleCheck={toggleMulti}
              onPreview={setPreview}
              onCleanOlder={(ids) => openOps("delete", ids)}
            />
          ) : trashedMode ? (
            <TrashPanel
              entries={trashEntries}
              pending={trashQ.isPending}
              error={trashQ.error as Error | null}
              busyId={trashBusyId}
              onRestore={(id) => restoreM.mutate(id)}
              onPurge={(id) => purgeM.mutate(id)}
              onOpen={(entry) => onSearchChange({ asset: entry.asset_id })}
            />
          ) : listQ.isPending || waitingCollections ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(168px,1fr))] gap-3 p-4">
              {Array.from({ length: 12 }, (_, i) => (
                <div
                  key={i}
                  className="h-[168px] overflow-hidden rounded-cell border border-rule-soft bg-cell"
                >
                  {/* 图片区骨架 + 底部文字骨架，接近真实卡片结构，加载时更「有内容感」 */}
                  <div className="h-[112px] animate-pulse bg-muted" />
                  <div className="space-y-1.5 px-2.5 py-2">
                    <div className="h-2.5 w-3/4 animate-pulse rounded bg-muted" />
                    <div className="h-2 w-1/2 animate-pulse rounded bg-muted" />
                  </div>
                </div>
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
          ) : !total ? (
            <div className="m-4 rounded-panel border border-dashed border-rule bg-panel px-6 py-12 text-center">
              <h3 className="text-[15px] font-medium">素材目录还是空的</h3>
              <p className="mx-auto mt-2 max-w-[52ch] text-[12.5px] leading-relaxed text-ash">
                上传人物定妆、场景图、配音，文件名会按{" "}
                <span className="font-mono">主体名_用途.png</span> 自动落名，主体名相同即归为一套。
                也可以直接拖文件进来，或复制图片后按 <span className="font-mono">Ctrl+V</span>。
              </p>
              <button
                type="button"
                onClick={() => setDlg({ mode: "new" })}
                className="mt-4 inline-flex h-8 items-center gap-1.5 rounded-ctl bg-paper px-3 text-[12.5px] font-medium text-[#131313] transition-transform hover:bg-white active:translate-y-px"
              >
                <Upload className="size-3.5" />
                上传第一个资产
              </button>
            </div>
          ) : !items.length ? (
            <div className="m-4 rounded-panel border border-dashed border-rule bg-panel px-6 py-12 text-center">
              <h3 className="text-[15px] font-medium">没有匹配的资产</h3>
              <p className="mx-auto mt-2 max-w-[46ch] text-[12.5px] leading-relaxed text-ash">
                换个角色或集合，或者把搜索清掉。
              </p>
            </div>
          ) : effectiveMode === "grouped" ? (
            /* 一个角色一节，节内一个主体一张「套」卡 */
            <div className="flex flex-col gap-4 px-4 pb-6 pt-3">
              {sections.map((sec) => (
                <div key={sec.role}>
                  <div className="mb-2.5 flex items-baseline gap-2.5 border-b border-rule-soft pb-2">
                    <h2 className="text-[13.5px] font-medium">
                      {roleRows.find((r) => r.key === sec.role)?.label ?? sec.role}
                    </h2>
                    <span className="font-mono text-[11.5px] text-ash-dim">
                      {sec.groups.length} 套 · {sec.n} 个文件
                    </span>
                    <span className="flex-1" />
                    {sec.role === "asset" && (
                      <span className="text-[11.5px] text-ash-dim">
                        接到 <span className="font-mono">&lt;Picture N&gt;</span> /{" "}
                        <span className="font-mono">&lt;Audio N&gt;</span>
                      </span>
                    )}
                  </div>
                  <div className="flex flex-col gap-3">
                    {sec.groups.map((g) => (
                      <SubjectCard
                        key={g.name}
                        name={g.name}
                        subdir={g.subdir}
                        tags={g.tags}
                        items={g.items}
                        selectedId={assetId}
                        multi={multi}
                        dropping={dropTarget === g.name}
                        onCell={(item, e) => {
                          if (e.ctrlKey || e.metaKey) toggleMulti(item.id)
                          else onSearchChange({ asset: item.id })
                        }}
                        onToggleCheck={toggleMulti}
                        onPreview={setPreview}
                        onUpload={() =>
                          setDlg({
                            mode: "attach",
                            subject: g.name,
                            subdir: g.subdir,
                            category: "character",
                          })
                        }
                        onDropFiles={(files) => incomingRef.current(files, g.name)}
                      />
                    ))}
                  </div>
                </div>
              ))}
              {listQ.isFetchingNextPage && (
                <p className="py-1 text-center text-[11px] text-ash-dim">加载其余资产…</p>
              )}
            </div>
          ) : (
            /* 平铺：按行窗口化的虚拟网格 */
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
                      onOpen={(e) => {
                        if (e.ctrlKey || e.metaKey) toggleMulti(item.id)
                        else onSearchChange({ asset: item.id })
                      }}
                      onToggleCheck={() => toggleMulti(item.id)}
                      onPreview={setPreview}
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
        </main>

        {/* ---------------- 右栏检查器 ---------------- */}
        <aside className="flex w-[328px] flex-none flex-col overflow-y-auto border-l border-rule-soft bg-panel max-[980px]:hidden">
          {trashedMode ? (
            assetId && trashEntry ? (
              <TrashInspector
                entry={trashEntry}
                onClose={() => onSearchChange({ asset: undefined })}
                onRestore={(id) => restoreM.mutate(id)}
                onPurge={(id) => purgeM.mutate(id)}
              />
            ) : (
              <div className="grid flex-1 place-items-center px-6 text-center">
                <div>
                  <Trash2 className="mx-auto size-5 text-ash-dim" strokeWidth={1.6} />
                  <p className="mt-2 text-[12.5px] text-ash">点回收站里的一项看详情</p>
                  <p className="mt-1 text-[11px] leading-relaxed text-ash-dim">
                    恢复会回到原路径，撞名自动补号；彻底清除不可恢复。
                  </p>
                </div>
              </div>
            )
          ) : !assetId ? (
            <div className="grid flex-1 place-items-center px-6 text-center">
              <div>
                <Images className="mx-auto size-5 text-ash-dim" strokeWidth={1.6} />
                <p className="mt-2 text-[12.5px] text-ash">点一个资产看详情</p>
                <p className="mt-1 text-[11px] leading-relaxed text-ash-dim">
                  路径 / 大小 / 哈希 / 引用位置都在这里；标签与别名本期只读。
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
            <AssetInspector
              detail={detailQ.data}
              onClose={() => onSearchChange({ asset: undefined })}
              onCopy={() =>
                detailQ.data?.media_url &&
                void copyOneToClipboard(detailQ.data.media_url, detailQ.data.name)
              }
              onRename={() =>
                openOps("rename", [detailQ.data!.id], {
                  name: detailQ.data!.name,
                  dir: detailQ.data!.dir_path,
                })
              }
              onMove={() =>
                openOps("move", [detailQ.data!.id], { dir: detailQ.data!.dir_path })
              }
              onTrash={() => openOps("delete", [detailQ.data!.id])}
            />
          ) : null}
        </aside>
      </div>

      {/* ================= 扫描进度（底条） ================= */}
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

      {/* ================= 拖放进行时的明示 ================= */}
      {dragActive && !dropTarget && !overlayOpen && (
        <div className="pointer-events-none fixed inset-0 z-70 grid place-items-center p-6">
          <div className="absolute inset-4 rounded-panel border border-dashed border-wash-line bg-black/45" />
          <div className="relative rounded-panel border border-rule bg-panel/95 px-5 py-4 text-center shadow-2xl shadow-black/60">
            <p className="text-[14px] font-medium">放开 → 弹出上传对话框</p>
            <p className="mt-1 text-[11.5px] text-ash-dim">
              也可以先拖到某一套的卡片上，直接就存进那一套。
            </p>
          </div>
        </div>
      )}

      {dlg && (
        <AssetUploadDialog
          target={dlg}
          rels={items.map((i) => i.path)}
          onClose={() => setDlg(null)}
          onDone={() => {
            setDlg(null)
            refreshAll()
          }}
        />
      )}

      {preview && <MediaPreview item={preview} onClose={() => setPreview(null)} />}

      {opsTarget && (
        <AssetOpsDialog
          target={opsTarget}
          onClose={() => setOpsTarget(null)}
          onApplied={() => {
            setMulti(new Set())
            if (opsTarget.mode === "delete") onSearchChange({ asset: undefined })
          }}
        />
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* 套卡：一个主体一张（选中目标 + 拖放目标）                              */
/* ------------------------------------------------------------------ */

function SubjectCard({
  name,
  subdir,
  tags,
  items,
  selectedId,
  multi,
  dropping,
  onCell,
  onToggleCheck,
  onPreview,
  onUpload,
  onDropFiles,
}: {
  name: string
  subdir: string
  tags: string[]
  items: IndexAssetItem[]
  selectedId?: string
  multi: Set<string>
  dropping: boolean
  onCell: (item: IndexAssetItem, e: React.MouseEvent) => void
  onToggleCheck: (id: string) => void
  onPreview: (item: IndexAssetItem) => void
  onUpload: () => void
  onDropFiles: (files: File[]) => void
}) {
  const visuals = items.filter((it) => it.media_kind === "image" || it.media_kind === "video")
  const audios = items.filter((it) => it.media_kind === "audio")
  const others = items.filter(
    (it) => it.media_kind !== "image" && it.media_kind !== "video" && it.media_kind !== "audio"
  )

  return (
    <section
      data-asset-group={name}
      onDragOver={(e) => {
        if (isFileDrag(e.nativeEvent)) {
          e.preventDefault()
          e.dataTransfer.dropEffect = "copy"
        }
      }}
      onDrop={(e) => {
        e.preventDefault()
        const files = Array.from(e.dataTransfer.files)
        if (files.length) onDropFiles(files)
      }}
      className={cn(
        "relative rounded-panel border p-3.5 transition-colors",
        dropping && "border-dashed border-wash-line",
        !dropping && "border-rule-soft bg-panel hover:border-rule"
      )}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2.5">
        <h3 className="text-[13px] font-medium">{name}</h3>
        <span className="rounded border border-rule px-1.5 py-0.5 font-mono text-[11px] text-ash">
          {items.length} 项
        </span>
        {tags.length > 0 && (
          <span className="truncate text-[11.5px] text-ash-dim">{tags.join(" · ")}</span>
        )}
        {subdir && (
          <span className="max-w-[28ch] truncate font-mono text-[10px] text-ash-dim" title={subdir}>
            {subdir}
          </span>
        )}
        <span className="flex-1" />
        <button
          type="button"
          onClick={onUpload}
          title={`往「${name}」里加素材`}
          className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] font-medium text-ash transition-all hover:bg-hi hover:text-paper active:translate-y-px"
        >
          <Upload className="size-3.5" />
          上传
        </button>
      </div>

      {(!!visuals.length || !!audios.length) && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(132px,1fr))] gap-2.5">
          {visuals.map((it) => (
            <AssetCell
              key={it.id}
              item={it}
              selected={selectedId === it.id}
              checked={multi.has(it.id)}
              onOpen={(e) => onCell(it, e)}
              onToggleCheck={() => onToggleCheck(it.id)}
              onPreview={onPreview}
            />
          ))}
          {audios.map((it) => (
            <AssetCell
              key={it.id}
              item={it}
              selected={selectedId === it.id}
              checked={multi.has(it.id)}
              onOpen={(e) => onCell(it, e)}
              onToggleCheck={() => onToggleCheck(it.id)}
              onPreview={onPreview}
            />
          ))}
          <DropTile />
        </div>
      )}

      {!!others.length && (
        <div className={cn("flex flex-col gap-1.5", (visuals.length || audios.length) && "mt-3")}>
          {others.map((it) => (
            <button
              key={it.id}
              type="button"
              onClick={(e) => onCell(it, e)}
              className={cn(
                "flex items-center gap-2.5 rounded-ctl border px-2.5 py-2 text-left transition-colors",
                selectedId === it.id
                  ? "border-wash-line bg-wash"
                  : "border-rule-soft bg-cell hover:bg-hi",
                multi.has(it.id) && "border-wash-line"
              )}
            >
              <KindIcon kind={it.media_kind} />
              <span className="min-w-0 flex-1 truncate text-[12px] text-ash">{it.name}</span>
              <span className="font-mono text-[10.5px] text-ash-dim">{humanSize(it.size)}</span>
            </button>
          ))}
        </div>
      )}

      {/* 拖放中的明确承诺 */}
      {dropping && (
        <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-panel border border-dashed border-wash-line bg-black/50">
          <div className="text-center">
            <p className="text-[13px] font-medium text-paper">放开 → 存到「{name}」</p>
            <p className="mt-1 font-mono text-[11px] text-ash">
              落名 {name}_用途 · 重名自动补号
            </p>
          </div>
        </div>
      )}
    </section>
  )
}

/* ------------------------------------------------------------------ */
/* 素材格                                                               */
/* ------------------------------------------------------------------ */

const KIND_LABEL: Record<string, string> = {
  image: "图",
  video: "视频",
  audio: "音频",
  text: "文本",
  archive: "压缩",
  other: "其它",
}

function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? KIND_LABEL.other
}

function AssetCell({
  item,
  selected,
  checked,
  onOpen,
  onToggleCheck,
  onPreview,
}: {
  item: IndexAssetItem
  selected: boolean
  checked: boolean
  onOpen: (e: React.MouseEvent) => void
  onToggleCheck: () => void
  onPreview: (item: IndexAssetItem) => void
}) {
  const missing = item.status === "missing"
  // 音频格：黑底区不放缩略图，放声部块（等宽竖条 = 材质，点亮段 = 真实进度）
  const isAudio = item.media_kind === "audio" && !!item.media_url
  /* 双击预览：图 / 视频 / 文本都能看。音频已经有悬停试听，再开一层大窗反而多余。
     单击仍然是选中（右键菜单、批量操作都挂在选中上），双击是「打开看一眼」——两件事。 */
  const canPreview =
    !missing &&
    !!item.media_url &&
    (item.media_kind === "video" ||
      item.media_kind === "image" ||
      item.media_kind === "text")
  const ap = useAudioPlayer(item.media_url)
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onDoubleClick={
        canPreview
          ? (e) => {
              e.stopPropagation()
              onPreview(item)
            }
          : undefined
      }
      onMouseEnter={isAudio ? ap.play : undefined}
      onMouseLeave={isAudio ? ap.stop : undefined}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return
        if (e.key === "Enter") {
          e.preventDefault()
          if (canPreview) onPreview(item)
          else onOpen(e as unknown as React.MouseEvent)
        } else if (e.key === " ") {
          e.preventDefault()
          onOpen(e as unknown as React.MouseEvent)
        }
      }}
      title={
        isAudio
          ? "悬停即试听 · 点固定钮钉住播放"
          : canPreview
            ? "双击预览"
            : undefined
      }
      className={cn(
        "group relative h-[168px] cursor-pointer overflow-hidden rounded-cell border bg-cell transition-colors",
        selected
          ? "border-wash-line bg-hi"
          : "border-rule-soft hover:border-rule hover:bg-hi",
        checked && !selected && "border-wash-line",
        missing && "border-dashed"
      )}
    >
      <div className="relative h-[112px] overflow-hidden bg-black">
        {isAudio ? (
          <>
            <div className="absolute inset-x-2 top-1/2 h-[46px] -translate-y-1/2">
              <VoiceBars t={ap.t} dur={ap.dur} />
            </div>
            {ap.dur > 0 && (
              <span className="absolute bottom-1.5 left-1.5 font-mono text-[10px] leading-none text-ash-dim">
                {fmt(ap.t)} / {fmt(ap.dur)}
              </span>
            )}
            <PinButton playing={ap.playing} pinned={ap.pinned} onToggle={() => ap.togglePin()} />
            <audio ref={ap.ref} src={item.media_url} {...ap.bind} />
          </>
        ) : item.thumb_url ? (
          <img
            src={item.thumb_url}
            alt=""
            loading="lazy"
            className={cn("h-full w-full object-cover", missing && "opacity-40")}
          />
        ) : (
          /* 视频没有封面时，胶片图标退成背景水印，把中间让给播放圆钮 */
          <span
            className={cn(
              "grid h-full w-full place-items-center",
              item.media_kind === "video" ? "text-white/15" : "text-ash-dim"
            )}
          >
            <KindIcon kind={item.media_kind} />
          </span>
        )}

        <span className="gb-pill left-1.5 top-1.5" data-on="1">
          {kindLabel(item.media_kind)}
        </span>
        {/* 视频默认没有封面（本机没装 ffmpeg，抽不了帧），格子里只剩一个胶片图标，
            光靠「双击预览」这个隐藏手势没人猜得到。这里给一个看得见的入口：
            圆钮常驻（不是 hover 才出现，触屏/不悬停也能发现），点它直接开预览，
            同时 stopPropagation —— 不然会顺带把这一项选中。 */}
        {item.media_kind === "video" && canPreview && (
          <button
            type="button"
            aria-label={`预览视频 ${item.name}`}
            onClick={(e) => {
              e.stopPropagation()
              onPreview(item)
            }}
            onDoubleClick={(e) => e.stopPropagation()}
            className="absolute left-1/2 top-1/2 grid size-9 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white/45 bg-black/50 text-white/85 transition-all hover:border-white hover:bg-black/70 hover:text-white group-hover:scale-[1.06]"
          >
            <Play className="size-4 translate-x-[1px]" fill="currentColor" strokeWidth={0} />
          </button>
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
            "absolute right-1.5 top-1.5 grid size-5 place-items-center rounded-[4px] border transition-colors",
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
          {item.subject || item.dir_path || "—"} · {humanSize(item.size)}
        </p>
      </div>
    </div>
  )
}

/* 双击预览：整块遮罩 + 面板，和提示词工程单镜弹窗同一套规矩——
   高度按百分比封顶（h-[92%]），媒体放 object-contain 自己留黑边，
   面板永远不越出视口；Esc / 点遮罩 / 点右上角都能关。
   视频直接吃 /api/media（Starlette FileResponse 支持 Range），所以能拖进度条。 */
function MediaPreview({ item, onClose }: { item: IndexAssetItem; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const isVideo = item.media_kind === "video"
  const isText = item.media_kind === "text"

  /* 文本预览：拿到原始文本就显示，拿不到（后端没回）就给个可读的提示。 */
  const [text, setText] = useState<string | null>(null)
  const [textErr, setTextErr] = useState(false)
  useEffect(() => {
    if (!isText) return
    let alive = true
    fetch(item.media_url)
      .then((r) => {
        if (!r.ok) throw new Error(String(r.status))
        return r.text()
      })
      .then((t) => {
        if (alive) setText(t)
      })
      .catch(() => {
        if (alive) setTextErr(true)
      })
    return () => {
      alive = false
    }
  }, [isText, item.media_url])

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/85 p-4 sm:p-6"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`预览 ${item.name}`}
        onClick={(e) => e.stopPropagation()}
        className="flex h-[92%] w-full max-w-[1120px] flex-col overflow-hidden rounded-panel border border-rule bg-panel shadow-[0_24px_80px_rgba(0,0,0,.65)]"
      >
        <header className="flex shrink-0 items-center gap-2.5 border-b border-rule-soft px-4 py-2.5">
          <span className="min-w-0 flex-1 truncate text-[12.5px] text-paper" title={item.name}>
            {item.name}
          </span>
          <span className="shrink-0 font-mono text-[10.5px] text-ash-dim">
            {item.width && item.height ? `${item.width}×${item.height} · ` : ""}
            {humanSize(item.size)}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭预览"
            className="grid size-7 flex-none place-items-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper"
          >
            <X className="size-3.5" />
          </button>
        </header>

        <div className="min-h-0 flex-1 bg-black">
          {isText ? (
            <div className="h-full overflow-auto bg-[#0c0c0c]">
              {textErr ? (
                <p className="p-6 text-[12.5px] text-ash">读不到文件内容（可能被移动或删除）。</p>
              ) : text === null ? (
                <p className="p-6 text-[12.5px] text-ash-dim">读取中…</p>
              ) : (
                <MarkdownView text={text} />
              )}
            </div>
          ) : isVideo ? (
            <video
              key={item.id}
              src={item.media_url}
              controls
              autoPlay
              playsInline
              className="h-full w-full object-contain"
            />
          ) : (
            <img
              key={item.id}
              src={item.media_url}
              alt={item.name}
              className="h-full w-full object-contain"
            />
          )}
        </div>

        <footer className="flex shrink-0 items-center gap-2 border-t border-rule-soft px-4 py-2">
          <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-ash-dim" title={item.path}>
            {item.path}
          </span>
          <span className="shrink-0 text-[10.5px] text-ash-dim">Esc 关闭</span>
        </footer>
      </div>
    </div>
  )
}

/** 套卡末尾的「拖入」格 */
function DropTile() {
  return (
    <div className="grid h-[168px] place-items-center rounded-cell border border-dashed border-rule bg-cell/40 text-[11.5px] text-ash-dim">
      <div className="flex flex-col items-center gap-1.5">
        <Plus className="size-4" strokeWidth={1.7} />
        拖入
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

/* ------------------------------------------------------------------ */
/* 右栏检查器                                                           */
/* ------------------------------------------------------------------ */

function AssetInspector({
  detail,
  onClose,
  onCopy,
  onRename,
  onMove,
  onTrash,
}: {
  detail: IndexAssetDetail
  onClose: () => void
  onCopy: () => void
  onRename: () => void
  onMove: () => void
  onTrash: () => void
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
        {/* 预览 */}
        {detail.media_kind === "audio" && detail.media_url ? (
          <div className="relative mt-1">
            <AudioPreview src={detail.media_url} />
            <span className="gb-pill left-2 top-2" data-on="1">
              {kindLabel(detail.media_kind)}
            </span>
          </div>
        ) : (
          <div className="relative mt-1 grid h-[150px] place-items-center overflow-hidden rounded-cell bg-black">
            {detail.media_kind === "image" && detail.thumb_url ? (
              <img src={detail.thumb_url} alt="" className="h-full w-full object-contain" />
            ) : (
              <span className="text-ash-dim">
                <KindIcon kind={detail.media_kind} />
              </span>
            )}
            <span className="gb-pill left-2 top-2" data-on="1">
              {kindLabel(detail.media_kind)}
            </span>
          </div>
        )}

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
          ) : detail.status === "trashed" ? (
            <span className="text-ash">已移入回收站，可恢复</span>
          ) : detail.status === "active" ? (
            "正常"
          ) : (
            detail.status
          )}
        </InfoRow>

        {detail.status === "trashed" && (
          <p className="mt-2 rounded-ctl border border-dashed border-rule px-3 py-2 text-[11.5px] leading-relaxed text-ash">
            文件已移入回收站，可在集合「回收站」里恢复到原路径；引用保持只读，恢复后自动有效。
          </p>
        )}

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

        {/* 单件操作（P2）：都走先计划后应用 */}
        <div className="mt-4 border-t border-rule-soft pt-3">
          <div className="flex flex-wrap items-center gap-2">
            {detail.media_kind === "image" && (
              <button
                type="button"
                onClick={onCopy}
                className={cn(INSPECTOR_BTN, "border-wash-line bg-wash text-paper")}
              >
                <ClipboardCopy className="size-3.5" />
                复制图片
              </button>
            )}
            <button type="button" onClick={onRename} className={INSPECTOR_BTN}>
              改名
            </button>
            <button type="button" onClick={onMove} className={INSPECTOR_BTN}>
              移动
            </button>
            <button
              type="button"
              onClick={onTrash}
              className={cn(INSPECTOR_BTN, "border-dashed")}
            >
              <Trash2 className="size-3.5" />
              移到回收站
            </button>
          </div>
          <p className="mt-1.5 text-[11px] leading-relaxed text-ash-dim">
            动手前先算影响面：引用这个文件的提示词 / 分镜表会一并列出，应用后可回滚。
          </p>
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

/* ------------------------------------------------------------------ */
/* 回收站视图（F4）：按主体分组，虚线格 + 已删除章                        */
/* ------------------------------------------------------------------ */

function TrashPanel({
  entries,
  pending,
  error,
  busyId,
  onRestore,
  onPurge,
  onOpen,
}: {
  entries: TrashEntry[]
  pending: boolean
  error: Error | null
  busyId: string | null
  onRestore: (id: string) => void
  onPurge: (id: string) => void
  onOpen: (entry: TrashEntry) => void
}) {
  const [armed, setArmed] = useState<string | null>(null)
  useEffect(() => {
    if (!armed) return
    const t = window.setTimeout(() => setArmed(null), 4000)
    return () => window.clearTimeout(t)
  }, [armed])

  if (pending) {
    return (
      <div className="flex flex-col gap-2 p-4">
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="h-[196px] animate-pulse rounded-panel bg-cell" />
        ))}
      </div>
    )
  }
  if (error) {
    return (
      <div className="m-4 rounded-panel border border-rule bg-panel p-4">
        <p className="text-[13px] font-medium">回收站读不出来</p>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-ash">
          {opsErrorMessage(error)}
        </p>
      </div>
    )
  }
  if (!entries.length) {
    return (
      <div className="m-4 rounded-panel border border-dashed border-rule bg-panel px-6 py-12 text-center">
        <h3 className="text-[15px] font-medium">回收站是空的</h3>
        <p className="mx-auto mt-2 max-w-[46ch] text-[12.5px] leading-relaxed text-ash">
          删除的文件会先到这里，能恢复；彻底清除才不可逆。
        </p>
      </div>
    )
  }

  /* 按主体分组，和主列表的「套」同一个读法 */
  const groups = useMemo(() => {
    const m = new Map<string, TrashEntry[]>()
    for (const it of entries) {
      const key = subjectOf(it.name)
      if (m.has(key)) m.get(key)!.push(it)
      else m.set(key, [it])
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length)
  }, [entries])

  return (
    <div className="flex flex-col gap-3 px-4 pb-6 pt-3">
      <div className="flex items-baseline gap-2.5 border-b border-rule-soft pb-2">
        <h2 className="text-[13.5px] font-medium">回收站</h2>
        <span className="font-mono text-[11.5px] text-ash-dim">
          {entries.length} 项 · 可恢复或彻底清除
        </span>
        <span className="flex-1" />
        <span className="text-[11.5px] text-ash-dim">删除不物理抹掉，可随时恢复</span>
      </div>

      {groups.map(([subject, list]) => (
        <section key={subject} className="rounded-panel border border-rule-soft bg-panel p-3.5">
          <div className="mb-3 flex flex-wrap items-center gap-2.5">
            <h3 className="text-[13px] font-medium">{subject}</h3>
            <span className="rounded border border-rule px-1.5 py-0.5 font-mono text-[11px] text-ash">
              {list.length} 项
            </span>
            <span className="text-[11.5px] text-ash-dim">
              已删除 {shortStamp(list[0].deleted_at)}
            </span>
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(148px,1fr))] gap-2.5">
            {list.map((entry) => (
              <div
                key={entry.id}
                className="flex h-[204px] flex-col overflow-hidden rounded-cell border border-dashed border-[#6e6e6e] bg-cell/60 opacity-80"
              >
                <button
                  type="button"
                  onClick={() => onOpen(entry)}
                  className="flex flex-1 flex-col items-center justify-center gap-1.5 px-2 text-ash-dim"
                  title="看这一项的原始路径"
                >
                  <KindIcon kind={entry.media_kind} />
                  <span className="line-clamp-2 text-center text-[11px] leading-snug text-ash">
                    {entry.name}
                  </span>
                  <span className="font-mono text-[10px] text-ash-dim">
                    {humanSize(entry.size)}
                  </span>
                </button>
                <span className="gb-pill left-1.5 top-1.5" data-on="0">
                  已删除
                </span>
                <div className="flex items-center gap-1.5 border-t border-rule-soft px-2 py-1.5">
                  <button
                    type="button"
                    disabled={busyId === entry.id}
                    onClick={() => onRestore(entry.id)}
                    className="inline-flex h-6 items-center rounded-ctl border border-rule bg-panel px-2 text-[11px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-45"
                  >
                    恢复
                  </button>
                  <button
                    type="button"
                    disabled={busyId === entry.id}
                    onClick={() => (armed === entry.id ? onPurge(entry.id) : setArmed(entry.id))}
                    className={cn(
                      "inline-flex h-6 items-center rounded-ctl border px-2 text-[11px] transition-colors disabled:opacity-45",
                      armed === entry.id
                        ? "border-dashed border-[#8a8a8a] bg-wash text-paper"
                        : "border-rule bg-panel text-ash hover:bg-hi hover:text-paper"
                    )}
                  >
                    {armed === entry.id ? "确认彻底清除" : "彻底清除"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function TrashInspector({
  entry,
  onClose,
  onRestore,
  onPurge,
}: {
  entry: TrashEntry | null
  onClose: () => void
  onRestore: (id: string) => void
  onPurge: (id: string) => void
}) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const t = window.setTimeout(() => setArmed(false), 4000)
    return () => window.clearTimeout(t)
  }, [armed])

  if (!entry) {
    return (
      <div className="grid flex-1 place-items-center px-6 text-center">
        <div>
          <Trash2 className="mx-auto size-5 text-ash-dim" strokeWidth={1.6} />
          <p className="mt-2 text-[12.5px] text-ash">这一项已不在回收站</p>
          <p className="mt-1 text-[11px] leading-relaxed text-ash-dim">
            可能刚被恢复或彻底清除了。
          </p>
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="flex flex-none items-start gap-2 border-b border-rule-soft px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[13.5px] font-medium" title={entry.name}>
            {entry.name}
          </h2>
          <p className="mt-0.5 font-mono text-[10.5px] text-ash-dim">已删除 · 可恢复</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭详情"
          className="grid size-7 flex-none place-items-center rounded-ctl border border-rule bg-cell text-ash hover:bg-hi hover:text-paper"
        >
          <X className="size-3.5" />
        </button>
      </div>
      <div className="px-4 pb-5 pt-1.5">
        <div className="mt-1 grid h-[120px] place-items-center rounded-cell border border-dashed border-[#6e6e6e] bg-cell/60 text-ash-dim">
          <KindIcon kind={entry.media_kind} />
        </div>
        <InfoRow label="原路径">
          <span className="break-all font-mono text-[11px]">{entry.original_path}</span>
        </InfoRow>
        <InfoRow label="大小">{humanSize(entry.size)}</InfoRow>
        <InfoRow label="删除于">
          <span className="font-mono text-[11px]">{entry.deleted_at}</span>
        </InfoRow>
        <InfoRow label="回收站内">
          <span className="break-all font-mono text-[11px]">{entry.trash_path}</span>
        </InfoRow>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => onRestore(entry.id)}
            className="inline-flex h-8 items-center rounded-ctl bg-paper px-3 text-[12.5px] font-medium text-[#131313] transition-all hover:bg-white active:translate-y-px"
          >
            <RotateCcw className="size-3.5" />
            恢复到原路径
          </button>
          <button
            type="button"
            onClick={() => (armed ? onPurge(entry.id) : setArmed(true))}
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-ctl border px-3 text-[12.5px] font-medium transition-all active:translate-y-px",
              armed
                ? "border-dashed border-[#8a8a8a] bg-wash text-paper"
                : "border-rule bg-cell text-ash hover:bg-hi hover:text-paper"
            )}
          >
            <Trash2 className="size-3.5" />
            {armed ? "确认彻底清除" : "彻底清除"}
          </button>
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-ash-dim">
          恢复会回到原路径，撞名自动补 <span className="font-mono">_02</span>；彻底清除删文件与索引记录，不可恢复。
        </p>
      </div>
    </>
  )
}

/* ------------------------------------------------------------------ */
/* 重复视图                                                             */
/* ------------------------------------------------------------------ */

function DuplicatesPanel({
  groups,
  pending,
  error,
  selectedId,
  multi,
  onOpen,
  onToggleCheck,
  onPreview,
  onCleanOlder,
}: {
  groups: DuplicateGroup[]
  pending: boolean
  error: Error | null
  selectedId?: string
  multi: Set<string>
  onOpen: (id: string) => void
  onToggleCheck: (id: string) => void
  onPreview: (item: IndexAssetItem) => void
  onCleanOlder: (ids: string[]) => void
}) {
  if (pending) {
    return (
      <div className="grid grid-cols-[repeat(auto-fill,minmax(168px,1fr))] gap-3 p-4">
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="h-[168px] animate-pulse rounded-cell border border-rule-soft bg-cell" />
        ))}
      </div>
    )
  }
  if (error) {
    return (
      <div className="m-4 rounded-panel border border-rule bg-panel p-4">
        <p className="text-[13px] font-medium">重复文件读不出来</p>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-ash">
          {opsErrorMessage(error)}
        </p>
      </div>
    )
  }
  if (!groups.length) {
    return (
      <div className="m-4 rounded-panel border border-dashed border-rule bg-panel px-6 py-12 text-center">
        <h3 className="text-[15px] font-medium">没有重复文件</h3>
        <p className="mx-auto mt-2 max-w-[46ch] text-[12.5px] leading-relaxed text-ash">
          按 SHA-256 分组，只统计完整哈希的资产。大文件走快速哈希时不参与重复检测。
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 px-4 pb-6 pt-3">
      <div className="flex items-baseline gap-2.5 border-b border-rule-soft pb-2">
        <h2 className="text-[13.5px] font-medium">重复</h2>
        <span className="font-mono text-[11.5px] text-ash-dim">{groups.length} 组</span>
        <span className="flex-1" />
        <span className="text-[11.5px] text-ash-dim">
          每组保留修改时间最新的一份，其余可清理
        </span>
      </div>
      {groups.map((group) => {
        /* 新的在前：默认保留它，旧副本可一键进回收站 */
        const sorted = [...group.items].sort((a, b) => b.mtime - a.mtime)
        const older = sorted.slice(1)
        return (
          <section key={group.sha256} className="rounded-panel border border-rule-soft bg-panel p-3.5">
            <div className="mb-3 flex flex-wrap items-center gap-2.5">
              <h3 className="text-[13px] font-medium">{group.count} 份相同</h3>
              <span className="rounded border border-rule px-1.5 py-0.5 font-mono text-[11px] text-ash">
                {humanSize(group.size)}
              </span>
              <span
                className="max-w-[28ch] truncate font-mono text-[10px] text-ash-dim"
                title={group.sha256}
              >
                {group.sha256.slice(0, 12)}…
              </span>
              <span className="flex-1" />
              {older.length > 0 && (
                <button
                  type="button"
                  onClick={() => onCleanOlder(older.map((i) => i.id))}
                  title="把旧副本移入回收站（先看影响面）"
                  className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] font-medium text-ash transition-all hover:bg-hi hover:text-paper active:translate-y-px"
                >
                  <Trash2 className="size-3.5" />
                  清理旧副本
                </button>
              )}
            </div>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(148px,1fr))] gap-2.5">
              {sorted.map((item, i) => (
                <div key={item.id} className="relative">
                  <AssetCell
                    item={item}
                    selected={selectedId === item.id}
                    checked={multi.has(item.id)}
                    onOpen={(e) => {
                      if (e.ctrlKey || e.metaKey) onToggleCheck(item.id)
                      else onOpen(item.id)
                    }}
                    onToggleCheck={() => onToggleCheck(item.id)}
                    onPreview={onPreview}
                  />
                  <span className="gb-pill right-1.5 bottom-1.5" data-on={i === 0 ? "1" : "0"}>
                    {i === 0 ? "最新" : "旧副本"}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
