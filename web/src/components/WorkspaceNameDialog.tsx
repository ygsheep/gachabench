import { useEffect, useRef, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Check, Undo2, X } from "lucide-react"
import { api } from "@/lib/api"
import { wsTitle } from "@/lib/workspace"
import type { Workspace } from "@/lib/types"

const LABEL_MAX = 40

/**
 * 改项目「显示名」。
 *
 * 为什么不做成改目录名：`name` 同时是目录名、`cards.db` 里 workspace 列的键、
 * 以及所有接口的路径段 —— 改它要先关库、搬目录、改写五张表，Windows 上连目录
 * 改名都会被打开的文件句柄顶回来（WinError 5）。而「名字不好看」根本不需要
 * 付这个代价：label 只是个标签，改它零风险。
 *
 * 万一有人确实要连盘一起改，后端 `PATCH /api/workspaces/{name}` 传 `name`
 * 走的是已经补全的安全路径（关连接 → 改名 → 改库 → 重开），但界面不给入口。
 */
export function WorkspaceNameDialog({ ws, onClose }: { ws: Workspace; onClose: () => void }) {
  const qc = useQueryClient()
  const [value, setValue] = useState(ws.label || ws.name)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])

  const save = useMutation({
    mutationFn: (label: string) =>
      api.patch<{ workspace: Workspace }>(`/api/workspaces/${encodeURIComponent(ws.name)}`, { label }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["workspaces"] })
      qc.invalidateQueries({ queryKey: ["config"] })
      toast.success(`项目名已改为「${wsTitle(r.workspace)}」`, {
        description: `磁盘目录仍是 ${ws.name}，卡池和索引不受影响`,
      })
      onClose()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const typed = value.trim()
  const current = (ws.label || ws.name).trim()
  const same = typed === current || (!typed && !(ws.label ?? "").trim())

  return (
    <div
      className="fixed inset-0 z-80 grid place-items-center bg-black/70 p-6"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        role="dialog"
        aria-label="改项目名"
        className="w-[min(460px,92vw)] rounded-panel border border-rule bg-panel p-5"
      >
        <div className="flex items-baseline gap-3">
          <h2 className="text-[14px] font-medium">改项目名</h2>
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

        <p className="mt-2 text-[11.5px] leading-relaxed text-ash-dim">
          只改界面上显示的名字。磁盘目录仍是 <span className="font-mono text-ash">{ws.name}</span>，
          卡池、任务、素材索引都不动。
        </p>

        <input
          ref={inputRef}
          value={value}
          maxLength={LABEL_MAX}
          spellCheck={false}
          placeholder={ws.name}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !same && !save.isPending) save.mutate(typed)
            if (e.key === "Escape") onClose()
          }}
          className="mt-3 h-9 w-full rounded-ctl border border-rule bg-cell px-2.5 text-[13px] outline-none placeholder:text-ash-dim focus:border-wash-line"
        />
        <div className="mt-1.5 flex items-baseline gap-2">
          <span className="text-[11px] text-ash-dim">
            {value.trim().length}/{LABEL_MAX}
          </span>
          <span className="flex-1" />
          {!!(ws.label ?? "").trim() && (
            <button
              type="button"
              onClick={() => save.mutate("")}
              disabled={save.isPending}
              className="inline-flex items-center gap-1 text-[11px] text-ash-dim transition-colors hover:text-paper disabled:opacity-45"
            >
              <Undo2 className="size-3" />
              恢复成目录名
            </button>
          )}
        </div>

        <div className="mt-4 flex items-center gap-2">
          <span className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-8 items-center rounded-ctl border border-rule bg-cell px-3 text-[12.5px] text-ash transition-colors hover:bg-hi hover:text-paper"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => save.mutate(typed)}
            disabled={save.isPending || same}
            className="inline-flex h-8 items-center gap-1.5 rounded-ctl bg-paper px-3 text-[12.5px] font-medium text-[#131313] transition-all hover:bg-white active:translate-y-px disabled:opacity-45"
          >
            <Check className="size-3.5" strokeWidth={2.4} />
            {save.isPending ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  )
}
