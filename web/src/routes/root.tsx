import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  createRootRoute,
  Link,
  Outlet,
  redirect,
  useRouterState,
} from "@tanstack/react-router"
import { toast } from "sonner"
import { Rail } from "@/components/Rail"
import { TaskBar } from "@/components/TaskBar"
import { WorkspaceGate } from "@/pages/WorkspaceGate"
import { api } from "@/lib/api"
import { useHealth } from "@/lib/health"
import { WorkspaceProvider } from "@/lib/workspace"
import { legacyHashKey } from "@/app/legacy-hash"
import { useApp } from "@/store/useApp"
import type { Workspace } from "@/lib/types"

interface WsResp {
  workspaces: Workspace[]
  active: string | null
}

export const rootRoute = createRootRoute({
  component: RootLayout,
  beforeLoad: ({ location }) => {
    const legacy = legacyHashKey(location.hash)
    if (legacy === "board") throw redirect({ to: "/review", replace: true })
    if (legacy === "split" || legacy === "locator") {
      throw redirect({ to: "/prompts", replace: true })
    }
    if (legacy === "library") {
      throw redirect({ to: "/assets", search: { collection: "novel" }, replace: true })
    }
    if (legacy === "search") throw redirect({ to: "/search", replace: true })
    if (legacy === "assets") throw redirect({ to: "/assets", replace: true })
    if (legacy === "settings") throw redirect({ to: "/settings", replace: true })
  },
  errorComponent: RootError,
  notFoundComponent: NotFound,
})

function RootLayout() {
  const resetFilter = useApp((s) => s.resetFilter)
  const queryClient = useQueryClient()
  const [gateOpen, setGateOpen] = useState(false)

  /* 启动探测与 Rail 的健康灯共用同一个 queryKey（lib/health.ts）：
     这里只负责「从来没连上过」时的满屏提示；连上之后后端再掉线，
     根布局照常渲染，由 Rail 底部的健康灯 + 压暗的旧数据说话。 */
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
    <WorkspaceProvider value={active}>
      <div className="flex min-h-svh max-[980px]:flex-col-reverse">
        <Rail
          workspaces={workspaces}
          active={active}
          onPickWorkspace={(n) => activate.mutate(n)}
          switching={activate.isPending}
          onOpenProjects={() => setGateOpen(true)}
        />

        <main className="flex min-w-0 flex-1 flex-col max-[980px]:pb-[58px]">
          {/* 全局顶部状态栏：sticky 钉在视口顶，只在有任务时占位 */}
          <TaskBar />
          <Outlet />
        </main>

        {gateOpen && <WorkspaceGate onClose={() => setGateOpen(false)} />}
      </div>
    </WorkspaceProvider>
  )
}

function RootError({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error ?? "")
  return (
    <div className="grid min-h-svh place-items-center p-6">
      <div className="max-w-[58ch] rounded-panel border border-rule-soft bg-panel p-6">
        <h1 className="text-[15px] font-medium">页面出错了</h1>
        <p className="mt-2 break-words text-[12.5px] leading-relaxed text-ash">
          {message || "未知错误"}
        </p>
        <Link
          to="/assets"
          className="mt-4 inline-flex h-8 items-center rounded-ctl border border-rule bg-cell px-3 text-[12.5px] font-medium transition-colors hover:bg-hi"
        >
          返回资产库
        </Link>
      </div>
    </div>
  )
}

function NotFound() {
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  return (
    <div className="grid min-h-svh place-items-center p-6">
      <div className="max-w-[52ch] rounded-panel border border-rule-soft bg-panel p-6 text-center">
        <h1 className="text-[15px] font-medium">页面不存在</h1>
        <p className="mt-2 break-all font-mono text-[12px] text-ash">{pathname}</p>
        <Link
          to="/assets"
          className="mt-4 inline-flex h-8 items-center rounded-ctl border border-rule bg-cell px-3 text-[12.5px] font-medium transition-colors hover:bg-hi"
        >
          返回资产库
        </Link>
      </div>
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
