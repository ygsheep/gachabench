import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDown, Loader2, Square } from "lucide-react"
import { toast } from "sonner"
import { cn } from "cn"
import { api } from "@/lib/api"
import { shortStamp } from "@/lib/format"
import type { Job, JobsResp, JobStatus } from "@/lib/types"

const JOB_LABEL: Record<JobStatus, string> = {
  queued: "排队中",
  running: "生成中",
  done: "已完成",
  error: "失败",
  stopped: "已停止",
}

const LIVE: JobStatus[] = ["queued", "running"]

/** 跑完之后气泡再留一会儿，让「刚做完」这件事有个收尾，而不是啪一下消失 */
const LINGER_MS = 8000
/** 删除是会丢记录的动作，两步确认；超时就自动收回，避免误点 */
const ARM_MS = 4000

function isLive(s: JobStatus) {
  return LIVE.includes(s)
}

function lastLine(s: string | null): string {
  if (!s) return ""
  const lines = s.trimEnd().split("\n")
  return lines[lines.length - 1] ?? ""
}

export function TaskBar() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [linger, setLinger] = useState(false)
  const [armed, setArmed] = useState<number | null>(null)
  const prevLive = useRef(0)
  /* 亲眼见过「在跑」的任务 id；只认这些，避免刚打开页面时的一堆历史终态乱刷 */
  const seenLive = useRef<Set<number>>(new Set())
  const settled = useRef<Set<number>>(new Set())

  const jobsQ = useQuery({
    queryKey: ["jobs", "bar"],
    queryFn: () => api.get<JobsResp>("/api/jobs?limit=30"),
    retry: 1,
    refetchInterval: (q) => {
      const list = (q.state.data as JobsResp | undefined)?.jobs ?? []
      return list.some((j) => isLive(j.status)) ? 2500 : 20000
    },
  })

  const jobs: Job[] = jobsQ.data?.jobs ?? []
  const live = jobs.filter((j) => isLive(j.status))

  useEffect(() => {
    if (live.length > 0) {
      prevLive.current = live.length
      setLinger(true)
      return
    }
    if (prevLive.current > 0) {
      prevLive.current = 0
      const t = window.setTimeout(() => setLinger(false), LINGER_MS)
      return () => window.clearTimeout(t)
    }
    setLinger(false)
  }, [live.length])

  useEffect(() => {
    if (armed === null) return
    const t = window.setTimeout(() => setArmed(null), ARM_MS)
    return () => window.clearTimeout(t)
  }, [armed])

  /* 任务从「在跑」落到终态 = 产物刚进 草稿/。此时让卡池重拉一次，
     否则「跑完了但草稿里找不到」很可能只是前端缓存没更新。 */
  const jobsKey = jobs.map((j) => `${j.id}:${j.status}`).join(",")
  useEffect(() => {
    let fresh = false
    for (const j of jobs) {
      if (isLive(j.status)) {
        seenLive.current.add(j.id)
        continue
      }
      if (seenLive.current.has(j.id) && !settled.current.has(j.id)) {
        settled.current.add(j.id)
        fresh = true
      }
    }
    if (fresh) qc.invalidateQueries({ queryKey: ["cards"] })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobsKey, qc])

  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false)
        setArmed(null)
      }
    }
    function onDown(e: MouseEvent) {
      const el = e.target as HTMLElement | null
      if (el?.closest?.("[data-taskbar]")) return
      setOpen(false)
      setArmed(null)
    }
    window.addEventListener("keydown", onKey)
    window.addEventListener("mousedown", onDown)
    return () => {
      window.removeEventListener("keydown", onKey)
      window.removeEventListener("mousedown", onDown)
    }
  }, [open])

  const stop = useMutation({
    mutationFn: (id: number) => api.post(`/api/jobs/${id}/stop`),
    onSuccess: (_r, id) => {
      qc.invalidateQueries({ queryKey: ["jobs"] })
      toast.success(`任务 #${id} 已停止`)
    },
    onError: (e: Error) => {
      qc.invalidateQueries({ queryKey: ["jobs"] })
      toast.error(e.message)
    },
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/api/jobs/${id}`),
    onSuccess: (_r, id) => {
      qc.invalidateQueries({ queryKey: ["jobs"] })
      setArmed(null)
      toast.success(`任务 #${id} 记录已删除`, { description: "产物文件不受影响。" })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const busy = stop.isPending || remove.isPending
  const visible = live.length > 0 || open || linger
  if (!visible) return null

  const top = live.find((j) => j.status === "running") ?? live[0]
  const running = live.filter((j) => j.status === "running").length
  const queued = live.length - running

  return (
    /* 全局顶部状态栏（用户定稿）：sticky 钉在视口顶 —— 未滚动时占位、滚动后常驻，
       任何页面滚动它都在。z-50：低于审片器（z-60）与全屏对话框（z-80）。 */
    <div data-taskbar className="sticky top-0 z-50 flex-none border-b border-rule-soft bg-panel">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(
          "flex h-10 w-full items-center gap-2.5 px-5 text-left text-[12.5px] transition-colors max-[980px]:px-4",
          live.length ? "text-paper" : "text-ash hover:text-paper"
        )}
      >
        {live.length ? (
          running > 0 ? (
            <Loader2 className="size-3.5 flex-none animate-spin text-paper" />
          ) : (
            <span className="size-2 flex-none rounded-full bg-paper" />
          )
        ) : (
          <Square className="size-3 flex-none text-ash-dim" />
        )}
        <span className="min-w-0 truncate">
          {live.length ? (
            <>
              {live.length} 个任务进行中
              <span className="text-ash-dim">
                {running > 0 && queued > 0 ? ` · 生成 ${running} · 排队 ${queued}` : ""}
              </span>
            </>
          ) : (
            "任务已完成"
          )}
        </span>
        <span className="flex-1" />
        {top?.status === "running" && top.progress != null && (
          <span className="font-mono text-paper">{top.progress}%</span>
        )}
        <ChevronDown className={cn("size-3.5 flex-none transition-transform", open && "rotate-180")} />
      </button>

      {/* 通栏细进度条贴在底边上 —— 状态栏该一眼看到整体推进，不用点开 */}
      {top?.status === "running" && top.progress != null && (
        <div className="absolute inset-x-0 bottom-[-1px] h-0.5">
          <div
            className="h-full bg-paper transition-[width] duration-500"
            style={{ width: `${Math.max(0, Math.min(100, top.progress))}%` }}
          />
        </div>
      )}

      {open && (
        <div className="absolute right-4 top-full z-50 mt-px w-[min(640px,92vw)] overflow-hidden rounded-panel border border-rule bg-panel shadow-2xl shadow-black/60">
          <div className="flex items-center gap-2.5 border-b border-rule-soft px-4 py-2.5">
            <h3 className="text-[13px] font-medium">任务</h3>
            <span className="font-mono text-[11px] text-ash-dim">
              {live.length} 进行中 · 共 {jobs.length} 条
            </span>
            <span className="flex-1" />
            <button
              type="button"
              onClick={() => jobsQ.refetch()}
              className="text-[11.5px] text-ash-dim transition-colors hover:text-paper"
            >
              刷新
            </button>
          </div>

          <div className="max-h-[62svh] overflow-auto p-3">
            {jobsQ.isPending && <p className="px-1 py-2 text-[12.5px] text-ash">读任务列表…</p>}
            {jobsQ.isError && (
              <p className="px-1 py-2 text-[12.5px] text-ash">
                任务列表读不出来：{(jobsQ.error as Error).message}
              </p>
            )}
            {!jobsQ.isPending && !jobs.length && (
              <p className="px-1 py-3 text-[12.5px] text-ash-dim">
                还没有任务。到「拆分台」挑一块跑片，这里会实时显示进度。
              </p>
            )}

            <div className="flex flex-col gap-2">
              {jobs.map((j) => {
                const liveOne = isLive(j.status)
                const pct = Math.max(0, Math.min(100, j.progress ?? 0))
                return (
                  <div
                    key={j.id}
                    className={cn(
                      "flex flex-col gap-2 rounded-ctl border px-3 py-2.5",
                      liveOne ? "border-rule bg-cell" : "border-rule-soft bg-cell/60"
                    )}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[12px] text-paper">#{j.id}</span>
                      <span className="font-mono text-[12px] text-ash">
                        {j.seg ?? "—"} · {j.chunk ?? "—"}
                      </span>
                      {/* 用 .gb-chip（内联、占宽度、跟着滚），不要 .gb-pill —— 那是绝对定位的贴片 */}
                      <span className="gb-chip" data-on={liveOne ? "1" : "0"}>
                        {JOB_LABEL[j.status] ?? j.status}
                      </span>
                      <span className="flex-1" />

                      {liveOne && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => stop.mutate(j.id)}
                          title="打断 ComfyUI 执行并停止这个任务"
                          className="inline-flex h-7 items-center gap-1.5 rounded-ctl border border-rule bg-panel px-2.5 text-[12px] font-medium text-ash transition-all hover:bg-hi hover:text-paper active:translate-y-px disabled:opacity-45"
                        >
                          <Square className="size-3" />
                          停止
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          if (armed === j.id) remove.mutate(j.id)
                          else setArmed(j.id)
                        }}
                        title={
                          liveOne
                            ? "先停止再删掉这条记录（产物文件不删）"
                            : "删掉这条记录（产物文件不删）"
                        }
                        className={cn(
                          "inline-flex h-7 items-center rounded-ctl border px-2.5 text-[12px] font-medium transition-all active:translate-y-px disabled:opacity-45",
                          armed === j.id
                            ? "border-dashed border-[#8a8a8a] bg-wash text-paper"
                            : "border-rule bg-panel text-ash hover:bg-hi hover:text-paper"
                        )}
                      >
                        {armed === j.id ? "确认删除" : "删除"}
                      </button>
                    </div>

                    {j.stage && (
                      <p className="text-[11.5px] text-ash">{j.stage}</p>
                    )}

                    <div className="flex items-center gap-2.5">
                      <div className="h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-hi">
                        <div
                          className={cn(
                            "h-full transition-[width] duration-500",
                            j.status === "error" ? "bg-[#8a8a8a]" : "bg-paper"
                          )}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className="shrink-0 font-mono text-[10.5px] text-ash-dim">{pct}%</span>
                      <span className="shrink-0 font-mono text-[10.5px] text-ash-dim">
                        {shortStamp(j.updated_at)}
                      </span>
                    </div>

                    {!!j.log_tail && (
                      <p
                        className="line-clamp-2 break-all font-mono text-[10.5px] leading-relaxed text-ash-dim"
                        title={j.log_tail}
                      >
                        {lastLine(j.log_tail)}
                      </p>
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          <p className="border-t border-rule-soft px-4 py-2 text-[11px] text-ash-dim">
            停止 = 打断这一条的 ComfyUI 执行（不影响别的任务）；删除 = 只清记录，产物文件留在工作区里。
          </p>
        </div>
      )}
    </div>
  )
}
