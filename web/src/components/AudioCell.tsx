import { useEffect, useRef, useState } from "react"
import { Pin, Play, Square } from "lucide-react"
import { cn } from "cn"

/**
 * 全局播放仲裁：
 * - activeEl = 正在响的元素（悬停预览顶掉上一个）
 * - pinnedEl = 被钉住的元素（钉住期间悬停别的格不抢，鼠标移开也不停）
 */
let activeEl: HTMLAudioElement | null = null
let pinnedEl: HTMLAudioElement | null = null

export function fmt(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) sec = 0
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s.toString().padStart(2, "0")}`
}

/** 悬停即播的播放器状态。ref 挂到 <audio> 上，bind 摊到同一个元素。 */
export function useAudioPlayer(src: string) {
  const ref = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [t, setT] = useState(0)
  const [dur, setDur] = useState(0)

  // 格被卸载（筛选变化 / 切视图）时停声，不留孤儿
  useEffect(
    () => () => {
      const el = ref.current
      if (el) {
        if (activeEl === el) activeEl = null
        if (pinnedEl === el) pinnedEl = null
        el.pause()
      }
    },
    []
  )

  /** 悬停进入：预览播放。有别的格被钉住在响时，不抢。 */
  function play() {
    const el = ref.current
    if (!el || !src) return
    if (pinnedEl && pinnedEl !== el) return
    if (activeEl && activeEl !== el) {
      activeEl.pause()
      activeEl.currentTime = 0
    }
    activeEl = el
    // 浏览器可能拦无手势自动播放（rejection 静默，点固定钮仍可播）
    el.play().then(
      () => setPlaying(true),
      () => setPlaying(false)
    )
  }

  /** 悬停离开：停止并归零。被钉住的不理离开。 */
  function stop() {
    const el = ref.current
    if (!el) return
    if (pinnedEl === el) return
    el.pause()
    el.currentTime = 0
    if (activeEl === el) activeEl = null
    setPlaying(false)
    setT(0)
  }

  /**
   * 固定钮三态：
   * - 没在播 → 点它 = 开始播并钉住
   * - 悬停播着但没钉 → 点它 = 钉住（移开不打断）
   * - 已钉住 → 点它 = 停止并解除
   */
  function togglePin() {
    const el = ref.current
    if (!el) return
    if (pinnedEl === el) {
      pinnedEl = null
      setPinned(false)
      el.pause()
      el.currentTime = 0
      if (activeEl === el) activeEl = null
      setPlaying(false)
      setT(0)
      return
    }
    pinnedEl = el
    setPinned(true)
    if (activeEl && activeEl !== el) {
      activeEl.pause()
      activeEl.currentTime = 0
    }
    activeEl = el
    if (el.paused) {
      el.play().then(
        () => setPlaying(true),
        () => setPlaying(false)
      )
    } else {
      setPlaying(true)
    }
  }

  return {
    ref,
    playing,
    pinned,
    t,
    dur,
    play,
    stop,
    togglePin,
    bind: {
      preload: "none" as const,
      onTimeUpdate: () => setT(ref.current?.currentTime ?? 0),
      onLoadedMetadata: () => setDur(ref.current?.duration ?? 0),
      onEnded: () => {
        const el = ref.current
        if (el) {
          if (pinnedEl === el) pinnedEl = null
          if (activeEl === el) activeEl = null
        }
        setPinned(false)
        setPlaying(false)
        setT(0)
      },
      onPause: () => setPlaying(false),
      onPlay: () => setPlaying(true),
    },
  }
}

/**
 * 声部块：等宽竖条 = 材质（rule 灰），点亮的那一段 = 真实播放进度（paper 白）。
 * 和拆分台参考锚带（RefStrip）同一个音频隐喻，全产品一致。
 */
export function VoiceBars({
  t,
  dur,
  on = 3,
  period = 7,
  className,
}: {
  t: number
  dur: number
  on?: number
  period?: number
  className?: string
}) {
  const pct = dur > 0 ? Math.min(100, (t / dur) * 100) : 0
  return (
    <div
      className={cn("h-full w-full rounded-[1px]", className)}
      style={{
        backgroundImage:
          `repeating-linear-gradient(90deg, var(--gb-paper) 0 ${on}px, transparent ${on}px ${period}px),` +
          `repeating-linear-gradient(90deg, var(--gb-rule) 0 ${on}px, transparent ${on}px ${period}px)`,
        backgroundSize: `${pct}% 100%, 100% 100%`,
        backgroundRepeat: "no-repeat, no-repeat",
      }}
    />
  )
}

/** 黑底区右下角的固定钮（和右上角勾选钮同一套浮层语言）：
 *  没在播 = 播放符（点了开始播并钉住）；悬停播着 = 图钉（点了钉住）；已钉住 = 停止符 */
export function PinButton({
  playing,
  pinned,
  onToggle,
}: {
  playing: boolean
  pinned: boolean
  onToggle: () => void
}) {
  const label = pinned ? "停止" : "固定播放"
  const tip = pinned
    ? "停止并取消固定"
    : playing
      ? "钉住这段（鼠标移开继续播）"
      : "固定播放（鼠标移开也不打断）"
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation()
        onToggle()
      }}
      aria-label={label}
      title={tip}
      className={cn(
        "absolute bottom-1.5 right-1.5 grid size-6 place-items-center rounded-[4px] border transition-colors",
        pinned
          ? "border-white bg-paper text-[#131313]"
          : "border-white/40 bg-black/55 text-ash hover:border-white hover:text-paper"
      )}
    >
      {pinned ? (
        <Square className="size-3" strokeWidth={2.5} />
      ) : playing ? (
        <Pin className="size-3" strokeWidth={2.5} />
      ) : (
        <Play className="size-3" strokeWidth={2.5} />
      )}
    </button>
  )
}

/**
 * 检查器里的音频预览：同一套声部块语言，整块是悬停面。
 */
export function AudioPreview({ src }: { src: string }) {
  const p = useAudioPlayer(src)
  return (
    <div
      onMouseEnter={p.play}
      onMouseLeave={p.stop}
      title="悬停即试听"
      className="relative grid h-[150px] cursor-pointer place-items-center overflow-hidden rounded-cell bg-black"
    >
      <div className="h-[54px] w-full px-8">
        <VoiceBars t={p.t} dur={p.dur} />
      </div>

      {/* 中央态标：空闲 = 空心播放符；播放中 = 实心停止符 */}
      <span
        className={cn(
          "absolute grid size-9 place-items-center rounded-full border transition-colors",
          p.playing
            ? "border-white/70 bg-black/45 text-paper"
            : "border-white/25 bg-black/30 text-ash-dim"
        )}
      >
        {p.playing ? (
          <Square className="size-3.5" strokeWidth={2.5} />
        ) : (
          <Play className="size-3.5" strokeWidth={2.5} />
        )}
      </span>

      <span className="absolute inset-x-3 bottom-2.5 flex items-center gap-2 font-mono text-[10.5px] text-ash-dim">
        <span>{p.dur > 0 ? `${fmt(p.t)} / ${fmt(p.dur)}` : "—"}</span>
        <span className="flex-1" />
        <span>{p.playing ? "播放中" : "悬停播放"}</span>
      </span>

      <audio ref={p.ref} src={src} {...p.bind} />
    </div>
  )
}
