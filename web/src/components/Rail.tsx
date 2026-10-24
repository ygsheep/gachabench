import {
  BookSearch,
  ChevronsLeft,
  ChevronsRight,
  FileText,
  Film,
  FolderPlus,
  Images,
  LayoutGrid,
  Pencil,
  RotateCw,
  Settings,
} from "lucide-react"
import { useEffect, useState } from "react"
import { Link, useRouterState } from "@tanstack/react-router"
import { cn } from "cn"
import { WorkspaceNameDialog } from "@/components/WorkspaceNameDialog"
import { useHealth } from "@/lib/health"
import { PREFS_KEY, loadPrefs, writeJSON } from "@/lib/prefs"
import { wsTitle } from "@/lib/workspace"
import type { Workspace } from "@/lib/types"

/**
 * 侧栏：两个状态，一个组件。
 *
 * - 收起（56px）：只留图标。给内容让宽度，日常干活用这个。
 * - 展开（208px）：图标 + 名称，另外把「当前项目 / 切项目 / 健康」摊开写清楚。
 *
 * 🔴 两态的**行高必须一样**（40 + 2 = 42）：高亮块是靠 `translateY(index * ROW)`
 * 滑过去的，行高一变，滑动就会歪。所以收起时不是把行压扁，只是把文字宽度收成 0。
 *
 * 🔴 宽度动画只动 `width`，不动 `padding`：padding 参与布局重排，动画会掉帧；
 * 文字用 `w-0 opacity-0` 收起来，靠 `overflow-hidden` 防止换行抖动。
 *
 * 🔴 移动端（<980px）是底部横条，没有「收起」这回事：用 `max-[980px]:` 那组类
 * 把文字和布局强制回原样，不受 `open` 影响。状态存 localStorage（`gb.ui.v1.railOpen`）。
 */

const NAV = [
  { key: "assets", to: "/assets", label: "资产库", Icon: Images },
  { key: "search", to: "/search", label: "小说搜索", Icon: BookSearch },
  { key: "prompts", to: "/prompts", label: "提示词工程", Icon: FileText },
  { key: "review", to: "/review", label: "审片台", Icon: LayoutGrid },
  { key: "produce", to: "/produce", label: "制作", Icon: Film },
  { key: "settings", to: "/settings", label: "设置", Icon: Settings },
] as const

type NavKey = (typeof NAV)[number]["key"]

function activeKey(pathname: string): NavKey {
  if (pathname.startsWith("/search")) return "search"
  if (pathname.startsWith("/prompts")) return "prompts"
  if (pathname.startsWith("/review")) return "review"
  if (pathname.startsWith("/produce")) return "produce"
  if (pathname.startsWith("/settings")) return "settings"
  return "assets"
}

