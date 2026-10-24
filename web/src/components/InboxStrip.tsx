import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { ChevronDown, ChevronLeft, ChevronRight, Inbox, RefreshCw } from "lucide-react"
import { cn } from "cn"
import { api } from "@/lib/api"
import { FilmCell } from "@/components/FilmCell"
import { ASSETS_KEY_ROOT } from "@/lib/assetsApi"
import type { Card, CardsResp, IntakeStats } from "@/lib/types"

/**
 * 收件箱：草稿里**还没定性**的片子，横排在资产页最上面。
 *
 * 为什么放在资产页而不是抽卡台：抽卡台是「按段找块」的工作台，看的是某一段齐没齐；
 * 这里是「这一批新东西要不要」的入口，看的是时间倒序的一堆散片。两个问题不一样。
 *
 * 🔴 不新建后端接口。`/api/cards?status=draft` + `to-final` + `PATCH status` 已经够用，
 * 再包一层 `/api/inbox` 只会多一份需要同步的真相。列表接口本身带节流读盘
 * （`cardwatch.ensure_fresh`），所以 5s 轮一次也不会变成反复扫盘。
 *
 * 两个动作（和画布稿一致）：
 * - 通过 → `to-final`：交付一份到 `素材/未分类/`，原名留草稿；名字由后端按模板现算
 * - 废稿 → `status=reject`：从这里消失，但**不删文件**，抽卡台按状态筛还能找回来
 *
 * 🔴 「通过」的按钮本身住在 `FilmCell` 里（右上角 `onPass`），这里只传回调，
 * 不另画一份 —— 同一个动作有两个长相，往后必然有一处忘了改。
 */

/** 最多横排几条，剩下的给一句「还有 N 条」——这里不是用来翻完全部草稿的 */
const MAX_SHOWN = 12

