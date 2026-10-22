import { useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Check } from "lucide-react"
import { cn } from "cn"
import { api } from "@/lib/api"
import { seconds } from "@/lib/format"
import { loadPrefs, savePrefs, type RunParams } from "@/lib/prefs"
import { useApp } from "@/store/useApp"
import { RefStrip } from "@/components/RefStrip"
import { RunParamsBar, buildRunBody } from "@/components/RunParamsBar"
import type {
  Job,
  JobsResp,
  LocateResult,
  SegmentChunksResp,
  SegmentsResp,
  SegmentsShotsResp,
  SplitPreviewResp,
  SplitResult,
  WorkflowsResp,
} from "@/lib/types"

/*
 * 分块不再由前端自己算。
 * 以前前后端各写一份 groupShots，两边一旦有一处改动就会「显示 3 块、后端切出 4 块」。
 * 现在只认后端 /api/segments/{seg}/chunks 的实时结果，前端只负责把它画到时间轴上。
 */

export function SplitPanel({
  seg: routeSeg,
  onSegChange,
}: {
  /** 来自 /prompts/$seg；没有时用 store 里的上次段号 */
  seg?: string
  onSegChange?: (seg: string) => void
} = {}) {
  const storeSeg = useApp((s) => s.seg)
  const setStoreSeg = useApp((s) => s.setSeg)
  const seg = routeSeg ?? storeSeg
  const setSeg = (next: string) => {
    setStoreSeg(next)
    onSegChange?.(next)
  }
  const [strategy, setStrategy] = useState<"jia" | "yi">("yi")
  const [focus, setFocus] = useState<number | null>(null)
  const [planId, setPlanId] = useState<number | null>(null)
  const [runParams, setRunParamsState] = useState<RunParams>(() => loadPrefs().runParams)
  const [jobs, setJobs] = useState<Record<string, number>>({})
  const qc = useQueryClient()
  const settled = useRef<Set<number>>(new Set())

  const setRunParams = (next: RunParams) => {
    setRunParamsState(next)
    savePrefs({ runParams: next })
  }

  const segsQ = useQuery({
    queryKey: ["segments"],
    queryFn: () => api.get<SegmentsResp>("/api/segments"),
    retry: 1,
  })
  const segments = segsQ.data?.segments ?? []
  /* 全局 seg 可能是上个会话留下的脏值（卡池里的 004 / 00），不在列表里就退回第一段 */
  const cur = seg && segments.includes(seg) ? seg : (segments[0] ?? "")

  const shotsQ = useQuery({
    queryKey: ["shots", cur],
    queryFn: () => api.get<SegmentsShotsResp>(`/api/segments/${cur}/shots`),
    enabled: !!cur,
    retry: 1,
  })
  const rawShots = useMemo(() => shotsQ.data?.shots ?? [], [shotsQ.data])

  const allTags = rawShots
    .map((s) => `SH${Number(cur)}${String(s.index).padStart(2, "0")}`)
    .join(",")

  /* 镜表时长以 locate 为准（和拆分器同一份数据），拿不到才用 At 差兜底 */
  const locQ = useQuery({
    queryKey: ["locate-all", cur, allTags],
    queryFn: () =>
      api.get<LocateResult>(`/api/segments/${cur}/locate?tags=${encodeURIComponent(allTags)}`),
    enabled: !!cur && !!allTags,
    retry: 1,
  })

  const shots = useMemo(() => {
    const byIdx = new Map((locQ.data?.hits ?? []).map((h) => [h.index, h]))
    const sorted = [...rawShots].sort((a, b) => a.index - b.index)
    const est = (i: number): number => {
      const next = sorted[i + 1]
      if (next) return Math.max(0, next.at_sec - sorted[i].at_sec)
      const prev = sorted[i - 1]
      if (prev) return Math.max(0, sorted[i].at_sec - prev.at_sec)
      return 3
    }
    return sorted.map((s, i) => {
      const h = byIdx.get(s.index)
      const fromTable = h?.duration ? h.duration : 0
      return {
        index: s.index,
        dur: fromTable || est(i),
        fromTable: !!fromTable,
        framing: h?.framing ?? "",
        camera: h?.camera ?? "",
        head: s.head,
      }
    })
  }, [rawShots, locQ.data])

  /* 分块以服务端实时结果为准：改完源 md 刷新一下就变，不存在「显示新的、跑旧的」 */
  const chunksQ = useQuery({
    queryKey: ["chunks", cur, strategy],
    queryFn: () =>
      api.get<SegmentChunksResp>(`/api/segments/${cur}/chunks?strategy=${strategy}`),
    enabled: !!cur,
    retry: 1,
  })
  const chunks = useMemo(() => chunksQ.data?.chunks ?? [], [chunksQ.data])

  const model = useMemo(() => {
    if (!shots.length || !chunks.length) return null
    // 后端给的是镜号，时间轴用的是「第几个镜」，这里换算一次
    const pos = new Map(shots.map((s, i) => [s.index, i]))
    const groups = chunks.map((c) =>
      c.shots.map((n) => pos.get(n)).filter((i): i is number => i !== undefined)
    )
    const targets = chunks.map((c) => c.target_sec)
    return {
      groups,
      targets,
      srcTotal: chunksQ.data?.source_total_sec ?? shots.reduce((a, s) => a + s.dur, 0),
      outTotal: targets.reduce((a, b) => a + b, 0),
    }
  }, [shots, chunks, chunksQ.data?.source_total_sec])

  /* 工作流参数：与参数条共用同一份缓存（同一 queryKey 不会重复请求） */
  const wfQ = useQuery({
    queryKey: ["workflows"],
    queryFn: () => api.get<WorkflowsResp>("/api/workflows"),
    retry: 1,
    staleTime: 60_000,
  })
  const wfMeta = useMemo(() => {
    const list = wfQ.data?.workflows ?? []
    if (!list.length) return undefined
    return (
      (runParams.workflow
        ? list.find((w) => w.name === runParams.workflow || w.rel === runParams.workflow)
        : undefined) ??
      list.find((w) => w.name === wfQ.data?.default) ??
      list[0]
    )
  }, [wfQ.data, runParams.workflow])

  /* 计划只记「这段用这个策略」。块内容每次现算，所以换段/换策略时重建一次即可。
     失败不弹 toast —— 用户什么都没点，进页面就被弹窗打断很烦；错误写在卡片里。 */
  const [planErr, setPlanErr] = useState<string | null>(null)
  const ensure = useMutation({
    mutationFn: (s: "jia" | "yi") =>
      api.post<SplitResult>(`/api/segments/${cur}/split`, { strategy: s }),
    onSuccess: (r) => {
      setPlanId(r.plan_id)
      setPlanErr(null)
    },
    onError: (e: Error) => setPlanErr(e.message),
  })

  useEffect(() => {
    setPlanId(null)
    if (!cur) return
    ensure.mutate(strategy)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cur, strategy])

  const run = useMutation({
    mutationFn: async ({ chunk }: { chunk: string }) => {
      let pid = planId
      if (!pid) {
        // 进页面时没建成（比如 md 刚被改名），跑片前再试一次，别让用户点两次
        const r = await api.post<SplitResult>(`/api/segments/${cur}/split`, { strategy })
        pid = r.plan_id
        setPlanId(pid)
      }
      return api.post<{ job_id: number; status: string }>(`/api/split/${pid}/generate`, {
        chunk,
        ...buildRunBody(wfMeta, runParams),
      })
    },
    onSuccess: (r, v) => {
      setJobs((m) => ({ ...m, [v.chunk]: r.job_id }))
      // 让顶部任务气泡立刻出现，不必等它 20s 的闲时轮询
      qc.invalidateQueries({ queryKey: ["jobs"] })
      toast.success("已提交到 ComfyUI", { description: `块 ${v.chunk} · 任务 #${r.job_id}` })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const jobIds = Object.values(jobs)
  const jobsQ = useQuery({
    queryKey: ["jobs", jobIds.join(",")],
    queryFn: () => api.get<JobsResp>("/api/jobs?limit=30"),
    enabled: jobIds.length > 0,
    refetchInterval: (q) => {
      const list = (q.state.data as JobsResp | undefined)?.jobs ?? []
      const live = list.some(
        (j) => jobIds.includes(j.id) && (j.status === "queued" || j.status === "running")
      )
      return live ? 2500 : false
    },
  })
  const jobOf = (chunk: string): Job | undefined => {
    const id = jobs[chunk]
    if (!id) return undefined
    return jobsQ.data?.jobs.find((j) => j.id === id)
  }

  /* 任务落到终态 = 产物刚刚进 草稿/。此时必须让卡池重拉一次：
     不刷的话「跑完了但草稿里找不到」会被当成没入库（其实只是前端缓存太旧）。 */
  const terminalKey = (jobsQ.data?.jobs ?? [])
    .filter((j) => jobIds.includes(j.id) && (j.status === "done" || j.status === "error" || j.status === "stopped"))
    .map((j) => `${j.id}:${j.status}`)
    .join(",")
  useEffect(() => {
    if (!terminalKey) return
    let fresh = false
    for (const part of terminalKey.split(",")) {
      const id = Number(part.split(":")[0])
      if (!settled.current.has(id)) {
        settled.current.add(id)
        fresh = true
      }
    }
    if (fresh) qc.invalidateQueries({ queryKey: ["cards"] })
  }, [terminalKey, qc])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-end gap-x-3.5 gap-y-2 px-6 pb-3.5 pt-5">
        <div className="max-w-[82ch]">
          <h1 className="text-[19px] font-semibold leading-tight">拆分台</h1>
          <p className="pt-0.5 text-[12.5px] text-ash">
            15 秒一次出片容易崩尾巴。拆成 3 块跑，每块只盯一个镜组；切点只落在镜的自然边界。
          </p>
        </div>
        <span className="flex-1" />
        {segsQ.isPending && <span className="text-[11.5px] text-ash-dim">读段号…</span>}
      </header>

      <div className="flex flex-col gap-3.5 px-6 pb-11">
        <RunParamsBar value={runParams} onChange={setRunParams} />

        <Card title="选段" desc="段号来自提示词目录里的 NN_正片_第N段_15s.md，以及卡池里已经跑过的片段。">
          {!segments.length && (
            <p className="text-[12.5px] text-ash">没有扫到任何段。先确认工作区里的 提示词/ 有 md 文件。</p>
          )}
          <div className="flex flex-wrap gap-2">
            {segments.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setSeg(s)}
                aria-pressed={s === cur}
                className={cn(
                  "flex h-11 min-w-[58px] flex-col items-center justify-center rounded-ctl border transition-all active:scale-[0.96]",
                  s === cur
                    ? "border-wash-line bg-wash"
                    : "border-rule bg-cell hover:bg-hi"
                )}
              >
                <span className="font-mono text-[14px] font-medium">{s}</span>
                <span className={cn("text-[9.5px]", s === cur ? "text-paper" : "text-ash-dim")}>
                  段
                </span>
              </button>
            ))}
          </div>
        </Card>

        <Card
          title="切分策略"
          desc={
            strategy === "jia"
              ? "按镜组自然边界累计，每块取 ceil(组时长)。整镜不截断，尾巴多出来的时间自己处理。"
              : "每块固定 5.0s，段内各镜时长等比缩放。拼回去严丝合缝，代价是镜内节奏被轻微压缩。"
          }
        >
          <div className="inline-flex gap-2">
            {(
              [
                ["jia", "甲 · 镜组自然边界"],
                ["yi", "乙 · 定长 5s 等比缩放"],
              ] as ["jia" | "yi", string][]
            ).map(([k, label]) => (
              <button
                key={k}
                type="button"
                onClick={() => setStrategy(k)}
                aria-pressed={strategy === k}
                className={cn(
                  "h-8 rounded-ctl border px-3 text-[12.5px] transition-all active:translate-y-px",
                  strategy === k
                    ? "border-wash-line bg-wash font-medium text-paper"
                    : "border-rule bg-cell text-ash hover:bg-hi hover:text-paper"
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </Card>

        <Card title="切分预览" desc="上面是原片镜序，下面是切出来的块。悬停任一块，看它压住了哪几个镜。">
          {!model && (
            <p className="text-[12.5px] text-ash">
              {shotsQ.isPending ? "解析镜块…" : `段 ${cur || "—"} 的 §④ 里没解析到 [Shot N]。`}
            </p>
          )}
          {model && (
            <>
              <div className="gb-tlrow">
                <span className="w-[76px] flex-none text-right text-[11.5px] text-ash">原片</span>
                <span className="w-[150px] flex-none font-mono text-[11px] text-ash">
                  {seconds(model.srcTotal)} · {shots.length} 镜
                </span>
                <div className="gb-tlbar">
                  {shots.map((s, i) => {
                    const isCut = model.groups.some(
                      (g) => g[g.length - 1] === i && i < shots.length - 1
                    )
                    const on = focus == null || model.groups[focus]?.includes(i)
                    return (
                      <div
                        key={s.index}
                        data-cut={isCut ? "1" : "0"}
                        data-dim={on ? "0" : "1"}
                        style={{ flex: s.dur }}
                        title={`SH${cur}${String(s.index).padStart(2, "0")} · ${s.framing} · ${s.dur.toFixed(1)}s`}
                        className="gb-shot"
                      >
                        {String(s.index).padStart(2, "0")}
                      </div>
                    )
                  })}
                </div>
              </div>

              <div className="gb-tlrow">
                <span className="w-[76px] flex-none text-right text-[11.5px] text-ash">
                  {strategy === "jia" ? "甲" : "乙"} 切分
                </span>
                <span
                  className={cn(
                    "w-[150px] flex-none font-mono text-[11px]",
                    model.outTotal > model.srcTotal + 0.001 ? "text-[#c9c9c9]" : "text-ash"
                  )}
                >
                  合计 {seconds(model.outTotal)}
                  {model.outTotal > model.srcTotal + 0.001
                    ? `（+${(model.outTotal - model.srcTotal).toFixed(1)}）`
                    : ""}
                </span>
                <div className="gb-tlbar">
                  {model.groups.map((_g, i) => {
                    const over = model.outTotal > model.srcTotal + 0.001 && i === model.groups.length - 1
                    return (
                      <div
                        key={i}
                        data-k={over ? "over" : strategy === "jia" ? "jia" : "yi"}
                        data-dim={focus != null && focus !== i ? "1" : "0"}
                        onMouseEnter={() => setFocus(i)}
                        onMouseLeave={() => setFocus(null)}
                        style={{ flex: model.targets[i] }}
                        className="gb-blk"
                      >
                        {i + 1}of{model.groups.length} · {model.targets[i].toFixed(1)}s
                      </div>
                    )
                  })}
                </div>
              </div>

              <div className="mt-3 flex flex-wrap gap-4 text-[11.5px] text-ash">
                <span>
                  <i className="mr-1.5 inline-block size-2.5 rounded-sm bg-hi align-[-1px]" />
                  原镜
                </span>
                <span>
                  <i className="mr-1.5 inline-block size-2.5 rounded-sm border border-wash-line bg-wash align-[-1px]" />
                  甲 · 镜组边界
                </span>
                <span>
                  <i className="mr-1.5 inline-block size-2.5 rounded-sm border border-rule bg-cell align-[-1px]" />
                  乙 · 等比缩放
                </span>
                <span>
                  <i className="mr-1.5 inline-block size-2.5 rounded-sm border border-dashed border-[#8a8a8a] align-[-1px]" />
                  超出原片，自己裁尾
                </span>
                <span className="flex-1" />
                <span className="text-ash-dim">
                  时长取自 {shots.some((s) => s.fromTable) ? "§③bis 镜表" : "§④ 的 At 时间码推算"}
                </span>
              </div>
            </>
          )}
        </Card>

        <Card
          title="分块与跑片"
          desc="块按当前提示词实时切出来，改完 md 刷新就变，不用再点一次生成。正文用词一字不改，只裁剪、重编号、时间码重基。"
        >
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="gb-chip">
              {chunks.length} 块 · {seconds(model?.outTotal ?? 0)}
            </span>
            <span className="gb-chip text-ash-dim">{wfMeta?.name ?? "默认工作流"}</span>
            <span className="flex-1" />
            <span className="text-[11.5px] text-ash-dim">
              {chunksQ.isFetching
                ? "重切中…"
                : chunksQ.isError
                  ? "分块读取失败"
                  : "分块来自当前源 md，无落盘副本"}
            </span>
          </div>

          {(chunksQ.isError || planErr) && (
            <p className="mt-2.5 text-[12px] text-ash">
              {(chunksQ.error as Error | null)?.message ?? planErr}
            </p>
          )}

          <div className="mt-3.5 flex flex-col gap-1.5">
            {chunks.map((c) => (
              <ChunkRow
                key={c.chunk}
                cur={cur}
                chunk={c.chunk}
                shots={c.shots}
                targetSec={c.target_sec}
                planId={planId ?? 0}
                job={jobOf(c.chunk)}
                onRun={(ch) => run.mutate({ chunk: ch })}
              />
            ))}
          </div>
          {locQ.isError && (
            <p className="mt-3 text-[11.5px] text-ash-dim">
              镜表时长没读出来，上表用的是 §④ 的 At 时间码推算，生成后以清单为准。
            </p>
          )}
        </Card>
      </div>
    </div>
  )
}

/** 单块产物行：状态 + 跑片按钮 + 参考条（参考图/音频是否就位，可点击替换） */
function ChunkRow({
  cur,
  chunk,
  shots,
  targetSec,
  planId,
  job,
  onRun,
}: {
  cur: string
  chunk: string
  shots: number[]
  targetSec: number
  planId: number
  job: Job | undefined
  onRun: (chunk: string) => void
}) {
  const previewQ = useQuery({
    queryKey: ["split-preview", planId, chunk],
    queryFn: () => api.get<SplitPreviewResp>(`/api/split/${planId}/preview?chunk=${chunk}`),
    enabled: planId > 0,
    retry: 1,
  })
  const pics = previewQ.data?.pictures ?? []
  const auds = previewQ.data?.audios ?? []
  const live = job?.status === "queued" || job?.status === "running"

  const refetchPreview = () => previewQ.refetch()

  return (
    <div className="flex flex-wrap items-center gap-2.5 rounded-ctl border border-rule-soft bg-cell px-3 py-2">
      {job?.status === "done" ? (
        <Check className="size-3.5 flex-none text-paper" />
      ) : (
        <span className="size-[5px] flex-none rounded-full bg-ash-dim" />
      )}
      <span className="font-mono text-[11.5px] text-[#c6c6c6]">{chunk}</span>
      <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-ash-dim">
        第 {Number(cur)} 段 · {shots.length} 镜 · 无落盘副本
      </span>
      <span className="font-mono text-[11px] text-ash">
        {shots.map((s) => `SH${cur}${String(s).padStart(2, "0")}`).join(" ")}
      </span>
      <span className="font-mono text-[11px] text-ash">{seconds(targetSec)}</span>
      {job && (
        <span
          className={cn(
            "font-mono text-[11px]",
            job.status === "error" ? "text-[#c9c9c9]" : "text-ash"
          )}
          title={job.log_tail ?? ""}
        >
          {job.status === "error"
            ? `失败：${(job.log_tail ?? "").slice(0, 40)}`
            : `#${job.id} ${job.stage ?? job.status} ${job.progress ?? 0}%`}
        </span>
      )}
      {!live && job?.status !== "done" && (
        <button
          type="button"
          onClick={() => onRun(chunk)}
          className="h-7 rounded-ctl border border-rule bg-panel px-2.5 text-[12px] transition-colors hover:bg-hi"
        >
          跑这块
        </button>
      )}

      {previewQ.isPending && (
        <span className="w-full text-[11px] text-ash-dim">检查参考图 / 音频…</span>
      )}
      {previewQ.isError && (
        <span className="w-full text-[11px] text-ash-dim">
          参考检查失败：{(previewQ.error as Error).message}
        </span>
      )}
      {previewQ.data && (
        <RefStrip
          planId={planId}
          chunk={chunk}
          pics={pics}
          auds={auds}
          contiguous={previewQ.data.audio_contiguous ?? true}
          onChanged={refetchPreview}
        />
      )}
    </div>
  )
}

function Card({
  title,
  desc,
  children,
}: {
  title: string
  desc: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-panel border border-rule-soft bg-panel p-4">
      <h2 className="text-[13.5px] font-medium">{title}</h2>
      <p className="mb-3.5 mt-1 max-w-[78ch] text-[12px] leading-relaxed text-ash">{desc}</p>
      {children}
    </section>
  )
}
