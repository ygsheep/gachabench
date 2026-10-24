import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  ArrowLeft,
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  Keyboard,
  List,
  Pause,
  Play,
  Repeat,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
  X,
} from "lucide-react"
import { cn } from "cn"
import { FilmCell } from "@/components/FilmCell"
import { api } from "@/lib/api"
import { exportItems } from "@/lib/download"
import { STATUS_LABEL, chunkLabel, seconds, timecode } from "@/lib/format"
import type {
  AssetCategory,
  AssetsResp,
  Card,
  CardStatus,
  SegmentPrompt,
  SegmentsShotsResp,
} from "@/lib/types"

const FPS = 25
const FRAME = 1 / FPS
const RATES = [0.5, 1, 2]

type Tab = "prompt" | "asset" | "shot"

/** 上半区高度的记忆键 —— 拖过分隔条之后下次进来还用这个高度 */
const STAGE_KEY = "gb-review-stage"

export function Reviewer({
  seg,
  chunk,
  takes,
  blocks,
  initialId,
  shotTags,
  rejectTags,
  onClose,
  onJump,
}: {
  seg: string
  chunk: string
  takes: Card[]
  /** 本段所有「有候选」的块，按块序排好 —— 上一个/下一个和抽屉都靠它导航 */
  blocks: { chunk: string; takes: Card[] }[]
  initialId: number
  shotTags: string[]
  rejectTags: string[]
  onClose: () => void
  onJump: (chunk: string) => void
}) {
  const [id, setId] = useState(initialId)
  const [tab, setTab] = useState<Tab>("prompt")
  const [sheet, setSheet] = useState(false)
  const [drawer, setDrawer] = useState(false)
  const [picked, setPicked] = useState<string[]>([])
  const [rejectNote, setRejectNote] = useState("")
  const [copyToFinal, setCopyToFinal] = useState(false)
  const [stamp, setStamp] = useState<{ id: number; s: CardStatus } | null>(null)

  const [playing, setPlaying] = useState(false)
  const [pos, setPos] = useState(0)
  const [rate, setRate] = useState(1)
  const [loop, setLoop] = useState(true)
  const [muted, setMuted] = useState(true)

  const videoRef = useRef<HTMLVideoElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()

  /* 上半区（TAKE 预览 + 播放器）的高度：拖过分隔条就记住，下次进来还是这个高度。
     null = 没拖过，用 52svh 的默认值。 */
  const [stageH, setStageH] = useState<number | null>(() => {
    const v = Number(localStorage.getItem(STAGE_KEY))
    return Number.isFinite(v) && v > 0 ? v : null
  })

  function onStageResizeStart(e: React.PointerEvent) {
    e.preventDefault()
    const startY = e.clientY
    const startH = stageRef.current?.getBoundingClientRect().height ?? 0
    const clamp = (h: number) =>
      Math.round(Math.min(window.innerHeight - 240, Math.max(220, h)))
    const move = (ev: PointerEvent) => {
      const h = clamp(startH + ev.clientY - startY)
      setStageH(h)
      localStorage.setItem(STAGE_KEY, String(h))
    }
    const prevSelect = document.body.style.userSelect
    document.body.style.userSelect = "none"
    const up = () => {
      document.body.style.userSelect = prevSelect
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", up)
    }
    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", up)
  }

  const idx = Math.max(
    0,
    takes.findIndex((t) => t.id === id)
  )
  const card: Card | undefined = takes[idx] ?? takes[0]
  const dur = card?.duration ?? 5

  /* 块导航：本段块序列里的当前位置 + 上/下目标。用块码（00/01…）当路标，
     不用「上一块/下一块」这种空话——看到下一个块的编号，才知道要切到哪去。 */
  const bi = Math.max(
    0,
    blocks.findIndex((b) => b.chunk === chunk)
  )
  const blockCount = blocks.length
  const prevChunk = blockCount > 1 ? blocks[(bi - 1 + blockCount) % blockCount].chunk : ""
  const nextChunk = blockCount > 1 ? blocks[(bi + 1) % blockCount].chunk : ""

  /* 切候选：停住、回到开头，等你看清楚再放 */
  useEffect(() => {
    const v = videoRef.current
    setPlaying(false)
    setPos(0)
    if (v) {
      v.pause()
      v.currentTime = 0
    }
  }, [id])

  /* 播放时用 rAF 刷时间码（timeupdate 只有 4Hz，会跳） */
  useEffect(() => {
    if (!playing) return
    let raf = 0
    const tick = () => {
      setPos(videoRef.current?.currentTime ?? 0)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing])

  useEffect(() => {
    setRejectNote(card?.reject_note ?? "")
    setPicked(card?.reject_tags ?? [])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card?.id])

  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    v.playbackRate = rate
    v.loop = loop
    v.muted = muted
  }, [rate, loop, muted, id])

  /* ---- 落库 ---- */
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patch<Card>(`/api/cards/${card!.id}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cards"] }),
    onError: (e: Error) => toast.error(e.message),
  })

  const toFinal = useMutation({
    mutationFn: (copy: boolean) =>
      api.post<{ card: Card; copied_to: string | null; missing_chunk?: boolean }>(
        `/api/cards/${card!.id}/to-final`,
        { copy_to_final: copy },
      ),
    onSuccess: (r) => {
      queryClient.invalidateQueries({ queryKey: ["cards"] })
      const name = r.copied_to?.split(/[\\/]/).pop()
      toast.success(name ? `已通过 → 素材/未分类/${name}` : "已标为正片", {
        description: r.missing_chunk
          ? "缺块号，只写了段号；可在资产页补"
          : name
            ? `${seg} 段 ${chunk} · 原件留在 草稿/`
            : `${seg} 段 ${chunk} · 原件留在 草稿/`,
      })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const classify = useCallback(
    (status: CardStatus) => {
      if (!card) return
      setStamp({ id: card.id, s: status })
      window.setTimeout(() => setStamp(null), 1500)

      if (status === "final") {
        toFinal.mutate(copyToFinal)
      } else if (status === "reject") {
        patch.mutate({ status, reject_tags: picked, reject_note: rejectNote })
        toast.success("已归入废稿", {
          description: picked.length ? picked.join("、") : "没选原因，之后还能补",
        })
      } else {
        patch.mutate({ status: "draft" })
        toast.success("已退回草稿", { description: `${seg} 段 ${chunk} · 还能继续抽` })
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [card?.id, copyToFinal, picked, rejectNote, seg, chunk]
  )

  const step = useCallback(
    (d: number) => {
      const v = videoRef.current
      if (!v) return
      v.currentTime = Math.min(dur, Math.max(0, v.currentTime + d))
      setPos(v.currentTime)
    },
    [dur]
  )

  const toggle = useCallback(() => {
    const v = videoRef.current
    if (!v) return
    if (v.paused) {
      v.play().catch(() => {})
      setPlaying(true)
    } else {
      v.pause()
      setPlaying(false)
    }
  }, [])

  /* ---- 键盘：审片是双手不离键盘的活 ---- */
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null
      const typing = t ? /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) : false
      if (e.key === "?" && !typing) {
        setSheet((s) => !s)
        return
      }
      if (e.key === "Escape") {
        if (sheet) setSheet(false)
        else onClose()
        return
      }
      if (typing) return
      const n = takes.length
      switch (e.key) {
        case " ":
          e.preventDefault()
          toggle()
          break
        case "ArrowLeft":
          e.preventDefault()
          if (n > 1) setId(takes[(idx - 1 + n) % n].id)
          break
        case "ArrowRight":
          e.preventDefault()
          if (n > 1) setId(takes[(idx + 1) % n].id)
          break
        case "ArrowUp":
          e.preventDefault()
          if (blockCount > 1) onJump(prevChunk)
          break
        case "ArrowDown":
          e.preventDefault()
          if (blockCount > 1) onJump(nextChunk)
          break
        case ",":
          step(-FRAME)
          break
        case ".":
          step(FRAME)
          break
        case "l":
        case "L":
          setLoop((v) => !v)
          break
        case "m":
        case "M":
          setMuted((v) => !v)
          break
        case "1":
          classify("final")
          break
        case "2":
          classify("reject")
          break
        case "3":
          classify("draft")
          break
        default:
          break
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [takes, idx, toggle, step, classify, onClose, onJump, prevChunk, nextChunk, blockCount, sheet])

  if (!card) return null

  const rejectList = picked.length ? picked : card.reject_tags

  return (
    <div
      className="fixed inset-0 z-60 flex flex-col bg-void"
      role="dialog"
      aria-modal="true"
      aria-label="审片器"
    >
      <header className="flex flex-none flex-wrap items-center gap-2.5 border-b border-rule-soft px-5 py-3">
        <button
          type="button"
          onClick={onClose}
          className="inline-flex h-7 items-center gap-1 rounded-ctl px-2 text-[12.5px] text-ash transition-colors hover:bg-cell hover:text-paper"
        >
          <ArrowLeft className="size-4" />
          返回
        </button>
        <Chip mono>{seg}</Chip>
        <Chip>{chunkLabel(chunk)}</Chip>
        {shotTags.map((s) => (
          <Chip key={s} mono>
            {s}
          </Chip>
        ))}
        <Chip mono>{seconds(dur)}</Chip>
        {card.reject_tags.map((t) => (
          <Chip key={t} dim>
            {t}
          </Chip>
        ))}
        <span className="flex-1" />
        {/* 块切换：上一个 / 下一个直接给出目标块号（00 → 01），中间的 N/M 是当前位置。
            候选条（同一块里的几条）仍是右边那组「候选 x/y」，两套导航各管各的。 */}
        <div className="flex items-center gap-0.5 rounded-ctl border border-rule bg-cell p-0.5">
          <button
            type="button"
            onClick={() => onJump(prevChunk)}
            disabled={blockCount <= 1}
            title={blockCount > 1 ? `上一个块：${chunkLabel(prevChunk)}` : "只有这一个块"}
            className="inline-flex h-6 items-center gap-0.5 rounded px-1.5 text-[11.5px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-40"
          >
            <ChevronLeft className="size-3.5" />
            <span className="font-mono">{blockCount > 1 ? chunkLabel(prevChunk) : "上"}</span>
          </button>
          <span className="px-1 font-mono text-[11px] text-ash-dim">
            {blockCount ? `${bi + 1}/${blockCount}` : "0/0"}
          </span>
          <button
            type="button"
            onClick={() => onJump(nextChunk)}
            disabled={blockCount <= 1}
            title={blockCount > 1 ? `下一个块：${chunkLabel(nextChunk)}` : "只有这一个块"}
            className="inline-flex h-6 items-center gap-0.5 rounded px-1.5 text-[11.5px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-40"
          >
            <span className="font-mono">{blockCount > 1 ? chunkLabel(nextChunk) : "下"}</span>
            <ChevronRight className="size-3.5" />
          </button>
        </div>
        <button
          type="button"
          onClick={() => setDrawer(true)}
          title="块目录"
          className="inline-flex h-7 items-center gap-1.5 rounded-ctl px-2 text-[12.5px] text-ash transition-colors hover:bg-cell hover:text-paper"
        >
          <List className="size-3.5" />
          块 {blockCount ? `${bi + 1}/${blockCount}` : "—"}
        </button>
        <span className="font-mono text-[12px] text-ash">
          候选 <span className="text-paper">{idx + 1}</span>/{takes.length}
        </span>
        <button
          type="button"
          onClick={() => setSheet(true)}
          className="inline-flex h-7 items-center gap-1.5 rounded-ctl px-2 text-[12.5px] text-ash transition-colors hover:bg-cell hover:text-paper"
        >
          <Keyboard className="size-3.5" />
          快捷键
        </button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_328px] max-[980px]:grid-cols-1 max-[980px]:overflow-auto">
        {/* ---------- 放映区 ---------- */}
        <div className="flex min-w-0 flex-col overflow-hidden px-5 pt-4 max-[980px]:overflow-visible">
          {/* 上半区：左 = 同块候选的预览列，右 = 播放器。高度可拖，拖过记在 localStorage */}
          <div
            ref={stageRef}
            style={stageH ? { height: stageH } : undefined}
            className={cn(
              "flex min-h-0 flex-none gap-4",
              stageH ? "" : "h-[52svh]",
              "max-[980px]:h-auto max-[980px]:flex-col"
            )}
          >
            {/* 候选预览列 —— 竖排不横滚，几条都一眼看到；窄屏回退成横排 */}
            <div className="flex w-[154px] flex-none flex-col max-[980px]:order-2 max-[980px]:w-full">
              <div className="flex flex-none items-baseline justify-between pb-2">
                <span className="text-[12px] text-ash">
                  这块跑了 <span className="font-mono text-paper">{takes.length}</span> 条
                </span>
                <span className="text-[10.5px] text-ash-dim max-[980px]:hidden">← →</span>
              </div>
              <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto pr-0.5 max-[980px]:flex-row max-[980px]:overflow-x-auto max-[980px]:pb-1">
                {takes.map((t, i) => (
                  <div key={t.id} className="w-[138px] flex-none">
                    <FilmCell
                      card={t}
                      variant="take"
                      index={i + 1}
                      active={t.id === id}
                      stamp={stamp?.id === t.id ? stamp.s : null}
                      onClick={() => setId(t.id)}
                    />
                  </div>
                ))}
              </div>
            </div>

            {/* 播放器列：视频按 16:9 自动取「装得下的最大值」，不再写死 46svh */}
            <div className="flex min-w-0 flex-1 flex-col max-[980px]:order-1">
              <div className="grid min-h-0 flex-1 place-items-center">
                <div className="relative aspect-video max-h-full w-full max-w-full overflow-hidden rounded-cell bg-black outline-1 outline-rule">
                  <video
                    ref={videoRef}
                    key={card.id}
                    src={`/api/media/${card.id}`}
                    playsInline
                    preload="metadata"
                    className="size-full object-contain"
                    onPlay={() => setPlaying(true)}
                    onPause={() => setPlaying(false)}
                    onEnded={() => setPlaying(false)}
                    onTimeUpdate={(e) => {
                      if (!playing) setPos(e.currentTarget.currentTime)
                    }}
                    onLoadedMetadata={(e) => {
                      e.currentTarget.muted = muted
                      e.currentTarget.playbackRate = rate
                      e.currentTarget.loop = loop
                    }}
                  />
                  <span className="pointer-events-none absolute left-3 top-2.5 font-mono text-[11px] tracking-wide text-white/60">
                    {seg} · {chunkLabel(chunk)} · {timecode(pos)}
                  </span>
                  {/* 文件没了就别装：盖一层说明，别让人对着黑屏按帧步进 */}
                  {card.missing && (
                    <div className="absolute inset-0 grid place-items-center bg-black/88 px-6 text-center">
                      <div className="max-w-[34ch]">
                        <p className="text-[13px] font-medium text-paper">文件已不在盘上</p>
                        <p className="mt-1.5 break-all font-mono text-[10.5px] leading-relaxed text-ash-dim">
                          {card.rel_path}
                        </p>
                        <p className="mt-2 text-[11.5px] leading-relaxed text-ash">
                          被改名或移动了。这条记录可以留着（备注/废稿原因还在），
                          重新扫描后新路径下会另立一张卡。
                        </p>
                      </div>
                    </div>
                  )}
                  {stamp && (
                    <span
                      className="gb-stamp absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
                      data-s={stamp.s}
                    >
                      <b>{STATUS_LABEL[stamp.s]}</b>
                    </span>
                  )}
                </div>
              </div>

              <div className="mt-3 flex flex-none flex-wrap items-center gap-2">
              <TButton onClick={() => step(-0.1)} title="退 0.1 秒">
                <SkipBack className="size-4" />
              </TButton>
              <TButton onClick={() => step(-FRAME)} title="退一帧">
                <ChevronLeft className="size-4" />
              </TButton>
              <TButton onClick={toggle} title="播放 / 暂停">
                {playing ? <Pause className="size-4" /> : <Play className="ml-0.5 size-4" />}
              </TButton>
              <TButton onClick={() => step(FRAME)} title="进一帧">
                <ChevronRight className="size-4" />
              </TButton>
              <TButton onClick={() => step(0.1)} title="进 0.1 秒">
                <SkipForward className="size-4" />
              </TButton>

              <div className="flex min-w-[190px] flex-1 flex-col gap-1.5">
                <div className="flex items-baseline justify-between font-mono text-[11px] text-ash">
                  <span>
                    帧 <span className="text-paper">{timecode(pos)}</span> / {timecode(dur)}
                  </span>
                  <span>{FPS} fps</span>
                </div>
                <input
                  type="range"
                  className="gb-range"
                  min={0}
                  max={Math.max(1, Math.round(dur * 1000))}
                  value={Math.round(Math.min(pos, dur) * 1000)}
                  style={{ ["--p" as string]: `${((pos / (dur || 1)) * 100).toFixed(2)}%` }}
                  onChange={(e) => {
                    const v = videoRef.current
                    if (!v) return
                    v.currentTime = Number(e.target.value) / 1000
                    setPos(v.currentTime)
                  }}
                  aria-label="播放进度"
                />
              </div>

              <TButton on={loop} onClick={() => setLoop((v) => !v)} title="循环">
                <Repeat className="size-4" />
              </TButton>
              <TButton
                on={rate !== 1}
                wide
                onClick={() => setRate(RATES[(RATES.indexOf(rate) + 1) % RATES.length])}
                title="播放速度"
              >
                {rate.toFixed(1)}×
              </TButton>
              <TButton on={!muted} onClick={() => setMuted((v) => !v)} title={muted ? "取消静音" : "静音"}>
                {muted ? <VolumeX className="size-4" /> : <Volume2 className="size-4" />}
              </TButton>
              </div>
            </div>
          </div>

          {/* 拖这里调上半区高度；拖完记住，tab 区至少留 220px */}
          <div
            onPointerDown={onStageResizeStart}
            role="separator"
            aria-orientation="horizontal"
            aria-label="拖动调节播放器区域高度"
            className="group mx-[-20px] mt-2.5 flex h-3 flex-none cursor-row-resize touch-none items-center justify-center max-[980px]:hidden"
          >
            <span className="h-[3px] w-24 rounded-full bg-rule transition-colors group-hover:bg-wash-line" />
          </div>

          <div className="flex flex-none gap-0.5 border-b border-rule-soft">
            {(
              [
                ["prompt", "提示词"],
                ["asset", "素材"],
                ["shot", "分镜"],
              ] as [Tab, string][]
            ).map(([k, label]) => (
              <button
                key={k}
                type="button"
                onClick={() => setTab(k)}
                aria-selected={tab === k}
                className={cn(
                  "relative h-9 px-3 text-[12.5px] transition-colors",
                  tab === k ? "font-medium text-paper" : "text-ash hover:text-paper"
                )}
              >
                {label}
                {tab === k && (
                  <span className="absolute inset-x-2 -bottom-px h-0.5 rounded bg-white" />
                )}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-auto pb-6">
            <TabBody
              tab={tab}
              seg={seg}
              seedCard={card}
              shotTags={shotTags}
              promptMd={card.prompt_md}
            />
          </div>
        </div>

        {/* ---------- 归类 ---------- */}
        <aside className="overflow-auto border-l border-rule-soft bg-panel max-[980px]:border-l-0 max-[980px]:border-t">
          <Sec title="归类">
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => classify("final")}
                disabled={toFinal.isPending}
                className="inline-flex h-9 items-center justify-center gap-2 rounded-ctl bg-paper text-[13.5px] font-medium text-[#131313] transition-transform hover:bg-white active:translate-y-px active:scale-[0.99] disabled:opacity-45"
              >
                <Check className="size-4" strokeWidth={2.4} />
                标为正片
              </button>
              <label className="flex items-start gap-2.5">
                <button
                  type="button"
                  role="switch"
                  aria-checked={copyToFinal}
                  onClick={() => setCopyToFinal((v) => !v)}
                  className={cn(
                    "relative mt-px h-[19px] w-[34px] flex-none rounded-full transition-colors",
                    copyToFinal
                      ? "bg-wash shadow-[inset_0_0_0_1px_var(--gb-wash-line)]"
                      : "bg-hi"
                  )}
                >
                  <span
                    className={cn(
                      "absolute top-[2.5px] size-3.5 rounded-full transition-transform",
                      copyToFinal ? "translate-x-[15px] bg-white" : "translate-x-[2.5px] bg-[#a0a0a0]"
                    )}
                  />
                </button>
                <span className="text-[11.5px] leading-relaxed text-ash-dim">
                  同时复制到 <span className="font-mono">生成/</span>，原件留在草稿
                </span>
              </label>
              <button
                type="button"
                onClick={() => classify("reject")}
                disabled={patch.isPending}
                className="inline-flex h-9 items-center justify-center gap-2 rounded-ctl border border-[#5c5c5c] text-[13.5px] font-medium text-[#c9c9c9] transition-colors hover:border-[#828282] hover:bg-wash active:translate-y-px disabled:opacity-45"
              >
                <X className="size-4" strokeWidth={2.4} />
                标为废稿
              </button>
            </div>
          </Sec>

          <Sec title="废稿原因">
            <div className="flex flex-wrap gap-1.5">
              {rejectTags.map((t) => {
                const on = picked.includes(t)
                return (
                  <button
                    key={t}
                    type="button"
                    aria-pressed={on}
                    onClick={() =>
                      setPicked((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]))
                    }
                    className={cn(
                      "rounded-full border px-2.5 py-[3px] text-[11.5px] transition-all active:scale-[0.94]",
                      on
                        ? "border-[#8a8a8a] bg-wash text-[#dcdcdc]"
                        : "border-rule text-ash hover:border-[#5c5c5c] hover:text-paper"
                    )}
                  >
                    {t}
                  </button>
                )
              })}
            </div>
            <textarea
              value={rejectNote}
              onChange={(e) => setRejectNote(e.target.value)}
              rows={2}
              placeholder="补充说明，会写进废稿记录"
              className="mt-2.5 w-full resize-y rounded-ctl border border-rule bg-cell px-2.5 py-2 text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
            />
          </Sec>

          <Sec title="备注">
            <textarea
              key={card.id}
              defaultValue={card.note}
              rows={2}
              placeholder="这条想记住什么"
              onBlur={(e) => {
                if (e.target.value !== card.note) {
                  patch.mutate({ note: e.target.value })
                  toast.success("备注已保存")
                }
              }}
              className="w-full resize-y rounded-ctl border border-rule bg-cell px-2.5 py-2 text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
            />
            <p className="mt-2 font-mono text-[11px] text-ash-dim">{card.rel_path}</p>
            <p className="mt-1 text-[11px] text-ash-dim">
              {rejectList.length ? `已记：${rejectList.join("、")}` : "还没记废稿原因"}
            </p>
          </Sec>

          <Sec title="导出">
            <button
              type="button"
              onClick={async () => {
                const r = await exportItems([{ rel_path: card.rel_path }])
                if (!r.ok) {
                  // 取消不算错误，别弹红条
                  if (!r.message.includes("取消")) toast.error(r.message)
                  return
                }
                toast.success("已导出这一条", {
                  description: r.path ?? r.message,
                })
              }}
              className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-ctl border border-rule bg-cell text-[13px] font-medium text-paper transition-colors hover:bg-hi active:translate-y-px"
            >
              <Download className="size-4" />
              导出这一条
            </button>
            <p className="mt-2 text-[11px] leading-relaxed text-ash-dim">
              浏览器里是直接下载；桌面版会先问导出到哪个目录，重名会自动加{" "}
              <span className="font-mono">_02</span>，不会覆盖你上次导的东西。
            </p>
          </Sec>
        </aside>
      </div>

      {sheet && (
        <div
          className="fixed inset-0 z-80 grid place-items-center bg-black/65"
          onClick={(e) => {
            if (e.target === e.currentTarget) setSheet(false)
          }}
        >
          <div className="w-[min(520px,92vw)] rounded-panel border border-rule bg-panel p-5">
            <h2 className="mb-3.5 text-[14px] font-medium">快捷键</h2>
            <div className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2.5 text-[12.5px]">
              <Kbd>1</Kbd>
              <span className="text-ash">正片</span>
              <Kbd>2</Kbd>
              <span className="text-ash">废稿</span>
              <Kbd>3</Kbd>
              <span className="text-ash">退回草稿</span>
              <Kbd>← →</Kbd>
              <span className="text-ash">换同一块的候选条</span>
              <Kbd>↑ ↓</Kbd>
              <span className="text-ash">换块</span>
              <Kbd>空格</Kbd>
              <span className="text-ash">播放 / 暂停</span>
              <Kbd>, .</Kbd>
              <span className="text-ash">退 / 进一帧（1/25 秒）</span>
              <Kbd>L M</Kbd>
              <span className="text-ash">循环 / 静音</span>
              <Kbd>Esc</Kbd>
              <span className="text-ash">返回抽卡台</span>
            </div>
          </div>
        </div>
      )}

      {/* 块目录抽屉：本段所有块一列排开，当前块高亮，点哪块跳到哪块。
          缩略图复用 FilmCell（悬停即预览），和左侧候选列同一种读法。 */}
      {drawer && (
        <>
          <div className="absolute inset-0 z-10 bg-black/45" onClick={() => setDrawer(false)} />
          <aside className="absolute inset-y-0 right-0 z-20 flex w-[300px] flex-col border-l border-rule-soft bg-panel">
            <header className="flex flex-none items-center gap-2 border-b border-rule-soft px-3.5 py-2.5">
              <span className="text-[12.5px] text-paper">本段 {blockCount} 个块</span>
              <span className="flex-1" />
              <button
                type="button"
                onClick={() => setDrawer(false)}
                aria-label="关闭块目录"
                className="grid size-7 place-items-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper"
              >
                <X className="size-3.5" />
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              <div className="flex flex-col gap-2.5">
                {blocks.map((b, i) => (
                  <div
                    key={b.chunk}
                    role="button"
                    tabIndex={0}
                    onClick={() => {
                      setDrawer(false)
                      onJump(b.chunk)
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault()
                        setDrawer(false)
                        onJump(b.chunk)
                      }
                    }}
                    className={cn(
                      "cursor-pointer rounded-cell border p-2 transition-colors",
                      i === bi
                        ? "border-wash-line bg-wash"
                        : "border-rule-soft bg-cell hover:bg-hi"
                    )}
                  >
                    <FilmCell card={b.takes[0]} variant="take" index={i + 1} active={i === bi} />
                    <span className="mt-1.5 flex items-baseline gap-2 px-0.5">
                      <span className="font-mono text-[12px] text-paper">{chunkLabel(b.chunk)}</span>
                      <span className="text-[10.5px] text-ash-dim">{b.takes.length} 条</span>
                      {i === bi && <span className="ml-auto text-[10px] text-ash-dim">正在看</span>}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </aside>
        </>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */

function Chip({ children, mono, dim }: { children: React.ReactNode; mono?: boolean; dim?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px]",
        dim ? "border-[#555] text-[#c9c9c9]" : "border-rule text-ash",
        mono && "font-mono"
      )}
    >
      {children}
    </span>
  )
}

function TButton({
  children,
  onClick,
  title,
  on,
  wide,
}: {
  children: React.ReactNode
  onClick: () => void
  title: string
  on?: boolean
  wide?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      className={cn(
        "inline-grid place-items-center rounded-ctl border transition-all active:translate-y-px active:scale-[0.94]",
        wide ? "h-8 px-2.5 font-mono text-[11.5px]" : "size-8",
        on
          ? "border-wash-line bg-wash text-paper"
          : "border-rule bg-cell text-paper hover:bg-hi"
      )}
    >
      {children}
    </button>
  )
}

function Sec({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-rule-soft p-4 last:border-b-0">
      <h3 className="mb-2.5 text-[11.5px] font-medium text-ash">{title}</h3>
      {children}
    </section>
  )
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5.5 items-center justify-center rounded border border-rule border-b-2 bg-cell px-1.5 font-mono text-[10.5px] text-[#c6c6c6]">
      {children}
    </kbd>
  )
}

/* ------------------------------------------------------------------ */

function TabBody({
  tab,
  seg,
  seedCard,
  shotTags,
  promptMd,
}: {
  tab: Tab
  seg: string
  seedCard: Card
  shotTags: string[]
  promptMd: string | null
}) {
  if (tab === "prompt")
    return <PromptTab seg={seg} shotTags={shotTags} promptMd={promptMd} card={seedCard} />
  if (tab === "asset") return <AssetTab />
  return <ShotTab seg={seg} shotTags={shotTags} card={seedCard} />
}

/**
 * 提示词：默认显示**这条片子跑片时用的正文快照**，可切到当前源 md 对比。
 *
 * 以前这里只读 /api/segments/{seg}/prompt（整段 md 的最新内容），于是出现
 * 「面板上是刚改的词、片子还是旧的那版」的错位 —— 因为跑片读的是另一份。
 * 现在块正文每次从源 md 现算、跑完把当时那份存进卡片，所以默认展示的就是
 * 真正喂给模型的那一版。
 */
function PromptTab({
  seg,
  shotTags,
  promptMd,
  card,
}: {
  seg: string
  shotTags: string[]
  promptMd: string | null
  card: Card
}) {
  const detailQ = useQuery({
    queryKey: ["card", card.id],
    queryFn: () => api.get<Card>(`/api/cards/${card.id}`),
    enabled: !!card.id,
    retry: 1,
  })
  const snapshot = detailQ.data?.prompt_snapshot ?? null
  const params = detailQ.data?.params ?? null

  const { data, isPending, isError, error } = useQuery({
    queryKey: ["prompt", seg],
    queryFn: () => api.get<SegmentPrompt>(`/api/segments/${seg}/prompt`),
    enabled: !!seg,
    retry: 1,
  })

  /** 用户没手动切过就跟数据走：有快照看快照，没有看源 md */
  const [manual, setManual] = useState<"snapshot" | "live" | null>(null)
  const mode = manual ?? (snapshot ? "snapshot" : "live")

  const lines = useMemo(() => {
    const text = data?.codeblock ?? ""
    const raw = text ? text.split("\n") : []
    // 本地扫 [Shot N] 的块区间，用于高亮
    const want = new Set(
      shotTags.map((t) => Number(t.replace(/\D/g, "")) % 100).filter((n) => !Number.isNaN(n))
    )
    const hit = new Array(raw.length).fill(false)
    let cur = -1
    raw.forEach((l, i) => {
      const m = l.match(/^\[Shot (\d+)\]/)
      if (m) cur = Number(m[1])
      if (cur >= 0 && want.has(cur)) hit[i] = true
    })
    return raw.map((l, i) => ({ n: i + 1, text: l, hit: hit[i] }))
  }, [data?.codeblock, shotTags])

  const snapLines = useMemo(
    () => (snapshot ? snapshot.replace(/\n$/, "").split("\n") : []),
    [snapshot]
  )

  const switcher = (
    <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
      <button
        type="button"
        onClick={() => setManual("snapshot")}
        disabled={!snapshot}
        aria-pressed={mode === "snapshot"}
        className={cn(
          "h-7 rounded-ctl border px-2.5 text-[11.5px] transition-colors disabled:opacity-40",
          mode === "snapshot"
            ? "border-wash-line bg-wash font-medium text-paper"
            : "border-rule bg-cell text-ash hover:bg-hi hover:text-paper"
        )}
      >
        跑片快照
      </button>
      <button
        type="button"
        onClick={() => setManual("live")}
        aria-pressed={mode === "live"}
        className={cn(
          "h-7 rounded-ctl border px-2.5 text-[11.5px] transition-colors",
          mode === "live"
            ? "border-wash-line bg-wash font-medium text-paper"
            : "border-rule bg-cell text-ash hover:bg-hi hover:text-paper"
        )}
      >
        当前源 md
      </button>
      <span className="flex-1" />
      <span className="text-[11px] text-ash-dim">
        {mode === "snapshot" ? "这条片子实际喂给模型的那一版" : "源 md 现状（可能已经不是它了）"}
      </span>
    </div>
  )

  /* ---------- 快照模式 ---------- */
  if (mode === "snapshot") {
    if (detailQ.isPending) return <p className="text-[12.5px] text-ash">读取跑片快照…</p>
    if (!snapLines.length)
      return (
        <div>
          {switcher}
          <p className="text-[12.5px] text-ash">
            这条卡片没有快照（改造前跑的片子）。切到「当前源 md」看现在的提示词。
          </p>
        </div>
      )
    return (
      <div>
        {switcher}
        {!!params && Object.keys(params).length > 0 && (
          <div className="mb-2.5 flex flex-wrap gap-1.5">
            {Object.entries(params)
              .filter(([k, v]) => k !== "chunk" && v !== null && v !== undefined && v !== "")
              .map(([k, v]) => (
                <span key={k} className="gb-chip font-mono">
                  {k}=
                  {String(v).length > 22 ? `${String(v).slice(0, 22)}…` : String(v)}
                </span>
              ))}
          </div>
        )}
        <div className="rounded-panel border border-rule-soft bg-panel p-3.5">
          <pre className="m-0 overflow-auto font-mono text-[11.5px] leading-[1.75] whitespace-pre text-[#c6c6c6]">
            {snapLines.map((l, i) => (
              <span key={i} className="block">
                <span className="select-none pr-3.5 text-[#555]">
                  {String(i + 1).padStart(4, "0")}
                </span>
                {l}
              </span>
            ))}
          </pre>
        </div>
        <p className="mt-2.5 text-[11.5px] text-ash-dim">
          这是本块实际提交的正文（已裁剪、重编号、时间码重基）。改源 md 不会动到它，
          想用新词就回拆分台重跑这一块。
        </p>
      </div>
    )
  }

  /* ---------- 源 md 模式 ---------- */
  if (isPending) return <p className="text-[12.5px] text-ash">读取提示词…</p>
  if (isError)
    return (
      <p className="text-[12.5px] text-ash">
        {promptMd ? `关联文件 ${promptMd}，但接口没读出来。` : ""}
        {(error as Error)?.message ?? "这个段还没关联提示词。"}
      </p>
    )
  if (!lines.length) return <p className="text-[12.5px] text-ash">这个段的 md 里没找到 §④ 代码块。</p>

  return (
    <div>
      {switcher}
      <div className="rounded-panel border border-rule-soft bg-panel p-3.5">
        <pre className="m-0 overflow-auto font-mono text-[11.5px] leading-[1.75] whitespace-pre text-[#c6c6c6]">
          {lines.map((l) => (
            <span
              key={l.n}
              className={cn("block", l.hit && "bg-wash shadow-[inset_2px_0_0_#fff]")}
            >
              <span className="select-none pr-3.5 text-[#555]">
                {String(l.n).padStart(4, "0")}
              </span>
              {l.text}
            </span>
          ))}
        </pre>
      </div>
      <p className="mt-2.5 text-[11.5px] text-ash-dim">
        高亮的是这块实际用到的镜块。其余镜块被裁掉了，原文一个字没改。
      </p>
    </div>
  )
}

/** 素材：读工作区 {asset} 目录，给这块做参考锚 */
function AssetTab() {
  const { data, isPending, isError } = useQuery({
    queryKey: ["assets"],
    queryFn: () => api.get<AssetsResp>("/api/assets/groups"),
    retry: 1,
  })

  if (isPending) return <p className="text-[12.5px] text-ash">读取素材目录…</p>
  if (isError) return <p className="text-[12.5px] text-ash">素材目录读不出来，先在「资产管理」里确认工作区。</p>

  const cats: AssetCategory[] = data?.categories ?? []
  if (!cats.length)
    return <p className="text-[12.5px] text-ash">素材目录还是空的。把参考图丢进 素材/ 就会出现在这里。</p>

  return (
    <div className="flex flex-col gap-3">
      {cats.map((c) => (
        <div key={c.key} className="rounded-panel border border-rule-soft bg-panel p-3.5">
          <h3 className="mb-2.5 text-[12.5px] font-medium text-paper">
            {c.label}
            <span className="ml-2 font-mono text-[11px] text-ash-dim">
              {c.groups.reduce((n, g) => n + g.items.length, 0)} 个文件
            </span>
          </h3>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(122px,1fr))] gap-2.5">
            {c.groups.flatMap((g) =>
              g.items
                .filter((it) => it.kind === "image" || it.kind === "video")
                .slice(0, 12)
                .map((it) => (
                  <div key={it.rel} className="overflow-hidden rounded-cell border border-rule-soft bg-cell">
                    <img
                      src={it.url}
                      alt={it.name}
                      loading="lazy"
                      className="block aspect-square w-full object-cover"
                    />
                    <span className="block truncate px-1.5 py-1 text-[10.5px] text-ash">{g.name}</span>
                  </div>
                ))
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

/** 分镜：这块压住的镜 */
function ShotTab({ seg, shotTags, card }: { seg: string; shotTags: string[]; card: Card }) {
  const { data, isPending, isError } = useQuery({
    queryKey: ["shots", seg],
    queryFn: () => api.get<SegmentsShotsResp>(`/api/segments/${seg}/shots`),
    enabled: !!seg,
    retry: 1,
  })

  const wanted = new Set(shotTags.map((t) => Number(t.replace(/\D/g, "")) % 100))
  const shots = (data?.shots ?? []).filter((s) => !wanted.size || wanted.has(s.index))

  return (
    <div className="flex flex-col gap-2.5">
      <div className="rounded-panel border border-rule-soft bg-panel p-3.5">
        <h3 className="mb-3 text-[12.5px] font-medium text-paper">
          这块压住的镜
          <span className="ml-2 font-mono text-[11px] text-ash-dim">
            {shots.length} 镜 · {seconds(card.duration)}
          </span>
        </h3>
        {isPending && <p className="text-[12px] text-ash">解析分镜…</p>}
        {isError && <p className="text-[12px] text-ash">这个段的分镜表读不出来。</p>}
        {!isPending && !isError && !shots.length && (
          <p className="text-[12px] text-ash">这张卡还没打上镜号标签，去「定位工单」写 SH 号再回来。</p>
        )}
        {shots.map((s) => (
          <div key={s.index} className="border-b border-rule-soft py-2 last:border-b-0">
            <div className="flex items-baseline gap-2.5">
              <span className="font-mono text-[11.5px] text-paper">
                SH{seg}
                {String(s.index).padStart(2, "0")}
              </span>
              <span className="font-mono text-[11px] text-ash-dim">@{s.at_sec.toFixed(3)}s</span>
            </div>
            <p className="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-ash">{s.head}</p>
          </div>
        ))}
      </div>
      <p className="text-[11.5px] text-ash-dim">
        要改这里的描述，去「定位工单」写镜号，生成工单交给外部 Agent。
      </p>
    </div>
  )
}
