import { useMemo, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, ChevronDown, ChevronRight, X } from "lucide-react"
import { toast } from "sonner"
import { cn } from "cn"
import {
  applyOpsPlan,
  createOpsPlan,
  diffLines,
  type DiffLine,
  invalidateAfterAssetOps,
  opsErrorMessage,
  rollbackOpsPlan,
  type OpsPlanInput,
} from "@/lib/opsApi"
import type { AssetOpsKind, OpsPlan } from "@/lib/types"

export interface AssetOpsTarget {
  mode: AssetOpsKind
  assetIds: string[]
  /** 改名时的原文件名（预填） */
  defaultName?: string
  /** 移动时的当前目录（预填） */
  defaultDir?: string
}

const TITLE: Record<AssetOpsKind, string> = {
  rename: "改名",
  move: "移动",
  delete: "删除",
}

const REF_KIND_LABEL: Record<string, string> = {
  prompt_filename: "提示词上传表",
  prompt_body: "提示词正文",
  storyboard_material: "分镜素材列",
  workflow_input: "workflow 输入",
}

const BLOCKED_REASON: Record<string, string> = {
  NAME_CONFLICT: "目标文件名已被占用",
  NOT_ACTIVE: "资产不是正常状态（已丢失 / 已删除）",
  PATH_INVALID: "目标目录不合法",
}

function blockedReason(code: string): string {
  return BLOCKED_REASON[code] ?? code
}

function refKindLabel(kind: string): string {
  return REF_KIND_LABEL[kind] ?? kind
}

interface CompactRow {
  kind: DiffLine["kind"] | "gap"
  beforeNo: number | null
  afterNo: number | null
  text: string
}

/** 只保留变更行前后若干行上下文，长文件不至于整段铺出来 */
function compactDiff(before: string, after: string, context = 2): CompactRow[] {
  const rows = diffLines(before, after)
  const keep = new Set<number>()
  rows.forEach((row, i) => {
    if (row.kind === "same") return
    for (let k = i - context; k <= i + context; k += 1) {
      if (k >= 0 && k < rows.length) keep.add(k)
    }
  })
  if (!keep.size) return []

  const out: CompactRow[] = []
  let last = -1
  rows.forEach((row, i) => {
    if (!keep.has(i)) return
    if (last >= 0 && i - last > 1) {
      out.push({ kind: "gap", beforeNo: null, afterNo: null, text: "…" })
    }
    out.push(row)
    last = i
  })
  return out
}

