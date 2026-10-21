import { useEffect, useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { FileUp, X } from "lucide-react"
import { cn } from "cn"
import { api } from "@/lib/api"
import { humanSize, relDir, subjectOf } from "@/lib/format"
import { FILE_ACCEPT, KIND_LABEL, fileKey, resolveRows, sanitizePart, useAssetUpload } from "@/lib/assetUpload"
import type { AssetTargetResp } from "@/lib/types"

/** 与后端 CATEGORIES 对齐：决定新主体落在哪个类别目录下 */
const CATEGORY_OPTIONS: { key: string; label: string }[] = [
  { key: "character", label: "人物" },
  { key: "scene", label: "场景" },
  { key: "style", label: "风格参考" },
  { key: "other", label: "其他" },
]

export interface UploadTarget {
  mode: "attach" | "new"
  /** attach：该套资产的主体名，预填进前缀 */
  subject?: string
  /** 该套资产所在的相对子目录，落盘时回到同一处 */
  subdir?: string
  /** 该套资产所属类别（人物 / 场景 …），新主体建目录时用 */
  category?: string
  /** 入口处已经拿到文件（粘贴 / 页面级拖放），直接带进来 */
  files?: File[]
}

export function AssetUploadDialog({
  target,
  rels,
  onClose,
  onDone,
}: {
  target: UploadTarget
  /** 全库已有的 rel 名单，用来在保存前预演重名补号 */
  rels: string[]
  onClose: () => void
  onDone: () => void
}) {
  const isNew = target.mode === "new"
  const subdir = target.subdir ?? ""

  const [files, setFiles] = useState<File[]>(target.files ?? [])
  const [prefix, setPrefix] = useState(target.subject ?? "")
  const [suffix, setSuffix] = useState("")
  const [category, setCategory] = useState(target.category ?? "character")
  const [dragging, setDragging] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const prefixClean = sanitizePart(prefix)
  const suffixClean = sanitizePart(suffix)

  /** 落位预览：留空 subdir 时由后端算出「这个主体该放哪」 */
  const targetQ = useQuery({
    queryKey: ["asset-target", prefixClean, category, subdir],
    queryFn: () =>
      api.get<AssetTargetResp>(
        `/api/assets/target?subject=${encodeURIComponent(prefixClean)}&category=${category}&subdir=${encodeURIComponent(subdir)}`
      ),
    enabled: !!prefixClean,
    retry: 1,
  })
  /** 实际落盘目录：优先用前端已有的套目录提示，其次用后端算出来的位置 */
  const effectiveDir = subdir || targetQ.data?.dir || ""

  const takenNames = useMemo(
    () =>
      new Set(
        rels.filter((r) => relDir(r) === effectiveDir).map((r) => r.slice(r.lastIndexOf("/") + 1))
      ),
    [rels, effectiveDir]
  )

  /** 保存前就把真实落名算出来——名字是这套系统的核心，不能等到写盘才揭晓 */
  const rows = useMemo(
    () =>
      resolveRows(files, {
        prefix: prefixClean,
        suffix: suffixClean,
        mode: target.mode,
        taken: takenNames,
      }),
    [files, prefixClean, suffixClean, target.mode, takenNames]
  )

  const okRows = rows.filter((r) => !r.reason)
  const dedupCount = okRows.filter((r) => r.deduped).length
  const badRows = rows.filter((r) => r.reason)
  const landing = okRows.length ? subjectOf(okRows[0].name.replace(/\.[^.]+$/, "")) : ""
  /** 主体名里带空格会被 `_split` 截断，落到的套跟用户想的不一样——提前说 */
  const drift = !!landing && !!prefixClean && landing !== prefixClean
  const canSave = okRows.length > 0

  const upload = useAssetUpload()

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  function addFiles(list: FileList | File[] | null) {
    if (!list) return
    const incoming = Array.from(list)
    if (!incoming.length) return
    setFiles((prev) => {
      const seen = new Set(prev.map(fileKey))
      return [...prev, ...incoming.filter((f) => !seen.has(fileKey(f)))]
    })
    setErr(null)
  }

  function submit() {
    if (!canSave || upload.isPending) return
    setErr(null)
    upload.mutate(
      {
        prefix: prefixClean,
        suffix: suffixClean,
        category,
        subdir,
        files: okRows.map((r) => r.file),
      },
      { onSuccess: () => onDone() }
    )
  }

  const title = isNew ? "新建资产" : "上传资产"
  const suffixHint = isNew ? "留空 = 直接用主体名" : "留空 = 参考 / 配音"

  return (
    <div
      className="fixed inset-0 z-80 grid place-items-center bg-black/75 p-6"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onKeyDown={(e) => {
          const tag = (e.target as HTMLElement).tagName
          if (e.key === "Enter" && tag === "INPUT" && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
        }}
        className="w-[min(680px,94vw)] max-h-[92svh] overflow-auto rounded-panel border border-rule bg-panel p-5 shadow-2xl shadow-black/60"
      >
        <div className="flex items-center gap-3">
          <h2 className="text-[15px] font-semibold">{title}</h2>
          {isNew && (
            <span className="rounded border border-dashed border-[#6e6e6e] px-1.5 py-px font-mono text-[10.5px] text-ash">
              新主体
            </span>
          )}
          <span className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="grid size-7 place-items-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px"
          >
            <X className="size-3.5" />
          </button>
        </div>

        <p className="mt-1.5 text-[12px] leading-relaxed text-ash">
          按 <span className="font-mono">主体名_用途</span> 自动落名。同名文件不会被覆盖，
          会自动补 <span className="font-mono">_02</span>、<span className="font-mono">_03</span>。
        </p>

        {/* 拖放区：虚线 = 还在等你放东西，实线 = 已经选好了（沿用全站的轮廓语言） */}
        <label
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            e.stopPropagation()
            setDragging(false)
            addFiles(e.dataTransfer.files)
          }}
          className={cn(
            "mt-3.5 flex min-h-[112px] cursor-pointer flex-col items-center justify-center gap-1.5 rounded-ctl bg-cell px-4 py-5 text-center transition-all duration-150",
            files.length ? "border border-rule" : "border border-dashed border-[#5c5c5c]",
            dragging && "border-wash-line bg-hi"
          )}
        >
          <input
            type="file"
            multiple
            accept={FILE_ACCEPT}
            className="sr-only"
            onChange={(e) => {
              addFiles(e.currentTarget.files)
              e.currentTarget.value = ""
            }}
          />
          <FileUp className={cn("size-5 transition-colors", dragging ? "text-paper" : "text-ash-dim")} />
          {files.length ? (
            <>
              <span className="text-[13px] font-medium">已选 {files.length} 个文件</span>
              <span className="text-[11.5px] text-ash-dim">再拖入可继续追加 · 点这里重选</span>
            </>
          ) : (
            <>
              <span className="text-[13px] font-medium">拖文件到这里，或 选择文件</span>
              <span className="text-[11.5px] text-ash-dim">图片 / 音频 / 视频 · 可多选 · 也支持直接 Ctrl+V</span>
            </>
          )}
        </label>

        <div className="mt-4 flex flex-col gap-3 sm:flex-row">
          <label className="flex min-w-0 flex-1 flex-col gap-1.5">
            <span className="text-[11.5px] text-ash-dim">主体名（前缀）· 决定归到哪一套</span>
            <input
              value={prefix}
              onChange={(e) => setPrefix(e.target.value)}
              autoFocus
              spellCheck={false}
              placeholder="江洛菡"
              className="h-9 rounded-ctl border border-rule bg-cell px-2.5 font-mono text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
            />
          </label>
          <label className="flex min-w-0 flex-1 flex-col gap-1.5">
            <span className="text-[11.5px] text-ash-dim">用途（后缀）· {suffixHint}</span>
            <input
              value={suffix}
              onChange={(e) => setSuffix(e.target.value)}
              spellCheck={false}
              placeholder={isNew ? "四视图" : "四视图 / 配音"}
              className="h-9 rounded-ctl border border-rule bg-cell px-2.5 font-mono text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
            />
          </label>
        </div>

        {isNew && (
          <label className="mt-3 flex flex-col gap-1.5">
            <span className="text-[11.5px] text-ash-dim">
              类别 · 决定新主体落在哪个目录（人物 / 场景 / …）
            </span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="h-9 rounded-ctl border border-rule bg-cell px-2.5 text-[12.5px] outline-none focus:border-wash-line"
            >
              {CATEGORY_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="mt-3.5 flex flex-col gap-2">
          <div className="flex items-center gap-2.5">
            <span className="text-[11.5px] text-ash-dim">落名预览</span>
            <span className="flex-1" />
            {!!files.length && (
              <>
                <button
                  type="button"
                  onClick={() => setFiles([])}
                  className="text-[11.5px] text-ash-dim transition-colors hover:text-paper"
                >
                  清空
                </button>
                <span className="font-mono text-[11px] text-ash-dim">
                  {okRows.length} / {rows.length} 个可保存
                </span>
              </>
            )}
          </div>

          {!files.length && (
            <p className="rounded-ctl border border-dashed border-rule-soft px-3 py-2.5 text-[12px] text-ash-dim">
              还没有选文件。选好之后这里会逐条列出最终文件名，重名的会标出来。
            </p>
          )}

          {rows.map((r) => (
            <div
              key={fileKey(r.file)}
              className={cn(
                "flex min-h-8 flex-wrap items-center gap-2 rounded-cell px-2.5 py-1.5",
                r.reason ? "border border-dashed border-[#6e6e6e]" : "bg-cell"
              )}
            >
              <span
                className={cn(
                  "min-w-0 flex-1 truncate font-mono text-[12px]",
                  r.reason ? "text-ash-dim line-through" : "text-paper"
                )}
                title={r.reason ? `${r.file.name} — ${r.reason}` : r.name}
              >
                {r.reason ? r.file.name || "(无名文件)" : r.name}
              </span>
              {r.reason ? (
                <span className="font-mono text-[10.5px] text-ash-dim">{r.reason}</span>
              ) : (
                <>
                  <span className="font-mono text-[10.5px] text-ash-dim">{humanSize(r.file.size)}</span>
                  <span className="rounded border border-rule px-1.5 py-px font-mono text-[10.5px] text-ash">
                    {KIND_LABEL[r.kind]}
                  </span>
                  {r.deduped && (
                    <span className="rounded border border-dashed border-[#8a8a8a] px-1.5 py-px font-mono text-[10.5px] text-ash">
                      重名 · 自动补号
                    </span>
                  )}
                </>
              )}
            </div>
          ))}

          {!!dedupCount && (
            <p className="text-[11.5px] text-ash-dim">
              {dedupCount} 个文件重名，保存时会自动补 <span className="font-mono">_02</span>… 不会覆盖原文件。
            </p>
          )}
          {drift && (
            <p className="rounded-ctl border border-dashed border-[#6e6e6e] px-3 py-2 text-[11.5px] text-ash">
              主体名里有空格，会被切成第一段：这一批实际归到{" "}
              <b className="font-medium text-paper">{landing}</b> 而不是{" "}
              <b className="font-medium text-paper">{prefixClean}</b>。想整段保留就把空格换成{" "}
              <span className="font-mono">_</span>。
            </p>
          )}
          {err && (
            <p className="rounded-ctl border border-dashed border-[#8a8a8a] px-3 py-2 text-[12px] text-paper">
              {err}
            </p>
          )}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <span className="text-[12px] text-ash">
            归入 <b className="font-medium text-paper">{landing || prefixClean || "—"}</b>
            <span className="text-ash-dim">
              {" · "}
              {subdir ? (
                <span className="font-mono">{subdir}/</span>
              ) : targetQ.data ? (
                <>
                  <span className="font-mono">{effectiveDir ? `${effectiveDir}/` : "资产根"}</span>
                  {"（自动定位）"}
                </>
              ) : (
                "自动定位目录…"
              )}
            </span>
          </span>
          <span className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 items-center rounded-ctl border border-rule px-4 text-[12.5px] text-ash transition-colors hover:border-[#5c5c5c] hover:text-paper active:translate-y-px"
          >
            取消
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSave || upload.isPending}
            className="inline-flex h-9 items-center rounded-ctl bg-paper px-4 text-[12.5px] font-medium text-[#131313] transition-transform hover:bg-white active:translate-y-px active:scale-[0.99] disabled:pointer-events-none disabled:opacity-40"
          >
            {upload.isPending ? "保存中…" : canSave ? `保存 ${okRows.length} 个文件` : "选择文件后再保存"}
          </button>
        </div>

        {!!badRows.length && !canSave && (
          <p className="mt-2 text-right text-[11.5px] text-ash-dim">
            选中的文件都不支持：只收图片 / 音频 / 视频。
          </p>
        )}
      </div>
    </div>
  )
}
