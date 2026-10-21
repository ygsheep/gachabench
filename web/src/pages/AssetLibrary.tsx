import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Plus, RefreshCw, Search, Upload, X } from "lucide-react"
import { toast } from "sonner"
import { cn } from "cn"
import { AssetDirSetter } from "@/components/AssetDirSetter"
import { AssetGroupCard } from "@/components/AssetGroupCard"
import { AssetUploadDialog, type UploadTarget } from "@/components/AssetUploadDialog"
import { ApiError, api } from "@/lib/api"
import { useSwrQuery } from "@/lib/useSwrQuery"
import { humanSize, relDir } from "@/lib/format"
import { filesFromDataTransfer, isFileDrag, useAssetUpload } from "@/lib/assetUpload"
import type {
  AssetCategory,
  AssetGroup,
  AssetItem,
  AssetsResp,
} from "@/lib/types"

const OUTLINE_BTN =
  "inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12.5px] font-medium transition-all hover:bg-hi active:translate-y-px"

function subdirOf(g: AssetGroup): string {
  return g.items[0] ? relDir(g.items[0].rel) : ""
}

export function AssetLibrary({ workspace }: { workspace: string | null }) {
  const [cat, setCat] = useState("all")
  const [q, setQ] = useState("")
  const [zoom, setZoom] = useState<AssetItem | null>(null)
  const [dlg, setDlg] = useState<UploadTarget | null>(null)
  /** 当前选中的一套 —— 它就是 Ctrl+V / 空白处拖放的落点 */
  const [selected, setSelected] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  /** 指针此刻悬在哪一套上（拖放的唯一落点判据）；非空时页面级提示让位 */
  const [dropTarget, setDropTarget] = useState<string | null>(null)

  // 冷启动先画上一次的素材库（SWR）；queryKey 带工作区，切工作区才会真的重拉
  const { data, isPending, isError, error, isFetching } = useSwrQuery<AssetsResp>(
    `swr:assets:${workspace ?? "_"}`,
    ["assets", workspace],
    (signal) => api.get<AssetsResp>("/api/assets", { signal }),
    { enabled: !!workspace }
  )

  const upload = useAssetUpload()
  const qc = useQueryClient()

  /** 资产目录还没确定（后端 409）—— 走「手动选择资产目录」流程 */
  const noAssetDir = isError && (error as ApiError)?.status === 409

  const rescan = useMutation({
    mutationFn: () => api.post<{ added: number; removed: number; scanned: number }>("/api/assets/scan"),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["assets"] })
      toast.success(`已重扫资产目录：${r.scanned} 个文件（新增 ${r.added} · 移除 ${r.removed}）`)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const cats: AssetCategory[] = data?.categories ?? []
  const counts = useMemo(
    () =>
      cats.map((c) => ({
        ...c,
        n: c.groups.reduce((a, g) => a + g.items.length, 0),
      })),
    [cats]
  )

  /** 全库文件名（含目录），给上传对话框预演重名补号 */
  const allRels = useMemo(
    () => cats.flatMap((c) => c.groups.flatMap((g) => g.items.map((i) => i.rel))),
    [cats]
  )

  const selectedGroup = useMemo(() => {
    if (!selected) return null
    for (const c of cats) for (const g of c.groups) if (g.name === selected) return g
    return null
  }, [cats, selected])

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return counts
      .filter((c) => cat === "all" || c.key === cat)
      .map((c) => ({
        ...c,
        groups: c.groups
          .map((g) => ({
            ...g,
            items: needle
              ? g.items.filter((it) =>
                  `${g.name} ${it.name} ${it.tags.join(" ")}`.toLowerCase().includes(needle)
                )
              : g.items,
          }))
          .filter((g) => g.items.length),
      }))
      .filter((c) => c.groups.length)
  }, [counts, cat, q])

  const total = data?.total ?? 0

  /** 主体名 → 类别 key，上传时带上，新主体才知道该建档到 人物/场景 哪个目录 */
  const catKeyOf = useMemo(() => {
    const m = new Map<string, string>()
    for (const c of cats) for (const g of c.groups) m.set(g.name, c.key)
    return m
  }, [cats])

  function categoryOf(g: AssetGroup): string {
    return catKeyOf.get(g.name) ?? "character"
  }

  function uploadInto(g: AssetGroup) {
    setSelected(g.name)
    setDlg({ mode: "attach", subject: g.name, subdir: subdirOf(g), category: categoryOf(g) })
  }

  function quickUpload(g: AssetGroup, files: File[]) {
    upload.mutate({
      prefix: g.name,
      suffix: "",
      category: categoryOf(g),
      subdir: subdirOf(g),
      files,
    })
  }

  /** 三个入口（拖到卡片 / 页面级拖放 / 粘贴）汇到这一处决定去向 */
  const incomingRef = useRef<(files: File[], explicit?: AssetGroup | null) => void>(() => {})
  useEffect(() => {
    incomingRef.current = (files, explicit) => {
      const g = explicit ?? selectedGroup
      if (!g) {
        // 没选中任何一套 → 交给对话框去问主体名，而不是猜一个
        setDlg({ mode: "attach", files })
        return
      }
      quickUpload(g, files)
    }
  })

  const dlgRef = useRef(false)
  useEffect(() => {
    dlgRef.current = !!dlg
  })

  // 拖放 / 粘贴：两个全局手势，一套去向规则
  useEffect(() => {
    // 不用 dragenter/dragleave 配对计数 —— 实测它会漏配，导致提示层卡住或不出现。
    // 改成：dragover 持续触发即视为「拖拽中」，停 350ms 就自动收掉（看门狗）。
    let lastOver = 0
    const clear = () => {
      lastOver = 0
      setDragActive(false)
      setDropTarget(null)
    }
    function onOver(e: DragEvent) {
      if (!isFileDrag(e)) return
      // 必须吃掉默认行为，否则浏览器会直接打开这个文件
      e.preventDefault()
      lastOver = Date.now()
      if (dlgRef.current) return
      // 落点只在这里算一次，卡片不再自己判断 —— 单一事实来源
      const card = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-asset-group]")
      setDropTarget(card ? card.getAttribute("data-asset-group") : null)
      setDragActive(true)
    }
    function onDrop(e: DragEvent) {
      if (!isFileDrag(e)) return
      e.preventDefault()
      clear()
      if (dlgRef.current) return
      const el = e.target as HTMLElement | null
      if (el?.closest?.("[data-asset-group]")) return // 卡片自己收下了
      const files = Array.from(e.dataTransfer?.files ?? [])
      if (files.length) incomingRef.current(files)
    }
    function onPaste(e: ClipboardEvent) {
      if (dlgRef.current) return
      const el = document.activeElement as HTMLElement | null
      // 正在输入框里粘贴文本，别抢
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) return
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

  useEffect(() => {
    if (!zoom) return
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setZoom(null)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [zoom])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !dlg && !zoom) setSelected(null)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [dlg, zoom])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-end gap-x-3.5 gap-y-2 px-6 pb-3.5 pt-5">
        <div className="max-w-[82ch]">
          <h1 className="text-[19px] font-semibold leading-tight">资产管理</h1>
          <p className="pt-0.5 text-[12.5px] text-ash">
            参考图和参考音频是 <span className="font-mono">&lt;Picture N&gt;</span> /{" "}
            <span className="font-mono">&lt;Audio N&gt;</span> 的锚。文件名主体名相同即归为同一套。
          </p>
          <p className="pt-0.5 text-[11.5px] text-ash-dim">
            点面板选中一套 · 拖文件到面板上直接存入 · 选中后按{" "}
            <span className="font-mono">Ctrl+V</span> 粘贴上传
          </p>
        </div>
        <span className="flex-1" />
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={rescan.isPending || noAssetDir}
            onClick={() => rescan.mutate()}
            title="重新递归扫描资产目录并入库"
            className={cn(OUTLINE_BTN, "disabled:opacity-45")}
          >
            <RefreshCw className={cn("size-3.5", rescan.isPending && "animate-spin")} />
            重扫
          </button>
          <button type="button" onClick={() => setDlg({ mode: "attach" })} className={OUTLINE_BTN}>
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

      <div className="flex flex-wrap items-center gap-2.5 px-6 pb-3.5">
        <div className="relative inline-flex rounded-ctl border border-rule bg-panel p-0.5">
          {[{ key: "all", label: "全部", n: total }, ...counts].map((c) => (
            <button
              key={c.key}
              type="button"
              onClick={() => setCat(c.key)}
              aria-pressed={cat === c.key}
              className={cn(
                "inline-flex h-[26px] items-center gap-1.5 rounded-[4px] px-2.5 text-[12.5px] transition-colors",
                cat === c.key ? "bg-hi font-medium text-paper" : "text-ash hover:text-paper"
              )}
            >
              {c.label}
              <span className={cn("font-mono text-[11px]", cat === c.key ? "text-white" : "text-ash-dim")}>
                {c.n}
              </span>
            </button>
          ))}
        </div>
        <div className="relative w-[212px]">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-ash-dim" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="主体名 / 文件名 / 标签"
            className="h-8 w-full rounded-ctl border border-rule bg-panel pl-7 pr-2.5 text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
          />
        </div>
        <span className="flex-1" />
        {selectedGroup && (
          <span className="inline-flex h-7 items-center gap-2 rounded-ctl border border-wash-line bg-wash px-2.5 text-[11.5px] text-paper">
            已选中 <b className="font-medium">{selectedGroup.name}</b>
            <button
              type="button"
              onClick={() => setSelected(null)}
              className="text-ash-dim transition-colors hover:text-paper"
            >
              取消
            </button>
          </span>
        )}
        {isFetching && data && (
          <span
            className="inline-flex h-7 items-center rounded-ctl border border-dashed border-rule px-2 text-[11px] text-ash-dim"
            title="先用上次的结果占位，扫盘结束会替换"
          >
            旧结果 · 更新中
          </span>
        )}
        {data?.root && (
          <span className="max-w-[52ch] truncate font-mono text-[11px] text-ash-dim" title={data.root}>
            资产目录 <b className="text-ash">{data.asset_dir}</b> · {data.root}
          </span>
        )}
      </div>

      <div
        className="flex flex-col gap-5 px-6 pb-11"
        onClick={(e) => {
          if (e.target === e.currentTarget) setSelected(null)
        }}
      >
        {isPending && <p className="text-[12.5px] text-ash">读取素材目录…</p>}

        {isError && noAssetDir && <AssetDirSetter />}

        {isError && !noAssetDir && (
          <div className="rounded-panel border border-rule bg-panel p-4">
            <p className="text-[13px] font-medium">素材目录读不出来</p>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-ash">
              {(error as Error)?.message ?? "后端没有响应。"} 确认已经激活工作区。
            </p>
          </div>
        )}

        {!isPending && !isError && !total && (
          <div className="rounded-panel border border-dashed border-rule bg-panel px-6 py-12 text-center">
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
        )}

        {!isPending && !isError && !!total && !shown.length && (
          <div className="rounded-panel border border-dashed border-rule bg-panel px-6 py-12 text-center">
            <h3 className="text-[15px] font-medium">没有匹配的素材</h3>
            <p className="mx-auto mt-2 max-w-[52ch] text-[12.5px] leading-relaxed text-ash">
              换个分类，或者把搜索清掉。
            </p>
          </div>
        )}

        {shown.map((c) => (
          <div key={c.key}>
            <div className="mb-2.5 flex items-baseline gap-2.5 border-b border-rule-soft pb-2">
              <h2 className="text-[13.5px] font-medium">{c.label}</h2>
              <span className="font-mono text-[11.5px] text-ash-dim">
                {c.groups.length} 套 · {c.n} 个文件
              </span>
              <span className="flex-1" />
              <span className="text-[11.5px] text-ash-dim">
                {c.key === "scene"
                  ? "接到 <Picture N>"
                  : c.key === "character"
                    ? "接到 <Picture N> / <Audio N>"
                    : "参考素材"}
              </span>
            </div>

            <div className="flex flex-col gap-4">
              {c.groups.map((g) => (
                <AssetGroupCard
                  key={g.name}
                  group={g}
                  selected={g.name === selected}
                  dropping={g.name === dropTarget}
                  onSelect={() => setSelected((cur) => (cur === g.name ? null : g.name))}
                  onUpload={() => uploadInto(g)}
                  onZoom={setZoom}
                  onDropFiles={(files) => incomingRef.current(files, g)}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* 拖拽进行中：把「放开会发生什么」明写出来，而不是让人猜 */}
      {dragActive && !dropTarget && !dlg && (
        <div className="pointer-events-none fixed inset-0 z-70 grid place-items-center p-6">
          <div className="absolute inset-4 rounded-panel border border-dashed border-wash-line bg-black/45" />
          <div className="relative rounded-panel border border-rule bg-panel/95 px-5 py-4 text-center shadow-2xl shadow-black/60">
            {selectedGroup ? (
              <>
                <p className="text-[14px] font-medium">放开 → 存到「{selectedGroup.name}」</p>
                <p className="mt-1 font-mono text-[11.5px] text-ash-dim">
                  落名 {selectedGroup.name}_用途 · 重名自动补号
                </p>
              </>
            ) : (
              <>
                <p className="text-[14px] font-medium">放开 → 弹出上传对话框</p>
                <p className="mt-1 text-[11.5px] text-ash-dim">
                  还没选中资产。也可以先点一下某一套，之后拖放就直接进那一套。
                </p>
              </>
            )}
          </div>
        </div>
      )}

      {dlg && (
        <AssetUploadDialog
          target={dlg}
          rels={allRels}
          onClose={() => setDlg(null)}
          onDone={() => setDlg(null)}
        />
      )}

      {zoom && (
        <div
          className="fixed inset-0 z-80 grid place-items-center bg-black/80 p-6"
          onClick={(e) => {
            if (e.target === e.currentTarget) setZoom(null)
          }}
        >
          <div className="max-h-[92svh] w-[min(1100px,94vw)] overflow-auto rounded-panel border border-rule bg-panel p-4">
            <div className="mb-3 flex items-center gap-3">
              <h2 className="min-w-0 flex-1 truncate text-[14px] font-medium">{zoom.name}</h2>
              <span className="font-mono text-[11px] text-ash-dim">{humanSize(zoom.size)}</span>
              <button
                type="button"
                onClick={() => setZoom(null)}
                aria-label="关闭"
                className="grid size-8 place-items-center rounded-ctl border border-rule bg-cell hover:bg-hi"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="grid place-items-center rounded-cell bg-black">
              {zoom.kind === "video" ? (
                <video src={zoom.url} controls autoPlay loop className="max-h-[74svh] w-full" />
              ) : (
                <img src={zoom.url} alt={zoom.name} className="max-h-[74svh] w-full object-contain" />
              )}
            </div>
            <p className="mt-2.5 break-all font-mono text-[11px] text-ash-dim">{zoom.rel}</p>
          </div>
        </div>
      )}
    </div>
  )
}