/** 行高（40）+ 行间距（2）。高亮块的位移靠这个常量，改行高必须一起改。 */
const ROW = 42

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
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const current = activeKey(pathname)
  const health = useHealth()
  const [renaming, setRenaming] = useState(false)
  const [open, setOpen] = useState(() => loadPrefs().railOpen)
  const activeWs = workspaces.find((w) => w.name === active) ?? null
  const online = !!health.data && !health.isError
  const ffmpeg = health.data?.ffmpeg ?? false
  const comfy = health.data?.comfyui.configured ?? false
  const index = Math.max(
    0,
    NAV.findIndex((n) => n.key === current)
  )

  function toggle() {
    const next = !open
    setOpen(next)
    // 直接落盘（不走 savePrefs 的防抖）：这是个一眼看得见结果的开关
    writeJSON(PREFS_KEY, { ...loadPrefs(), railOpen: next })
  }

  /* Ctrl/Cmd+B 收起/展开。正在输入框里打字时不抢这个键。 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "b") return
      const t = e.target as HTMLElement | null
      const tag = t?.tagName ?? ""
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t?.isContentEditable) return
      e.preventDefault()
      toggle()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  })

  return (
    <aside
      className={cn(
        "sticky top-0 z-50 flex h-svh flex-none flex-col border-r border-rule-soft bg-panel",
        "transition-[width] duration-150 ease-[cubic-bezier(.2,.8,.25,1)]",
        open ? "w-[208px]" : "w-[56px]",
        "max-[980px]:fixed max-[980px]:inset-x-0 max-[980px]:bottom-0 max-[980px]:top-auto max-[980px]:h-auto max-[980px]:w-auto max-[980px]:flex-row max-[980px]:border-r-0 max-[980px]:border-t"
      )}
    >
      {/* 品牌 + 折叠钮：收起时整块是一个按钮，悬停把方标换成箭头 */}
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-label={open ? "收起侧栏" : "展开侧栏"}
        title={open ? "收起侧栏（Ctrl+B）" : "展开侧栏（Ctrl+B）"}
        className={cn(
          "group/brand flex h-12 w-full flex-none items-center gap-2 border-b border-rule-soft text-ash transition-colors hover:bg-hi hover:text-paper",
          open ? "px-3" : "justify-center px-0",
          "max-[980px]:hidden"
        )}
      >
        <span className="relative grid size-4 flex-none place-items-center">
          {/* 方标：四格里的最后一格是灰的，呼应「已归档」那套形状语言 */}
          <span className="grid size-4 grid-cols-2 gap-[2px] transition-opacity group-hover/brand:opacity-0">
            <span className="rounded-[1px] bg-paper" />
            <span className="rounded-[1px] bg-paper" />
            <span className="rounded-[1px] bg-paper" />
            <span className="rounded-[1px] bg-ash-dim" />
          </span>
          <ChevronsRight
            className={cn(
              "absolute size-4 opacity-0 transition-opacity group-hover/brand:opacity-100",
              open && "hidden"
            )}
          />
        </span>
        <span
          className={cn(
            "min-w-0 truncate text-[12.5px] font-medium tracking-tight text-paper transition-opacity duration-150",
            open ? "flex-1 opacity-100" : "w-0 opacity-0"
          )}
        >
          GachaBench
        </span>
        <ChevronsLeft
          className={cn(
            "size-3.5 flex-none transition-opacity",
            open ? "opacity-60 group-hover/brand:opacity-100" : "hidden"
          )}
        />
      </button>

      <nav
        className={cn(
          "relative flex flex-col gap-0.5 p-2",
          "max-[980px]:w-full max-[980px]:flex-row max-[980px]:gap-0 max-[980px]:p-1.5"
        )}
      >
        {/* 高亮块：宽度跟着侧栏走（inset-x-2），只有纵向位移是算出来的 */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-2 top-2 h-10 rounded-ctl border border-rule bg-hi transition-transform duration-300 ease-[cubic-bezier(.2,.8,.25,1)] max-[980px]:hidden"
          style={{ transform: `translateY(${index * ROW}px)` }}
        >
          <span className="absolute -left-px bottom-[10px] top-[10px] w-0.5 rounded bg-white" />
        </span>

        {NAV.map(({ key, to, label, Icon }) => {
          const on = current === key
          return (
            <Link
              key={key}
              to={to}
              aria-current={on ? "page" : undefined}
              title={open ? undefined : label}
              className={cn(
                "relative z-1 flex h-10 items-center gap-2.5 rounded-ctl transition-colors",
                open ? "px-2.5" : "justify-center px-0",
                on ? "font-medium text-paper" : "text-ash hover:text-paper",
                // 移动端底部横条：图标在上、文字在下，和折叠无关
                "max-[980px]:h-11 max-[980px]:flex-1 max-[980px]:flex-col max-[980px]:justify-center max-[980px]:gap-1 max-[980px]:px-0 max-[980px]:text-[10.5px]",
                on && "max-[980px]:bg-hi"
              )}
            >
              <Icon
                className={cn("size-[17px] flex-none transition-transform active:scale-90", on && "text-white")}
                strokeWidth={1.7}
              />
              <span
                className={cn(
                  "truncate transition-opacity duration-150",
                  open ? "min-w-0 flex-1 opacity-100" : "pointer-events-none w-0 opacity-0",
                  "max-[980px]:w-auto max-[980px]:flex-none max-[980px]:opacity-100"
                )}
              >
                {label}
              </span>
            </Link>
          )
        })}
      </nav>

      <div
        className={cn(
          "mt-auto flex flex-col gap-2 border-t border-rule-soft p-2 max-[980px]:hidden",
          !open && "items-center gap-1.5"
        )}
      >
        {open ? (
          <div className="flex gap-1">
            <button
              type="button"
              onClick={onOpenProjects}
              title="新建 / 打开项目"
              className="inline-flex h-7 flex-1 items-center justify-center gap-1 rounded-ctl border border-rule bg-cell text-[10.5px] font-medium text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px"
            >
              <FolderPlus className="size-3.5" />
              项目
            </button>
            {/* 改名只改显示名（label），不碰磁盘目录与库 —— 见 WorkspaceNameDialog */}
            <button
              type="button"
              onClick={() => setRenaming(true)}
              disabled={!activeWs}
              title={`改项目名（当前显示「${wsTitle(activeWs)}」，目录名 ${active}）`}
              aria-label="改项目名"
              className="grid h-7 w-7 flex-none place-items-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px disabled:opacity-45"
            >
              <Pencil className="size-3" />
            </button>
          </div>
        ) : (
          <>
            <button
              type="button"
              onClick={onOpenProjects}
              title="新建 / 打开项目"
              aria-label="新建 / 打开项目"
              className="grid size-8 place-items-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px"
            >
              <FolderPlus className="size-3.5" />
            </button>
            <button
              type="button"
              onClick={() => setRenaming(true)}
              disabled={!activeWs}
              title={`改项目名（当前「${wsTitle(activeWs)}」）`}
              aria-label="改项目名"
              className="grid size-8 place-items-center rounded-ctl border border-rule bg-cell text-ash transition-colors hover:bg-hi hover:text-paper active:translate-y-px disabled:opacity-45"
            >
              <Pencil className="size-3" />
            </button>
          </>
        )}

        {/* 切项目：展开时是一个下拉（能直接看到全部项目名）；收起时塞不下，交给「项目」面板 */}
        {open && (
          <select
            value={active}
            disabled={switching}
            onChange={(e) => {
              if (e.target.value !== active) onPickWorkspace(e.target.value)
            }}
            aria-label="切换工作区"
            title={`${wsTitle(activeWs)}（目录名 ${active}）`}
            className="h-7 w-full truncate rounded-ctl border border-rule bg-cell px-1.5 text-[10.5px] outline-none focus:border-wash-line disabled:opacity-45"
          >
            {workspaces.map((w) => (
              <option key={w.name} value={w.name}>
                {wsTitle(w)}
              </option>
            ))}
          </select>
        )}

        {/* 健康区（G-8）：后端常驻轮询，ffmpeg / ComfyUI 跟着同一份载荷刷新。
            后端离线时下面两个数是上次成功的快照，压暗表示「旧数据」。 */}
        <BackendStatus health={health} compact={!open} />
        <div className={cn("flex flex-col gap-1.5", !open && "items-center gap-1.5", !online && "opacity-45")}>
          <Health on={ffmpeg} label="ffmpeg" compact={!open} />
          <Health on={comfy} label={comfy ? "ComfyUI" : "ComfyUI 未配"} compact={!open} />
        </div>
      </div>

      {renaming && activeWs && (
        <WorkspaceNameDialog ws={activeWs} onClose={() => setRenaming(false)} />
      )}
    </aside>
  )
}

