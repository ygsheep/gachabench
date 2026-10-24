import { useRef } from "react"
import { FileX, Play } from "lucide-react"
import { cn } from "cn"
import type { Card, CardStatus } from "@/lib/types"
import { STATUS_LABEL, fileName, seconds, shortStamp } from "@/lib/format"

/**
 * 胶片格。状态一律靠形状 + 明度表达：
 * 正片 = 实心亮条 ／ 废稿 = 斜纹胶条 + 虚线轮廓 ／ 草稿 = 点线。
 * 缩略图是真实视频流，悬停即预览 —— 抽卡就是要快速扫。
 *
 * `card.missing`（文件已不在盘上）时不给 <video>：那会是一个永远黑的框，
 * 看起来像「播不动」，实际上是「东西没了」。改成虚线占位 + 一句实话。
 *
 * 「通过」位（2026-10-24）：右上角常驻一个低对比的小按钮，点一下即通过。
 * 它必须**和看片在同一个格子上** —— 以前要「进审片器 → 找到按钮 → 确认」，
 * 看的地方和做决定的地方不是同一个地方，这才是「步骤太多」的根。
 *
 * 外层是 div[role=button] 而不是 <button>：里面还要放一个真正的按钮，
 * 交互元素不能嵌套。键盘 Enter/Space 仍然可用。
 */
export function FilmCell({
  card,
  variant = "cell",
  shots = [],
  takeCount,
  stamp,
  active,
  index,
  onClick,
  onPass,
  passPending,
}: {
  card: Card
  variant?: "cell" | "take"
  shots?: string[]
  takeCount?: number
  stamp?: CardStatus | null
  active?: boolean
  index?: number
  onClick?: () => void
  /** 给了才显示「通过」位；点一下 = 标为正片 + 复制到 素材/未分类/ */
  onPass?: () => void
  passPending?: boolean
}) {
  const ref = useRef<HTMLVideoElement>(null)
  const take = variant === "take"
  const rejected = card.status === "reject"
  const missing = !!card.missing
  const passed = card.status === "final"
  const canPass = !!onPass && !passed && !missing

  function play() {
    const v = ref.current
    if (!v || missing) return
    v.play().catch(() => {})
  }
  function stop() {
    const v = ref.current
    if (!v || missing) return
    v.pause()
    v.currentTime = 0
  }

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          onClick?.()
        }
      }}
      data-s={card.status}
      data-missing={missing || undefined}
      aria-label={`${card.seg ? card.seg + " 段 " : ""}${card.chunk ?? ""}，${STATUS_LABEL[card.status]}${
        takeCount ? `，共 ${takeCount} 条候选` : ""
      }${missing ? "，文件已不在盘上" : ""}`}
      aria-current={active ? true : undefined}
      onMouseEnter={play}
      onMouseLeave={stop}
      onFocus={play}
      onBlur={stop}
      className={cn(
        "gb-cell group/cell min-w-0 cursor-pointer select-none text-left",
        take
          ? "relative w-full"
          : "rounded-cell transition-transform hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.99]"
      )}
    >
      <div
        className={cn(
          "relative overflow-hidden rounded-cell bg-black outline-offset-[-1px]",
          active
            ? "outline-2 outline-white"
            : card.status === "final"
              ? "outline-1 outline-wash-line"
              : "outline-1 outline-rule",
          rejected && !active && "outline-dashed outline-[#6e6e6e]"
        )}
      >
        <span className="gb-tab" data-s={card.status} />
        {missing ? (
          <span className="grid aspect-video w-full place-items-center bg-[#0c0c0c]">
            <span className="flex flex-col items-center gap-1 text-ash-dim">
              <FileX className="size-[18px]" strokeWidth={1.5} />
              <span className="text-[10px] leading-tight">文件已不在盘上</span>
            </span>
          </span>
        ) : (
          <video
            ref={ref}
            src={`/api/media/${card.id}`}
            muted
            playsInline
            preload="metadata"
            className="block aspect-video w-full bg-black object-cover"
          />
        )}
        <span className="gb-scrim" />

        {!take && takeCount != null && (
          <span className="gb-pill left-[11px] top-1.5">{takeCount} 条</span>
        )}
        {take && index != null && (
          <span className="gb-pill left-[11px] top-1.5">{String(index).padStart(2, "0")}</span>
        )}
        <span className="gb-pill bottom-1.5 right-1.5 text-[10.5px]">
          {seconds(card.duration)}
        </span>

        {canPass && (
          <button
            type="button"
            disabled={passPending}
            onClick={(e) => {
              // 不能让点击冒泡到外层：那会顺手把审片器打开
              e.stopPropagation()
              onPass?.()
            }}
            onKeyDown={(e) => e.stopPropagation()}
            onMouseEnter={(e) => e.stopPropagation()}
            onDoubleClick={(e) => e.stopPropagation()}
            title="通过：标为正片，并复制一份到 素材/未分类/"
            aria-label="通过：标为正片，并复制一份到 素材/未分类/"
            className={cn(
              "absolute right-[5px] top-[5px] z-[3] grid h-[21px] place-items-center rounded-[4px] border px-[6px]",
              "font-mono text-[10.5px] leading-none tracking-[0.06em] transition-[background-color,color,border-color,opacity] duration-100",
              "border-white/30 bg-black/45 text-white/80",
              "hover:border-white hover:bg-white hover:text-black",
              "focus-visible:outline focus-visible:outline-1 focus-visible:outline-white",
              passPending ? "opacity-100" : "opacity-0 focus-visible:opacity-100 group-hover/cell:opacity-100"
            )}
          >
            {passPending ? "…" : "通过"}
          </button>
        )}

        {!take && !missing && (
          <span className="pointer-events-none absolute inset-0 grid place-items-center opacity-0 transition-opacity group-hover/cell:opacity-100">
            <span className="grid size-[34px] place-items-center rounded-full border border-white/50 bg-black/50">
              <Play className="ml-0.5 size-3.5 fill-white text-white" />
            </span>
          </span>
        )}

        {stamp && (
          <span className="gb-stamp absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2" data-s={stamp}>
            <b>{STATUS_LABEL[stamp]}</b>
          </span>
        )}
      </div>

      {take ? (
        <div className="flex justify-between gap-1.5 pt-1.5 font-mono text-[10px] text-ash-dim">
          {/* 在块的列里，块号已经写在列头上了，这里改显示镜号更有用 */}
          <span className="min-w-0 truncate">
            {shots.length ? shots.slice(0, 3).join(" ") : card.chunk ?? "—"}
          </span>
          <span className="flex-none">{STATUS_LABEL[card.status]}</span>
        </div>
      ) : (
        <div className="flex items-baseline gap-2 px-0.5 pt-2">
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ash">
            {shots.length ? shots.join(" ") : fileName(card.rel_path)}
          </span>
          <span className="flex-none text-[10.5px] text-ash-dim">
            {shortStamp(card.updated_at || card.created_at)}
          </span>
        </div>
      )}
    </div>
  )
}
