import {
  BookOpen,
  BookSearch,
  FileText,
  FolderPlus,
  Images,
  LayoutGrid,
  RotateCw,
  Scissors,
  Settings,
} from "lucide-react"
import { cn } from "cn"
import { useApp, type View } from "@/store/useApp"
import { useHealth } from "@/lib/health"
import type { Workspace } from "@/lib/types"

const NAV: { key: View; label: string; Icon: typeof LayoutGrid }[] = [
  { key: "board", label: "抽卡台", Icon: LayoutGrid },
  { key: "split", label: "拆分台", Icon: Scissors },
  { key: "locator", label: "定位工单", Icon: FileText },
  { key: "assets", label: "资产管理", Icon: Images },
  { key: "library", label: "原著库", Icon: BookOpen },
  { key: "search", label: "小说搜索", Icon: BookSearch },
  { key: "settings", label: "设置", Icon: Settings },
]

const ROW = 54 // 52px 按钮 + 2px 间距

export function Rail({
  workspaces,
  active,
  onPickWorkspace,
  switching,
  onOpenProjects,
}: {
  workspaces: Workspace[]
  active: string
  onPickWorkspace: (name: string) => void
  switching: boolean
  onOpenProjects: () => void
}) {
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const health = useHealth()
  const online = !!health.data && !health.isError
  const ffmpeg = health.data?.ffmpeg ?? false
  const comfy = health.data?.comfyui.configured ?? false
  const index = Math.max(
    0,
    NAV.findIndex((n) => n.key === view)
  )

  return (
    <aside className="sticky top-0 z-50 flex h-svh w-[84px] flex-none flex-col border-r border-rule-soft bg-panel max-[980px]:fixed max-[980px]:inset-x-0 max-[980px]:bottom-0 max-[980px]:top-auto max-[980px]:h-auto max-[980px]:w-auto max-[980px]:flex-row max-[980px]:border-r-0 max-[980px]:border-t">
      <div className="border-b border-rule-soft py-4 text-center text-[12.5px] font-medium tracking-tight max-[980px]:hidden">
        GachaBench
      </div>

      <nav className="relative flex flex-col gap-0.5 p-2 max-[980px]:w-full max-[980px]:flex-row max-[980px]:gap-0 max-[980px]:p-1.5">
        <span
          aria-hidden
          className="pointer-events-none absolute left-2 top-2 h-[52px] w-[68px] rounded-ctl border border-rule bg-hi transition-transform duration-300 ease-[cubic-bezier(.2,.8,.25,1)] max-[980px]:hidden"
          style={{ transform: `translateY(${index * ROW}px)` }}
        >
          <span className="absolute -left-px bottom-[13px] top-[13px] w-0.5 rounded bg-white" />
        </span>

        {NAV.map(({ key, label, Icon }) => {
          const on = view === key
          return (
            <button
              key={key}
              type="button"
              onClick={() => setView(key)}
              aria-current={on}
              className={cn(
                "relative z-1 flex h-[52px] flex-col items-center justify-center gap-1 rounded-ctl text-[10.5px] transition-colors",
                "max-[980px]:h-11 max-[980px]:flex-1",
                on ? "font-medium text-paper" : "text-ash hover:text-paper",
                on && "max-[980px]:bg-hi"
              )}
            >
              <Icon
                className={cn("size-[17px] transition-transform active:scale-90", on && "text-white")}
                strokeWidth={1.7}
              />
              {label}
            </button>
          )
        })}
      </nav>

      <div className="mt-auto flex flex-col gap-2 border-t border-rule-soft p-2 max-[980px]:hidden">
        <button
          type="button"
          onClick={onOpenProjects}
          title="新建 / 打开项目"
          className="inline-flex h-7 w-full items-center justify-center gap-1.5 rounded-ctl border border-rule bg-cell text-[10.5px] font-medium text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px"
        >
          <FolderPlus className="size-3.5" />
          项目
        </button>
        <select
          value={active}
          disabled={switching}
          onChange={(e) => {
            if (e.target.value !== active) onPickWorkspace(e.target.value)
          }}
          aria-label="切换工作区"
          title={active}
          className="h-7 w-full truncate rounded-ctl border border-rule bg-cell px-1.5 text-[10.5px] outline-none focus:border-wash-line disabled:opacity-45"
        >
          {workspaces.map((w) => (
            <option key={w.name} value={w.name}>
              {w.name}
            </option>
          ))}
        </select>

        {/* 健康区（G-8）：后端常驻轮询，ffmpeg / ComfyUI 跟着同一份载荷刷新。
            后端离线时下面两个数是上次成功的快照，压暗表示「旧数据」。 */}
        <BackendStatus health={health} />
        <div className={cn("flex flex-col gap-1.5", !online && "opacity-45")}>
          <Health on={ffmpeg} label="ffmpeg" />
          <Health on={comfy} label={comfy ? "ComfyUI" : "ComfyUI 未配"} />
        </div>
      </div>
    </aside>
  )
}

/** 后端在线状态（等价 NBE 侧栏底部的 conn 灯，改写成 GB 的形状+明度语言）：
 *  实心亮点 = 通；虚线环 + 暗芯 = 断；空心细环 = 还没探到过。
 *  点击重检，探测时刷新图标转。 */
function BackendStatus({
  health,
}: {
  health: ReturnType<typeof useHealth>
}) {
  const online = !!health.data && !health.isError
  const offline = health.isError
  return (
    <button
      type="button"
      onClick={() => health.refetch()}
      title="点击重新检测后端连接"
      className="flex h-7 w-full items-center gap-1.5 rounded-ctl px-1.5 text-left text-[9.5px] text-ash transition-colors hover:bg-hi hover:text-paper"
    >
      <span
        className={cn(
          "size-[6px] flex-none rounded-full",
          online && "bg-white shadow-[0_0_0_3px_var(--gb-wash)]",
          offline && "border border-dashed border-wash-line bg-[#555]",
          !online && !offline && "border border-wash-line"
        )}
      />
      <span className="truncate">{online ? "后端在线" : offline ? "后端离线" : "后端探测中"}</span>
      <RotateCw
        className={cn("ml-auto size-3 flex-none opacity-50", health.isFetching && "animate-spin")}
        strokeWidth={2}
      />
    </button>
  )
}

/** 连接状态点：成功 = 绿（全站唯一特批状态色，只此一处），未连接 = 灰白。
 *  后端离线时整组压暗表示「旧数据快照」，颜色照实给。 */
function Health({ on, label }: { on: boolean; label: string }) {
  return (
    <div className="flex items-center gap-1.5 pl-px text-[9.5px] text-ash">
      <span
        className={cn(
          "size-[5px] flex-none rounded-full",
          on ? "bg-ok shadow-[0_0_0_3px_var(--gb-ok-wash)]" : "bg-[#c6c6c6]"
        )}
      />
      <span className="truncate">{label}</span>
    </div>
  )
}
