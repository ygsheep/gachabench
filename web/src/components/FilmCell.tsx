import { useRef } from "react"
import { Play } from "lucide-react"
import { cn } from "cn"
import type { Card, CardStatus } from "@/lib/types"
import { STATUS_LABEL, fileName, seconds, shortStamp } from "@/lib/format"

/**
 * 胶片格。状态一律靠形状 + 明度表达：
 * 正片 = 实心亮条 ／ 废稿 = 斜纹胶条 + 虚线轮廓 ／ 草稿 = 点线。
 * 缩略图是真实视频流，悬停即预览 —— 抽卡就是要快速扫。
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
}: {
  card: Card
  variant?: "cell" | "take"
  shots?: string[]
  takeCount?: number
  stamp?: CardStatus | null
  active?: boolean
  index?: number
  onClick?: () => void
}) {
  const ref = useRef<HTMLVideoElement>(null)
  const take = variant === "take"
  const rejected = card.status === "reject"

  function play() {
    const v = ref.current
    if (!v) return
    v.play().catch(() => {})
  }
  function stop() {
    const v = ref.current
    if (!v) return
    v.pause()
    v.currentTime = 0
  }

  return (
    <button
      type="button"
      onClick={onClick}
      data-s={card.status}
      aria-label={`${card.seg ? card.seg + " 段 " : ""}${card.chunk ?? ""}，${STATUS_LABEL[card.status]}${
        takeCount ? `，共 ${takeCount} 条候选` : ""
      }`}
      aria-current={active ? true : undefined}
      onMouseEnter={play}
      onMouseLeave={stop}
      onFocus={play}
      onBlur={stop}
      className={cn(
        "gb-cell min-w-0 text-left",
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
        <video
          ref={ref}
          src={`/api/media/${card.id}`}
          muted
          playsInline
          preload="metadata"
          className="block aspect-video w-full bg-black object-cover"
        />
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

        {!take && (
          <span className="pointer-events-none absolute inset-0 grid place-items-center opacity-0 transition-opacity group-hover/cell:opacity-100 [button:hover>&]:opacity-100">
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
          <span>{card.chunk ?? "—"}</span>
          <span>{STATUS_LABEL[card.status]}</span>
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
    </button>
  )
}