export function InboxStrip({ workspace }: { workspace: string | null }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(true)
  const rowRef = useRef<HTMLDivElement>(null)
  /* 带子两端是否还有内容：只在能滚的时候画箭头，不留两个永远灰着的钮 */
  const [edge, setEdge] = useState({ left: false, right: false })

  const cardsQ = useQuery({
    queryKey: ["inbox", "cards", workspace],
    queryFn: () => api.get<CardsResp>("/api/cards?status=draft"),
    enabled: !!workspace,
    // 一更新就同步：后台只比对文件指纹，没新片零写入，所以这条轮询几乎不花钱
    refetchInterval: 5000,
  })

  const dirsQ = useQuery({
    queryKey: ["inbox", "watch-dirs", workspace],
    queryFn: () =>
      api.get<{ name: string; watch_dirs: string[] }>(
        `/api/workspaces/${encodeURIComponent(workspace!)}/watch-dirs`
      ),
    enabled: !!workspace,
  })

  /** 通过 / 废稿都会让「卡池」和「资产索引」两侧都变，别只刷一边 */
  function refreshBoth() {
    qc.invalidateQueries({ queryKey: ["inbox", "cards"] })
    qc.invalidateQueries({ queryKey: ["cards"] })
    qc.invalidateQueries({ queryKey: [ASSETS_KEY_ROOT[0]] })
  }

  const pass = useMutation({
    mutationFn: (id: number) =>
      api.post<{ card: Card; copied_to: string | null; missing_chunk?: boolean }>(
        `/api/cards/${id}/to-final`,
        { copy_to_final: true }
      ),
    onSuccess: (r) => {
      refreshBoth()
      const name = r.copied_to?.split(/[\\/]/).pop()
      toast.success(name ? `已通过 → 素材/未分类/${name}` : "已标为正片", {
        description: r.missing_chunk
          ? "缺块号，只写了段号；可在资产页补"
          : name
            ? "原件留在 草稿/"
            : undefined,
      })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const reject = useMutation({
    mutationFn: (id: number) =>
      api.patch<Card>(`/api/cards/${id}`, {
        status: "reject",
        reject_note: "收件箱里直接废稿",
      }),
    onSuccess: () => {
      refreshBoth()
      toast.success("已标废稿", { description: "文件没删，抽卡台按状态筛还能找回来" })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const intake = useMutation({
    mutationFn: () =>
      api.post<IntakeStats>(`/api/workspaces/${encodeURIComponent(workspace!)}/intake`),
    onSuccess: (r) => {
      refreshBoth()
      const bits = [`扫到 ${r.scanned}`]
      if (r.copied) bits.push(`收编 ${r.copied}`)
      if (r.unclaimed) bits.push(`未认领 ${r.unclaimed}`)
      toast.success(r.copied ? "收编完成" : "没有新文件", {
        description: r.problems.length ? `${bits.join(" · ")}；${r.problems[0]}` : bits.join(" · "),
      })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const cards = cardsQ.data?.cards ?? []
  const shown = cards.slice(0, MAX_SHOWN)
  const rest = cards.length - shown.length
  const dirs = dirsQ.data?.watch_dirs ?? []
  const err = cardsQ.error as Error | null

  /* 🔴 竖向滚轮 → 横向滚动。
     这条带子只有一行，用户不会想「上下」滚它；而原生 overflow-x 只吃横向滚轮
     （或 Shift+滚轮），鼠标停在这排卡片上滚一下什么都不动 —— 这就是「收件箱
     无法滚动」的真正原因。React 的 onWheel 是 passive 的、preventDefault 无效，
     所以必须挂原生监听。 */
  useEffect(() => {
    const el = rowRef.current
    if (!el) return
    const sync = () =>
      setEdge({
        left: el.scrollLeft > 4,
        right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4,
      })
    const onWheel = (e: WheelEvent) => {
      if (el.scrollWidth <= el.clientWidth + 1) return
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return
      e.preventDefault()
      el.scrollLeft += e.deltaY
    }
    sync()
    el.addEventListener("wheel", onWheel, { passive: false })
    el.addEventListener("scroll", sync, { passive: true })
    window.addEventListener("resize", sync)
    return () => {
      el.removeEventListener("wheel", onWheel)
      el.removeEventListener("scroll", sync)
      window.removeEventListener("resize", sync)
    }
    // 条数变化（新片进来 / 收起再展开）之后要重新算两端
  }, [open, shown.length, cards.length])

  function nudge(dir: -1 | 1) {
    const el = rowRef.current
    if (!el) return
    el.scrollBy({ left: dir * Math.round(el.clientWidth * 0.8), behavior: "smooth" })
  }

  return (
    <section className="flex-none border-b border-rule-soft bg-[#1b1b1b]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-6 py-1.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex items-center gap-1.5 rounded-ctl px-1 py-0.5 text-ash transition-colors hover:bg-hi hover:text-paper"
        >
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          <Inbox className="size-3.5" />
          <span className="text-[12.5px] font-medium">收件箱</span>
          <span className="gb-chip" data-on={cards.length > 0 ? "1" : "0"} title="草稿里还没定性的条数">
            {cards.length}
          </span>
        </button>

        <span className="text-[11px] text-ash-dim">
          草稿里还没定性的片子 · 出现就自动排到这里，不用刷新
        </span>

        <span className="flex-1" />

        <span className="font-mono text-[10.5px] text-ash-dim">
          {dirsQ.isPending ? "采集源…" : dirs.length ? `${dirs.length} 个采集源` : "未配置采集源"}
        </span>
        <button
          type="button"
          disabled={!workspace || !dirs.length || intake.isPending}
          onClick={() => intake.mutate()}
          title={
            dirs.length
              ? "立即把监听目录里的新文件收编进 草稿/"
              : "先在 设置 › 采集源 里添加平台文件夹"
          }
          className="inline-flex h-6 items-center gap-1 rounded-ctl border border-rule px-2 text-[11px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:cursor-not-allowed disabled:opacity-40"
        >
          <RefreshCw className={cn("size-3", intake.isPending && "animate-spin")} />
          {intake.isPending ? "收编中" : "收编一次"}
        </button>
      </div>

      {open && (
        <div className="pb-2">
          {cardsQ.isPending ? (
            <div className="flex gap-3 px-6">
              {[0, 1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-[124px] w-[232px] flex-none animate-pulse rounded-cell bg-hi"
                />
              ))}
            </div>
          ) : err ? (
            <div className="flex items-center gap-3 px-6">
              <p className="text-[11.5px] text-ash">收件箱读不出来：{err.message}</p>
              <button
                type="button"
                onClick={() => cardsQ.refetch()}
                className="rounded-ctl border border-rule px-2 py-0.5 text-[11px] text-ash hover:bg-hi hover:text-paper"
              >
                重试
              </button>
            </div>
          ) : cards.length === 0 ? (
            <p className="px-6 text-[11.5px] text-ash-dim">
              收件箱空了。草稿里一出现新片子就会自己排到这里。
            </p>
          ) : (
            <div className="relative">
              <div ref={rowRef} className="flex gap-2 overflow-x-auto px-6 pb-1">
              {shown.map((c) => {
                const busy = pass.isPending || reject.isPending
                return (
                  <div key={c.id} className="group relative w-[148px] flex-none">
                    {/* 「通过」不在这里再画一份：FilmCell 右上角已经有一个（onPass），
                        全站一个入口就够了。这里只补它没有的「废稿」，叠在它正下方。 */}
                    <FilmCell
                      card={c}
                      onPass={c.missing ? undefined : () => pass.mutate(c.id)}
                      passPending={pass.isPending}
                    />
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => reject.mutate(c.id)}
                      title="废稿 → 从这里消失，文件不删"
                      className={cn(
                        "absolute right-[5px] top-[30px] z-[3] h-[21px] rounded-[4px] border border-white/30 bg-black/45 px-[6px]",
                        "font-mono text-[10.5px] leading-none tracking-[0.06em] text-white/80",
                        "transition-[background-color,color,border-color,opacity] duration-100",
                        "hover:border-white hover:bg-white hover:text-black",
                        "disabled:cursor-not-allowed disabled:opacity-40",
                        "opacity-0 focus-visible:opacity-100 group-hover:opacity-100"
                      )}
                    >
                      废稿
                    </button>
                  </div>
                )
              })}

              {rest > 0 && (
                <div className="grid w-[148px] flex-none place-items-center rounded-cell border border-dashed border-rule text-center">
                  <span className="px-3 text-[11px] leading-relaxed text-ash-dim">
                    还有 {rest} 条
                  </span>
                </div>
              )}
              </div>

              {/* 能滚才画箭头：带子只有一行，光靠滚轮不够明显，尤其触控板用户 */}
              {edge.left && (
                <button
                  type="button"
                  aria-label="向左滚"
                  onClick={() => nudge(-1)}
                  className="absolute left-1.5 top-1/2 z-10 grid size-6 -translate-y-1/2 place-items-center rounded-full border border-white/30 bg-black/70 text-white/80 transition-colors hover:border-white hover:bg-white hover:text-black"
                >
                  <ChevronLeft className="size-3.5" strokeWidth={2} />
                </button>
              )}
              {edge.right && (
                <button
                  type="button"
                  aria-label="向右滚"
                  onClick={() => nudge(1)}
                  className="absolute right-1.5 top-1/2 z-10 grid size-6 -translate-y-1/2 place-items-center rounded-full border border-white/30 bg-black/70 text-white/80 transition-colors hover:border-white hover:bg-white hover:text-black"
                >
                  <ChevronRight className="size-3.5" strokeWidth={2} />
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
