import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChevronDown, RotateCcw, SlidersHorizontal } from "lucide-react"
import { cn } from "cn"
import { api } from "@/lib/api"
import type { RunParams } from "@/lib/prefs"
import type { WorkflowMeta, WorkflowParam, WorkflowsResp } from "@/lib/types"

/**
 * 跑片参数条 —— 拆分台顶部常驻，折叠状态也显示「当前工作流 + 改了几项」。
 *
 * 控件清单不写死：全部来自 `GET /api/workflows` 返回的 params 元数据
 * （后端按工作流节点自动探测模型 / LoRA / 采样器 / 画幅 / MP / 倍数 / sigmas …）。
 * 换工作流时控件跟着换，不会出现「设置项和工作流对不上」。
 *
 * 两个默认值刻意不跟随工作流 JSON：
 * - 时长：默认「跟随块目标」（拆分器算出来的 target_sec），否则每块都会被同一个
 *   固定时长压平，拆分彻底失效。
 * - 种子：默认「随机」，不把工作流里写死的那个数字带上来。想复现就在参数条里填。
 */

/** 参数默认值：null 表示「不发送，交给后端/工作流决定」 */
function defaultOf(p: WorkflowParam): string | number | null {
  if (p.key === "duration") return null
  if (p.key === "seed") return null
  return p.value ?? null
}

export function effectiveValue(
  p: WorkflowParam,
  values: Record<string, string | number | null>
): string | number | null {
  return p.key in values ? values[p.key] : defaultOf(p)
}

/** 把参数条状态压成跑片请求体（字段名与后端 GenerateBody 一一对应） */
export function buildRunBody(
  meta: WorkflowMeta | undefined,
  params: RunParams
): Record<string, unknown> {
  const body: Record<string, unknown> = {}
  const wf = params.workflow
  if (wf) body.workflow = wf
  const values = (wf ? params.byWorkflow[wf] : params.byWorkflow["__default__"]) ?? {}
  for (const p of meta?.params ?? []) {
    const v = effectiveValue(p, values)
    if (v === null || v === undefined || v === "") continue
    body[p.key] = p.type === "number" ? Number(v) : v
  }
  return body
}

/** 改了几项（和默认值不同才算） */
function dirtyCount(meta: WorkflowMeta | undefined, params: RunParams): number {
  if (!meta) return 0
  const values = (params.workflow ? params.byWorkflow[params.workflow] : undefined) ?? {}
  let n = 0
  for (const p of meta.params) {
    if (!(p.key in values)) continue
    if (String(values[p.key] ?? "") !== String(defaultOf(p) ?? "")) n++
  }
  return n
}

