import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { FolderOpen, FolderSearch, X } from "lucide-react"
import { cn } from "cn"
import { api } from "@/lib/api"
import type { FolderPickerCapability, InspectResp, PickFolderResp } from "@/lib/types"

/** 资产目录选择器的哨兵值 */
const ASSET_AUTO = "__auto__"
const ASSET_NONE = "__none__"
const ASSET_CUSTOM = "__custom__"

const ROLE_LABEL: Record<string, string> = {
  draft: "草稿",
  final: "生成",
  prompt: "提示词",
  storyboard: "分镜表",
  asset: "资产",
}

export function WorkspaceGate({ onClose }: { onClose?: () => void }) {
  const qc = useQueryClient()
  const [mode, setMode] = useState<"create" | "open">("create")

  const [parentDir, setParentDir] = useState("")
  const [name, setName] = useState("")
  const [rootPath, setRootPath] = useState("")

  const [assetDir, setAssetDir] = useState(ASSET_AUTO)
  const [customAsset, setCustomAsset] = useState("")
  const [inspect, setInspect] = useState<InspectResp | null>(null)
  /** 正在等系统选择框的是哪个字段（null = 没在选） */
  const [browsing, setBrowsing] = useState<"parent" | "root" | null>(null)

  const targetRoot =
    mode === "create"
      ? parentDir.trim() && name.trim()
        ? `${parentDir.trim().replace(/[\\/]+$/, "")}/${name.trim()}`
        : ""
      : rootPath.trim()

  const inspectM = useMutation({
    mutationFn: (root: string) => api.post<InspectResp>("/api/workspaces/inspect", { root }),
    onSuccess: (d) => {
      setInspect(d)
      if (!d.exists) setAssetDir(ASSET_AUTO)
      else if (d.asset_dir) setAssetDir(d.asset_dir)
      else setAssetDir(ASSET_AUTO)
      if (mode === "open" && !name.trim() && d.suggested_name) setName(d.suggested_name)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  // 能力探测：本机没有可用的原生实现就不显示「浏览」，免得留个点了没反应的死按钮。
  const pickerQ = useQuery({
    queryKey: ["folder-picker"],
    queryFn: () => api.get<FolderPickerCapability>("/api/system/folder-picker"),
    staleTime: Infinity,
  })
  const canBrowse = !!pickerQ.data?.available

  /** 弹系统的「选择文件夹」。路径由后端拿 —— 浏览器 API 给不了绝对路径。 */
  async function browse(which: "parent" | "root") {
    if (browsing) return
    const initial = (which === "parent" ? parentDir : rootPath).trim()
    setBrowsing(which)
    try {
      const r = await api.post<PickFolderResp>("/api/system/pick-folder", {
        title: which === "parent" ? "选择父目录 —— 项目目录会建在它里面" : "选择项目目录",
        initial,
      })
      // 取消是正常操作（用户点了「取消」），不该弹报错打扰他
      if (r.cancelled || !r.path) return
      if (which === "parent") {
        setParentDir(r.path)
      } else {
        setRootPath(r.path)
        setInspect(null)
        inspectM.mutate(r.path) // 选完就检测，省一次点击
      }
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBrowsing(null)
    }
  }

  function assetValue(): string | undefined {
    if (assetDir === ASSET_AUTO) return undefined
    if (assetDir === ASSET_NONE) return ""
    if (assetDir === ASSET_CUSTOM) return customAsset.trim() || undefined
    return assetDir
  }

  const noMatch =
    mode === "open" && !!inspect?.exists && !inspect.asset_dir && assetDir === ASSET_AUTO
  const createOk = !!parentDir.trim() && !!name.trim()
  const openOk = !!rootPath.trim() && !!inspect?.exists && !noMatch

  const create = useMutation({
    mutationFn: () =>
      api.post<{ active: string }>("/api/workspaces", {
        parent_dir: parentDir.trim(),
        name: name.trim(),
        asset_dir: assetValue(),
      }),
    onSuccess: (d) => {
      toast.success(`项目「${d.active}」已新建并进入`)
      qc.invalidateQueries()
      onClose?.()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const open = useMutation({
    mutationFn: () =>
      api.post<{ active: string }>("/api/workspaces/open", {
        root: rootPath.trim(),
        name: name.trim() || undefined,
        asset_dir: assetValue(),
      }),
    onSuccess: (d) => {
      toast.success(`项目「${d.active}」已打开`)
      qc.invalidateQueries()
      onClose?.()
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const pending = create.isPending || open.isPending

  const body = (
    <div className="w-full max-w-[520px] rounded-panel border border-rule-soft bg-panel p-6 max-h-[92svh] overflow-auto">
      <div className="flex items-center gap-3">
        <h1 className="text-[17px] font-semibold">项目</h1>
        {onClose && (
          <>
            <span className="flex-1" />
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭"
              className="grid size-7 place-items-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper"
            >
              <X className="size-3.5" />
            </button>
          </>
        )}
      </div>

      {/* 模式切换 */}
      <div className="mt-4 inline-flex rounded-ctl border border-rule bg-panel p-0.5">
        {(
          [
            ["create", "新建项目", FolderOpen],
            ["open", "打开项目", FolderSearch],
          ] as const
        ).map(([key, label, Icon]) => (
          <button
            key={key}
            type="button"
            onClick={() => {
              setMode(key)
              setInspect(null)
              setAssetDir(ASSET_AUTO)
              setCustomAsset("")
              if (key === "create") setRootPath("")
              else {
                setParentDir("")
                setName("")
              }
            }}
            aria-pressed={mode === key}
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-[4px] px-3 text-[12.5px] transition-colors",
              mode === key ? "bg-hi font-medium text-paper" : "text-ash hover:text-paper"
            )}
          >
            <Icon className="size-3.5" />
            {label}
          </button>
        ))}
      </div>

      <p className="mt-3 text-[12.5px] leading-relaxed text-ash">
        五个内容目录：<span className="font-mono">草稿 / 生成 / 提示词 / 分镜表 / 资产</span>。
        <b className="font-medium">资产目录名各家不同</b>（素材、资产、assets…），
        会自动匹配；匹配不到就由你在下面手动指定。
      </p>

      <div className="mt-5 flex flex-col gap-4">
        {mode === "create" ? (
          <>
            <Field
              label="父目录"
              hint={
                canBrowse
                  ? "项目目录会建在这里面；点右边「浏览」用系统选择框"
                  : "项目目录会建在这里面" // 没有可用实现时别提「浏览」，按钮不在
              }
              value={parentDir}
              onChange={setParentDir}
              placeholder="D:/Notely/workspace/AIGC"
              onBrowse={canBrowse ? () => void browse("parent") : undefined}
              browsing={browsing === "parent"}
            />
            <Field
              label="项目名称"
              hint="建议用剧本或批次名"
              value={name}
              onChange={setName}
              placeholder="复试3"
            />
          </>
        ) : (
          <Field
            label="项目目录"
            hint="已有目录的绝对路径；会自动匹配里面的资产目录"
            value={rootPath}
            onChange={(v) => {
              setRootPath(v)
              setInspect(null)
            }}
            placeholder="D:/Notely/workspace/AIGC/复试2"
            onBrowse={canBrowse ? () => void browse("root") : undefined}
            browsing={browsing === "root"}
          />
        )}

        <div className="flex items-center gap-2.5">
          <button
            type="button"
            disabled={!targetRoot || inspectM.isPending}
            onClick={() => inspectM.mutate(targetRoot)}
            className="inline-flex h-8 items-center gap-1.5 rounded-ctl border border-rule bg-cell px-3 text-[12.5px] font-medium transition-all hover:bg-hi disabled:opacity-45"
          >
            <FolderSearch className="size-3.5" />
            {inspectM.isPending ? "检测中…" : "检测目录"}
          </button>
          {targetRoot && <span className="min-w-0 truncate font-mono text-[11px] text-ash-dim">{targetRoot}</span>}
        </div>

        {inspect && <InspectPanel inspect={inspect} />}

        <AssetDirField
          inspect={inspect}
          value={assetDir}
          custom={customAsset}
          onChange={setAssetDir}
          onCustom={setCustomAsset}
          highlightNoMatch={noMatch}
        />

        <button
          type="button"
          disabled={pending || (mode === "create" ? !createOk : !openOk)}
          onClick={() => (mode === "create" ? create.mutate() : open.mutate())}
          className="mt-1 inline-flex h-9 items-center justify-center rounded-ctl bg-paper text-[13.5px] font-medium text-[#131313] transition-all hover:bg-white active:translate-y-px disabled:opacity-45"
        >
          {pending ? "处理中…" : mode === "create" ? "创建并进入" : "打开并进入"}
        </button>
      </div>
    </div>
  )

  if (onClose) {
    return (
      <div
        className="fixed inset-0 z-80 grid place-items-center bg-black/75 p-6"
        onMouseDown={(e) => {
          if (e.target === e.currentTarget) onClose()
        }}
      >
        {body}
      </div>
    )
  }
  return <div className="grid min-h-svh place-items-center p-6">{body}</div>
}

function InspectPanel({ inspect }: { inspect: InspectResp }) {
  return (
    <div className="rounded-ctl border border-rule-soft bg-cell p-3">
      {!inspect.exists ? (
        <p className="text-[12.5px] text-ash">
          目录还不存在 —— 会按所选名称新建（默认 <span className="font-mono">素材</span>）。
        </p>
      ) : (
        <>
          <p className="text-[12.5px] text-ash">
            目录下已有 <b className="font-medium text-paper">{inspect.subdirs.length}</b> 个子目录：
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {inspect.subdirs.length ? (
              inspect.subdirs.map((d) => (
                <span
                  key={d}
                  className="rounded border border-rule px-1.5 py-px font-mono text-[11px] text-ash"
                >
                  {d}
                </span>
              ))
            ) : (
              <span className="text-[11.5px] text-ash-dim">（空目录）</span>
            )}
          </div>
          <div className="mt-2.5 border-t border-rule-soft pt-2">
            {inspect.asset_dir ? (
              <p className="text-[12.5px]">
                <span className="text-ash">已自动匹配资产目录：</span>
                <b className="font-mono text-paper">{inspect.asset_dir}</b>
              </p>
            ) : (
              <p className="text-[12.5px] text-paper">
                没匹配到候选目录名（{inspect.asset_candidates.slice(0, 6).join(" / ")} …），
                请在下面手动选择。
              </p>
            )}
            {!!Object.keys(inspect.matched).length && (
              <p className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1 text-[11.5px] text-ash-dim">
                {Object.entries(inspect.matched).map(([k, v]) => (
                  <span key={k}>
                    {ROLE_LABEL[k] ?? k} → <span className="font-mono">{v}</span>
                  </span>
                ))}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function AssetDirField({
  inspect,
  value,
  custom,
  onChange,
  onCustom,
  highlightNoMatch,
}: {
  inspect: InspectResp | null
  value: string
  custom: string
  onChange: (v: string) => void
  onCustom: (v: string) => void
  highlightNoMatch: boolean
}) {
  const existing = inspect?.exists ? inspect.subdirs : []
  const candLower = new Set((inspect?.asset_candidates ?? []).map((c) => c.toLowerCase()))

  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[12.5px] font-medium">资产目录</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "h-9 rounded-ctl border bg-cell px-2.5 text-[12.5px] outline-none focus:border-wash-line",
          highlightNoMatch ? "border-[#8a8a8a]" : "border-rule"
        )}
      >
        <option value={ASSET_AUTO}>自动匹配（素材 / 资产 / assets …）</option>
        {existing.map((d) => (
          <option key={d} value={d}>
            {d}
            {candLower.has(d.toLowerCase()) ? "（候选名）" : "（已有目录）"}
          </option>
        ))}
        <option value={ASSET_CUSTOM}>自定义目录名…</option>
        <option value={ASSET_NONE}>暂不指定（稍后在资产库选择）</option>
      </select>

      {value === ASSET_CUSTOM && (
        <input
          value={custom}
          onChange={(e) => onCustom(e.target.value)}
          spellCheck={false}
          placeholder="例如 资产 / 参考素材"
          className="mt-1 h-9 rounded-ctl border border-rule bg-cell px-2.5 font-mono text-[12.5px] outline-none placeholder:font-sans placeholder:text-ash-dim focus:border-wash-line"
        />
      )}

      <span className="text-[11.5px] text-ash-dim">
        {value === ASSET_AUTO
          ? "候选顺序命中；新建项目时目录不存在，会用默认「素材」。"
          : value === ASSET_NONE
            ? "先登记项目，之后在资产管理页选择资产目录。"
            : "会把选定目录当作本项目的资产目录（不存在则新建）。"}
      </span>
    </label>
  )
}

function Field({
  label,
  hint,
  value,
  onChange,
  placeholder,
  onBrowse,
  browsing,
}: {
  label: string
  hint: string
  value: string
  onChange: (v: string) => void
  placeholder: string
  /** 给了就显示「浏览」—— 用系统的文件夹选择框填这个字段 */
  onBrowse?: () => void
  browsing?: boolean
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[12.5px] font-medium">{label}</span>
      {/* 输入框和「浏览」合成一个控件：focus 高亮移交给外层容器 */}
      <div className="flex h-9 items-stretch overflow-hidden rounded-ctl border border-rule bg-cell transition-colors focus-within:border-wash-line">
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          spellCheck={false}
          className="min-w-0 flex-1 bg-transparent px-2.5 font-mono text-[12.5px] outline-none placeholder:font-sans placeholder:text-ash-dim"
        />
        {onBrowse && (
          <button
            type="button"
            // 别让这一下顺带把焦点塞进输入框（label 包裹会做这件事）
            onMouseDown={(e) => e.preventDefault()}
            disabled={browsing}
            onClick={onBrowse}
            title="打开系统的文件夹选择框"
            className="flex flex-none items-center gap-1.5 border-l border-rule-soft px-2.5 text-[11.5px] text-ash transition-colors hover:bg-hi hover:text-paper disabled:opacity-45"
          >
            <FolderOpen className="size-3.5" />
            {browsing ? "选择中…" : "浏览"}
          </button>
        )}
      </div>
      <span className="text-[11.5px] text-ash-dim">{hint}</span>
    </label>
  )
}
