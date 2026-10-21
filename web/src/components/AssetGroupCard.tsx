import { Upload } from "lucide-react"
import { cn } from "cn"
import { humanSize } from "@/lib/format"
import { isFileDrag } from "@/lib/assetUpload"
import type { AssetGroup, AssetItem } from "@/lib/types"

const KIND_LABEL: Record<string, string> = {
  image: "图",
  video: "视频",
  audio: "音频",
  other: "其它",
}

/**
 * 一套资产。既是「选中目标」（选中后 Ctrl+V 就粘到这里），
 * 也是「拖放目标」（拖文件到面板上直接存进这一套）。
 */
export function AssetGroupCard({
  group,
  selected,
  dropping,
  onSelect,
  onUpload,
  onZoom,
  onDropFiles,
}: {
  group: AssetGroup
  selected: boolean
  /** 指针此刻正悬在本卡片上（落点由页面统一判定，卡片不再自己维护一份，否则两层说法会打架） */
  dropping: boolean
  onSelect: () => void
  onUpload: () => void
  onZoom: (it: AssetItem) => void
  onDropFiles: (files: File[]) => void
}) {
  const visuals = group.items.filter((it) => it.kind === "image" || it.kind === "video")
  const audios = group.items.filter((it) => it.kind === "audio")

  return (
    <div
      onClick={onSelect}
      onDragOver={(e) => {
        // 允许落在卡片上（否则浏览器会把这个文件当成导航打开）
        if (isFileDrag(e.nativeEvent)) {
          e.preventDefault()
          e.dataTransfer.dropEffect = "copy"
        }
      }}
      onDrop={(e) => {
        e.preventDefault()
        // 不 stopPropagation：让页面级处理器收到事件去复位拖放态，
        // 它靠 data-asset-group 认出「卡片已经收下了」而跳过。
        const files = Array.from(e.dataTransfer.files)
        if (files.length) onDropFiles(files)
      }}
      data-asset-group={group.name}
      className={cn(
        "relative rounded-panel border p-3.5 transition-colors",
        selected ? "border-wash-line bg-wash" : "border-rule-soft bg-panel hover:border-rule",
        dropping && "border-dashed border-wash-line"
      )}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2.5">
        <h3 className="text-[13px] font-medium">{group.name}</h3>
        <span className="rounded border border-rule px-1.5 py-0.5 font-mono text-[11px] text-ash">
          {group.items.length} 项
        </span>
        {group.tags.length > 0 && (
          <span className="truncate text-[11.5px] text-ash-dim">{group.tags.join(" · ")}</span>
        )}
        <span className="flex-1" />
        {selected && (
          <span className="inline-flex h-6 shrink-0 items-center rounded border border-wash-line px-2 font-mono text-[10.5px] text-paper">
            Ctrl+V 粘到这里
          </span>
        )}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onUpload()
          }}
          title={`往「${group.name}」里加素材`}
          className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[12px] font-medium text-ash transition-all hover:bg-hi hover:text-paper active:translate-y-px"
        >
          <Upload className="size-3.5" />
          上传
        </button>
      </div>

      {!!visuals.length && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(132px,1fr))] gap-2.5">
          {visuals.map((it) => (
            <button
              key={it.rel}
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                onZoom(it)
              }}
              className="overflow-hidden rounded-cell border border-rule-soft bg-cell p-1 text-left transition-all hover:-translate-y-0.5 hover:border-rule"
            >
              <span className="relative block overflow-hidden rounded-[3px] bg-black">
                {it.kind === "video" ? (
                  <video
                    src={it.url}
                    muted
                    playsInline
                    preload="metadata"
                    className="block aspect-square w-full object-cover"
                  />
                ) : (
                  <img
                    src={it.url}
                    alt={it.name}
                    loading="lazy"
                    className="block aspect-square w-full object-cover"
                  />
                )}
                <span className="gb-pill left-1.5 top-1.5" data-on="1">
                  {KIND_LABEL[it.kind]}
                </span>
              </span>
              <span className="mt-1.5 block truncate text-[12px]" title={it.name}>
                {it.name}
              </span>
              <span className="block font-mono text-[10px] text-ash-dim">{humanSize(it.size)}</span>
            </button>
          ))}
        </div>
      )}

      {!!audios.length && (
        <div className={cn("flex flex-col gap-1.5", visuals.length && "mt-3")}>
          {audios.map((it) => (
            <div
              key={it.rel}
              onClick={(e) => e.stopPropagation()}
              className="flex flex-wrap items-center gap-2.5 rounded-ctl border border-rule-soft bg-cell px-2.5 py-2"
            >
              <span className="rounded border border-dashed border-[#8a8a8a] px-1.5 py-px font-mono text-[10.5px] text-ash">
                音频
              </span>
              <span className="min-w-0 flex-1 truncate text-[12px]">{it.name}</span>
              <span className="font-mono text-[10.5px] text-ash-dim">{humanSize(it.size)}</span>
              <audio controls preload="none" src={it.url} className="h-8" />
            </div>
          ))}
        </div>
      )}

      {/* 拖放中的明确承诺：放开会发生什么，就写在面板上 */}
      {dropping && (
        <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-panel border border-dashed border-wash-line bg-black/50">
          <div className="text-center">
            <p className="text-[13px] font-medium text-paper">放开 → 存到「{group.name}」</p>
            <p className="mt-1 font-mono text-[11px] text-ash">
              落名 {group.name}_用途 · 重名自动补号
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