/** 后端在线状态（等价 NBE 侧栏底部的 conn 灯，改写成 GB 的形状+明度语言）：
 *  实心亮点 = 通；虚线环 + 暗芯 = 断；空心细环 = 还没探到过。
 *  点击重检，探测时刷新图标转。`compact` = 只给一个点（收起态），文案进 title。 */
function BackendStatus({
  health,
  compact,
}: {
  health: ReturnType<typeof useHealth>
  compact?: boolean
}) {
  const online = !!health.data && !health.isError
  const offline = health.isError
  const text = online ? "后端在线" : offline ? "后端离线" : "后端探测中"
  return (
    <button
      type="button"
      onClick={() => health.refetch()}
      title={`${text} · 点击重新检测`}
      aria-label={`${text}，点击重新检测`}
      className={cn(
        "flex items-center gap-1.5 rounded-ctl text-left text-[9.5px] text-ash transition-colors hover:bg-hi hover:text-paper",
        compact ? "size-8 justify-center" : "h-7 w-full px-1.5"
      )}
    >
      <span
        className={cn(
          "size-[6px] flex-none rounded-full",
          online && "bg-white shadow-[0_0_0_3px_var(--gb-wash)]",
          offline && "border border-dashed border-wash-line bg-[#555]",
          !online && !offline && "border border-wash-line"
        )}
      />
      {!compact && (
        <>
          <span className="truncate">{text}</span>
          <RotateCw
            className={cn("ml-auto size-3 flex-none opacity-50", health.isFetching && "animate-spin")}
            strokeWidth={2}
          />
        </>
      )}
    </button>
  )
}

/** 连接状态点：成功 = 绿（全站唯一特批状态色，只此一处），未连接 = 灰白。
 *  后端离线时整组压暗表示「旧数据快照」，颜色照实给。`compact` = 只留点。 */
function Health({ on, label, compact }: { on: boolean; label: string; compact?: boolean }) {
  return (
    <div
      title={label}
      className={cn("flex items-center gap-1.5 text-[9.5px] text-ash", compact ? "justify-center" : "pl-px")}
    >
      <span
        className={cn(
          "size-[5px] flex-none rounded-full",
          on ? "bg-ok shadow-[0_0_0_3px_var(--gb-ok-wash)]" : "bg-[#c6c6c6]"
        )}
      />
      {!compact && <span className="truncate">{label}</span>}
    </div>
  )
}
