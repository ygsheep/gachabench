import { useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { toast } from "sonner"
import { FolderOpen, Pencil, RotateCw, Save } from "lucide-react"
import { cn } from "cn"
import { Switch } from "@/components/ui/switch"
import { WorkspaceNameDialog } from "@/components/WorkspaceNameDialog"
import { api } from "@/lib/api"
import { PREFS_KEY, loadPrefs, resetPrefs, writeJSON, type Prefs } from "@/lib/prefs"
import { wsTitle } from "@/lib/workspace"
import type { ConfigResp, FolderPickerCapability, FolderKey, WorkflowsResp } from "@/lib/types"

const OUTLINE_BTN =
  "inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12.5px] font-medium transition-all hover:bg-hi active:translate-y-px disabled:opacity-45"

const PRIMARY_BTN =
  "inline-flex h-8 items-center gap-1.5 rounded-ctl bg-paper px-3 text-[12.5px] font-medium text-[#131313] transition-all hover:bg-white active:translate-y-px disabled:opacity-45"

const INPUT =
  "h-8 w-full rounded-ctl border border-rule bg-panel px-2.5 font-mono text-[12px] outline-none placeholder:text-ash-dim focus:border-wash-line"

const FOLDER_LABEL: Record<FolderKey, string> = {
  draft: "草稿",
  final: "生成",
  prompt: "提示词",
  storyboard: "分镜表",
  asset: "素材",
}

/** 跑片参数的合法区间 —— 与后端 `routers/config_api.py` 的钳制一致。
 *  前端只负责提示，越界值传到后端也会被夹回来（不会 500）。 */
const POLL_RANGE = [1, 60] as const
const TIMEOUT_RANGE = [1, 1440] as const

export function SettingsPage({ workspace }: { workspace: string | null }) {
  const qc = useQueryClient()
  const navigate = useNavigate()

  const cfgQ = useQuery({
    queryKey: ["config"],
    queryFn: () => api.get<ConfigResp>("/api/config"),
    retry: 1,
  })
  const wfQ = useQuery({
    queryKey: ["workflows"],
    queryFn: () => api.get<WorkflowsResp>("/api/workflows"),
    enabled: !!workspace,
    retry: 1,
  })
  const pickerQ = useQuery({
    queryKey: ["folder-picker"],
    queryFn: () => api.get<FolderPickerCapability>("/api/system/folder-picker"),
    retry: 1,
  })
  const canPick = !!pickerQ.data?.available

  if (cfgQ.isPending) {
    return <p className="px-6 py-8 text-[12.5px] text-ash">读取配置…</p>
  }
  if (cfgQ.isError) {
    return (
      <div className="mx-6 my-6 rounded-panel border border-rule bg-panel p-4">
        <p className="text-[13px] font-medium">配置读不出来</p>
        <p className="mt-1.5 text-[12.5px] text-ash">{(cfgQ.error as Error)?.message}</p>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="px-6 pb-3.5 pt-5">
        <h1 className="text-[19px] font-semibold leading-tight">设置</h1>
        <p className="pt-0.5 text-[12.5px] text-ash">
          改完点各组的「保存」。目录名类改动会影响后续扫描与落位，不会搬动已有文件。
        </p>
      </header>

      <div className="flex flex-col gap-4 px-6 pb-11">
        <ProjectSection cfg={cfgQ.data} workspace={workspace} />
        <WatchSection cfg={cfgQ.data} onSaved={() => qc.invalidateQueries({ queryKey: ["config"] })} />
        <ConnectionSection cfg={cfgQ.data} canPick={canPick} onSaved={() => qc.invalidateQueries({ queryKey: ["config"] })} />
        <RunSection cfg={cfgQ.data} onSaved={() => qc.invalidateQueries({ queryKey: ["config"] })} />
        <WorkflowSection cfg={cfgQ.data} workflows={wfQ.data} onSaved={() => qc.invalidateQueries({ queryKey: ["workflows", "config"] })} />
        <FoldersSection cfg={cfgQ.data} workspace={workspace} onSaved={() => qc.invalidateQueries({ queryKey: ["config", "workspaces"] })} />
        <UiSection onJump={() => void navigate({ to: "/review" })} />
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* 外壳                                                                       */
/* -------------------------------------------------------------------------- */

function Section({
  title,
  desc,
  children,
  aside,
}: {
  title: string
  desc?: string
  children: React.ReactNode
  aside?: React.ReactNode
}) {
  return (
    <section className="rounded-panel border border-rule bg-panel p-4">
      <div className="flex items-baseline gap-3">
        <h2 className="text-[14px] font-medium">{title}</h2>
        <span className="flex-1" />
        {aside}
      </div>
      {desc && <p className="mt-1 text-[11.5px] leading-relaxed text-ash-dim">{desc}</p>}
      <div className="mt-3.5 flex flex-col gap-3.5">{children}</div>
    </section>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <label className="grid grid-cols-[150px_1fr] items-center gap-x-4 gap-y-1.5 max-[720px]:grid-cols-1">
      <span className="text-[12.5px] text-ash">{label}</span>
      <span className="min-w-0">
        {children}
        {hint && <span className="mt-1 block text-[11px] leading-relaxed text-ash-dim">{hint}</span>}
      </span>
    </label>
  )
}

function SaveBtn({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} disabled={busy} className={PRIMARY_BTN}>
      {busy ? <RotateCw className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
      {busy ? "保存中…" : "保存"}
    </button>
  )
}

/**
 * 项目：显示名可改，目录名只读。
 *
 * 为什么不给「改目录名」的按钮：`name` 是目录名 + 库里 workspace 列的键 + 接口路径段，
 * 改它要先后端松手关库、搬整个目录、再改写五张表；任何一步失败，卡池就空了。
 * 而「换个显示名」只是配置里的一个字段，改它零风险（见 WorkspaceNameDialog）。
 */
function ProjectSection({ cfg, workspace }: { cfg: ConfigResp; workspace: string | null }) {
  const [open, setOpen] = useState(false)
  const ws = cfg.workspaces.find((w) => w.name === workspace) ?? null
  if (!ws) return null
  const custom = (ws.label ?? "").trim().length > 0

  return (
    <>
      <Section
        title="项目"
        desc="显示名全站显示，随便改；目录名是磁盘上的真实文件夹，也是卡池的键，这里只读。"
        aside={
          <button type="button" onClick={() => setOpen(true)} className={OUTLINE_BTN}>
            <Pencil className="size-3.5" />
            改显示名
          </button>
        }
      >
        <Field label="显示名" hint="就是左侧栏那一个名字；留空则回落到目录名。">
          <span className="flex items-center gap-2 text-[13px]">
            {wsTitle(ws)}
            {custom && (
              <span className="rounded border border-rule px-1.5 py-px text-[10.5px] text-ash-dim">
                自定义
              </span>
            )}
          </span>
        </Field>
        <Field label="目录名" hint="cards.db 里 workspace 列的键，也是所有接口的路径段。">
          <span className="font-mono text-[12px] text-ash">{ws.name}</span>
        </Field>
        <Field label="根目录">
          <span className="break-all font-mono text-[11.5px] text-ash-dim">{ws.root}</span>
        </Field>
      </Section>
      {open && <WorkspaceNameDialog ws={ws} onClose={() => setOpen(false)} />}
    </>
  )
}

/**
 * 卡池刷新：实时读盘的节流阀。
 *
 * 「实时」不等于「一直扫」：后台每 N 秒只 stat 一次文件指纹（大小 + 修改时间），
 * 只有新增/变化的文件才跑 ffprobe 入库；没变化时这一轮零写入。所以间隔可以给得很小
 * 也不心疼，但不能给到 0（人手没那么快放片子，纯属白扫）。
 */
function WatchSection({ cfg, onSaved }: { cfg: ConfigResp; onSaved: () => void }) {
  const cur = cfg.watch ?? { enabled: true, interval_sec: 8, request_gap_sec: 1.5 }
  const [enabled, setEnabled] = useState(cur.enabled)
  const [text, setText] = useState(String(cur.interval_sec))

  const num = Number(text)
  const valid = Number.isFinite(num) && num >= 2 && num <= 120
  const dirty = enabled !== cur.enabled || (valid && num !== cur.interval_sec)

  const save = useMutation({
    mutationFn: () => api.put<ConfigResp>("/api/config", { watch: { enabled, interval_sec: num } }),
    onSuccess: () => {
      onSaved()
      toast.success("卡池刷新已保存", {
        description: enabled
          ? `每 ${num} 秒看一眼盘，有新片才登记`
          : "已关闭：只在打开审片台 / 手动点「重新扫描」时更新",
      })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Section
      title="卡池刷新"
      desc="后台定时看一眼 草稿/ 与 生成/ 的文件指纹（大小 + 修改时间，不跑 ffprobe）；只有新片或被改过的片才探测入库，没变化时零写入。"
      aside={dirty ? <SaveBtn busy={save.isPending} onClick={() => save.mutate()} /> : undefined}
    >
      <div className="flex items-center gap-3">
        <Switch checked={enabled} onCheckedChange={setEnabled} aria-label="自动刷新卡池" />
        <span className="text-[12.5px] text-ash">自动发现新片</span>
      </div>
      <Field
        label="间隔（秒）"
        hint="2–120。太短没意义（人手放不了那么快），太长会像「还是看不到新片」。"
      >
        <div className="flex items-center gap-2">
          <input
            className={cn(INPUT, "max-w-[110px]")}
            value={text}
            inputMode="numeric"
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
            aria-label="刷新间隔秒数"
          />
          <span className="text-[11.5px] text-ash-dim">
            {valid ? `每 ${num} 秒看一次盘` : "填 2–120 之间的秒数"}
          </span>
        </div>
      </Field>
    </Section>
  )
}

function DirField({
  label,
  value,
  onChange,
  canPick,
  hint,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  canPick: boolean
  hint?: string
}) {
  const [picking, setPicking] = useState(false)

  async function browse() {
    setPicking(true)
    try {
      const r = await api.post<{ path: string | null; cancelled: boolean }>(
        "/api/system/pick-folder",
        { title: `选择${label}`, initial: value },
        // 🔴 原生对话框要等用户操作，多慢都可能 —— 绝不能给它套默认超时
        { timeoutMs: 0 }
      )
      if (r.path) onChange(r.path)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setPicking(false)
    }
  }

  return (
    <Field label={label} hint={hint}>
      <span className="flex items-center gap-2">
        <input className={INPUT} value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} />
        {canPick && (
          <button
            type="button"
            onClick={browse}
            disabled={picking}
            title="弹出系统对话框选目录"
            className="inline-flex h-8 w-8 flex-none items-center justify-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-45"
          >
            <FolderOpen className={cn("size-3.5", picking && "animate-pulse")} />
          </button>
        )}
      </span>
    </Field>
  )
}

/* -------------------------------------------------------------------------- */
/* 各组                                                                       */
/* -------------------------------------------------------------------------- */

function ConnectionSection({
  cfg,
  canPick,
  onSaved,
}: {
  cfg: ConfigResp
  canPick: boolean
  onSaved: () => void
}) {
  const [comfyui, setComfyui] = useState(cfg.comfyui)
  const [ffmpeg, setFfmpeg] = useState(cfg.ffmpeg)

  const save = useMutation({
    mutationFn: () =>
      api.put<ConfigResp>("/api/config", {
        comfyui: {
          base_url: comfyui.base_url,
          comfy_output_dir: comfyui.comfy_output_dir,
          input_dir: comfyui.input_dir,
        },
        ffmpeg: { binary: ffmpeg.binary, probe_binary: ffmpeg.probe_binary },
      }),
    onSuccess: () => {
      onSaved()
      toast.success("连接设置已保存")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Section
      title="连接"
      desc="ComfyUI 与 ffmpeg 的地址。改地址只影响之后的跑片，不影响已入卡的片子。"
      aside={<SaveBtn busy={save.isPending} onClick={() => save.mutate()} />}
    >
      <Field label="ComfyUI 地址" hint="本机默认 http://127.0.0.1:8188，要以 http:// 或 https:// 开头">
        <input
          className={INPUT}
          value={comfyui.base_url}
          onChange={(e) => setComfyui({ ...comfyui, base_url: e.target.value })}
          spellCheck={false}
        />
      </Field>
      <DirField
        label="ComfyUI 产物目录"
        value={comfyui.comfy_output_dir}
        canPick={canPick}
        onChange={(v) => setComfyui({ ...comfyui, comfy_output_dir: v })}
        hint="跑完的片子先落在这里，再被复制进 草稿/"
      />
      <DirField
        label="ComfyUI 输入目录"
        value={comfyui.input_dir}
        canPick={canPick}
        onChange={(v) => setComfyui({ ...comfyui, input_dir: v })}
        hint="参考图与参考音频的唯一汇合点：input_dir/<提示词里写的文件名>"
      />
      <Field label="ffmpeg" hint="留 ffmpeg / ffprobe 表示走 PATH；探测不到时抽帧与拼接会禁用而不是报错">
        <span className="flex items-center gap-2">
          <input
            className={INPUT}
            value={ffmpeg.binary}
            onChange={(e) => setFfmpeg({ ...ffmpeg, binary: e.target.value })}
            spellCheck={false}
          />
          <input
            className={INPUT}
            value={ffmpeg.probe_binary}
            onChange={(e) => setFfmpeg({ ...ffmpeg, probe_binary: e.target.value })}
            spellCheck={false}
            aria-label="ffprobe 可执行文件"
          />
        </span>
      </Field>
    </Section>
  )
}

function RunSection({ cfg, onSaved }: { cfg: ConfigResp; onSaved: () => void }) {
  const [poll, setPoll] = useState(cfg.comfyui.poll_interval_sec)
  const [timeout, setTimeoutMin] = useState(cfg.comfyui.timeout_min)

  const save = useMutation({
    mutationFn: () =>
      api.put<ConfigResp>("/api/config", {
        comfyui: { poll_interval_sec: poll, timeout_min: timeout },
      }),
    onSuccess: () => {
      onSaved()
      toast.success("跑片参数已保存")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <Section
      title="跑片"
      desc="轮询间隔与单任务超时。越界值会被后端夹回合法区间，不会保存失败。"
      aside={<SaveBtn busy={save.isPending} onClick={() => save.mutate()} />}
    >
      <Field label="轮询间隔" hint={`${poll} 秒查一次进度。太快只会让 ComfyUI 忙，${POLL_RANGE[0]}–${POLL_RANGE[1]} 秒。`}>
        <span className="flex items-center gap-3">
          <input
            type="range"
            min={POLL_RANGE[0]}
            max={POLL_RANGE[1]}
            value={poll}
            onChange={(e) => setPoll(Number(e.target.value))}
            className="h-1 flex-1 accent-white"
            aria-label="轮询间隔（秒）"
          />
          <span className="w-12 text-right font-mono text-[12px] text-paper">{poll}s</span>
        </span>
      </Field>
      <Field label="单任务超时" hint={`${timeout} 分钟没跑完就放弃并标错误。长片（15s）建议 ${TIMEOUT_RANGE[0]}–${TIMEOUT_RANGE[1]} 分钟。`}>
        <span className="flex items-center gap-3">
          <input
            type="range"
            min={TIMEOUT_RANGE[0]}
            max={TIMEOUT_RANGE[1]}
            step={5}
            value={timeout}
            onChange={(e) => setTimeoutMin(Number(e.target.value))}
            className="h-1 flex-1 accent-white"
            aria-label="单任务超时（分钟）"
          />
          <span className="w-12 text-right font-mono text-[12px] text-paper">{timeout}m</span>
        </span>
      </Field>
    </Section>
  )
}

function WorkflowSection({
  cfg,
  workflows,
  onSaved,
}: {
  cfg: ConfigResp
  workflows?: WorkflowsResp
  onSaved: () => void
}) {
  const [jsonPath, setJsonPath] = useState(cfg.workflow.json_path)

  const save = useMutation({
    mutationFn: () => api.put<ConfigResp>("/api/config", { workflow: { json_path: jsonPath } }),
    onSuccess: () => {
      onSaved()
      toast.success("工作流已切换")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const nodeText = useMemo(() => {
    const nodes = cfg.workflow.nodes as Record<string, unknown>
    const lines = Object.entries(nodes)
      .filter(([, v]) => typeof v === "string" || Array.isArray(v))
      .map(([k, v]) => `${k} = ${Array.isArray(v) ? v.join(", ") : String(v)}`)
    return lines.join("\n")
  }, [cfg.workflow.nodes])

  const options = workflows?.workflows ?? []
  const dirty = jsonPath !== cfg.workflow.json_path

  return (
    <Section
      title="工作流"
      desc="换工作流只改这里；节点号由 ComfyUI 侧按 class_type 自动探测，探测不到才回退下面这份兜底值。"
      aside={dirty ? <SaveBtn busy={save.isPending} onClick={() => save.mutate()} /> : undefined}
    >
      <Field label="工作流文件" hint="列的是 提示词/ 下的 JSON；{prompt} 占位符会按当前工作区解析">
        <select
          value={jsonPath}
          onChange={(e) => setJsonPath(e.target.value)}
          className="h-8 w-full rounded-ctl border border-rule bg-panel px-2 text-[12px] outline-none focus:border-wash-line"
        >
          {!options.some((o) => o.rel === jsonPath || o.name === jsonPath) && (
            <option value={jsonPath}>{jsonPath}（当前）</option>
          )}
          {options.map((o) => (
            <option key={o.rel} value={o.rel}>
              {o.name}
              {o.error ? "（解析失败）" : ""}
            </option>
          ))}
        </select>
      </Field>
      <Field
        label="节点号兜底"
        hint="只读：改错会产出废片，而且画面照样出得来、光看片子发现不了。真要改请直接编辑 config.json。"
      >
        <pre className="max-h-40 overflow-auto rounded-ctl border border-rule bg-cell p-2.5 font-mono text-[11px] leading-relaxed text-ash">
          {nodeText || "（空）"}
        </pre>
      </Field>
    </Section>
  )
}

function FoldersSection({
  cfg,
  workspace,
  onSaved,
}: {
  cfg: ConfigResp
  workspace: string | null
  onSaved: () => void
}) {
  const [folders, setFolders] = useState(cfg.folders)

  const save = useMutation({
    mutationFn: () => api.put<ConfigResp>("/api/config", { folders }),
    onSuccess: () => {
      onSaved()
      toast.success("目录名已保存", { description: "只影响之后的建档与扫描，不搬动已有文件" })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const wsEntry = cfg.workspaces.find((w) => w.name === workspace) ?? null
  const dirty = (Object.keys(folders) as FolderKey[]).some((k) => folders[k] !== cfg.folders[k])

  return (
    <Section
      title="文件夹（全局默认）"
      desc="新建工作区时按这套名字建档；单个工作区可以在打开项目时覆盖。"
      aside={dirty ? <SaveBtn busy={save.isPending} onClick={() => save.mutate()} /> : undefined}
    >
      {(Object.keys(FOLDER_LABEL) as FolderKey[]).map((key) => (
        <Field key={key} label={FOLDER_LABEL[key]} hint={`目录名 ${key}，只能是单个文件夹名`}>
          <input
            className={INPUT}
            value={folders[key] ?? ""}
            onChange={(e) => setFolders({ ...folders, [key]: e.target.value })}
            spellCheck={false}
          />
        </Field>
      ))}

      {wsEntry && (
        <div className="rounded-ctl border border-dashed border-rule bg-cell/50 p-3">
          <p className="text-[12px] font-medium text-ash">
            当前工作区「{wsEntry.name}」的实际目录名
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-ash-dim">
            实线 = 本工作区自己的覆盖；虚线 = 还没覆盖，跟随上面的全局默认。覆盖是在打开项目 /
            资产管理页里选的，这里只展示，避免误改。
          </p>
          <ul className="mt-2 grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-1.5">
            {(Object.keys(FOLDER_LABEL) as FolderKey[]).map((key) => {
              const override = key in wsEntry.folders
              const name = (override ? wsEntry.folders[key] : folders[key]) || "未确定"
              return (
                <li
                  key={key}
                  className={cn(
                    "flex items-baseline gap-2 rounded border px-2 py-1 text-[11.5px]",
                    override ? "border-rule text-paper" : "border-dashed border-rule text-ash-dim"
                  )}
                >
                  <span className="text-ash-dim">{FOLDER_LABEL[key]}</span>
                  <span className="ml-auto font-mono">{name}</span>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </Section>
  )
}

function UiSection({ onJump }: { onJump: () => void }) {
  const [prefs, setPrefs] = useState<Prefs>(() => loadPrefs())
  const dirty = prefs.keepOnReload !== loadPrefs().keepOnReload

  function patch(p: Partial<Prefs>) {
    setPrefs((cur) => ({ ...cur, ...p }))
  }

  function save() {
    // 直接写盘（不等防抖）：设置页的开关应当即生效
    writeJSON(PREFS_KEY, prefs)
    toast.success("界面偏好已保存", {
      description: prefs.keepOnReload ? "刷新后会保留筛选条件" : "刷新后回到默认筛选",
    })
  }

  function reset() {
    const next = resetPrefs()
    setPrefs(next)
    toast.success("已恢复默认")
  }

  return (
    <Section title="界面" desc="只影响这一个浏览器/这台机器，存 localStorage（键 gb.ui.v1）。">
      <div className="flex items-center gap-3">
        <Switch
          checked={prefs.keepOnReload}
          onCheckedChange={(v) => patch({ keepOnReload: v })}
          aria-label="刷新后保留筛选条件"
        />
        <span className="text-[12.5px] text-ash">刷新后保留筛选条件</span>
        <span className="flex-1" />
        {dirty && <SaveBtn busy={false} onClick={save} />}
      </div>
      <p className="text-[11px] leading-relaxed text-ash-dim">
        关掉之后，每次刷新都清空卡池筛选。页面位置由地址栏决定，不受这里影响。审片器分隔条的高度另有自己的记忆
        （键 <span className="font-mono">gb-review-stage</span>），不受这里影响。
      </p>
      <div className="flex items-center gap-2.5">
        <button type="button" onClick={onJump} className={OUTLINE_BTN}>
          回到抽卡台
        </button>
        <button type="button" onClick={reset} className={OUTLINE_BTN}>
          恢复默认
        </button>
      </div>
    </Section>
  )
}