export function RunParamsBar({
  value,
  onChange,
}: {
  value: RunParams
  onChange: (next: RunParams) => void
}) {
  const [open, setOpen] = useState(false)

  const wfQ = useQuery({
    queryKey: ["workflows"],
    queryFn: () => api.get<WorkflowsResp>("/api/workflows"),
    retry: 1,
    staleTime: 60_000,
  })

  const workflows = wfQ.data?.workflows ?? []
  const current = useMemo(() => {
    if (!workflows.length) return undefined
    if (value.workflow) {
      const hit = workflows.find((w) => w.name === value.workflow || w.rel === value.workflow)
      if (hit) return hit
    }
    const def = workflows.find((w) => w.name === wfQ.data?.default)
    return def ?? workflows[0]
  }, [workflows, value.workflow, wfQ.data?.default])

  const key = current?.name ?? "__default__"
  const values = value.byWorkflow[key] ?? {}
  const dirty = dirtyCount(current, value)

  const setValue = (k: string, v: string | number | null) => {
    onChange({
      ...value,
      byWorkflow: { ...value.byWorkflow, [key]: { ...values, [k]: v } },
    })
  }

  const reset = () => {
    const next = { ...value.byWorkflow }
    delete next[key]
    onChange({ ...value, byWorkflow: next })
  }

  return (
    <section className="rounded-panel border border-rule-soft bg-panel">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-4 py-3 text-left transition-colors hover:bg-cell/60"
      >
        <SlidersHorizontal className="size-3.5 flex-none text-ash" />
        <span className="text-[13.5px] font-medium">跑片参数</span>
        <span className="gb-chip">{current?.name ?? "读工作流…"}</span>
        {dirty > 0 && (
          <span className="gb-chip text-paper">{dirty} 项已改</span>
        )}
        <span className="min-w-0 flex-1 truncate text-[11.5px] text-ash-dim">
          {open ? "" : "时长跟随块目标 · 种子每次随机"}
        </span>
        <ChevronDown
          className={cn("size-3.5 flex-none text-ash transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div className="border-t border-rule-soft px-4 pb-4 pt-3.5">
          {wfQ.isPending && <p className="text-[12px] text-ash">读取工作流参数…</p>}
          {wfQ.isError && (
            <p className="text-[12px] text-ash">
              工作流列表读不出来：{(wfQ.error as Error).message}
              <span className="text-ash-dim">（ComfyUI 没起也能跑，参数会按工作流 JSON 原值）</span>
            </p>
          )}

          {!!workflows.length && (
            <div className="flex flex-col gap-3">
              <label className="flex flex-col gap-1.5">
                <span className="text-[11.5px] text-ash">工作流</span>
                <select
                  value={current?.name ?? ""}
                  onChange={(e) =>
                    onChange({ ...value, workflow: e.target.value || null })
                  }
                  className="h-8 w-full rounded-ctl border border-rule bg-cell px-2 text-[12px] text-paper outline-none transition-colors hover:bg-hi focus:border-wash-line"
                >
                  {workflows.map((w) => (
                    <option key={w.name} value={w.name}>
                      {w.name}
                      {w.error ? "（解析失败）" : ""}
                    </option>
                  ))}
                </select>
              </label>

              {current?.error && (
                <p className="text-[11.5px] text-ash">这个工作流解析失败：{current.error}</p>
              )}

              <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                {(current?.params ?? []).map((p) => (
                  <ParamField
                    key={p.key}
                    param={p}
                    value={effectiveValue(p, values)}
                    onChange={(v) => setValue(p.key, v)}
                  />
                ))}
              </div>

              <div className="flex items-center gap-2.5 pt-0.5">
                <button
                  type="button"
                  onClick={reset}
                  disabled={dirty === 0}
                  className="inline-flex h-7 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-2.5 text-[11.5px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-40"
                >
                  <RotateCcw className="size-3" />
                  恢复工作流原值
                </button>
                <span className="text-[11px] text-ash-dim">
                  改完立刻生效，下次跑片带上；不填的项保持工作流 JSON 原值。
                </span>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

function ParamField({
  param,
  value,
  onChange,
}: {
  param: WorkflowParam
  value: string | number | null
  onChange: (v: string | number | null) => void
}) {
  const isDuration = param.key === "duration"
  const isSeed = param.key === "seed"
  const follow = value === null || value === ""

  const hint =
    isDuration && follow
      ? "跟随块目标"
      : isSeed && follow
        ? "每次随机"
        : undefined

  return (
    <label className="flex flex-col gap-1.5">
      <span className="flex items-baseline gap-1.5 text-[11.5px] text-ash">
        <span className="truncate">{param.label}</span>
        {hint && <span className="flex-none font-mono text-[10.5px] text-ash-dim">{hint}</span>}
      </span>

      {param.type === "select" ? (
        <select
          value={value === null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 w-full rounded-ctl border border-rule bg-cell px-2 font-mono text-[11.5px] text-paper outline-none transition-colors hover:bg-hi focus:border-wash-line"
        >
          {follow && <option value="">（工作流原值）</option>}
          {/* 当前值不在候选里（ComfyUI 没起 / 模型是私有的）也要能看到，不能显示空白 */}
          {!follow &&
            !(param.options ?? []).some((o) => String(o) === String(value)) && (
              <option value={String(value)}>{String(value)}</option>
            )}
          {(param.options ?? []).map((o) => (
            <option key={String(o)} value={String(o)}>
              {String(o)}
            </option>
          ))}
        </select>
      ) : param.type === "number" ? (
        <div className="flex items-center gap-1.5">
          <input
            type="number"
            value={follow ? "" : String(value)}
            min={param.min ?? undefined}
            max={param.max ?? undefined}
            step={param.step ?? "any"}
            placeholder={hint ?? String(param.value ?? "")}
            onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
            className="h-8 w-full rounded-ctl border border-rule bg-cell px-2 font-mono text-[11.5px] text-paper outline-none transition-colors hover:bg-hi focus:border-wash-line"
          />
          {(isDuration || isSeed) && !follow && (
            <button
              type="button"
              onClick={() => onChange(null)}
              className="h-8 flex-none rounded-ctl border border-rule bg-cell px-2 text-[11px] text-ash transition-colors hover:bg-hi hover:text-paper"
            >
              {isDuration ? "跟块" : "随机"}
            </button>
          )}
        </div>
      ) : (
        <input
          type="text"
          value={follow ? "" : String(value)}
          placeholder={String(param.value ?? "")}
          onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
          title={String(value ?? param.value ?? "")}
          className="h-8 w-full rounded-ctl border border-rule bg-cell px-2 font-mono text-[11px] text-paper outline-none transition-colors hover:bg-hi focus:border-wash-line"
        />
      )}
    </label>
  )
}
