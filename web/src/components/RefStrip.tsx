import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { ImageOff, Play, Repeat, Search, Square, Upload, X } from "lucide-react"
import { toast } from "sonner"
import { cn } from "cn"
import { api } from "@/lib/api"
import { humanSize } from "@/lib/format"
import type {
  AssetItem,
  AssetsResp,
  PreviewAudioItem,
  PreviewPicItem,
  RefSelectResp,
  RefState,
} from "@/lib/types"

/**
 * 参考条：把「跑片要用到的图 / 音频」画成一排格子。
 *
 * 判据不是「input 目录里有没有」，而是「跑片时能不能拿到」：
 *   已就位   → input 目录里有
 *   自动送入 → 资产库匹配到了，跑片时后端自己送进去，用户不用点任何按钮
 *   缺       → 两边都没有，只有这种情况才需要人动手
 */

function stateOf(x: {
  exists: boolean
  asset?: { found: boolean } | null
  state?: RefState
}): RefState {
  if (x.state) return x.state
  if (x.exists) return "ready"
  return x.asset?.found ? "auto" : "missing"
}

function mmss(s: number): string {
  if (!Number.isFinite(s) || s < 0) return "0:00"
  const m = Math.floor(s / 60)
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`
}

const STATE_LABEL: Record<RefState, string> = {
  ready: "已就位",
  auto: "库内可补齐",
  missing: "两边都没有",
}

export interface PickerSlot {
  n: number
  kind: "image" | "audio"
  expected: string
}

export function RefStrip({
  planId,
  chunk,
  pics,
  auds,
  contiguous = true,
  onChanged,
}: {
  planId: number
  chunk: string
  pics: PreviewPicItem[]
  auds: PreviewAudioItem[]
  /** <Audio N> 是否从 1 起连续（不连续跑片会被后端拦下，见下方说明） */
  contiguous?: boolean
  onChanged: () => void
}) {
  const [slot, setSlot] = useState<PickerSlot | null>(null)

  const send = useMutation({
    mutationFn: (items: { n: number; kind: string; source_rel: string | null }[]) =>
      api.post<RefSelectResp>(`/api/split/${planId}/refs/select`, { chunk, items }),
    onSuccess: (r) => {
      const ok = r.items.filter((i) => i.copied)
      const bad = r.items.filter((i) => !i.copied)
      if (ok.length) {
        toast.success(`已送入 ComfyUI input：${ok.map((i) => i.filename).join("、")}`, {
          description: "只放在 input 目录，没写进资产库。",
        })
      }
      if (bad.length) {
        toast.error(bad.map((i) => `${i.filename}：${i.reason ?? "失败"}`).join("；"))
      }
      onChanged()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const upload = useMutation({
    mutationFn: ({ n, kind, file }: { n: number; kind: string; file: File }) => {
      const fd = new FormData()
      fd.append("chunk", chunk)
      fd.append("n", String(n))
      fd.append("kind", kind)
      fd.append("file", file)
      return api.upload<RefSelectResp>(`/api/split/${planId}/refs/upload`, fd)
    },
    onSuccess: (r) => {
      const it = r.items[0]
      if (it?.copied) {
        toast.success(`已上传 → input/${it.filename}`, {
          description: "只放 ComfyUI input，没写进资产库。",
        })
      } else {
        toast.error("上传没成功")
      }
      onChanged()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const autoItems = [
    ...pics.filter((p) => stateOf(p) === "auto").map((p) => ({ n: p.n, kind: "image", source_rel: null })),
    ...auds.filter((a) => stateOf(a) === "auto").map((a) => ({ n: a.n, kind: "audio", source_rel: null })),
  ]
  const missCount = [...pics, ...auds].filter((x) => stateOf(x) === "missing").length

  return (
    <div className="mt-2.5 w-full">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-[11.5px] text-ash">参考</span>
        <span className="font-mono text-[11px] text-ash-dim">
          {pics.length} 图{auds.length ? ` · ${auds.length} 音` : ""}
        </span>
        {autoItems.length > 0 && (
          <span className="gb-chip" data-on="0">
            {autoItems.length} 项自动送入
          </span>
        )}
        {/* 音频不只是「送进 input」—— 它会被接到工作流的 ref_audio 槽上，是硬接线 */}
        {auds.length > 0 && (
          <span className="gb-chip" data-on="1" title="跑片时接到 <Audio N> 对应的 ref_audio 槽">
            {auds.length} 段音频接入工作流
          </span>
        )}
        {missCount > 0 && (
          <span className="gb-chip" data-on="0">
            {missCount} 项待补
          </span>
        )}
        <span className="flex-1" />
        {autoItems.length > 0 && (
          <button
            type="button"
            disabled={send.isPending}
            onClick={() => send.mutate(autoItems)}
            title="现在就送进 ComfyUI input（不点也行，跑片时也会自动做）"
            className="inline-flex h-7 items-center rounded-ctl border border-rule bg-panel px-2.5 text-[11.5px] text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px disabled:opacity-45"
          >
            现在送入 {autoItems.length} 项
          </button>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {pics.map((p) => (
          <PicCell
            key={`p${p.n}`}
            item={p}
            onPick={() => setSlot({ n: p.n, kind: "image", expected: p.filename })}
          />
        ))}
        {auds.map((a) => (
          <AudioCell
            key={`a${a.n}`}
            item={a}
            onPick={() => setSlot({ n: a.n, kind: "audio", expected: a.filename ?? "?" })}
          />
        ))}
      </div>

      {!contiguous && auds.length > 0 && (
        <p className="mt-2 text-[11.5px] leading-relaxed text-ash">
          <span className="font-mono">&lt;Audio N&gt;</span> 编号不连续（
          <span className="font-mono">{auds.map((a) => a.n).join("、")}</span>
          ）—— 参考音频是<span className="text-paper">按位置</span>接线的（
          <span className="font-mono">Audio N</span> →
          <span className="font-mono"> ref_audio_{"{N-1}"}</span>
          ），跳号会把音色接给错的人，所以跑片会被拦下。改成从 1 连续编号再跑。
        </p>
      )}

      {missCount > 0 && (
        <p className="mt-2 text-[11.5px] leading-relaxed text-ash">
          {missCount} 项在 ComfyUI input 和资产库里都没找到。点对应格子从资产库换一个，
          或直接上传 —— 只是这一块跑片要用，不会写进资产库。
        </p>
      )}

      {slot && (
        <RefPickerDialog
          slot={slot}
          onClose={() => setSlot(null)}
          onPick={(it) => {
            const cur = slot
            setSlot(null)
            send.mutate([{ n: cur.n, kind: cur.kind, source_rel: it.rel }])
          }}
          onUpload={(file) => {
            const cur = slot
            setSlot(null)
            upload.mutate({ n: cur.n, kind: cur.kind, file })
          }}
        />
      )}
    </div>
  )
}

/** 图片格：缩略图 + <Picture N> 贴片 + 文件名 + 状态 */
function PicCell({ item, onPick }: { item: PreviewPicItem; onPick: () => void }) {
  const st = stateOf(item)
  const fuzzy = !!item.asset?.fuzzy
  const matched = item.asset?.name && item.asset.name !== item.filename ? item.asset.name : null
  return (
    <button
      type="button"
      onClick={onPick}
      title={
        `${item.filename}\n` +
        (st === "ready"
          ? "已在 ComfyUI input 目录"
          : st === "auto"
            ? `资产库匹配到 ${matched}，跑片时自动送入`
            : "ComfyUI input 和资产库里都没有") +
        "\n点击可换一张"
      }
      className={cn(
        "w-[126px] flex-none overflow-hidden rounded-cell border bg-cell p-1 text-left transition-all hover:-translate-y-0.5",
        st === "ready"
          ? "border-rule hover:border-wash-line"
          : "border-dashed border-[#6e6e6e] hover:border-wash-line"
      )}
    >
      <span className="relative block aspect-[4/3] overflow-hidden rounded-[3px] bg-black">
        {item.url ? (
          <img src={item.url} alt="" loading="lazy" className="h-full w-full object-cover" />
        ) : (
          <span className="grid h-full w-full place-items-center text-ash-dim">
            <ImageOff className="size-4" />
          </span>
        )}
        <span className="gb-pill left-1.5 top-1.5" data-on={st === "ready" ? "1" : "0"}>
          P{item.n}
        </span>
        {/* 图上只留一个两字标记，别把缩略图糊住；完整说法在下面那行 */}
        {st !== "ready" && (
          <span className="gb-pill bottom-1.5 left-1.5" data-on="0">
            {st === "auto" ? "自动" : "缺图"}
          </span>
        )}
      </span>
      <span className="mt-1 block truncate font-mono text-[10.5px] text-ash" title={item.filename}>
        {item.filename}
      </span>
      <span
        className={cn("block font-mono text-[10px]", st === "missing" ? "text-ash" : "text-ash-dim")}
      >
        {st === "auto" && fuzzy ? "库内近似" : STATE_LABEL[st]}
      </span>
    </button>
  )
}

/** 音频格：试听 + 声部块（等宽竖条是材质，点亮的是真实播放进度）+ 文件名 + 状态 */
function AudioCell({ item, onPick }: { item: PreviewAudioItem; onPick: () => void }) {
  const st = stateOf(item)
  const ref = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState(false)
  const [cur, setCur] = useState(0)
  const [dur, setDur] = useState(0)

  useEffect(() => {
    setPlaying(false)
    setCur(0)
    setDur(0)
  }, [item.url])

  const pct = dur > 0 ? Math.min(100, (cur / dur) * 100) : 0

  function toggle() {
    const a = ref.current
    if (!a) return
    if (a.paused) {
      void a.play()
      setPlaying(true)
    } else {
      a.pause()
      setPlaying(false)
    }
  }

  function seek(e: React.MouseEvent<HTMLDivElement>) {
    const a = ref.current
    if (!a || !dur) return
    const r = e.currentTarget.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width))
    a.currentTime = ratio * dur
    setCur(a.currentTime)
  }

  return (
    <div
      className={cn(
        // flex-col：让声部块吃掉拉伸出来的剩余高度，跟左侧图格对齐（flex 行默认 stretch）
        "flex w-[244px] flex-none flex-col rounded-cell border bg-cell p-2",
        st === "ready" ? "border-rule" : "border-dashed border-[#6e6e6e]"
      )}
    >
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={toggle}
          disabled={!item.url}
          aria-label={playing ? "暂停" : "试听"}
          className="grid size-7 flex-none place-items-center rounded-ctl border border-rule bg-panel text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px disabled:opacity-40"
        >
          {playing ? <Square className="size-3" /> : <Play className="size-3" />}
        </button>
        <span className="gb-chip" data-on={st === "ready" ? "1" : "0"}>
          A{item.n}
        </span>
        <button
          type="button"
          onClick={onPick}
          title={
            `${item.filename ?? "源 md 里没有这一项"}\n` +
            `跑片时接到 ref_audio_${item.slot ?? item.n - 1}，作为 <Audio ${item.n}> 的音色参考\n` +
            "点击可换一段"
          }
          className="min-w-0 flex-1 truncate text-left font-mono text-[10.5px] text-ash transition-colors hover:text-paper"
        >
          {item.filename ?? "（源 md 里没有音频表）"}
        </button>
        <button
          type="button"
          onClick={onPick}
          aria-label="换一段音频"
          className="flex-none text-ash-dim transition-colors hover:text-paper"
        >
          <Repeat className="size-3.5" />
        </button>
      </div>

      {/* 声部块：等宽竖条 = 材质；点亮的那段 = 真实播放进度 */}
      <div
        onClick={seek}
        title={dur ? `${mmss(cur)} / ${mmss(dur)}` : "选中音频后点左边的播放键试听"}
        className="my-auto h-[18px] w-full cursor-pointer rounded-[1px]"
        style={{
          backgroundImage:
            "repeating-linear-gradient(90deg, var(--gb-paper) 0 2px, transparent 2px 5px)," +
            "repeating-linear-gradient(90deg, var(--gb-rule) 0 2px, transparent 2px 5px)",
          backgroundSize: `${pct}% 100%, 100% 100%`,
          backgroundRepeat: "no-repeat, no-repeat",
        }}
      />

      <div className="mt-1 flex items-center gap-2">
        <span className="font-mono text-[10px] text-ash-dim">
          {dur ? `${mmss(cur)} / ${mmss(dur)}` : "—"}
        </span>
        <span className="flex-1" />
        {/* 接线位置写出来：音频按位置接，槽位和编号对不上就是接错人 */}
        <span
          className="font-mono text-[10px] text-ash-dim"
          title="跑片时这一条接到的节点槽位"
        >
          ref_audio_{item.slot ?? item.n - 1}
        </span>
        <span
          className={cn("font-mono text-[10px]", st === "missing" ? "text-ash" : "text-ash-dim")}
        >
          {STATE_LABEL[st]}
        </span>
      </div>

      {item.url && (
        <audio
          ref={ref}
          src={item.url}
          preload="metadata"
          onLoadedMetadata={(e) => setDur(e.currentTarget.duration || 0)}
          onTimeUpdate={(e) => setCur(e.currentTarget.currentTime)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => {
            setPlaying(false)
            setCur(0)
          }}
          className="hidden"
        />
      )}
    </div>
  )
}

/** 换一张 / 换一段：从资产库里挑，选中即送进 ComfyUI input（覆盖期望的那个文件名） */
function RefPickerDialog({
  slot,
  onClose,
  onPick,
  onUpload,
}: {
  slot: PickerSlot
  onClose: () => void
  onPick: (item: AssetItem) => void
  onUpload: (file: File) => void
}) {
  const [kw, setKw] = useState("")
  const assetsQ = useQuery({
    queryKey: ["assets"],
    queryFn: () => api.get<AssetsResp>("/api/assets"),
    retry: 1,
  })

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const flat = useMemo(() => {
    const out: (AssetItem & { group: string; cat: string })[] = []
    for (const c of assetsQ.data?.categories ?? []) {
      for (const g of c.groups) {
        for (const it of g.items) {
          if (it.kind !== slot.kind) continue
          out.push({ ...it, group: g.name, cat: c.label })
        }
      }
    }
    return out
  }, [assetsQ.data, slot.kind])

  const shown = useMemo(() => {
    const needle = kw.trim().toLowerCase()
    return needle
      ? flat.filter((it) => `${it.group} ${it.name}`.toLowerCase().includes(needle))
      : flat
  }, [flat, kw])

  const isImg = slot.kind === "image"

  return (
    <div
      className="fixed inset-0 z-80 grid place-items-center bg-black/70 p-6"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={isImg ? `为 P${slot.n} 换一张参考图` : `为 A${slot.n} 换一段音频`}
        className="flex max-h-[86svh] w-[min(760px,94vw)] flex-col rounded-panel border border-rule bg-panel shadow-2xl shadow-black/60"
      >
        <div className="flex flex-wrap items-center gap-2.5 border-b border-rule-soft px-4 py-3">
          <h3 className="text-[13.5px] font-medium">
            为 {isImg ? "P" : "A"}
            {slot.n} 换{isImg ? "一张" : "一段"}
          </h3>
          <span className="min-w-0 truncate font-mono text-[11px] text-ash-dim">
            目标名 {slot.expected}
          </span>
          <span className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="grid size-7 place-items-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper"
          >
            <X className="size-3.5" />
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2.5 px-4 py-2.5">
          <div className="relative w-[240px]">
            <Search className="pointer-events-none absolute left-2.5 top-2 size-3.5 text-ash-dim" />
            <input
              value={kw}
              onChange={(e) => setKw(e.target.value)}
              autoFocus
              placeholder="主体名 / 文件名"
              className="h-8 w-full rounded-ctl border border-rule bg-cell pl-7 pr-2.5 text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
            />
          </div>
          <span className="font-mono text-[11px] text-ash-dim">{shown.length} 项</span>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-4 pb-2">
          {assetsQ.isPending && <p className="py-6 text-center text-[12px] text-ash">读取资产库…</p>}
          {assetsQ.isError && (
            <p className="py-6 text-center text-[12px] text-ash">
              资产库读不出来：{(assetsQ.error as Error).message}
            </p>
          )}
          {!assetsQ.isPending && !flat.length && (
            <p className="py-6 text-center text-[12px] text-ash-dim">
              资产库里还没有{isImg ? "图片" : "音频"}。到「资产管理」传一个再回来。
            </p>
          )}

          {isImg ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-2.5">
              {shown.map((it) => (
                <button
                  key={it.rel}
                  type="button"
                  onClick={() => onPick(it)}
                  title={`${it.name}\n${it.group} · ${it.dir || "根目录"}`}
                  className="overflow-hidden rounded-cell border border-rule-soft bg-cell p-1 text-left transition-all hover:-translate-y-0.5 hover:border-wash-line"
                >
                  <span className="block aspect-[4/3] overflow-hidden rounded-[3px] bg-black">
                    <img src={it.url} alt="" loading="lazy" className="h-full w-full object-cover" />
                  </span>
                  <span className="mt-1 block truncate font-mono text-[10.5px] text-ash">
                    {it.name}
                  </span>
                  <span className="block truncate text-[10px] text-ash-dim">{it.group}</span>
                </button>
              ))}
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              {shown.map((it) => (
                <button
                  key={it.rel}
                  type="button"
                  onClick={() => onPick(it)}
                  className="flex items-center gap-2.5 rounded-ctl border border-rule-soft bg-cell px-2.5 py-2 text-left transition-colors hover:border-wash-line"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-ash">
                    {it.name}
                  </span>
                  <span className="flex-none text-[10.5px] text-ash-dim">{it.group}</span>
                  <span className="flex-none font-mono text-[10.5px] text-ash-dim">
                    {humanSize(it.size)}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t border-rule-soft px-4 py-2.5">
          <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-ash-dim">
            选中的文件会以
            <span className="font-mono text-ash"> {slot.expected} </span>
            这个名字复制进 ComfyUI input 目录（同名会被覆盖），只供这一块跑片用，不写进资产库。
          </p>
          <label className="inline-flex h-8 flex-none cursor-pointer items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12px] text-ash transition-colors hover:bg-hi hover:text-paper">
            <Upload className="size-3.5" />
            直接上传
            <input
              type="file"
              accept={isImg ? ".png,.jpg,.jpeg,.webp" : ".mp3,.wav,.m4a,.aac,.flac,.ogg"}
              className="sr-only"
              onChange={(e) => {
                const f = e.target.files?.[0]
                e.currentTarget.value = ""
                if (f) onUpload(f)
              }}
            />
          </label>
        </div>
      </div>
    </div>
  )
}