export function AssetOpsDialog({
  target,
  onClose,
  onApplied,
}: {
  target: AssetOpsTarget
  onClose: () => void
  onApplied?: () => void
}) {
  const qc = useQueryClient()
  const { mode, assetIds } = target
  const [name, setName] = useState(target.defaultName ?? "")
  const [destDir, setDestDir] = useState(target.defaultDir ?? "")
  const [plan, setPlan] = useState<OpsPlan | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())

  const planM = useMutation({
    mutationFn: (input: OpsPlanInput) => createOpsPlan(input),
    onSuccess: (data) => {
      setPlan(data)
      setError(null)
      setExpanded(new Set())
    },
    onError: (e: Error) => setError(opsErrorMessage(e)),
  })

  const applyM = useMutation({
    mutationFn: (planId: string) => applyOpsPlan(planId),
    onSuccess: (data) => {
      setPlan(data)
      setError(null)
      invalidateAfterAssetOps(qc)
      onApplied?.()
      toast.success(appliedText(data))
    },
    onError: (e: Error) => setError(opsErrorMessage(e)),
  })

  const rollbackM = useMutation({
    mutationFn: (planId: string) => rollbackOpsPlan(planId),
    onSuccess: (data) => {
      setPlan(data)
      setError(null)
      invalidateAfterAssetOps(qc)
      onApplied?.()
      toast.success("已撤销本次应用")
    },
    onError: (e: Error) => setError(opsErrorMessage(e)),
  })

  const busy = planM.isPending || applyM.isPending || rollbackM.isPending
  const canApply =
    !!plan &&
    plan.status === "planned" &&
    plan.blocked.length === 0 &&
    plan.stats.assets > 0 &&
    !busy

  function calculate() {
    setError(null)
    const input: OpsPlanInput =
      mode === "rename"
        ? { kind: "rename", assetId: assetIds[0] ?? "", newName: name }
        : mode === "move"
          ? { kind: "move", assetIds, destDir }
          : { kind: "delete", assetIds }
    planM.mutate(input)
  }

  function toggleExpanded(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  function appliedText(data: OpsPlan): string {
    if (data.kind === "rename") return `已改名：${data.payload.new_name ?? "新文件名"}`
    if (data.kind === "move") return `已移动 ${data.stats.assets} 个资产`
    return `已把 ${data.stats.assets} 个资产移入回收站`
  }

  const applied = plan?.status === "applied"
  const rolledBack = plan?.status === "rolled_back"

  return (
    <div
      className="fixed inset-0 z-80 grid place-items-center bg-black/75 p-6"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={TITLE[mode]}
        className="max-h-[92svh] w-[min(880px,94vw)] overflow-auto rounded-panel border border-rule bg-panel p-5 shadow-2xl shadow-black/60"
      >
        <div className="flex items-center gap-3">
          <h2 className="text-[15px] font-semibold">{TITLE[mode]}</h2>
          <span className="rounded border border-rule px-1.5 py-px font-mono text-[10.5px] text-ash">
            {assetIds.length} 个资产
          </span>
          <span className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="grid size-7 place-items-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px"
          >
            <X className="size-3.5" />
          </button>
        </div>

        {!plan ? (
          <div className="mt-3.5 flex flex-col gap-3">
            {mode === "rename" && (
              <label className="flex flex-col gap-1.5">
                <span className="text-[11.5px] text-ash-dim">
                  新文件名 · 只改名字，不换目录
                </span>
                {target.defaultName && (
                  <span className="font-mono text-[11px] text-ash-dim">
                    当前：{target.defaultName}
                  </span>
                )}
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoFocus
                  spellCheck={false}
                  placeholder="白陌_写实_四视图_v2.png"
                  className="h-9 rounded-ctl border border-rule bg-cell px-2.5 font-mono text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.nativeEvent.isComposing) calculate()
                  }}
                />
              </label>
            )}

            {mode === "move" && (
              <label className="flex flex-col gap-1.5">
                <span className="text-[11.5px] text-ash-dim">
                  目标相对目录 · 留空表示资产根目录
                </span>
                {target.defaultDir !== undefined && (
                  <span className="font-mono text-[11px] text-ash-dim">
                    当前：{target.defaultDir || "资产根目录"}
                  </span>
                )}
                <input
                  value={destDir}
                  onChange={(e) => setDestDir(e.target.value)}
                  autoFocus
                  spellCheck={false}
                  placeholder="资产/人物"
                  className="h-9 rounded-ctl border border-rule bg-cell px-2.5 font-mono text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.nativeEvent.isComposing) calculate()
                  }}
                />
              </label>
            )}

            {mode === "delete" && (
              <div className="rounded-ctl border border-dashed border-rule px-3 py-2.5 text-[12px] leading-relaxed text-ash">
                文件会移入回收站，可从集合「回收站」里恢复；引用不会被改写，恢复后自动有效。
                彻底清除需要单独在回收站里二次确认。
              </div>
            )}

            {error && (
              <p className="rounded-ctl border border-dashed border-[#8a8a8a] px-3 py-2 text-[12px] text-paper">
                {error}
              </p>
            )}

            <div className="flex items-center gap-3">
              <span className="flex-1" />
              <button
                type="button"
                onClick={onClose}
                className="inline-flex h-9 items-center rounded-ctl border border-rule px-4 text-[12.5px] text-ash transition-colors hover:border-[#5c5c5c] hover:text-paper active:translate-y-px"
              >
                取消
              </button>
              <button
                type="button"
                onClick={calculate}
                disabled={busy}
                className="inline-flex h-9 items-center rounded-ctl bg-paper px-4 text-[12.5px] font-medium text-[#131313] transition-transform hover:bg-white active:translate-y-px active:scale-[0.99] disabled:pointer-events-none disabled:opacity-40"
              >
                {planM.isPending ? "计算中…" : "计算影响面"}
              </button>
            </div>
          </div>
        ) : (
          <div className="mt-2">
            <div className="flex flex-wrap items-center gap-2 border-b border-rule-soft pb-2.5">
              <span className="min-w-0 flex-1 truncate text-[12.5px] text-ash">
                {plan.kind === "rename" ? (
                  <>
                    <span className="font-mono">{plan.payload.old_name}</span>
                    <span className="px-1 text-ash-dim">→</span>
                    <span className="font-mono text-paper">{plan.payload.new_name}</span>
                  </>
                ) : plan.kind === "move" ? (
                  <>
                    <b className="font-medium text-paper">{plan.stats.assets}</b> 个资产 →
                    <span className="font-mono"> {plan.payload.dest_dir || "资产根目录"}</span>
                  </>
                ) : (
                  <>
                    <b className="font-medium text-paper">{plan.stats.assets}</b> 个资产将移入回收站
                  </>
                )}
              </span>
              <span
                className={cn(
                  "rounded border px-1.5 py-px font-mono text-[10.5px]",
                  plan.status === "applied"
                    ? "border-wash-line bg-wash text-paper"
                    : plan.status === "failed" || plan.blocked.length
                      ? "border-dashed border-[#8a8a8a] text-ash"
                      : "border-rule text-ash"
                )}
              >
                {plan.status}
              </span>
            </div>

            <div className="mt-2.5 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[12px] text-ash">
              <span>
                <b className="font-mono font-medium text-paper">{plan.stats.files}</b> 个文件受影响
              </span>
              <span>
                <b className="font-mono font-medium text-paper">{plan.stats.refs}</b> 处引用
              </span>
              {plan.expires_at && plan.status === "planned" && (
                <span className="text-[11px] text-ash-dim">计划 30 分钟内有效</span>
              )}
            </div>

            {mode === "delete" && plan.status === "planned" && (
              <p className="mt-2 rounded-ctl border border-dashed border-rule px-3 py-2 text-[11.5px] leading-relaxed text-ash">
                删除只会把文件移入回收站，可从「回收站」恢复。上面的引用数仅用于确认，不会改写正文。
              </p>
            )}

            {plan.blocked.length > 0 && (
              <div className="mt-3 rounded-ctl border border-dashed border-[#8a8a8a] p-2.5">
                <p className="flex items-center gap-1.5 text-[12px] font-medium text-paper">
                  <AlertTriangle className="size-3.5" />
                  有 {plan.blocked.length} 项不可执行
                </p>
                <ul className="mt-1.5 flex flex-col gap-1">
                  {plan.blocked.map((b) => (
                    <li
                      key={`${b.asset_id}:${b.path}`}
                      className="flex items-baseline gap-2 rounded-ctl bg-cell px-2 py-1"
                    >
                      <span className="grid size-4 flex-none place-items-center rounded-[3px] border border-rule text-transparent">
                        ✓
                      </span>
                      <span
                        className="min-w-0 flex-1 break-all font-mono text-[11px] text-ash"
                        title={b.path}
                      >
                        {b.path}
                      </span>
                      <span className="flex-none text-[11px] text-ash">
                        {blockedReason(b.reason)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {plan.files.length > 0 && (
              <div className="mt-3">
                <div className="mb-1.5 flex items-baseline gap-2">
                  <h3 className="text-[11.5px] text-ash-dim">受影响文件</h3>
                  <span className="font-mono text-[10.5px] text-ash-dim">{plan.files.length}</span>
                  <span className="flex-1" />
                  <span className="text-[10.5px] text-ash-dim">
                    默认全选 · 应用时按计划全量改写
                  </span>
                </div>
                <ul className="flex flex-col gap-1">
                  {plan.files.map((file) => {
                    const open = expanded.has(file.file_path)
                    return (
                      <li key={file.file_path} className="rounded-ctl border border-rule-soft bg-cell">
                        <button
                          type="button"
                          onClick={() => toggleExpanded(file.file_path)}
                          aria-expanded={open}
                          className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
                        >
                          <span
                            role="checkbox"
                            aria-checked="true"
                            aria-disabled="true"
                            title="后端目前按计划全量改写，不支持只勾选部分文件"
                            className="grid size-4 flex-none place-items-center rounded-[3px] border border-white bg-paper text-[10px] text-[#131313]"
                          >
                            ✓
                          </span>
                          {open ? (
                            <ChevronDown className="size-3.5 flex-none text-ash-dim" />
                          ) : (
                            <ChevronRight className="size-3.5 flex-none text-ash-dim" />
                          )}
                          <span
                            className="min-w-0 flex-1 break-all font-mono text-[11px] text-paper"
                            title={file.file_path}
                          >
                            {file.file_path}
                          </span>
                          {file.line != null && (
                            <span className="flex-none font-mono text-[10.5px] text-ash-dim">
                              :{file.line}
                            </span>
                          )}
                          <span className="flex-none rounded border border-rule px-1.5 py-px text-[10px] text-ash">
                            {refKindLabel(file.kind)}
                          </span>
                          <span className="flex-none font-mono text-[10.5px] text-ash-dim">
                            {file.refs} 处
                          </span>
                        </button>
                        {open && <DiffView before={file.before} after={file.after} />}
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}

            {error && (
              <p className="mt-3 rounded-ctl border border-dashed border-[#8a8a8a] px-3 py-2 text-[12px] text-paper">
                {error}
              </p>
            )}

            {applied && (
              <p className="mt-3 rounded-ctl border border-wash-line bg-wash px-3 py-2 text-[12px] text-paper">
                本次操作已应用。备份保留在 <span className="font-mono">.gachabench/backups/</span>，
                需要还原时可以撤销。
              </p>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => {
                  setPlan(null)
                  setError(null)
                }}
                disabled={busy || applied}
                className="inline-flex h-9 items-center rounded-ctl border border-rule px-3.5 text-[12.5px] text-ash transition-colors hover:border-[#5c5c5c] hover:text-paper disabled:opacity-40"
              >
                重新计算
              </button>
              <span className="flex-1" />

              {applied ? (
                <>
                  <button
                    type="button"
                    onClick={onClose}
                    className="inline-flex h-9 items-center rounded-ctl border border-rule px-4 text-[12.5px] text-ash transition-colors hover:border-[#5c5c5c] hover:text-paper"
                  >
                    关闭
                  </button>
                  <button
                    type="button"
                    onClick={() => rollbackM.mutate(plan.id)}
                    disabled={busy}
                    className="inline-flex h-9 items-center rounded-ctl border border-wash-line bg-cell px-4 text-[12.5px] font-medium text-paper transition-colors hover:bg-hi disabled:opacity-40"
                  >
                    {rollbackM.isPending ? "撤销中…" : "撤销本次应用"}
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={onClose}
                    className="inline-flex h-9 items-center rounded-ctl border border-rule px-4 text-[12.5px] text-ash transition-colors hover:border-[#5c5c5c] hover:text-paper"
                  >
                    {rolledBack ? "关闭" : "取消"}
                  </button>
                  {!rolledBack && (
                    <button
                      type="button"
                      onClick={() => plan && applyM.mutate(plan.id)}
                      disabled={!canApply}
                      title={
                        plan.blocked.length
                          ? "计划里有不可执行项，请调整输入后重新计算"
                          : undefined
                      }
                      className="inline-flex h-9 items-center rounded-ctl bg-paper px-4 text-[12.5px] font-medium text-[#131313] transition-transform hover:bg-white active:translate-y-px active:scale-[0.99] disabled:pointer-events-none disabled:opacity-40"
                    >
                      {applyM.isPending
                        ? "应用中…"
                        : plan.status !== "planned"
                          ? "计划不可应用"
                          : mode === "delete"
                            ? `确认移入回收站（${plan.stats.assets}）`
                            : "应用计划"}
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function DiffView({ before, after }: { before: string; after: string }) {
  const rows = useMemo(() => compactDiff(before, after), [before, after])
  if (!rows.length) {
    return (
      <p className="border-t border-rule-soft px-3 py-2 text-[11px] text-ash-dim">
        这个文件的内容没有差异。
      </p>
    )
  }
  return (
    <div className="max-h-[280px] overflow-auto border-t border-rule-soft">
      {rows.map((row, i) => (
        <div
          key={i}
          className={cn(
            "flex gap-2 px-2 py-px font-mono text-[10.5px] leading-[1.6]",
            row.kind === "add" && "bg-wash",
            row.kind === "del" && "bg-black/45"
          )}
        >
          <span className="w-9 flex-none text-right text-ash-dim">{row.beforeNo ?? ""}</span>
          <span className="w-9 flex-none text-right text-ash-dim">{row.afterNo ?? ""}</span>
          <span
            className={cn(
              "w-3 flex-none text-center",
              row.kind === "add" ? "text-paper" : "text-ash-dim"
            )}
          >
            {row.kind === "add" ? "+" : row.kind === "del" ? "-" : " "}
          </span>
          <span
            className={cn(
              "min-w-0 flex-1 whitespace-pre-wrap break-all",
              row.kind === "del" ? "text-ash line-through" : "text-paper"
            )}
          >
            {row.text || " "}
          </span>
        </div>
      ))}
    </div>
  )
}
