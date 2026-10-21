import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { FolderSearch } from "lucide-react"
import { toast } from "sonner"
import { cn } from "cn"
import { api } from "@/lib/api"
import type { InspectResp, Workspace } from "@/lib/types"

interface WsResp {
  workspaces: Workspace[]
  active: string | null
}

/**
 * 工作区还没指定资产目录时（后端 409）顶在资产管理页里的补救面板。
 * 不用去「新建 / 打开项目」也能就地选：已有目录 / 内置候选 / 自填一个（会自动创建）。
 */
export function AssetDirSetter() {
  const qc = useQueryClient()
  const wsQ = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<WsResp>("/api/workspaces"),
    retry: 1,
  })
  const active = wsQ.data?.workspaces.find((w) => w.name === wsQ.data?.active) ?? null

  const insQ = useQuery({
    queryKey: ["inspect", active?.root ?? ""],
    queryFn: () => api.post<InspectResp>("/api/workspaces/inspect", { root: active?.root ?? "" }),
    enabled: !!active?.root,
    retry: 1,
  })
  const inspect = insQ.data ?? null

  /** 用户显式选过的名字；没选过就跟着后端给的默认走（自动匹配 → 第一个内置候选） */
  const [picked, setPicked] = useState<string | null>(null)
  const fallback = inspect?.asset_dir ?? inspect?.asset_candidates?.[0] ?? ""
  const dirName = picked ?? fallback
  const clean = dirName.trim()

  const choices = useMemo(() => {
    if (!inspect) return [] as { name: string; note: string }[]
    const out: { name: string; note: string }[] = inspect.subdirs.map((d) => ({
      name: d,
      note: "已有",
    }))
    for (const c of inspect.asset_candidates) {
      if (!inspect.subdirs.includes(c)) out.push({ name: c, note: "新建" })
    }
    return out
  }, [inspect])

  const setDir = useMutation({
    mutationFn: (dir: string) =>
      api.patch(`/api/workspaces/${encodeURIComponent(active?.name ?? "")}/folders`, {
        folders: { asset: dir },
      }),
    onSuccess: (_r, dir) => {
      qc.invalidateQueries({ queryKey: ["assets"] })
      qc.invalidateQueries({ queryKey: ["workspaces"] })
      qc.invalidateQueries({ queryKey: ["inspect"] })
      toast.success(`资产目录已设为「${dir}」，正在读取…`)
    },
    onError: (e) => toast.error((e as Error).message),
  })

  const rootPath = (active?.root ?? "").replace(/[\\/]+$/, "")

  return (
    <div className="rounded-panel border border-rule bg-panel p-5">
      <h3 className="text-[15px] font-medium">这个工作区还没有指定资产目录</h3>
      <p className="mt-1.5 max-w-[78ch] text-[12.5px] leading-relaxed text-ash">
        资产目录是 <span className="font-mono">&lt;Picture N&gt;</span> /{" "}
        <span className="font-mono">&lt;Audio N&gt;</span> 的锚所在，也是上传素材的落点。
        选一个已有目录，或者填一个新名字 —— 确认后立刻创建。
      </p>
      {active && (
        <p className="mt-1.5 font-mono text-[11px] text-ash-dim">
          {active.name} · {active.root}
        </p>
      )}

      {wsQ.isPending && <p className="mt-3 text-[12.5px] text-ash">读取工作区…</p>}
      {wsQ.isError && (
        <p className="mt-3 text-[12.5px] text-ash">工作区读不出来：{(wsQ.error as Error).message}</p>
      )}
      {!active && wsQ.isSuccess && (
        <p className="mt-3 text-[12.5px] text-ash">还没有激活任何工作区，先去左侧「抽卡台」建一个。</p>
      )}

      {active && insQ.isPending && (
        <p className="mt-3 flex items-center gap-2 text-[12.5px] text-ash">
          <FolderSearch className="size-3.5" /> 检测这个目录里有什么…
        </p>
      )}
      {active && insQ.isError && (
        <p className="mt-3 text-[12.5px] text-ash">检测失败：{(insQ.error as Error).message}</p>
      )}

      {inspect && (
        <>
          {inspect.exists ? (
            <>
              <p className="mt-3.5 text-[11.5px] text-ash-dim">
                工作区里已有的目录（{inspect.subdirs.length}）
              </p>
              {choices.length ? (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {choices.map((c) => (
                    <button
                      key={c.name}
                      type="button"
                      onClick={() => setPicked(c.name)}
                      className={cn(
                        "inline-flex h-7 items-center gap-2 rounded-ctl border px-2.5 font-mono text-[12px] transition-colors",
                        clean === c.name
                          ? "border-wash-line bg-wash text-paper"
                          : "border-rule bg-cell text-ash hover:text-paper"
                      )}
                    >
                      {c.name}
                      <span className="font-sans text-[10.5px] text-ash-dim">{c.note}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <p className="mt-1.5 text-[12px] text-ash-dim">目录是空的，下面填一个名字即可。</p>
              )}
              {inspect.asset_dir && (
                <p className="mt-2 text-[11.5px] text-ash-dim">
                  自动匹配到 <span className="font-mono text-ash">{inspect.asset_dir}</span>
                  ，直接确认也行。
                </p>
              )}
            </>
          ) : (
            <p className="mt-3.5 text-[12.5px] text-ash">目录还不存在，确认后会连工作区一起建出来。</p>
          )}

          <div className="mt-3.5 flex flex-wrap items-center gap-2">
            <input
              value={dirName}
              onChange={(e) => setPicked(e.target.value)}
              spellCheck={false}
              placeholder="素材"
              className="h-9 w-[220px] rounded-ctl border border-rule bg-cell px-2.5 font-mono text-[12.5px] outline-none placeholder:text-ash-dim focus:border-wash-line"
            />
            <button
              type="button"
              disabled={!clean || !active || setDir.isPending}
              onClick={() => setDir.mutate(clean)}
              className="inline-flex h-9 items-center rounded-ctl bg-paper px-4 text-[12.5px] font-medium text-[#131313] transition-transform hover:bg-white active:translate-y-px active:scale-[0.99] disabled:pointer-events-none disabled:opacity-40"
            >
              {setDir.isPending ? "设置中…" : "设为资产目录"}
            </button>
            {!!clean && !!rootPath && (
              <span className="font-mono text-[11px] text-ash-dim">
                落点 {rootPath}/{clean}
              </span>
            )}
          </div>
        </>
      )}
    </div>
  )
}
