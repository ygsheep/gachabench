import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { cn } from "cn"
import { api } from "@/lib/api"

/** `GET /api/prep/{seg}` 的形状（后端 `prep.plan_prep`）。 */
type PrepItem = {
  n: number
  kind: "image" | "audio"
  filename: string
  status: "in-prep" | "copyable" | "missing"
  source: string | null
  preview: string | null
}

type PrepPlan = {
  seg: string | null
  dir: string | null
  prompt_file: string | null
  items: PrepItem[]
  ready: number
  copyable: number
  missing: number
  problems: string[]
}

type PrepStats = {
  seg: string | null
  copied: number
  skipped: number
  missing: string[]
  problems: string[]
}

const STATUS_TEXT: Record<PrepItem["status"], string> = {
  "in-prep": "已就位",
  copyable: "可从资产补",
  missing: "资产库里没有",
}

/**
 * 备料面板：这一段要哪几样素材、哪几样已经躺在 `素材/{段}/` 里。
 *
 * 备料区里的文件名 == 提示词里写的名字，所以「换素材」就是换掉那个文件，
 * 提示词一个字都不用改 —— 这个面板要让这件事**看得见**。
 *
 * 状态沿用胶片格的语言：实心亮条 = 已确认（已就位）／点线 = 未完成（可补）／
 * 斜纹 = 有问题（缺）。不引入任何颜色。
 */
export function PrepPanel({ seg }: { seg: string | null }) {
  const qc = useQueryClient()
  const [arming, setArming] = useState(false)

  const planQ = useQuery({
    queryKey: ["prep", seg],
    queryFn: () => api.get<PrepPlan>(`/api/prep/${encodeURIComponent(seg!)}`),
    enabled: !!seg,
  })

  const fill = useMutation({
    mutationFn: (overwrite: boolean) =>
      api.post<PrepStats>(`/api/prep/${encodeURIComponent(seg!)}`, { overwrite }),
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: ["prep", seg] })
      qc.invalidateQueries({ queryKey: ["assets"] })
      const parts = [`补进 ${s.copied} 个`]
      if (s.skipped) parts.push(`跳过 ${s.skipped} 个已就位`)
      if (s.missing?.length) parts.push(`缺 ${s.missing.length} 个`)
      toast.success("备料完成", {
        description: parts.join("，"),
      })
      if (s.problems?.length) toast.error(s.problems[0])
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (!seg) {
    return <p className="text-[12.5px] text-ash">先选一个段。</p>
  }
  if (planQ.isPending) {
    return <p className="text-[12.5px] text-ash-dim">读这一段要什么素材…</p>
  }
  if (planQ.isError) {
    return <p className="text-[12.5px] text-ash">{(planQ.error as Error).message}</p>
  }

  const plan = planQ.data!
  if (!plan.prompt_file) {
    return (
      <p className="text-[12.5px] text-ash">
        {plan.problems[0] ?? "没有找到这一段的提示词，没法解析素材表。"}
      </p>
    )
  }

  const need = plan.ready + plan.copyable + plan.missing
  const nothing = need === 0

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="font-mono text-[11.5px] text-ash">
          {plan.prompt_file} · 要 {need} 样
        </span>
        <span className="font-mono text-[11.5px] text-ash-dim">
          已就位 {plan.ready} · 可补 {plan.copyable} · 缺 {plan.missing}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          disabled={!plan.copyable || fill.isPending}
          onClick={() => fill.mutate(false)}
          className={cn(
            "h-8 rounded-ctl border px-3 text-[12.5px] transition-all active:translate-y-px",
            plan.copyable
              ? "border-wash-line bg-wash font-medium text-paper hover:bg-hi"
              : "cursor-not-allowed border-rule-soft text-ash-dim"
          )}
        >
          {fill.isPending && fill.variables === false
            ? "补齐中…"
            : `补齐缺失${plan.copyable ? `（${plan.copyable}）` : ""}`}
        </button>
        {/* 覆盖是破坏性动作：点一下变「确认覆盖」，几秒收回 */}
        <button
          type="button"
          disabled={!plan.ready && !plan.copyable}
          onClick={() => {
            if (!arming) {
              setArming(true)
              window.setTimeout(() => setArming(false), 4000)
              return
            }
            setArming(false)
            fill.mutate(true)
          }}
          className={cn(
            "h-8 rounded-ctl border px-3 text-[12.5px] transition-all active:translate-y-px",
            arming
              ? "border-dashed border-[#8a8a8a] text-paper"
              : "border-rule bg-cell text-ash hover:bg-hi hover:text-paper"
          )}
        >
          {arming ? "确认覆盖已就位的那几份" : "重新备料"}
        </button>
      </div>

      {nothing ? (
        <p className="text-[12.5px] text-ash">
          这一段没有素材表。提示词里加上{" "}
          <span className="font-mono text-paper">| Picture 1 | `文件名.png` |</span> 这样的行，
          这里才会长出槽位。
        </p>
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(212px,1fr))] gap-2">
          {plan.items.map((it) => (
            <li
              key={`${it.kind}-${it.n}-${it.filename}`}
              className={cn(
                "flex min-w-0 items-center gap-2.5 rounded-cell border p-1.5",
                it.status === "missing"
                  ? "border-dashed border-[#6e6e6e]"
                  : it.status === "copyable"
                    ? "border-dotted border-rule"
                    : "border-rule-soft"
              )}
            >
              <span className="relative size-[58px] flex-none overflow-hidden rounded-[3px] bg-black outline-1 outline-offset-[-1px] outline-rule">
                <span
                  className="gb-tab"
                  data-s={
                    it.status === "in-prep" ? "final" : it.status === "copyable" ? "draft" : "reject"
                  }
                />
                {it.kind === "image" && it.preview ? (
                  <img
                    src={it.preview}
                    alt={it.filename}
                    loading="lazy"
                    className="size-full object-cover"
                  />
                ) : (
                  <span className="grid size-full place-items-center font-mono text-[9.5px] tracking-[0.12em] text-ash-dim">
                    {it.kind === "audio" ? "AUDIO" : "—"}
                  </span>
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline gap-1.5">
                  <span className="flex-none font-mono text-[10px] text-ash-dim">
                    {it.kind === "audio" ? "A" : "P"}
                    {it.n}
                  </span>
                  <span className="min-w-0 truncate font-mono text-[11.5px] text-paper">
                    {it.filename}
                  </span>
                </span>
                <span
                  className={cn(
                    "mt-0.5 block truncate text-[11px]",
                    it.status === "missing" ? "text-ash-dim" : "text-ash"
                  )}
                  title={it.source ?? undefined}
                >
                  {STATUS_TEXT[it.status]}
                  {it.status === "copyable" && it.source && it.source !== it.filename
                    ? ` · ${it.source}`
                    : ""}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {plan.problems.length > 0 && (
        <p className="text-[11.5px] text-ash-dim">{plan.problems.join("；")}</p>
      )}
    </div>
  )
}
