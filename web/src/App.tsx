import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Rail } from "@/components/Rail"
import { TaskBar } from "@/components/TaskBar"
import { AssetLibrary } from "@/pages/AssetLibrary"
import { GachaBoard } from "@/pages/GachaBoard"
import { LibraryPage } from "@/pages/LibraryPage"
import { NovelSearch } from "@/pages/NovelSearch"
import { ShotLocator } from "@/pages/ShotLocator"
import { SplitPanel } from "@/pages/SplitPanel"
import { SettingsPage } from "@/pages/SettingsPage"
import { WorkspaceGate } from "@/pages/WorkspaceGate"
import { api } from "@/lib/api"
import { useHealth } from "@/lib/health"
import { loadPrefs } from "@/lib/prefs"
import { useApp, type View } from "@/store/useApp"
import type { Workspace } from "@/lib/types"

const VIEWS: View[] = ["board", "split", "locator", "assets", "library", "search", "settings"]

function viewFromHash(): View | null {
  const h = location.hash.replace(/^#\/?/, "")
  return (VIEWS as string[]).includes(h) ? (h as View) : null
}

interface WsResp {
  workspaces: Workspace[]
  active: string | null
}

export default function App() {
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const resetFilter = useApp((s) => s.resetFilter)
  const queryClient = useQueryClient()
  const [gateOpen, setGateOpen] = useState(false)

  /* 视图与地址栏 hash 双向同步：能分享单屏链接，浏览器后退键也能用。
     监听器**无条件注册**——关掉 keepOnReload 只是「启动时不恢复」，
     不能顺路把 hash 导航也打死。 */
  useEffect(() => {
    const onHash = () => {
      const v = viewFromHash()
      if (v) setView(v)
    }
    window.addEventListener("hashchange", onHash)
    return () => window.removeEventListener("hashchange", onHash)
  }, [setView])

  /* 启动时按偏好决定落在哪：keepOnReload 关着就回抽卡台（连 hash 一起改回去），
     否则 F5 之后 URL 里的 hash 还在，这个开关就等于没装。 */
  useEffect(() => {
    if (loadPrefs().keepOnReload) {
      const initial = viewFromHash()
      if (initial) setView(initial)
      return
    }
    setView("board")
    if (location.hash !== "#/board") history.replaceState(null, "", "#/board")
  }, [setView])

  useEffect(() => {
    const want = `#/${view}`
    if (location.hash !== want) history.replaceState(null, "", want)
  }, [view])


  /* 启动探测与 Rail 的健康灯共用同一个 queryKey（lib/health.ts）：
     这里只负责「从来没连上过」时的满屏提示；连上之后后端再掉线，
     App 照常渲染，由 Rail 底部的健康灯 + 压暗的旧数据说话（G-8）。 */
  const health = useHealth()

  const ws = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<WsResp>("/api/workspaces"),
    enabled: !!health.data && !health.isError,
    retry: 1,
  })

  const activate = useMutation({
    mutationFn: (name: string) => api.post(`/api/workspaces/${encodeURIComponent(name)}/activate`),
    onSuccess: () => {
      resetFilter()
      queryClient.invalidateQueries()
      toast.success("已切换工作区")
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (health.isPending) return <Splash title="正在连接后端…" body="读一下 /api/health。" />

  // 🔴 只有「从没连上过」才占满屏。曾经连上过又掉线时 health.data 还在，
  // 走下面的正常渲染——页面留着，Rail 显示「后端离线」，后端回来后自动恢复。
  if (health.isError && !health.data) {
    return (
      <Splash
        title="后端没连上"
        body={
          <>
            在 <span className="font-mono">backend/</span> 目录跑{" "}
            <span className="font-mono">uv run uvicorn main:app --port 8000</span>，然后刷新这一页。
          </>
        }
      />
    )
  }

  if (ws.isPending) return <Splash title="读取工作区…" body="列一下已经注册过的工作区。" />

  if (ws.isError) {
    return (
      <Splash
        title="工作区列表读不出来"
        body={(ws.error as Error)?.message ?? "后端返回了错误。"}
      />
    )
  }

  const workspaces = ws.data?.workspaces ?? []
  if (!workspaces.length || !health.data?.active_workspace) return <WorkspaceGate />

  const active = ws.data?.active ?? health.data.active_workspace

  return (
    <div className="flex min-h-svh max-[980px]:flex-col-reverse">
      <Rail
        workspaces={workspaces}
        active={active}
        onPickWorkspace={(n) => activate.mutate(n)}
        switching={activate.isPending}
        onOpenProjects={() => setGateOpen(true)}
      />

      <main className="flex min-w-0 flex-1 flex-col max-[980px]:pb-[58px]">
        {/* 全局顶部状态栏：sticky 钉在视口顶（用户定稿），只在有任务时占位 */}
        <TaskBar />
        {view === "board" && <GachaBoard workspace={active} />}
        {view === "split" && <SplitPanel />}
        {view === "locator" && <ShotLocator />}
        {view === "assets" && <AssetLibrary workspace={active} />}
        {view === "library" && <LibraryPage />}
        {view === "search" && <NovelSearch />}
        {view === "settings" && <SettingsPage workspace={active} />}
      </main>

      {gateOpen && <WorkspaceGate onClose={() => setGateOpen(false)} />}
    </div>
  )
}

function Splash({ title, body }: { title: string; body: React.ReactNode }) {
  return (
    <div className="grid min-h-svh place-items-center p-6">
      <div className="max-w-[52ch] rounded-panel border border-rule-soft bg-panel p-6 text-center">
        <h1 className="text-[15px] font-medium">{title}</h1>
        <p className="mt-2 text-[12.5px] leading-relaxed text-ash">{body}</p>
      </div>
    </div>
  )
}
